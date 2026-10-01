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

A check made through ``verify.require``, or any check with ``--verify-fail-fast``, raises
``ChecksFailedError`` as soon as it fails (see :mod:`pytest_verifier._run`). The end of the phase
then keeps that error instead of adding the same summary as a section.

Checks built with ``pytest_verifier.checks`` in the test body and still unused when the test's
teardown ends give an ``UnusedCheckWarning`` (see :mod:`pytest_verifier._unused`).
"""
from __future__ import annotations

import contextlib
import inspect
import os
import re
import sys
from types import CodeType
from typing import Any, Dict, FrozenSet, Generator, Iterator, List, Optional, Set

import pytest

from . import _unused
from ._descriptors import CheckDescriptor
from ._exceptions import ChecksFailedError, for_terminal, format_summary
from ._location import display_path, split
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
_tracker_key = pytest.StashKey[_unused.Tracker]()
_paused_key = pytest.StashKey[bool]()


_OLD_INSTALL = (
    "pytest-verify, the previous name of pytest-verifier, is still installed, and pytest "
    "cannot load both. Uninstall it with 'pip uninstall pytest-verify' (or 'uv pip uninstall "
    "pytest-verify'). If it is not installed, delete the pytest_verify.egg-info folder left in "
    "the project by an old editable install."
)

#: The entry point of pytest-verify 0.1 to 0.5, the previous name of this plugin.
_OLD_ENTRY_POINT = ("pytest11", "verify", "pytest_verify._fixture")


def _old_plugin_will_load(pluginmanager: pytest.PytestPluginManager) -> bool:
    """Whether an old pytest-verify is installed and pytest is going to load it too."""
    if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") or pluginmanager.is_blocked("verify"):
        return False
    try:
        from importlib import metadata

        entry_points = metadata.distribution("pytest-verify").entry_points
    except Exception:  # not installed
        return False
    return any(
        (entry.group, entry.name, entry.value) == _OLD_ENTRY_POINT for entry in entry_points
    )


_FAIL_FAST_HELP = (
    "Stop each test at its first failed check, as if every check were made with "
    "verify.require."
)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("pytest-verifier", "soft assertions (pytest-verifier)")
    group.addoption(
        "--verify-fail-fast",
        action="store_true",
        default=None,
        dest="verify_fail_fast",
        help=_FAIL_FAST_HELP + " Default: the verify_fail_fast ini setting (false).",
    )
    parser.addini("verify_fail_fast", _FAIL_FAST_HELP, type="bool", default=False)


def _fail_fast(config: pytest.Config) -> bool:
    option = config.getoption("verify_fail_fast", None)
    if option is not None:
        return bool(option)
    return bool(config.getini("verify_fail_fast"))


def pytest_addhooks(pluginmanager: pytest.PytestPluginManager) -> None:
    """Register ``pytest_verify_results`` for plugins that read results without importing us."""
    from . import _hookspecs

    # pytest-verify registers the same fixture and hooks (0.5 also this hook spec, so whichever
    # loads second would fail with a pluggy error): say what to do instead.
    if _old_plugin_will_load(pluginmanager):
        raise pytest.UsageError(_OLD_INSTALL)
    try:
        pluginmanager.add_hookspecs(_hookspecs)
    except ValueError as exc:  # pytest-verify 0.5 was loaded first
        raise pytest.UsageError(_OLD_INSTALL) from exc


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
    options = _summary_options(item.config)
    if run.raised(exc):  # a required check stopped the phase; its error lists the checks
        stopped: ChecksFailedError = exc  # type: ignore[assignment]
        if stopped.start == start and len(stopped.results) == len(pending):
            return None
        return ChecksFailedError(pending, start=start, **options)  # also checks made after it
    if exc is None or _is_skip(exc):
        return ChecksFailedError(pending, start=start, **options)
    run.sections[when] = format_summary(pending, start=start, **options)
    return None


#: How many passed checks a summary lists unless pytest runs with ``-vv``.
_PASSED_SHOWN = 10


def _summary_options(config: pytest.Config) -> Dict[str, Any]:
    """``max_passed`` for the summaries of this session."""
    verbosity = config.getoption("verbose", 0)
    max_passed = None if isinstance(verbosity, int) and verbosity >= 2 else _PASSED_SHOWN
    return {"max_passed": max_passed}


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


def _is_unittest(item: pytest.Item) -> bool:
    """Whether *item* is a unittest ``TestCase`` test, whose ``setUp`` runs with its body."""
    unittest = sys.modules.get("unittest")
    cls = getattr(item, "cls", None)
    return unittest is not None and isinstance(cls, type) and issubclass(cls, unittest.TestCase)


def _finish_tracking(item: pytest.Item, warn: bool) -> None:
    """Stop tracking the test's checks and, with *warn*, report the ones never used."""
    tracker = item.stash.get(_tracker_key, None)
    if tracker is None:
        return
    del item.stash[_tracker_key]
    unused = _unused.finish(tracker)
    if warn and not tracker.quiet:
        _unused.warn_unused(unused)  # raises when the warning is turned into an error


