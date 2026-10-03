"""Release 0.10.0: the behaviour changes and pinned behaviours that cross the new features.

* ``pytest.exit``, ``unittest.SkipTest`` and ``bdb.BdbQuit`` raised in a lazy ``conditional``
  case, a ``guard`` condition, an ``all_satisfy`` factory or an ``eventually`` sample go on:
  the session exits, the test is skipped, or it fails with that exception, and no failed check
  is left behind. An ordinary exception there is still a failed check with an error note.
* An xfailed test whose checks failed stays XFAIL (``pytest.xfail()`` after the checks, or a
  marker), and its reason ends with the failure headline in ``-rx`` and junitxml.
* Pinned (documented, not changed in 0.10.0): a unittest ``@expectedFailure`` test whose only
  failures are soft checks is an "Unexpected success"; with pytest 9 subtests, the subtest that
  made a failed check passes and the whole test fails with it.
* ``verify.record(check)`` of a check a composite took in: a copy when the composite did not
  select it, the check itself when it did (``tests/test_regressions_0_9.py`` has the basics).
* ``verify.fail(str(exc))`` with an empty message is a failed check named ``fail``.
"""
from __future__ import annotations

import bdb
import importlib.util
import inspect
import json
import unittest
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List

import pytest

from pytest_verifier import ChecksFailedError, checks
from pytest_verifier._run import Run, recording_verify

#: pytest 9 has a built-in ``subtests`` fixture.
_HAS_SUBTESTS = importlib.util.find_spec("_pytest.subtests") is not None


def _recording():
    run = Run()
    run.phase = "call"
    return run, recording_verify(run)


def _json_round_trip(record: Any) -> Any:
    return json.loads(json.dumps(record, allow_nan=False))


def _next_line() -> int:
    """The number of the line after the caller's."""
    frame = inspect.currentframe()
    assert frame is not None and frame.f_back is not None
    return frame.f_back.f_lineno + 1


def _junit_cases(pytester: pytest.Pytester) -> Dict[str, ET.Element]:
    root = ET.parse(str(pytester.path / "junit.xml")).getroot()
    return {case.get("name", ""): case for case in root.iter("testcase")}


# ---------------------------------------------------------------------------
# Exceptions that end the test or the session go on through composites and samples
# ---------------------------------------------------------------------------


def _exit() -> Any:
    pytest.exit("operator quit")


def _unittest_skip() -> Any:
    raise unittest.SkipTest("no bench")


def _debugger_quit() -> Any:
    raise bdb.BdbQuit()


def _ordinary() -> Any:
    raise ZeroDivisionError("division by zero")


_ENDING = [
    pytest.param(_exit, pytest.exit.Exception, id="pytest.exit"),
    pytest.param(_unittest_skip, unittest.SkipTest, id="unittest.SkipTest"),
    pytest.param(_debugger_quit, bdb.BdbQuit, id="bdb.BdbQuit"),
]


def _conditional_case(verify: Any, raising: Callable[[], Any]) -> Any:
    return verify.conditional(
        1, cases={1: raising, 2: lambda: verify.is_true(1, name="Two")}, name="Mode"
    )


def _conditional_default(verify: Any, raising: Callable[[], Any]) -> Any:
    return verify.conditional(
        9, cases={1: lambda: verify.is_true(1, name="One")}, default=raising, name="Mode"
    )


def _guard_condition(verify: Any, raising: Callable[[], Any]) -> Any:
    return verify.guard(
        [(raising, "powered", lambda: verify.is_true(1, name="On"))], name="Power"
    )


def _guard_check(verify: Any, raising: Callable[[], Any]) -> Any:
    return verify.guard([(True, "powered", raising)], name="Power")


def _all_satisfy_factory(verify: Any, raising: Callable[[], Any]) -> Any:
    return verify.all_satisfy([1, 2], lambda item: raising(), name="Rails")


