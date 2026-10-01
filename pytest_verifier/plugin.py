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

A check made through ``verify.require``, or any check with ``--verify-fail-fast`` (except in
teardown and a unittest ``TestCase``'s cleanup), raises ``ChecksFailedError`` as soon as it
fails (see :mod:`pytest_verifier._run`). The end of the phase then keeps that error, or one
that takes its place, instead of adding the same summary as a section.

Checks built with ``pytest_verifier.checks`` in the test body and still unused when the test's
teardown ends give an ``UnusedCheckWarning`` (see :mod:`pytest_verifier._unused`).
"""
from __future__ import annotations

import contextlib
import functools
import inspect
import os
import re
import sys
from types import CodeType
from typing import Any, Dict, FrozenSet, Generator, Iterator, List, Optional, Sequence, Set

import pytest

from . import _unused
from ._descriptors import CheckDescriptor
from ._exceptions import ChecksFailedError, for_terminal, format_summary, headline
from ._location import FunctionCode, display_path, split
from ._run import Run, cause_of, recording_verify
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
    "verify.require (checks made in fixture teardown, or in a TestCase's tearDown and "
    "cleanups, stay soft)."
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
    item: pytest.Item,
    run: Run,
    when: str,
    exc: Optional[BaseException],
    later: Sequence[ChecksFailedError] = (),
) -> Optional[ChecksFailedError]:
    """Judge the checks recorded up to the end of phase *when*.

    Returns the error to raise, or ``None``. If the phase already raised *exc*, the soft
    summary is kept for that phase's report instead, except after a skip: a skip must not
    hide a failed check, so the failure is raised in its place. *later* holds stop errors a
    ``TestCase`` recorded after *exc*.
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
    # A required check stopped the phase: its error lists the checks made so far. The phase
    # raised that error, or one linked to it (an error raised while it unwound, a group).
    stops = _linked_stops(run, exc) + list(later)
    stopped_at = run.stopped_at(stops[0]) if stops else None
    if run.raised(exc):
        stopped: ChecksFailedError = exc  # type: ignore[assignment]
        if stopped.start == start and stopped.results == pending:
            return None
        # Checks were made after it (in a ``finally``), or a composite absorbed some of them.
        return ChecksFailedError(pending, start=start, stopped_at=stopped_at, **options)
    if exc is None or _is_skip(exc):
        return ChecksFailedError(pending, start=start, stopped_at=stopped_at, **options)
    # Also when *exc* links to a stop: pytest may print it in the chain too, but only this
    # section shows the summary with every pytest (groups only from 7.2, 15 members at most)
    # and in the terminal's encoding.
    run.sections[when] = format_summary(pending, start=start, stopped_at=stopped_at, **options)
    return None


def _linked_stops(run: Run, exc: Optional[BaseException]) -> List[ChecksFailedError]:
    """The errors :meth:`Run.stop` raised that *exc* is, is chained to or groups (an exception
    group's members); nearest first."""
    found: List[ChecksFailedError] = []
    seen: Set[int] = set()
    queue: List[Optional[BaseException]] = [exc]
    while queue:
        link = queue.pop(0)
        if link is None or id(link) in seen:
            continue
        seen.add(id(link))
        if run.raised(link):
            found.append(link)  # type: ignore[arg-type]
        queue.append(cause_of(link))
        members = getattr(link, "exceptions", None)
        if isinstance(members, tuple):  # an exception group
            queue.extend(member for member in members if isinstance(member, BaseException))
    return found


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


def _take_place(run: Run, error: ChecksFailedError, exc: BaseException) -> Optional[BaseException]:
    """Prepare *error*, raised in place of *exc*; returns what it chains to.

    A skip stays visible as the cause. *error* lists the checks of a stop error that *exc* is
    or is chained to, so it takes that error's place: out of the chain, instead of repeating
    its summary, and with its traceback, so that ``--pdb`` still opens in the test.
    """
    stops = _linked_stops(run, exc)
    if stops:
        error.with_traceback(stops[0].__traceback__)
    if run.raised(exc):
        return cause_of(exc)
    _cut_stops(run, exc)
    return exc


def _cut_stops(run: Run, exc: BaseException) -> None:
    """Take the errors :meth:`Run.stop` raised out of *exc*'s chain."""
    link: Optional[BaseException] = exc
    seen: Set[int] = set()
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        after = cause_of(link)
        following = after
        while following is not None and run.raised(following) and id(following) not in seen:
            seen.add(id(following))
            following = cause_of(following)
        if following is not after:
            if link.__cause__ is not None or link.__suppress_context__:
                link.__cause__ = following
            else:
                link.__context__ = following
        link = following


def _replace_unittest_outcome(
    item: pytest.Item, run: Run, error: ChecksFailedError, recorded: BaseException
) -> None:
    """Raise *error* and report it in place of the skip or stop the ``TestCase`` recorded."""
    try:
        raise error from _take_place(run, error, recorded)
    except ChecksFailedError:
        item._excinfo[0] = pytest.ExceptionInfo.from_current()  # type: ignore[attr-defined]
        raise


def _take_later_stops(item: pytest.Item, run: Run) -> List[ChecksFailedError]:
    """Take out the stop errors a ``TestCase`` recorded after its first failure or skip: the
    summary of the call phase lists their checks, and pytest would report each one again at
    teardown."""
    recorded = getattr(item, "_excinfo", None)
    if not isinstance(recorded, list) or len(recorded) < 2:
        return []
    stops: List[ChecksFailedError] = []
    kept = recorded[:1]
    for info in recorded[1:]:
        value = getattr(info, "value", None)
        if run.raised(value):
            stops.append(value)  # type: ignore[arg-type]
        else:
            kept.append(info)
    recorded[:] = kept
    return stops


#: What a ``TestCase`` runs to clean up after the test method, in pytest's call phase:
#: ``_callTearDown`` calls ``tearDown`` (and ``asyncTearDown``), however it is defined.
_CLEANUP_METHODS = ("_callTearDown", "doCleanups")


def _soft_cleanup(item: pytest.Item, run: Run) -> List[Any]:
    """Mark *run* as cleaning up while the ``TestCase`` cleans up, so that fail-fast leaves the
    cleanup soft, as it does fixture teardown. Returns ``(testcase, name, wrapper)`` triples
    for :func:`_unwrap_cleanup`."""
    testcase = getattr(item, "_testcase", None)
    installed = []
    for name in _CLEANUP_METHODS:
        method = getattr(testcase, name, None)
        if not _is_bound_to(method, testcase):
            continue
        wrapper = _cleaning(run, method)
        try:
            setattr(testcase, name, wrapper)
        except Exception:  # a class that does not take instance attributes: stays strict
            continue
        installed.append((testcase, name, wrapper))
    return installed


def _cleaning(run: Run, method: Any) -> Any:
    @functools.wraps(method)
    def clean_up(*args: Any, **kwargs: Any) -> Any:
        cleaning, run.cleaning = run.cleaning, True
        try:
            return method(*args, **kwargs)
        finally:
            run.cleaning = cleaning

    return clean_up


def _unwrap_cleanup(installed: List[Any]) -> None:
    """Remove what :func:`_soft_cleanup` installed: each wrapper holds its instance, which
    could otherwise only be freed by the cyclic garbage collector."""
    for testcase, name, wrapper in installed:
        with contextlib.suppress(Exception):
            if vars(testcase).get(name) is wrapper:
                delattr(testcase, name)


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
    testcase = when == "call" and _is_unittest(item)
    installed = _soft_cleanup(item, run) if testcase and run.fail_fast else []
    # Checks built with ``checks.*`` in the test body must be used by the end of teardown;
    # nothing else is tracked (see _unused).
    tracker = None
    if when == "call" and not testcase:
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
            _unwrap_cleanup(installed)
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
            raise error from _take_place(run, error, exc)
        raise
    if when == "teardown":
        run.closed = True
    if when != "setup":  # checks made in setup are judged with the test body
        recorded = _unittest_outcome(item) if when == "call" else None
        later = _take_later_stops(item, run) if recorded is not None else []
        error = _close_phase(item, run, when, recorded, later)
        if error is not None:
            if recorded is not None:
                _replace_unittest_outcome(item, run, error, recorded)
            raise error
    return result


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, Any, Any]:
    """Start a fresh run for every attempt (pytest-rerunfailures reuses the item)."""
    _finish_tracking(item, warn=False)  # left over by an attempt that did not reach teardown
    config = item.config
    run = Run(
        str(config.rootpath),
        _test_function(item),
        fail_fast=_fail_fast(config),
        **_summary_options(config),
    )
    item.stash[_run_key] = run
    item.stash[check_results_key] = run.records
    return (yield from _run_phase(item, "setup"))