def _run_phase(item: pytest.Item, when: str) -> Generator[None, Any, Any]:
    run = item.stash.get(_run_key, None)
    if run is None:  # pragma: no cover - our setup wrapper always creates it
        return (yield)
    run.phase = when
    # Checks built with ``checks.*`` in the test body must be used by the end of teardown;
    # nothing else is tracked (see _unused).
    tracker = None
    if when == "call" and not _is_unittest(item):
        tracker = item.stash[_tracker_key] = _unused.start()
    token = _unused.pause() if tracker is None else None
    try:
        try:
            result = yield
        except BaseException:
            if tracker is not None:
                tracker.quiet = True  # the failure or skip says enough
            raise
        finally:
            if tracker is not None:
                _unused.stop_building(tracker)
            if token is not None:
                _unused.resume(token)
        if when == "teardown":
            _finish_tracking(item, warn=True)
    except BaseException as exc:
        if when == "teardown":
            _finish_tracking(item, warn=False)
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
    _finish_tracking(item, warn=False)  # left over by an attempt that did not reach teardown
    config = item.config
    run = Run(
        str(config.rootpath),
        _test_codes(item),
        fail_fast=_fail_fast(config),
        **_summary_options(config),
    )
    item.stash[_run_key] = run
    item.stash[check_results_key] = run.records
    return (yield from _run_phase(item, "setup"))


def _test_codes(item: pytest.Item) -> FrozenSet[CodeType]:
    """The code of the test function, also behind decorators, to tell a helper's checks from
    the test's own."""
    codes: Set[CodeType] = set()
    for name in ("function", "obj"):
        try:
            function = getattr(item, name, None)
            for candidate in (function, inspect.unwrap(function) if callable(function) else None):
                code = getattr(getattr(candidate, "__func__", candidate), "__code__", None)
                if isinstance(code, CodeType):
                    codes.add(code)
        except Exception:  # an item whose function cannot be read: no ``called_from``
            pass
    return frozenset(codes)


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
def pytest_fixture_setup() -> Generator[None, Any, Any]:
    """Track nothing while a fixture is set up, even when the test body requests it."""
    token = _unused.pause()
    try:
        return (yield)
    finally:
        _unused.resume(token)


def _pause_session(config: pytest.Config) -> None:
    """Track nothing for as long as *config* lives, except in the test bodies it runs.

    A ``pytester`` session run inside a test body imports modules, loads conftests and sets up
    fixtures while that test's tracker is active; none of that belongs to the test.
    """
    if config.stash.get(_paused_key, False):
        return
    config.stash[_paused_key] = True
    token = _unused.pause()
    config.add_cleanup(lambda: _unused.resume(token))


@pytest.hookimpl(wrapper=True)
def pytest_load_initial_conftests(early_config: pytest.Config) -> Generator[None, Any, Any]:
    _pause_session(early_config)
    return (yield)