def _all_satisfy_items(verify: Any, raising: Callable[[], Any]) -> Any:
    def items():
        raising()
        yield 1

    return verify.all_satisfy(items(), lambda item: verify.is_true(item, name="x"), name="Rails")


def _eventually_sample(verify: Any, raising: Callable[[], Any]) -> Any:
    return verify.eventually(raising, timeout=0, name="Settles")


_SITES = [
    pytest.param(_conditional_case, id="conditional-case"),
    pytest.param(_conditional_default, id="conditional-default"),
    pytest.param(_guard_condition, id="guard-condition"),
    pytest.param(_guard_check, id="guard-check"),
    pytest.param(_all_satisfy_factory, id="all_satisfy-factory"),
    pytest.param(_all_satisfy_items, id="all_satisfy-items"),
    pytest.param(_eventually_sample, id="eventually-sample"),
]


class TestEndingExceptionsGoOn:
    @pytest.mark.parametrize("required", [False, True], ids=["verify", "verify.require"])
    @pytest.mark.parametrize("site", _SITES)
    @pytest.mark.parametrize("raising, expected", _ENDING)
    def test_it_goes_on_and_records_nothing(self, site, raising, expected, required):
        run, verify = _recording()
        before = verify.is_true(True, name="before")
        with pytest.raises(expected):
            site(verify.require if required else verify, raising)
        assert run.records == [before]

    @pytest.mark.parametrize("site", _SITES)
    @pytest.mark.parametrize("raising, expected", _ENDING)
    def test_the_builder_lets_it_through_too(self, site, raising, expected):
        with pytest.raises(expected):
            site(checks, raising)

    @pytest.mark.parametrize("raising, expected", _ENDING)
    def test_the_checks_passed_to_a_conditional_go_with_it(self, raising, expected):
        run, verify = _recording()
        other = verify.equal(1, 2, name="Other mode")
        fallback = verify.equal(1, 2, name="Fallback")
        with pytest.raises(expected):
            verify.conditional(1, cases={1: raising, 2: other}, default=fallback, name="Mode")
        assert run.records == []

    @pytest.mark.parametrize("raising, expected", _ENDING)
    def test_the_checks_passed_to_a_guard_go_with_it(self, raising, expected):
        run, verify = _recording()
        later = verify.equal(1, 2, name="Later branch")
        fallback = verify.equal(1, 2, name="Fallback")
        with pytest.raises(expected):
            verify.guard([(raising, "powered", later)], default=fallback, name="Power")
        assert run.records == []

    @pytest.mark.parametrize("raising, expected", _ENDING)
    def test_checks_a_lazy_child_made_before_it_raised_stay_on_their_own(
        self, raising, expected
    ):
        run, verify = _recording()

        def child() -> Any:
            verify.is_true(True, name="Link up")
            return raising()

        with pytest.raises(expected):
            verify.conditional(1, cases={1: child}, name="Mode")
        assert [record["name"] for record in run.records] == ["Link up"]

    @pytest.mark.parametrize("raising, expected", _ENDING)
    def test_children_a_factory_made_before_it_raised_stay_on_their_own(
        self, raising, expected
    ):
        run, verify = _recording()

        def factory(item: int) -> Any:
            if item == 2:
                raising()
            return verify.greater(item, 0, name=f"item {item}")

        with pytest.raises(expected):
            verify.all_satisfy([1, 2, 3], factory, name="Rails")
        assert [record["name"] for record in run.records] == ["item 1"]