def _test_function(item: pytest.Item) -> FunctionCode:
    """The code of the test function, to tell a helper's checks from the test's own.

    Also the code behind decorators that keep ``__wrapped__``, and Hypothesis's inner test.
    When none of that code is named like the test, a decorator without ``functools.wraps``
    hid it: the test's file and name match it instead, and the code found is a wrapper's.
    Comprehensions and generator expressions written in the test body run in code of their
    own (list, set and dict comprehensions only before Python 3.12), which is part of the test
    too.
    """
    codes: Set[CodeType] = set()
    for name in ("function", "obj"):
        try:
            function = getattr(item, name, None)
            inner = getattr(getattr(function, "hypothesis", None), "inner_test", None)
            for candidate in (function, inner):
                if not callable(candidate):
                    continue
                for each in (candidate, inspect.unwrap(candidate)):
                    code = getattr(getattr(each, "__func__", each), "__code__", None)
                    if isinstance(code, CodeType):
                        codes.add(code)
        except Exception:  # an item whose function cannot be read: no ``called_from``
            pass
    original = getattr(item, "originalname", None)
    if not isinstance(original, str):
        original = None
    test_file = None
    with contextlib.suppress(Exception):
        test_file = str(item.path)
    # A plugin's generated test (pytest-bdd) or a decorator's wrapper: not the user's code. Code
    # in the test module is (a test made by a factory, an alias, a lambda).
    wrappers = frozenset(
        code
        for code in codes
        if original and code.co_name != original and code.co_filename != test_file
    )
    names: Dict[str, FrozenSet[str]] = {}
    if original and not any(code.co_name == original for code in codes):
        files = {code.co_filename for code in codes}
        if test_file is not None:
            files.add(test_file)
        names[original] = frozenset(files)
    pending = list(codes)
    while pending:
        for const in pending.pop().co_consts:
            if isinstance(const, CodeType) and const.co_name in _INLINE_SCOPES:
                codes.add(const)
                pending.append(const)
    return FunctionCode(frozenset(codes), names, wrappers)


