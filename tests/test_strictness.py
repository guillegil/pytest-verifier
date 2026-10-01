"""FEAT-3: required checks, fail-fast, and a first line that says what failed."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from pytest_verifier import ChecksFailedError, checks
from pytest_verifier._exceptions import format_summary
from pytest_verifier._run import Run, recording_verify


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest("-p", "no:cacheprovider", *args)


class TestRequire:
    def test_a_failed_required_check_stops_the_test(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_link(verify):
                verify.equal(1, 2, name="soft")
                verify.require.is_not_none(None, name="link")
                raise AssertionError("never reached")
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            [
                "2 of 2 checks failed: soft — expected 2, got 1 (+1 more)",
                "",
                "  ✗ ?0? soft (*.py:2) — expected 2, got 1",
                "  ✗ ?1? link (*.py:3) — expected not None, got None",
            ]
        )
        result.stdout.no_fnmatch_line("*never reached*")
        result.stdout.no_fnmatch_line("*Soft assertion failures*")

    def test_a_passed_required_check_goes_on(self) -> None:
        verify = recording_verify(Run())
        record = verify.require.greater(5, 1, name="ok")
        assert record["passed"] is True
        assert verify.require(verify.equal(1, 1, name="done"))["passed"] is True

    def test_require_with_a_check(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import pytest
            from pytest_verifier import ChecksFailedError, checks

            def test_built(verify):
                with pytest.raises(ChecksFailedError) as caught:
                    verify.require(checks.equal(1, 2, name="built"))
                assert [r["name"] for r in caught.value.results] == ["built"]

            def test_recorded(verify):
                check = verify.equal(1, 2, name="recorded")
                with pytest.raises(ChecksFailedError):
                    verify.require(check)
            """
        )
        # The checks stay recorded: catching the error does not make the tests pass.
        result = _run(pytester)
        result.assert_outcomes(failed=2)
        result.stdout.fnmatch_lines(["*::test_built - 1 of 1 checks failed: built*"])

    def test_a_test_that_catches_the_error_still_fails(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_swallowed(verify):
                try:
                    verify.require.equal(1, 2, name="first")
                except Exception:
                    pass
                verify.equal(3, 4, name="later")
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            [
                "2 of 2 checks failed: first — expected 2, got 1 (+1 more)",
                "",
                "  ✗ ?0? first *",
                "  ✗ ?1? later *",
            ]
        )

    def test_checks_made_before_reraising_join_the_error(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_reraised(verify):
                try:
                    verify.require.equal(1, 2, name="first")
                except Exception:
                    verify.equal(3, 4, name="cleanup")
                    raise
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["2 of 2 checks failed: first *(+1 more)"])
        result.stdout.no_fnmatch_line("*Soft assertion failures*")

    def test_results_and_report_carry_the_checks_once(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            calls = []

            def pytest_verify_results(item, when, checks, passed):
                calls.append((when, [c["name"] for c in checks], passed))

            def pytest_sessionfinish(session):
                assert calls == [("call", ["a", "b"], False)], calls
            """
        )
        pytester.makepyfile(
            """
            def test_it(verify):
                verify.equal(1, 1, name="a")
                verify.require.equal(1, 2, name="b")
            """
        )
        reprec = pytester.inline_run("-p", "no:cacheprovider")
        reprec.assertoutcome(failed=1)
        [call] = [r for r in reprec.getreports("pytest_runtest_logreport") if r.when == "call"]
        assert [c["name"] for c in call.verify_checks] == ["a", "b"]

    def test_in_fixture_setup_and_teardown(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            import pytest

            @pytest.fixture
            def broken_setup(verify):
                verify.require.equal(1, 2, name="supply on")
                yield

            @pytest.fixture
            def broken_teardown(verify):
                yield
                verify.require.equal(1, 2, name="supply off")
                raise AssertionError("never reached")
            """
        )
        pytester.makepyfile(
            """
            def test_setup(broken_setup):
                raise AssertionError("never reached")

            def test_teardown(broken_teardown):
                pass
            """
        )
        result = _run(pytester)
        result.assert_outcomes(passed=1, errors=2)
        result.stdout.fnmatch_lines(
            [
                "*ERROR at setup of test_setup*",
                "1 of 1 checks failed: supply on — expected 2, got 1",
                "*ERROR at teardown of test_teardown*",
                "1 of 1 checks failed: supply off — expected 2, got 1",
            ]
        )
        result.stdout.no_fnmatch_line("*never reached*")

    def test_composites(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_items(verify):
                verify.require.all_satisfy(
                    [1, 2, 3], lambda x: verify.less(x, 2, name=f"x{x}"), name="items"
                )
                raise AssertionError("never reached")
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["1 of 1 checks failed: items *"])
        result.stdout.no_fnmatch_line("*never reached*")

    def test_xfail_raises_assertion_error(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import pytest

            @pytest.mark.xfail(raises=AssertionError, strict=True)
            def test_known(verify):
                verify.require.is_true(False, name="known issue")
            """
        )
        _run(pytester).assert_outcomes(xfailed=1)

    def test_a_thread_raises_in_the_thread_and_the_test_fails(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makepyfile(
            """
            import threading

            def test_thread(verify):
                errors = []

                def work():
                    try:
                        verify.require.equal(1, 2, name="in thread")
                    except Exception as exc:
                        errors.append(exc)

                worker = threading.Thread(target=work)
                worker.start()
                worker.join()
                assert len(errors) == 1
            """
        )
        result = _run(pytester)
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["1 of 1 checks failed: in thread *"])

    def test_require_is_cached_and_idempotent(self) -> None:
        verify = recording_verify(Run())
        assert verify.require is verify.require
        assert verify.require.require is verify.require

    def test_checks_require_needs_the_fixture(self) -> None:
        with pytest.raises(RuntimeError, match="checks.require cannot stop a test"):
            checks.require.equal(1, 1, name="x")
        with pytest.raises(RuntimeError, match="checks.require cannot stop a test"):
            checks.require(checks.equal(1, 1, name="x"))
        with pytest.raises(RuntimeError, match="checks.require cannot stop a test"):
            checks.require.all_satisfy([1], lambda x: checks.equal(x, 1, name="x"), name="all")

    def test_checks_require_marks_its_check_used(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import pytest
            from pytest_verifier import checks

            def test_misuse():
                check = checks.equal(1, 1, name="x")
                with pytest.raises(RuntimeError):
                    checks.require(check)
            """
        )
        result = _run(pytester, "-W", "error::pytest_verifier.UnusedCheckWarning")
        result.assert_outcomes(passed=1)

    def test_a_non_descriptor_is_a_type_error(self) -> None:
        verify = recording_verify(Run())
        with pytest.raises(TypeError, match="record\\(\\) argument must be a check descriptor"):
            verify.require(42)  # type: ignore[arg-type]