_ORDINARY_ERRORS = [
    pytest.param(
        _conditional_case,
        "case 1 raised ZeroDivisionError: division by zero",
        "[mode=1 → no check] (case 1 raised ZeroDivisionError: division by zero)",
        id="conditional-case",
    ),
    pytest.param(
        _conditional_default,
        "default raised ZeroDivisionError: division by zero",
        "[mode=9 → no case matched: 1] (default raised ZeroDivisionError: division by zero)",
        id="conditional-default",
    ),
    pytest.param(
        _guard_condition,
        "condition of branch 0 (powered) raised ZeroDivisionError: division by zero",
        "[→ no branch chosen] "
        "(condition of branch 0 (powered) raised ZeroDivisionError: division by zero)",
        id="guard-condition",
    ),
    pytest.param(
        _guard_check,
        "branch 0 (powered) raised ZeroDivisionError: division by zero",
        "[→ powered] (branch 0 (powered) raised ZeroDivisionError: division by zero)",
        id="guard-check",
    ),
    pytest.param(
        _all_satisfy_factory,
        "descriptor_factory raised ZeroDivisionError: division by zero for item 0",
        "no item checked "
        "(descriptor_factory raised ZeroDivisionError: division by zero for item 0)",
        id="all_satisfy-factory",
    ),
    pytest.param(
        _all_satisfy_items,
        "iterating items raised ZeroDivisionError: division by zero",
        "no item checked (iterating items raised ZeroDivisionError: division by zero)",
        id="all_satisfy-items",
    ),
]


class TestOrdinaryExceptionsAreFailedChecks:
    @pytest.mark.parametrize("site, error, detail", _ORDINARY_ERRORS)
    def test_the_composite_fails_with_an_error(self, site, error, detail):
        run, verify = _recording()
        record = site(verify, _ordinary)
        assert run.records == [record]
        assert record["passed"] is False
        assert record["error"] == error
        assert record["detail"] == detail
        assert checks.evaluate(record) is False
        assert checks.evaluate(_json_round_trip(record)) is False

    @pytest.mark.parametrize("site, error, detail", _ORDINARY_ERRORS)
    def test_the_builder_fails_the_same_way(self, site, error, detail):
        built = site(checks, _ordinary)
        assert built["error"] == error
        [result] = checks.evaluate_detailed(built)
        assert result["passed"] is False and result["error"] == error

    def test_a_raising_sample_is_a_failed_try(self):
        run, verify = _recording()
        line = _next_line() + 2

        def sample() -> Any:
            raise ZeroDivisionError("division by zero")

        record = verify.eventually(sample, timeout=0, name="Settles")
        assert run.records == [record]
        assert record["passed"] is False and "error" not in record
        sample_error = record["sample_error"]
        assert sample_error.startswith("raised ZeroDivisionError: division by zero (at ")
        assert sample_error.endswith(f"test_release_0_10.py:{line})")
        assert record["detail"] == f"never passed in 0 s (1 try), last: the sample {sample_error}"
        assert checks.evaluate(record) is False
        assert checks.evaluate(_json_round_trip(record)) is False


_SITE_SOURCES = {
    "conditional-case": (
        'verify.conditional(1, cases={1: RAISE, 2: verify.equal(1, 2, name="Other")}, '
        'name="Mode")'
    ),
    "guard-condition": (
        'verify.guard([(RAISE, "powered", lambda: verify.is_true(1, name="On"))], name="Power")'
    ),
    "all_satisfy-factory": 'verify.all_satisfy([1, 2], lambda item: RAISE(), name="Rails")',
    "eventually-sample": 'verify.eventually(RAISE, timeout=0, name="Settles")',
}


def _bench(raising: str, sites: List[str]) -> str:
    """A test module with one test per site, each with a passing check before the site."""
    tests = "\n".join(
        f"def test_{site.replace('-', '_')}(verify):\n"
        f'    verify.is_true(True, name="Before")\n'
        f"    {_SITE_SOURCES[site]}\n"
        for site in sites
    )
    return (
        "import bdb\nimport unittest\n\nimport pytest\n\n\n"
        f"def RAISE(*args):\n    {raising}\n\n\n{tests}\n"
        "def test_after():\n    pass\n"
    )