#: The names of the code that comprehensions and generator expressions run in.
_INLINE_SCOPES = frozenset({"<listcomp>", "<setcomp>", "<dictcomp>", "<genexpr>"})


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
_HEADER = re.compile(r"\d+ of \d+ checks failed(, stopped at \[\d+\])?(: .*)?$")


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


#: The ``TerminalReporter`` methods that print crash lines (``--tb=line`` and ``-r``).
_CRASH_LINE_PRINTERS = ("summary_errors", "summary_failures", "short_test_summary")


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_terminal_summary(terminalreporter: Any) -> Generator[None, Any, Any]:
    """Print the crash lines of soft failures (``-r`` and ``--tb=line``) in the terminal's
    encoding, as :func:`_print_for_terminal` does for the full reports.

    Only while the terminal reporter prints them: other plugins' terminal summaries read the
    reports as they are.
    """
    printers: Dict[str, Any] = {}
    try:
        for name in _CRASH_LINE_PRINTERS:
            method = getattr(terminalreporter, name, None)
            if _is_bound_to(method, terminalreporter):
                printers[name] = _printing_crash_lines(terminalreporter, method)
                setattr(terminalreporter, name, printers[name])
    except Exception:  # pragma: no cover - reporting must never break the session
        pass
    try:
        return (yield)
    finally:
        for name, printer in printers.items():
            if vars(terminalreporter).get(name) is printer:
                delattr(terminalreporter, name)