class TestFailFast:
    _TEST = """
        def test_first(verify):
            verify.equal(1, 1, name="fine")
            verify.equal(1, 2, name="first")
            verify.equal(3, 4, name="second")
        """

    def test_off_by_default(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(self._TEST)
        _run(pytester).stdout.fnmatch_lines(["2 of 3 checks failed: first *(+1 more)"])

    def test_the_option_stops_at_the_first_failed_check(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(self._TEST)
        result = _run(pytester, "--verify-fail-fast")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["1 of 2 checks failed: first — expected 2, got 1"])
        result.stdout.no_fnmatch_line("*second*")

    def test_the_ini_setting(self, pytester: pytest.Pytester) -> None:
        pytester.makeini("[pytest]\nverify_fail_fast = true\n")
        pytester.makepyfile(self._TEST)
        _run(pytester).stdout.fnmatch_lines(["1 of 2 checks failed: first *"])

    def test_the_option_is_listed_in_help(self, pytester: pytest.Pytester) -> None:
        result = _run(pytester, "--help")
        result.stdout.fnmatch_lines(["*--verify-fail-fast*", "*verify_fail_fast*"])

    def test_eager_and_lazy_children(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_eager(verify):
                verify.conditional(
                    1, cases={1: verify.equal(1, 1, name="one"), 2: verify.equal(1, 2, name="two")},
                    name="mode",
                )

            def test_lazy(verify):
                verify.conditional(
                    1,
                    cases={
                        1: lambda: verify.equal(1, 1, name="one"),
                        2: lambda: verify.equal(1, 2, name="two"),
                    },
                    name="mode",
                )
            """
        )
        result = _run(pytester, "--verify-fail-fast")
        # A child made eagerly is judged when it is made, before the conditional chooses.
        result.assert_outcomes(failed=1, passed=1)
        result.stdout.fnmatch_lines(["*_ test_eager _*", "1 of 2 checks failed: two — *"])


class TestFirstLine:
    def _records(self, *passed: bool) -> List[Dict[str, Any]]:
        return [
            dict(checks.equal(i, i if ok else -1, name=f"c{i}"), passed=ok, detail=f"d{i}")
            for i, ok in enumerate(passed)
        ]

    @pytest.mark.parametrize(
        "passed, first",
        [
            ((True, True), "0 of 2 checks failed"),
            ((True, False), "1 of 2 checks failed: c1 — d1"),
            ((False, True, False, False), "3 of 4 checks failed: c0 — d0 (+2 more)"),
            ((), "0 of 0 checks failed"),
        ],
    )
    def test_the_first_failure_is_repeated(self, passed: Any, first: str) -> None:
        assert format_summary(self._records(*passed)).splitlines()[0] == first

    def test_the_first_line_is_bounded(self) -> None:
        [record] = self._records(False)
        record["detail"] = "x" * 5000
        first = format_summary([record]).splitlines()[0]
        assert first.startswith("1 of 1 checks failed: c0 — xxx")
        assert len(first) < 340

    def test_a_record_that_cannot_be_rendered(self) -> None:
        class Weird(dict):  # type: ignore[type-arg]
            def get(self, key: Any, default: Any = None) -> Any:
                if key == "name":
                    raise RuntimeError("boom")
                return super().get(key, default)

        summary = format_summary([Weird(check_type="equal", passed=False)])
        assert summary.splitlines()[0] == "1 of 1 checks failed"

    def test_junit_and_short_summary(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_rail(verify):
                verify.between(3.6, 3.2, 3.4, name="rail", units="V")
                verify.equal(1, 2, name="other")
            """
        )
        result = _run(pytester, "-rf", "--junitxml=out.xml")
        result.stdout.fnmatch_lines(["FAILED *::test_rail - 2 of 2 checks failed: ra*"])
        xml = (pytester.path / "out.xml").read_text(encoding="utf-8")
        assert (
            'message="2 of 2 checks failed: rail — expected [3.2V, 3.4V], got 3.6V (+1 more)'
            in xml
        )

    def test_pickled_errors_keep_the_first_line(self) -> None:
        import pickle

        error = ChecksFailedError(self._records(False, True))
        copy = pickle.loads(pickle.dumps(error))
        assert str(copy).splitlines()[0] == "1 of 2 checks failed: c0 — d0"