class TestEndingExceptionsInATestRun:
    @pytest.mark.parametrize("site", list(_SITE_SOURCES))
    def test_pytest_exit_ends_the_session(self, pytester: pytest.Pytester, site):
        pytester.makepyfile(_bench('pytest.exit("operator quit")', [site]))
        result = pytester.runpytest("-p", "no:cacheprovider")
        assert result.ret == pytest.ExitCode.INTERRUPTED
        result.stdout.fnmatch_lines(["*Exit: operator quit*"])
        result.assert_outcomes()  # test_after never ran
        assert "checks failed" not in result.stdout.str()

    def test_unittest_skiptest_skips_the_test(self, pytester: pytest.Pytester):
        pytester.makepyfile(_bench('raise unittest.SkipTest("no bench")', list(_SITE_SOURCES)))
        result = pytester.runpytest("-p", "no:cacheprovider", "-rs")
        result.assert_outcomes(skipped=4, passed=1)
        result.stdout.fnmatch_lines(["SKIPPED [[]4[]] *: no bench"])
        assert "checks failed" not in result.stdout.str()

    def test_skip_test_in_a_testcase_skips_it(self, pytester: pytest.Pytester):
        pytester.makepyfile("""
            import unittest

            import pytest

            class TestBench(unittest.TestCase):
                @pytest.fixture(autouse=True)
                def _verify(self, verify):
                    self.verify = verify

                def test_case(self):
                    other = self.verify.equal(1, 2, name="Other")
                    self.verify.conditional(
                        1, cases={1: lambda: self.skipTest("no bench"), 2: other}, name="Mode"
                    )

                def test_factory(self):
                    self.verify.all_satisfy(
                        [1], lambda item: self.skipTest("no bench"), name="Rails"
                    )

                def test_sample(self):
                    self.verify.eventually(
                        lambda: self.skipTest("no bench"), timeout=5, name="Settles"
                    )
        """)
        result = pytester.runpytest("-p", "no:cacheprovider", "-rs")
        result.assert_outcomes(skipped=3)
        assert "checks failed" not in result.stdout.str()

    def test_bdb_quit_fails_the_test_with_itself(self, pytester: pytest.Pytester):
        pytester.makepyfile(_bench("raise bdb.BdbQuit()", list(_SITE_SOURCES)))
        result = pytester.runpytest("-p", "no:cacheprovider", "-rf")
        result.assert_outcomes(failed=4, passed=1)
        for site in _SITE_SOURCES:
            result.stdout.fnmatch_lines([f"FAILED *::test_{site.replace('-', '_')} - bdb.BdbQuit"])
        assert "checks failed" not in result.stdout.str()

    def test_an_ordinary_exception_is_a_failed_check(self, pytester: pytest.Pytester):
        pytester.makepyfile(_bench('raise RuntimeError("probe lost")', list(_SITE_SOURCES)))
        result = pytester.runpytest("-p", "no:cacheprovider", "-rf")
        result.assert_outcomes(failed=4, passed=1)
        output = result.stdout.str()
        for note in [
            # The other case went into the composite with it: it is not on its own.
            "Mode [mode=1 → no check] (case 1 raised RuntimeError: probe lost)",
            "Power [→ no branch chosen] (condition of branch 0 (powered) raised RuntimeError: "
            "probe lost)",
            "Rails — no item checked (descriptor_factory raised RuntimeError: probe lost for "
            "item 0)",
            "Settles — never passed in 0 s (1 try), last: the sample raised RuntimeError: "
            "probe lost (at test_an_ordinary_exception_is_a_failed_check.py:8)",
        ]:
            assert f"1 of 2 checks failed: {note}" in output


# ---------------------------------------------------------------------------
# xfail: the reason names the failed checks
# ---------------------------------------------------------------------------

_XFAIL = """
    import pytest

    def test_imperative(verify):
        verify.equal(1, 1, name="Link")
        verify.approx(3.4, 3.3, abs_tol=0.05, name="Vout", units="V")
        pytest.xfail("known drift")

    @pytest.mark.xfail(reason="known drift")
    def test_marker(verify):
        with verify.section("3V3"):
            verify.approx(3.4, 3.3, abs_tol=0.05, name="Vout", units="V")

    def test_clean(verify):
        verify.equal(1, 1, name="Link")
        pytest.xfail("known")
"""