def _printing_crash_lines(terminalreporter: Any, method: Any) -> Any:
    @functools.wraps(method)
    def printer(*args: Any, **kwargs: Any) -> Any:
        saved: List[Any] = []
        try:
            saved = _crash_lines_for_terminal(terminalreporter)
        except Exception:  # pragma: no cover - reporting must never break the session
            pass
        try:
            return method(*args, **kwargs)
        finally:
            for crash, message in saved:
                crash.message = message

    return printer


def _crash_lines_for_terminal(terminalreporter: Any) -> List[Any]:
    """Adapt the crash messages of soft failures; returns ``(crash, original message)`` pairs."""
    encoding = getattr(getattr(terminalreporter._tw, "_file", None), "encoding", None)
    if not isinstance(encoding, str):
        return []
    saved = []
    for key in ("failed", "error"):
        for report in terminalreporter.stats.get(key, ()):
            crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
            if crash is None:
                continue
            message = getattr(crash, "message", None)
            if (
                getattr(report, "verify_checks", None)
                and isinstance(message, str)
                and _HEADER.match(message.split("\n", 1)[0])
            ):
                adapted = for_terminal(message, encoding)
                if adapted != message:
                    saved.append((crash, message))
                    crash.message = adapted
    return saved


def _point_crash_line_at_test(
    item: pytest.Item, excinfo: pytest.ExceptionInfo[BaseException], report: pytest.TestReport
) -> None:
    """Point the one-line crash entry (``--tb=line``, ``-r`` summaries) at the test.

    Its location becomes the line in the test function's file that made the check the message
    names first (else the test's ``def`` line) instead of this plugin's, and its message the
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
    crash.path, crash.lineno = str(path), lineno + 1
    # reportinfo's file is the one that defines the test function (a base class's, for an
    # inherited test), or a decorator's when one without ``functools.wraps`` hides the test.
    # ``called_from`` first: for a helper, the test's call is the line to show, not the
    # helper's, even when the helper is in one of these files.
    test_files = dict.fromkeys((str(path), str(getattr(item, "path", path))))
    for key in ("called_from", "location"):
        for test_file in test_files:
            line = _headline_line(item, test_file, excinfo.value, key)
            if line is not None:
                crash.path, crash.lineno = test_file, line
                return


def _headline_line(item: pytest.Item, path: str, error: Any, key: str) -> Optional[int]:
    """The line in *path*, a file of the test function, where the failed check the first line
    of *error* names was made (*key* ``"location"``) or called (``"called_from"``)."""
    results = getattr(error, "results", None)
    start = getattr(error, "start", 0)
    if not isinstance(results, list) or not isinstance(start, int):
        return None
    failed = [(i, r) for i, r in enumerate(results, start) if r.get("passed") is not True]
    if not failed:
        return None
    _, result = headline(failed, getattr(error, "stopped_at", None))
    site = split(result.get(key))
    if site is not None and site[0] == display_path(path, str(item.config.rootpath)):
        return site[1]
    return None


@pytest.fixture()
def verify(request: pytest.FixtureRequest) -> Verify:
    """Soft-assertion checks for this test.

    Every ``verify.*`` call is judged immediately, recorded, and returned as a descriptor with
    ``passed`` set. Failed checks are collected and raised together as ``ChecksFailedError``
    once the phase that recorded them ends. A check made through ``verify.require``, or any
    check with ``--verify-fail-fast`` (except checks made while fixtures are torn down or in a
    unittest ``TestCase``'s ``tearDown``, ``asyncTearDown`` and cleanups), raises as soon as it
    fails.
    """
    run = request.node.stash.get(_run_key, None)
    if run is None:
        pytest.fail(_NOT_ACTIVE, pytrace=False)
    return recording_verify(run)
