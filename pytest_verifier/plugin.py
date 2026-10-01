"""The pytest plugin: the ``verify`` fixture and the hooks that turn failed checks into a failed
test.

pytest loads it through the ``pytest_verifier`` entry point (the package lists this module in
``pytest_plugins``), or with ``-p pytest_verifier`` when plugin autoloading is disabled.

Each attempt to run a test item (pytest-rerunfailures runs the same item several times) gets a
fresh :class:`~pytest_verifier._run.Run`, created when its setup starts. Checks are judged when
they are recorded.
At the end of each phase the checks recorded so far that have not been judged yet are
examined, and if any failed, :class:`ChecksFailedError` is raised from that phase:

- after the call phase, for checks made in setup and in the test body;
- after teardown, for checks made while fixtures are torn down;
- after setup, only when setup itself is skipped (a skip never hides a failed check).

When the phase already failed with another exception, that exception is kept and the soft
summary is added to its report as a "Soft assertion failures" section. A unittest ``TestCase``
does not raise its failures and skips: pytest records them on the item and reports them
afterwards, so the call phase reads that record too.

The checks judged at the end of a phase are also passed to the ``pytest_verify_results`` hook
and attached to that phase's report as ``report.verify_checks``.

Checks built with ``pytest_verifier.checks`` in the test body and never used give an
``UnusedCheckWarning`` when the body ends (see :mod:`pytest_verifier._unused`).
"""
from __future__ import annotations

import sys
from typing import Any, Generator, List, Optional

import pytest

from . import _unused
from ._descriptors import CheckDescriptor
from ._exceptions import ChecksFailedError, format_summary
from ._run import Run, recording_verify
from ._stash import check_results_key
from ._verify import Verify

_NOT_ACTIVE = (
    "pytest-verifier: the 'verify' fixture was requested, but the pytest-verifier plugin is not "
    "active in this session, so failed checks could never fail the test. Load the plugin "
    "(remove '-p no:pytest_verifier', or add '-p pytest_verifier' when "
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD is set) instead of importing the fixture on its own."
)

_run_key = pytest.StashKey[Run]()


def pytest_addhooks(pluginmanager: pytest.PytestPluginManager) -> None:
    """Register ``pytest_verify_results`` for plugins that read results without importing us."""
    from . import _hookspecs

    pluginmanager.add_hookspecs(_hookspecs)


def _close_phase(
    item: pytest.Item, run: Run, when: str, exc: Optional[BaseException]
) -> Optional[ChecksFailedError]:
    """Judge the checks recorded up to the end of phase *when*.

    Returns the error to raise, or ``None``. If the phase already raised *exc*, the soft
    summary is kept for that phase's report instead, except after a skip: a skip must not
    hide a failed check, so the failure is raised in its place.
    """
    start, pending = run.take_unjudged()
    if not pending:
        return None
    passed = all(record.get("passed") is True for record in pending)
    run.judged[when] = pending
    checks: List[CheckDescriptor] = [dict(record) for record in pending]  # type: ignore[misc]
    item.ihook.pytest_verify_results(item=item, when=when, checks=checks, passed=passed)
    if passed:
        return None
    if exc is None or _is_skip(exc):
        return ChecksFailedError(pending, start=start)
    run.sections[when] = format_summary(pending, start=start)
    return None


def _is_skip(exc: BaseException) -> bool:
    """Whether *exc* skips the test: ``pytest.skip`` or unittest's ``SkipTest``."""
    if isinstance(exc, pytest.skip.Exception):
        return True
    unittest = sys.modules.get("unittest")  # nothing can raise SkipTest before it is imported
    return unittest is not None and isinstance(exc, unittest.SkipTest)


def _unittest_outcome(item: pytest.Item) -> Optional[BaseException]:
    """The failure or skip a unittest ``TestCase`` recorded instead of raising it, if any.

    pytest's unittest support stores them in ``item._excinfo`` and reports the first one.
    """
    recorded = getattr(item, "_excinfo", None)
    if isinstance(recorded, list) and recorded:
        return getattr(recorded[0], "value", None)
    return None


def _replace_unittest_outcome(
    item: pytest.Item, error: ChecksFailedError, skip: BaseException
) -> None:
    """Raise *error* and report it in place of the skip the ``TestCase`` recorded."""
    try:
        raise error from skip
    except ChecksFailedError:
        item._excinfo[0] = pytest.ExceptionInfo.from_current()  # type: ignore[attr-defined]
        raise