_IMPERATIVE_REASON = "known drift [1 of 2 checks failed: Vout — expected 3.3V ± 0.05V, got 3.4V]"
_MARKER_REASON = (
    "known drift [1 of 1 checks failed: 3V3 › Vout — expected 3.3V ± 0.05V, got 3.4V]"
)


class TestXfail:
    def test_the_reason_gets_the_failure_headline(self, pytester: pytest.Pytester):
        pytester.makepyfile(_XFAIL)
        result = pytester.runpytest("-p", "no:cacheprovider", "-rx", "--junitxml=junit.xml")
        result.assert_outcomes(xfailed=3)
        assert result.ret == pytest.ExitCode.OK
        output = result.stdout.str()
        assert _IMPERATIVE_REASON in output
        assert _MARKER_REASON in output
        cases = _junit_cases(pytester)
        messages = {
            name: [(child.tag, child.get("message")) for child in case]
            for name, case in cases.items()
        }
        assert messages == {
            "test_imperative": [("skipped", _IMPERATIVE_REASON)],
            "test_marker": [("skipped", _MARKER_REASON)],
            "test_clean": [("skipped", "known")],  # no failed check: the reason is kept as is
        }

    def test_with_fail_fast_only_the_marker_still_xfails(self, pytester: pytest.Pytester):
        pytester.makepyfile(_XFAIL)
        result = pytester.runpytest(
            "-p", "no:cacheprovider", "-rfx", "--verify-fail-fast", "--junitxml=junit.xml"
        )
        # The failed check stops test_imperative before it reaches pytest.xfail().
        result.assert_outcomes(failed=1, xfailed=2)
        result.stdout.fnmatch_lines(
            [
                "FAILED *::test_imperative - 1 of 2 checks failed, stopped at [[]1[]]: "
                "Vout — expected 3.3V ± 0.05V, got 3.4V"
            ]
        )
        [skipped] = list(_junit_cases(pytester)["test_marker"])
        assert skipped.get("message") == _MARKER_REASON

    def test_an_xfail_in_setup_names_the_setup_checks(self, pytester: pytest.Pytester):
        pytester.makepyfile("""
            import pytest

            @pytest.fixture
            def bench(verify):
                verify.equal("B2", "B1", name="Bench ID")
                pytest.xfail("wrong bench")

            def test_rail(bench, verify):
                verify.is_true(True, name="never made")
        """)
        result = pytester.runpytest("-p", "no:cacheprovider", "-rx", "--junitxml=junit.xml")
        result.assert_outcomes(xfailed=1)
        [skipped] = list(_junit_cases(pytester)["test_rail"])
        assert skipped.get("message") == (
            "wrong bench [1 of 1 checks failed: Bench ID — expected 'B1', got 'B2']"
        )

    @pytest.mark.parametrize(
        "marker, call",
        [("@pytest.mark.xfail", ""), ("", "pytest.xfail()")],
        ids=["marker-without-reason", "imperative-without-reason"],
    )
    def test_an_empty_reason_gets_the_headline_alone(
        self, pytester: pytest.Pytester, marker, call
    ):
        pytester.makepyfile(f"""
            import pytest

            {marker}
            def test_known(verify):
                verify.equal(1, 2, name="Soft")
                {call}
        """)
        result = pytester.runpytest("-p", "no:cacheprovider", "-rx", "--junitxml=junit.xml")
        result.assert_outcomes(xfailed=1)
        [skipped] = list(_junit_cases(pytester)["test_known"])
        assert skipped.get("message") == "[1 of 1 checks failed: Soft — expected 2, got 1]"

    def test_the_ascii_terminal_gets_the_reason_in_ascii(self, pytester: pytest.Pytester):
        pytester.makepyfile(_XFAIL)
        result = pytester.runpytest("-p", "no:cacheprovider", "-rx", "--verify-ascii")
        result.assert_outcomes(xfailed=3)
        lines = [line for line in result.outlines if "checks failed" in line]
        assert len(lines) == 2
        assert all(line.isascii() for line in lines), lines
        assert any("Vout \\u2014 expected 3.3V \\xb1 0.05V, got 3.4V" in line for line in lines)


