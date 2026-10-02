"""Regression tests: when and how the soft-check verdict is applied (bugs found in 0.3.1).

Every test here asserts the correct behaviour for a bug found by the 0.3.1 review
(see ``bugs-0.3.1.md``) and fixed in 0.4.0. Each one failed on 0.3.1 and keeps the bug
from coming back.

In 0.3.1 most of these bugs shared one root cause: the verdict was applied by rewriting the
call-phase report in ``pytest_runtest_makereport`` instead of raising ``ChecksFailedError``.

Bugs covered by this module:

* H-7: checks recorded during teardown are ignored and the test passes.
* H-8: soft failures are misreported under ``xfail`` and hidden by a later ``skip``.
* M-7: the results stash accumulates across rerun attempts.
* M-8: a clean rerun attempt is failed with the previous attempt's checks.
* M-9: pytest 9 subtests: one failing check fails every later subtest.
* M-10: the soft summary next to a hard failure is missing from junitxml and hidden by
  ``--show-capture``.
* M-11: soft failures recorded during setup vanish when setup errors.
* M-12: ``pytest_exception_interact`` (and so ``--pdb``) never fires for soft failures.
* M-13: other ``pytest_runtest_makereport`` implementations see a soft-failed test as passed.
* M-14: the fixture without its verdict hook turns every failure into a pass.
* L-7: ``--tb=line`` prints the summary, then a copy cut at 50 characters.
* L-8: on pytest 8 the ``-r`` short summary gives no reason for a soft failure.
* D-1: ``ChecksFailedError`` is documented as raised but never is.

No third-party plugins are used. pytest-rerunfailures is simulated by a small inner conftest
that drives ``runtestprotocol()`` once per attempt, the way that plugin does.

Where a check name must only reach the output through the soft-failure summary, the inner
source builds it by concatenation (``"Soft" + "Bad"``), so a traceback that echoes the test
source cannot satisfy the assertion by accident.
"""
from __future__ import annotations

import inspect
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

import pytest_verifier
from pytest_verifier import get_check_results
from pytest_verifier._exceptions import ChecksFailedError
from pytest_verifier import plugin as fixture_module

#: pytest's major version: the built-in ``subtests`` fixture and the string-longrepr summary
#: reason both arrived in pytest 9.
PYTEST_MAJOR = int(pytest.__version__.split(".")[0])


# ── Helpers ──


def _outcomes(result: pytest.RunResult) -> dict[str, int]:
    """Return the inner run's outcome counts, or ``{}`` if it printed no summary line."""
    try:
        return result.parseoutcomes()
    except ValueError:
        return {}


def _failed_or_errored(result: pytest.RunResult) -> int:
    outcomes = _outcomes(result)
    return outcomes.get("failed", 0) + outcomes.get("errors", 0)


class _MakereportRecorder:
    """Inner-session plugin: what a plain ``pytest_runtest_makereport`` impl sees.

    Plain implementations run inside every hookwrapper, so this is the view of any
    observer that is not itself an outer wrapper.
    """

    def __init__(self) -> None:
        self.excinfo: dict[tuple[str, str], Any] = {}

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo[None]) -> None:
        self.excinfo[(item.name, call.when)] = call.excinfo


# ======================================================================
# H-7: checks recorded during teardown are ignored
# ======================================================================

_TEARDOWN_SOURCES = {
    "yield-fixture": """
        import pytest

        @pytest.fixture
        def dut(verify):
            yield "dut"
            verify.equal("busy", "idle", name="DUT idle after test")

        def test_teardown_check(dut, verify):
            verify.equal(1, 1, name="body")
    """,
    "addfinalizer": """
        def test_teardown_check(request, verify):
            verify.equal(1, 1, name="body")
            request.addfinalizer(lambda: verify.equal(1, 2, name="finalizer check"))
    """,
    "autouse-teardown": """
        import pytest

        @pytest.fixture(autouse=True)
        def auto(verify):
            yield
            verify.is_true(False, name="AutoTeardownBad")

        def test_teardown_check():
            pass
    """,
}


@pytest.mark.parametrize("source", list(_TEARDOWN_SOURCES.values()), ids=list(_TEARDOWN_SOURCES))
def test_h7_failed_check_in_teardown_fails_the_run(pytester, source):
    """A failed check recorded after the call phase must still fail the run (a teardown ERROR
    is fine: pytest then reports ``1 passed, 1 error``). In 0.3.1 the run is green."""
    pytester.makepyfile(source)
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.TESTS_FAILED, result.stdout.str()
    assert _failed_or_errored(result) >= 1