_NOT_BLOCKED = (
    "'-p no:pytest_verifier' did not turn the plugin off, because a conftest loads it as "
    "'pytest_verifier.plugin'. List 'pytest_verifier' in pytest_plugins instead, or use "
    "'-p no:pytest_verifier.plugin'."
)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    _pause_session(config)  # loaded after the initial conftests, e.g. by a conftest
    manager = config.pluginmanager
    if manager.is_blocked("pytest_verifier") and manager.get_plugin("pytest_verifier") is None:
        config.issue_config_time_warning(pytest.PytestConfigWarning(_NOT_BLOCKED), stacklevel=2)


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
            longrepr.addsection(_SECTION, section)  # type: ignore[union-attr]
        elif isinstance(longrepr, str):
            report.longrepr = f"{longrepr}\n\n{_SECTION}:\n{section}"
    if call.excinfo is not None and isinstance(call.excinfo.value, ChecksFailedError):
        _point_crash_line_at_test(item, call.excinfo, report)
    if call.when == "teardown":
        # The run's records stay available through the results stash; drop the rest.
        del item.stash[_run_key]


#: The report section that holds the soft summary when the phase also raised.
_SECTION = "Soft assertion failures"

#: The first line of a ``ChecksFailedError`` message.
_HEADER = re.compile(r"\d+ of \d+ checks failed(: .*)?$")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Print soft summaries in the encoding of the terminal (also on an xdist controller)."""
    if report.failed and getattr(report, "verify_checks", None):
        try:
            _print_for_terminal(report.longrepr)
        except Exception:  # pragma: no cover - reporting must never break the session
            pass


def _print_for_terminal(longrepr: Any) -> None:
    """Make *longrepr* print its soft summaries in the encoding of the stream it is printed to.

    Its text stays as it is: junitxml, ``report.longreprtext`` and other plugins render it into
    a buffer that has no encoding. Only a terminal that cannot show every character gets the
    summary lines with ASCII markers and escapes, line by line.
    """
    original: Any = getattr(longrepr, "toterminal", None)
    if not _is_bound_to(original, longrepr):  # already adapted, or not a repr
        return

    def toterminal(tw: Any) -> None:
        encoding = getattr(getattr(tw, "_file", None), "encoding", None)
        if not isinstance(encoding, str):
            original(tw)
            return
        with _summaries_for(longrepr, encoding):
            original(tw)

    longrepr.toterminal = toterminal


def _is_bound_to(method: Any, owner: Any) -> bool:
    return getattr(method, "__self__", None) is owner


@contextlib.contextmanager
def _summaries_for(longrepr: Any, encoding: str) -> Iterator[None]:
    """Show *longrepr*'s soft summaries in *encoding* while the block runs."""
    sections = list(getattr(longrepr, "sections", None) or [])
    chain = getattr(longrepr, "chain", None)
    tracebacks = [link[0] for link in chain] if chain else [longrepr.reprtraceback]
    entries = [
        entry
        for traceback in tracebacks
        for entry in getattr(traceback, "reprentries", [])
        if getattr(entry, "style", None) == "value"
        and entry.lines
        and _HEADER.match(entry.lines[0])
    ]
    originals = [entry.lines for entry in entries]
    try:
        for entry in entries:
            entry.lines = for_terminal("\n".join(entry.lines), encoding).split("\n")
        longrepr.sections = [
            (name, for_terminal(content, encoding) if name == _SECTION else content, *rest)
            for name, content, *rest in sections
        ]
        yield
    finally:
        for entry, lines in zip(entries, originals):
            entry.lines = lines
        if hasattr(longrepr, "sections"):
            longrepr.sections = sections


def _point_crash_line_at_test(
    item: pytest.Item, excinfo: pytest.ExceptionInfo[BaseException], report: pytest.TestReport
) -> None:
    """Point the one-line crash entry (``--tb=line``, ``-r`` summaries) at the test.

    Its location becomes the line in the test's file that made the first failed check (else
    the test's ``def`` line) instead of this plugin's, and its message the ``N of M checks
    failed`` summary without the exception's module path, as in 0.3.1.
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
    crash.lineno = _first_failed_line(item, excinfo.value) or lineno + 1


def _first_failed_line(item: pytest.Item, error: Any) -> Optional[int]:
    """The line in the test's file that made the first failed check, if any."""
    test_file = display_path(str(item.path), str(item.config.rootpath))
    for result in getattr(error, "results", ()):
        if result.get("passed") is True:
            continue
        for key in ("called_from", "location"):
            site = split(result.get(key))
            if site is not None and site[0] == test_file:
                return site[1]
    return None


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