# ---------------------------------------------------------------------------
# Pinned: unittest @expectedFailure, pytest 9 subtests
# ---------------------------------------------------------------------------


def test_expected_failure_with_only_soft_failures_is_an_unexpected_success(
    pytester: pytest.Pytester,
):
    pytester.makepyfile("""
        import unittest

        import pytest

        class TestBench(unittest.TestCase):
            @pytest.fixture(autouse=True)
            def _verify(self, verify):
                self.verify = verify

            @unittest.expectedFailure
            def test_soft_only(self):
                self.verify.equal(1, 2, name="Soft")
    """)
    result = pytester.runpytest("-p", "no:cacheprovider", "-rf", "--junitxml=junit.xml")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*Unexpected success*",
            "*Soft assertion failures*",
            "1 of 1 checks failed: Soft — expected 2, got 1",
            "",
            "  ✗ [[]0] Soft (*.py:12) — expected 2, got 1",
        ]
    )
    # pytest 7.0 prints no message after the node ID for an unexpected success.
    result.stdout.fnmatch_lines(["FAILED *::TestBench::test_soft_only*"])
    [failure] = list(_junit_cases(pytester)["test_soft_only"])
    assert failure.tag == "failure"
    assert "Unexpected success" in (failure.text or "")
    assert "1 of 1 checks failed: Soft — expected 2, got 1" in (failure.text or "")


class _SubtestOutcomes:
    """Inner-session plugin: ``{subtest msg: outcome}`` of every subtest report."""

    def __init__(self) -> None:
        self.outcomes: Dict[str, str] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        context = getattr(report, "context", None)
        if context is not None:
            self.outcomes[context.msg] = report.outcome


@pytest.mark.skipif(not _HAS_SUBTESTS, reason="pytest < 9 has no built-in subtests fixture")
def test_a_failed_check_in_a_subtest_fails_the_whole_test(pytester: pytest.Pytester):
    pytester.makepyfile("""
        def test_rails(subtests, verify):
            for rail, ripple in (("3V3", 12), ("5V0", 27)):
                with subtests.test(msg=rail):
                    verify.less(ripple, 20, name=f"{rail} ripple", units="mV")
    """)
    recorder = _SubtestOutcomes()
    result = pytester.runpytest(
        "-p", "no:cacheprovider", "-v", "-rf", "--junitxml=junit.xml", plugins=[recorder]
    )
    assert recorder.outcomes == {"3V3": "passed", "5V0": "passed"}  # SUBPASSED, as in 0.10.0
    result.stdout.fnmatch_lines(["*::test_rails SUBPASSED*5V0*"])
    result.stdout.fnmatch_lines(
        ["FAILED *::test_rails - 1 of 2 checks failed: 5V0 ripple — expected < 20mV, got 27mV"]
    )
    result.stdout.fnmatch_lines(["*= 1 failed, *2 subtests passed in *"])
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    failures = [child for child in _junit_cases(pytester)["test_rails"] if child.tag == "failure"]
    assert len(failures) == 1


# ---------------------------------------------------------------------------
# verify.record of a check a composite took in
# ---------------------------------------------------------------------------