class _VerdictVsStash:
    """Inner-session plugin: each item's logged report outcomes next to its failed checks."""

    def __init__(self) -> None:
        self.outcomes: dict[str, list[str]] = {}
        self.failed_checks: dict[str, list[str]] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.outcomes.setdefault(report.nodeid, []).append(f"{report.when}:{report.outcome}")

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item, nextitem: pytest.Item | None) -> Any:
        result = yield
        self.failed_checks[item.nodeid] = [
            r["name"] for r in get_check_results(item) if r.get("passed") is False
        ]
        return result


def test_h7_verdict_never_contradicts_recorded_results(pytester):
    """If ``get_check_results(item)`` holds a failed check once the item is done, one of its
    reports must be failed (a reporter would otherwise draw red cards on a green test)."""
    pytester.makepyfile(_TEARDOWN_SOURCES["yield-fixture"])
    recorder = _VerdictVsStash()
    pytester.runpytest(plugins=[recorder])
    ((nodeid, failed_checks),) = recorder.failed_checks.items()
    assert failed_checks == ["DUT idle after test"]  # the stash holds the failure (true in 0.3.1 too)
    assert any(o.endswith(":failed") for o in recorder.outcomes[nodeid]), recorder.outcomes


# ======================================================================
# H-8: soft failures are misreported under xfail and hidden by skip
# ======================================================================

_XFAIL_MARKERS = {
    "non-strict": '@pytest.mark.xfail(reason="known soft failure")',
    "strict": '@pytest.mark.xfail(reason="known", strict=True)',
    "raises-AssertionError": "@pytest.mark.xfail(raises=AssertionError, strict=True)",
    "raises-ChecksFailedError": "@pytest.mark.xfail(raises=ChecksFailedError, strict=True)",
}


@pytest.mark.parametrize("marker", list(_XFAIL_MARKERS.values()), ids=list(_XFAIL_MARKERS))
def test_h8_soft_failure_under_xfail_is_xfailed(pytester, marker):
    """A failed soft check is a failure, so an xfail-marked test that has one is XFAIL, the
    same as a hard assert in the same place. In 0.3.1 it was FAILED (as a flipped XPASS)."""
    pytester.makepyfile(f"""
        import pytest
        from pytest_verifier._exceptions import ChecksFailedError

        {marker}
        def test_known(verify):
            verify.equal(1, 2, name="Soft")
    """)
    result = pytester.runpytest()
    result.assert_outcomes(xfailed=1)


def test_h8_terminal_exit_code_and_junitxml_agree_for_xfail_soft_failure(pytester):
    """The terminal, the exit code and junitxml must classify the test the same way. In 0.3.1 the
    terminal says FAILED while the exit code is 0 and junitxml writes ``<skipped
    message="xfail-marked test passes unexpectedly">`` (the stale ``wasxfail``)."""
    pytester.makepyfile("""
        import pytest

        @pytest.mark.xfail(reason="known soft failure")
        def test_known(verify):
            verify.equal(1, 2, name="Soft")
    """)
    result = pytester.runpytest("--junitxml=junit.xml")
    (testcase,) = ET.parse(pytester.path / "junit.xml").iter("testcase")
    failed_per = {
        "terminal": _outcomes(result).get("failed", 0) > 0,
        "exit code": result.ret == pytest.ExitCode.TESTS_FAILED,
        "junitxml": any(child.tag in ("failure", "error") for child in testcase),
    }
    assert len(set(failed_per.values())) == 1, (
        f"{failed_per}; junit children {[(c.tag, c.attrib) for c in testcase]}"
    )


_SKIP_AFTER_SOFT_FAIL_SOURCES = {
    "skip-in-body": """
        import pytest

        def test_env(verify):
            verify.equal(1, 2, name="Lost " + "failure")
            pytest.skip("env not ready")
    """,
    "importorskip-in-body": """
        import pytest

        def test_env(verify):
            verify.equal(1, 2, name="Lost " + "failure")
            pytest.importorskip("pytest_verify_no_such_module")
    """,
    "skip-in-fixture-setup": """
        import pytest

        @pytest.fixture
        def env(verify):
            verify.equal(1, 2, name="Lost " + "failure")
            pytest.skip("env not ready")

        def test_env(env):
            pass
    """,
}