def _run_phase(item: pytest.Item, when: str) -> Generator[None, Any, Any]:
    run = item.stash.get(_run_key, None)
    if run is None:  # pragma: no cover - our setup wrapper always creates it
        return (yield)
    run.phase = when
    # Checks built with ``checks.*`` in the test body must be used; see _unused.
    tracker = _unused.start() if when == "call" else None
    try:
        try:
            result = yield
        finally:
            if tracker is not None:
                _unused.stop(tracker)
        if tracker is not None and _unittest_outcome(item) is None:
            _unused.warn_unused(tracker)  # raises when the warning is turned into an error
    except BaseException as exc:
        error = _close_phase(item, run, when, exc)
        if when == "teardown":
            run.closed = True
        if error is not None:
            raise error from exc
        raise
    if when == "teardown":
        run.closed = True
    if when != "setup":  # checks made in setup are judged with the test body
        recorded = _unittest_outcome(item) if when == "call" else None
        error = _close_phase(item, run, when, recorded)
        if error is not None:
            if recorded is not None:
                _replace_unittest_outcome(item, error, recorded)
            raise error
    return result


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, Any, Any]:
    """Start a fresh run for every attempt (pytest-rerunfailures reuses the item)."""
    run = Run()
    item.stash[_run_key] = run
    item.stash[check_results_key] = run.records
    return (yield from _run_phase(item, "setup"))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, Any]:
    """Raise ``ChecksFailedError`` after the test body if a check made so far failed."""
    return (yield from _run_phase(item, "call"))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(
    item: pytest.Item, nextitem: Optional[pytest.Item]
) -> Generator[None, Any, Any]:
    """Raise ``ChecksFailedError`` after teardown if a check made during teardown failed."""
    return (yield from _run_phase(item, "teardown"))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Add pending soft summaries to the report; never changes the outcome."""
    report = yield
    try:
        _decorate_report(item, call, report)
    except Exception:  # pragma: no cover - reporting must never break the session
        pass
    return report


def _decorate_report(
    item: pytest.Item, call: pytest.CallInfo[None], report: pytest.TestReport
) -> None:
    run = item.stash.get(_run_key, None)
    if run is None:
        return
    checks = run.judged.pop(call.when, None)
    if checks:
        # JSON-safe, so it survives the serialization of reports (pytest-xdist).
        report.verify_checks = checks  # type: ignore[attr-defined]
    section = run.sections.pop(call.when, None)
    if section is not None and report.failed:
        longrepr = report.longrepr
        if hasattr(longrepr, "addsection"):
            longrepr.addsection("Soft assertion failures", section)  # type: ignore[union-attr]
        elif isinstance(longrepr, str):
            report.longrepr = f"{longrepr}\n\nSoft assertion failures:\n{section}"
    if call.excinfo is not None and isinstance(call.excinfo.value, ChecksFailedError):
        _point_crash_line_at_test(item, call.excinfo, report)
    if call.when == "teardown":
        # The run's records stay available through the results stash; drop the rest.
        del item.stash[_run_key]


def _point_crash_line_at_test(
    item: pytest.Item, excinfo: pytest.ExceptionInfo[BaseException], report: pytest.TestReport
) -> None:
    """Point the one-line crash entry (``--tb=line``, ``-r`` summaries) at the test.

    Its location becomes the test's line instead of this plugin's, and its message the
    ``N of M checks failed`` summary without the exception's module path, as in 0.3.1.
    """
    longrepr = report.longrepr
    if not hasattr(longrepr, "reprcrash"):
        return
    if longrepr.reprcrash is None:  # type: ignore[union-attr]
        # pytest < 7.4 gives pytrace=False failures no crash entry; 7.4+ builds it like this.
        longrepr.reprcrash = excinfo._getreprcrash()  # type: ignore[union-attr]
    crash = longrepr.reprcrash  # type: ignore[union-attr]
    if crash is None:
        return
    crash.message = str(excinfo.value)
    path, lineno, _ = item.reportinfo()
    if lineno is None:
        return
    crash.path = str(path)
    crash.lineno = lineno + 1


@pytest.fixture()
def verify(request: pytest.FixtureRequest) -> Verify:
    """Soft-assertion checks for this test.

    Every ``verify.*`` call is judged immediately, recorded, and returned as a descriptor with
    ``passed`` set. Failed checks never stop the test: they are collected and raised together
    as ``ChecksFailedError`` once the phase that recorded them ends.
    """
    run = request.node.stash.get(_run_key, None)
    if run is None:
        pytest.fail(_NOT_ACTIVE, pytrace=False)
    return recording_verify(run)