class TestRecordOfAbsorbedChecks:
    def test_an_unselected_case_is_recorded_as_a_copy_at_the_record_call(self):
        run, verify = _recording()
        made_at = _next_line()
        other = verify.equal(1, 2, name="Other")
        mode = verify.conditional(
            2, cases={1: other, 2: lambda: verify.is_true(1, name="Two")}, name="Mode"
        )
        assert run.records == [mode] and mode["passed"] is True
        recorded_at = _next_line()
        copy = verify.record(other)
        assert copy is not other and run.records == [mode, copy]
        assert copy["passed"] is False and copy["detail"] == "expected 2, got 1"
        assert other["location"].endswith(f":{made_at}")
        assert copy["location"].endswith(f":{recorded_at}")
        assert copy["phase"] == "call"
        assert "passed" not in mode["cases"]["1"]  # the composite keeps it unselected
        assert checks.evaluate(copy) is False
        assert checks.evaluate(_json_round_trip(copy)) is False
        assert checks.evaluate(mode) is True

    def test_a_selected_lazy_case_is_returned_as_is(self):
        run, verify = _recording()
        made: List[Any] = []

        def case() -> Any:
            made.append(verify.equal(1, 2, name="One"))
            return made[-1]

        mode = verify.conditional(1, cases={1: case}, name="Mode")
        assert verify.record(made[0]) is made[0]
        assert run.records == [mode] and mode["passed"] is False

    def test_the_kept_try_is_returned_as_is(self):
        run, verify = _recording()
        made: List[Any] = []

        def sample() -> Any:
            made.append(verify.less(30, 40, name="T"))
            return made[-1]

        settles = verify.eventually(sample, timeout=0, name="Cool")
        assert verify.record(made[0]) is made[0]
        assert run.records == [settles]

    def test_a_check_a_failed_call_took_in_is_recorded_as_a_copy(self):
        run, verify = _recording()
        other = verify.equal(1, 2, name="Other")
        with pytest.raises(TypeError, match=r"conditional\(\) case 2 must be a check"):
            verify.conditional(1, cases={1: other, 2: 5}, name="Mode")
        assert run.records == []  # the checks passed to the call went with it
        copy = verify.record(other)
        assert copy is not other and run.records == [copy] and copy["passed"] is False

    def test_an_unselected_check_in_a_nested_composite_is_recorded_as_a_copy(self):
        run, verify = _recording()
        other = verify.equal(1, 2, name="Other")
        inner = verify.guard(
            [(False, "never", other)], default=lambda: verify.is_true(1, name="d"), name="Inner"
        )
        outer = verify.all_satisfy([inner], lambda check: check, name="Outer")
        assert run.records == [outer]
        copy = verify.record(other)
        assert copy is not other and run.records == [outer, copy]

    def test_require_stops_at_the_copy_of_an_unselected_failed_check(self):
        run, verify = _recording()
        other = verify.equal(1, 2, name="Other")
        group = verify.guard(
            [(False, "never", other)], default=lambda: verify.is_true(1, name="d"), name="g"
        )
        with pytest.raises(ChecksFailedError) as stopped:
            verify.require(other)
        copy = run.records[1]
        assert run.records == [group, copy] and copy is not other
        assert stopped.value.stopped_at == 1
        assert str(stopped.value).splitlines()[0] == (
            "1 of 2 checks failed, stopped at [1]: Other — expected 2, got 1"
        )

    def test_recording_an_unselected_check_twice_counts_it_once(self):
        run, verify = _recording()
        other = verify.equal(1, 2, name="Other")
        mode = verify.conditional(
            2, cases={1: other, 2: lambda: verify.is_true(1, name="Two")}, name="Mode"
        )
        first = verify.record(other)
        second = verify.record(other)
        assert run.records == [mode, first]
        assert second is first

    def test_a_check_selected_inside_an_unselected_composite_is_recorded_as_a_copy(self):
        run, verify = _recording()
        other = verify.equal(1, 2, name="Other")
        inner = verify.all_satisfy([other], lambda check: check, name="Inner")
        outer = verify.guard(
            [(False, "never", inner)], default=lambda: verify.is_true(1, name="d"), name="Outer"
        )
        assert run.records == [outer] and outer["passed"] is True
        copy = verify.record(other)
        assert copy is not other and run.records == [outer, copy]

    def test_require_of_a_check_selected_inside_an_unselected_composite_stops(self):
        run, verify = _recording()
        other = verify.equal(1, 2, name="Other")
        inner = verify.all_satisfy([other], lambda check: check, name="Inner")
        outer = verify.guard(
            [(False, "never", inner)], default=lambda: verify.is_true(1, name="d"), name="Outer"
        )
        with pytest.raises(ChecksFailedError):
            verify.require(other)
        assert len(run.records) == 2 and run.records[0] is outer
        assert run.records[1] is not other and run.records[1]["name"] == "Other"

    def test_the_summary_points_at_the_record_call(self, pytester: pytest.Pytester):
        pytester.makepyfile("""
            def test_power(verify):
                vout = verify.equal(1, 2, name="Vout")
                verify.guard([(False, "never", vout)],
                             default=lambda: verify.is_true(1, name="Fallback"), name="Power")
                verify.record(vout)
        """)
        result = pytester.runpytest("-p", "no:cacheprovider", "--tb=line")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*.py:5: 1 of 2 checks failed: Vout — expected 2, got 1"])