@pytest.mark.parametrize(
    "source",
    list(_SKIP_AFTER_SOFT_FAIL_SOURCES.values()),
    ids=list(_SKIP_AFTER_SOFT_FAIL_SOURCES),
)
def test_h8_later_skip_does_not_hide_soft_failure(pytester, source):
    """A skip after a failed soft check must not hide it: the test is FAILED (or ERROR, for a
    skip in setup), or at least the output names the failed check. In 0.3.1 it was a plain
    SKIPPED and the name appears nowhere."""
    pytester.makepyfile(source)
    result = pytester.runpytest("-rs")
    reported = _failed_or_errored(result) >= 1 or "Lost failure" in result.stdout.str()
    assert reported, result.stdout.str()


# ======================================================================
# M-7 / M-8: stale state across rerun attempts
# ======================================================================

# Minimal stand-in for pytest-rerunfailures' protocol loop (not installed in CI): run the
# item again while an attempt failed, log the failing report of an intermediate attempt as
# "rerun", and log the last attempt normally. The Item object is reused, as in the plugin.
_RERUN_CONFTEST = """
    import pytest
    from _pytest.runner import runtestprotocol

    MAX_ATTEMPTS = 2


    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_protocol(item, nextitem):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
            reports = runtestprotocol(item, nextitem=nextitem, log=False)
            rerun = False
            for report in reports:
                if report.failed and attempt < MAX_ATTEMPTS:
                    report.outcome = "rerun"
                    item.ihook.pytest_runtest_logreport(report=report)
                    rerun = True
                    break
                item.ihook.pytest_runtest_logreport(report=report)
            item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
            if not rerun:
                break
        return True
"""


def test_m7_stash_holds_only_the_last_rerun_attempt(pytester):
    """A flaky test fails its soft check on attempt 1 and passes on attempt 2. The recorded
    results must describe attempt 2 only, not attempt 1's failure plus attempt 2."""
    pytester.makeconftest(_RERUN_CONFTEST)
    pytester.makepyfile("""
        ATTEMPTS = {"n": 0}

        def test_flaky(verify):
            ATTEMPTS["n"] += 1
            verify.equal(ATTEMPTS["n"], 2, name="attempt")  # fails on attempt 1 only
    """)
    stash: list[tuple[Any, Any]] = []

    class _StashDump:
        @pytest.hookimpl(wrapper=True)
        def pytest_runtest_protocol(self, item: pytest.Item, nextitem: Any) -> Any:
            result = yield
            stash.extend((r["name"], r["passed"]) for r in get_check_results(item))
            return result

    result = pytester.runpytest(plugins=[_StashDump()])
    outcomes = _outcomes(result)
    # The verdict itself was right in 0.3.1 too: attempt 1 is rerun, attempt 2 passes.
    assert (outcomes.get("passed"), outcomes.get("rerun"), outcomes.get("failed")) == (1, 1, None)
    # Only attempt 1's check failed, so a single passing entry means attempt 2 alone.
    assert stash == [("attempt", True)]


def test_m8_clean_rerun_attempt_is_not_failed_by_previous_attempt(pytester):
    """Attempt 1 requests ``verify`` dynamically and fails a check; attempt 2 records no
    checks and must pass. In 0.3.1 attempt 2 was judged on attempt 1's stale results."""
    pytester.makeconftest(_RERUN_CONFTEST)
    pytester.makepyfile("""
        N = {"n": 0}

        def test_flaky_then_clean(request):
            N["n"] += 1
            if N["n"] == 1:
                request.getfixturevalue("verify").fail("first attempt only")
    """)
    result = pytester.runpytest()
    outcomes = _outcomes(result)
    assert outcomes.get("rerun") == 1, result.stdout.str()  # the simulation reran it
    assert (outcomes.get("passed"), outcomes.get("failed")) == (1, None), result.stdout.str()


# ======================================================================
# M-9: pytest 9 subtests: one failing check fails every later subtest
# ======================================================================