# ---------------------------------------------------------------------------
# verify.fail(str(exc)) with an empty message
# ---------------------------------------------------------------------------


class TestFailWithAnEmptyMessage:
    @pytest.mark.parametrize("message", ["", " ", "\u00a0\u2003"], ids=["empty", "space", "nbsp"])
    def test_a_blank_message_is_a_failed_check_named_fail(self, message):
        run, verify = _recording()
        record = verify.fail(message)
        assert run.records == [record]
        assert record["name"] == "fail" and record["msg"] == message
        assert record["passed"] is False
        assert record["description"] == record["detail"] == "FAIL: (no message)"
        assert checks.evaluate(record) is False
        assert checks.evaluate(_json_round_trip(record)) is False

    def test_the_builder_names_it_fail_too(self):
        built = checks.fail(str(TimeoutError()))
        assert built == {
            "check_type": "fail",
            "name": "fail",
            "description": "FAIL: (no message)",
            "msg": "",
        }
        assert checks.evaluate(built) is False
        [result] = checks.evaluate_detailed(_json_round_trip(built))
        assert result["passed"] is False

    def test_an_explicit_name_keeps_the_empty_message(self):
        _, verify = _recording()
        record = verify.fail("", name="Timeout")
        assert record["name"] == "Timeout"
        assert record["detail"] == "FAIL: (no message)"

    def test_require_stops_with_it(self):
        run, verify = _recording()
        with pytest.raises(ChecksFailedError) as stopped:
            verify.require.fail(str(TimeoutError()))
        assert str(stopped.value).splitlines()[0] == (
            "1 of 1 checks failed, stopped at [0]: fail — FAIL: (no message)"
        )
        assert [record["name"] for record in run.records] == ["fail"]

    def test_a_lazy_case_can_fail_with_it(self):
        run, verify = _recording()
        mode = verify.conditional(1, cases={1: lambda: verify.fail("")}, name="Mode")
        assert run.records == [mode] and mode["passed"] is False
        assert mode["detail"] == "[mode=1 → fail] — FAIL: (no message)"

    @pytest.mark.parametrize("message", ["\n", "\t", " \r\n "], ids=["lf", "tab", "crlf"])
    def test_a_message_of_whitespace_controls_is_no_message_either(self, message):
        built = checks.fail(message)
        assert built["name"] == "fail"
        assert built["description"] == "FAIL: (no message)"

    def test_the_test_fails_with_it(self, pytester: pytest.Pytester):
        pytester.makepyfile("""
            def test_timeout(verify):
                try:
                    raise TimeoutError()
                except TimeoutError as exc:
                    verify.fail(str(exc))
                verify.is_true(True, name="Link")
        """)
        result = pytester.runpytest("-p", "no:cacheprovider", "-rf", "--junitxml=junit.xml")
        result.assert_outcomes(failed=1)
        headline = "1 of 2 checks failed: fail — FAIL: (no message)"
        result.stdout.fnmatch_lines([f"FAILED *::test_timeout - {headline}"])
        result.stdout.fnmatch_lines(["  ✗ [[]0] fail (*.py:5) — FAIL: (no message)"])
        [failure] = list(_junit_cases(pytester)["test_timeout"])
        assert (failure.get("message") or "").startswith(headline)