class _SubtestOutcomes:
    """Inner-session plugin: ``{subtest msg: outcome}`` for every subtest report."""

    def __init__(self) -> None:
        self.outcomes: dict[str, str] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        context = getattr(report, "context", None)
        if context is not None:
            self.outcomes[context.msg] = report.outcome


@pytest.mark.skipif(PYTEST_MAJOR < 9, reason="pytest<9 has no built-in subtests fixture")
def test_m9_passing_subtests_after_a_failing_one_are_not_failed(pytester):
    """Only the subtest holding the failing check may be SUBFAILED (or none, if subtest
    reports are left alone and only the parent fails)."""
    pytester.makepyfile("""
        def test_subtests_pass_after_fail(subtests, verify):
            with subtests.test(msg="first-bad"):
                verify.equal(0, 1, name="bad")
            with subtests.test(msg="second-good"):
                verify.equal(1, 1, name="good")
            with subtests.test(msg="third-no-checks"):
                pass
    """)
    recorder = _SubtestOutcomes()
    result = pytester.runpytest(plugins=[recorder])
    result.stdout.fnmatch_lines(["FAILED *::test_subtests_pass_after_fail*"])  # parent fails
    assert set(recorder.outcomes) == {"first-bad", "second-good", "third-no-checks"}
    later = {msg: recorder.outcomes[msg] for msg in ("second-good", "third-no-checks")}
    assert later == {"second-good": "passed", "third-no-checks": "passed"}


# ======================================================================
# M-10: soft summary next to a hard failure is only a captured-output section
# ======================================================================

# The check name is concatenated so "SoftBad" can only come from the soft-failure summary,
# never from the traceback's echo of the test source.
_HARD_AND_SOFT_SOURCE = """
    def test_hard_and_soft(verify):
        verify.equal(1, 2, name="Soft" + "Bad")
        raise RuntimeError("hard boom")
"""


@pytest.mark.parametrize("show_capture", ["no", "stdout", "log"])
def test_m10_soft_summary_shown_whatever_show_capture(pytester, show_capture):
    """The soft summary is part of the failure, not captured output: ``--show-capture``
    must not hide it. In 0.3.1 only the default ``--show-capture=all`` shows it."""
    pytester.makepyfile(_HARD_AND_SOFT_SOURCE)
    result = pytester.runpytest(f"--show-capture={show_capture}")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*RuntimeError*hard boom*"])
    result.stdout.fnmatch_lines(["*SoftBad*"])


def test_m10_soft_summary_in_junitxml_failure(pytester):
    """The junitxml ``<failure>`` for a test that failed hard and softly must carry the soft
    summary as well as the hard error."""
    pytester.makepyfile(_HARD_AND_SOFT_SOURCE)
    result = pytester.runpytest("--junitxml=junit.xml")
    result.assert_outcomes(failed=1)
    (testcase,) = ET.parse(pytester.path / "junit.xml").iter("testcase")
    (failure,) = testcase.iter("failure")
    text = f"{failure.get('message', '')}\n{failure.text or ''}"
    assert "hard boom" in text
    assert "SoftBad" in text, text


# ======================================================================
# M-11: soft failures recorded during setup vanish when setup errors
# ======================================================================

_SETUP_ERROR_SOURCES = {
    "later-fixture-raises": """
        import pytest

        @pytest.fixture
        def powered_board(verify):
            verify.equal(3.1, 3.3, name="Rail " + "3V3")
            verify.equal(0, 0, name="Fault register")
            return object()

        @pytest.fixture
        def connection(powered_board):
            raise ConnectionError("DUT did not answer")

        def test_uses_both(connection):
            pass
    """,
    "same-fixture-raises": """
        import pytest

        @pytest.fixture
        def powered_board(verify):
            verify.equal(3.1, 3.3, name="Rail " + "3V3")
            raise ConnectionError("DUT did not answer")

        def test_uses_board(powered_board):
            pass
    """,
}


@pytest.mark.parametrize(
    "source", list(_SETUP_ERROR_SOURCES.values()), ids=list(_SETUP_ERROR_SOURCES)
)
def test_m11_setup_error_report_shows_soft_failures(pytester, source):
    """The setup ERROR must show the failed check recorded before it (often the root cause).
    In 0.3.1 only the setup traceback is shown."""
    pytester.makepyfile(source)
    result = pytester.runpytest()
    assert _outcomes(result).get("errors", 0) >= 1, result.stdout.str()
    result.stdout.fnmatch_lines(["*DUT did not answer*"])
    result.stdout.fnmatch_lines(["*Rail 3V3*"])


# ======================================================================
# M-12: pytest_exception_interact never fires for soft-only failures
# ======================================================================


class _InteractRecorder:
    """Inner-session plugin: the tests ``pytest_exception_interact`` fired for (``--pdb``
    and on-failure screenshot/log plugins hang off this hook)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def pytest_exception_interact(self, node: Any, call: Any, report: Any) -> None:
        self.calls.append((node.name, report.when))


def test_m12_exception_interact_fires_for_soft_failure(pytester):
    pytester.makepyfile("""
        def test_soft(verify):
            verify.equal(1, 2, name="Soft")

        def test_hard():
            assert 1 == 2
    """)
    recorder = _InteractRecorder()
    result = pytester.runpytest(plugins=[recorder])
    result.assert_outcomes(failed=2)
    names = [name for name, _ in recorder.calls]
    assert "test_hard" in names  # control: a hard failure triggers the hook (true in 0.3.1 too)
    assert "test_soft" in names, recorder.calls


# ======================================================================
# M-13: other makereport implementations see a soft-failed test as passed
# ======================================================================


class _EarlyReporter:
    """Inner-session plugin registered before pytest-verifier (as pytest-reporter or an
    in-house uploader can be): an old-style makereport hookwrapper observing the outcome."""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: Any) -> Any:
        outcome = yield
        report = outcome.get_result()
        if report.when == "call":
            self.seen[item.name] = report.outcome


_SOFT_AND_HARD_SOURCE = """
    def test_soft(verify):
        verify.equal(3.9, 3.3, name="Vout")

    def test_hard():
        assert 4.1 == 3.3
"""


def test_m13_makereport_wrapper_registered_first_sees_failed(pytester):
    """A makereport wrapper registered before pytest-verifier runs inside its wrapper. It must
    still see FAILED for a soft-failed test, as it does for a hard failure."""
    pytester.makepyfile(_SOFT_AND_HARD_SOURCE)
    reporter = _EarlyReporter()
    result = pytester.runpytest(plugins=[reporter])
    result.assert_outcomes(failed=2)  # the final verdict was right in 0.3.1 too
    assert reporter.seen == {"test_soft": "failed", "test_hard": "failed"}


def test_m13_soft_failed_call_phase_has_assertion_excinfo(pytester):
    """``call.excinfo`` is what rerun filters (``--only-rerun``/``--rerun-except``) and
    on-failure hooks inspect: a soft failure must carry an AssertionError (ChecksFailedError
    subclasses it), the same as a hard assert."""
    pytester.makepyfile(_SOFT_AND_HARD_SOURCE)
    recorder = _MakereportRecorder()
    result = pytester.runpytest(plugins=[recorder])
    result.assert_outcomes(failed=2)
    hard = recorder.excinfo[("test_hard", "call")]
    assert hard is not None and hard.errisinstance(AssertionError)  # control (true in 0.3.1 too)
    soft = recorder.excinfo[("test_soft", "call")]
    assert soft is not None and soft.errisinstance(AssertionError), soft


# ======================================================================
# M-14: the fixture without its verdict hook turns every failure into a pass
# ======================================================================

_MUST_FAIL_SOURCE = """
    def test_must_fail(verify):
        verify.fail("this check must fail the test")
        verify.equal(1, 2, name="mismatch")
"""


@pytest.mark.parametrize(
    ("autoload", "args"),
    [(False, ()), (True, ("-p", "no:pytest_verifier"))],
    ids=["autoload-disabled", "entry-point-blocked"],
)
def test_m14_reexported_fixture_without_hook_never_passes(pytester, monkeypatch, autoload, args):
    """With the fixture vendored through a conftest re-export but the plugin module not
    registered, a test calling ``verify.fail()`` must not pass: it fails, or pytest-verifier
    stops the run loudly (usage error, collection error...). In 0.3.1 the checks are dropped and
    the run is green."""
    if autoload:
        monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", raising=False)
    else:
        monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    pytester.makeconftest("from pytest_verifier.plugin import verify  # noqa: F401\n")
    pytester.makepyfile(_MUST_FAIL_SOURCE)
    result = pytester.runpytest_subprocess(*args)
    output = f"{result.ret!r}\n{result.stdout.str()}\n{result.stderr.str()}"
    assert result.ret not in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED), output
    assert _outcomes(result).get("passed", 0) == 0, output


# ======================================================================
# L-7: --tb=line prints the summary, then a copy cut at 50 characters
# ======================================================================


def _crash_line(lines: list[str]) -> str:
    """Return the ``--tb=line`` crash line of the only failure: the last non-blank line
    between the FAILURES header and the next ``===`` line."""
    start = next(i for i, line in enumerate(lines) if line.startswith("=") and "FAILURES" in line)
    section: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("="):
            break
        if line.strip():
            section.append(line)
    return section[-1]


def test_l7_tb_line_crash_line_is_a_location_line(pytester):
    """``--tb=line`` ends each failure with one ``path:lineno: message`` crash line (a hard
    assert gives ``.../test_x.py:2: assert 1 == 2``). In 0.3.1 the crash line is
    ``str(longrepr)[:50]``: a copy of the summary cut mid-value, with no location."""
    pytester.makepyfile("""
        def test_soft_only(verify):
            verify.equal(1, 2, name="SoftOnly")
    """)
    result = pytester.runpytest("--tb=line")
    result.assert_outcomes(failed=1)
    crash_line = _crash_line(result.outlines)
    assert re.match(r"\S.*:\d+: \S", crash_line), crash_line


# ======================================================================
# L-8: pytest 8 short summary gives no reason for soft failures
# ======================================================================


def test_l8_short_summary_gives_reason_for_soft_failure(pytester):
    """The ``-r`` short-summary line of a soft failure carries a reason, as a hard failure's
    does. pytest 8.x reads ``longrepr.reprcrash`` and drops it for the plain-string longrepr."""
    pytester.makepyfile(test_summary="""
        def test_soft(verify):
            verify.equal(1, 2, name="value")

        def test_hard():
            assert 1 == 2, "value mismatch"
    """)
    result = pytester.runpytest("-rf")
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(["FAILED test_summary.py::test_hard - *"])  # control
    result.stdout.fnmatch_lines(
        ["FAILED test_summary.py::test_soft - 1 of 1 checks failed: value*"]
    )


# ======================================================================
# D-1: ChecksFailedError is documented as raised but never is
# ======================================================================


def _docs_claiming_checks_failed_error_is_raised() -> dict[str, str]:
    """Return the doc locations (bugs-0.3.1.md D-1) that still say the error is raised."""
    readme_path = Path(__file__).resolve().parents[1] / "README.md"
    readme = ""
    if readme_path.exists():
        readme = " ".join(readme_path.read_text(encoding="utf-8").split())
    # getattr: a fix may move the fixture out of ``_fixture``; then it has no stale docstring.
    fixture_function = getattr(fixture_module, "verify", None)
    fixture_doc = inspect.getdoc(fixture_function) if fixture_function is not None else None
    claims = {
        "ChecksFailedError docstring": ("Raised at fixture teardown", ChecksFailedError.__doc__),
        "verify fixture docstring": ("fails at teardown", fixture_doc),
        "README.md": ("reported together in a single `ChecksFailedError`", readme),
    }
    return {where: phrase for where, (phrase, text) in claims.items() if phrase in (text or "")}


def test_d1_checks_failed_error_raised_and_exported_or_not_documented_as_raised(pytester):
    """Either a soft failure really raises ``ChecksFailedError`` (so ``pytest.raises``,
    ``xfail(raises=...)`` and ``--only-rerun`` can match it) and ``pytest_verifier`` exports it,
    or the README and docstrings stop saying it is raised."""
    pytester.makepyfile("""
        def test_soft(verify):
            verify.equal(1, 2, name="X")
    """)
    recorder = _MakereportRecorder()
    pytester.runpytest(plugins=[recorder])
    raised = any(
        excinfo is not None and excinfo.errisinstance(ChecksFailedError)
        for excinfo in recorder.excinfo.values()
    )
    exported = "ChecksFailedError" in pytest_verifier.__all__ and hasattr(
        pytest_verifier, "ChecksFailedError"
    )
    stale_docs = _docs_claiming_checks_failed_error_is_raised()
    assert (raised and exported) or not stale_docs, (
        f"raised={raised} exported={exported}; docs still claim it is raised: {stale_docs}"
    )
