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
and attached to that phase's report as ``report.verify_checks``. With the
``verify_junit_properties`` setting they also become ``user_properties`` that junitxml writes,
and ``--verify-json`` writes them to a JSON Lines file, from the reports, so both work under
pytest-xdist.

A check made through ``verify.require``, or any check with ``--verify-fail-fast`` (except in
teardown and a unittest ``TestCase``'s cleanup), raises ``ChecksFailedError`` as soon as it
fails (see :mod:`pytest_verifier._run`). The end of the phase then keeps that error, or one
that takes its place, instead of adding the same summary as a section.

Checks built with ``pytest_verifier.checks`` in the test body and still unused when the test's
teardown ends give an ``UnusedCheckWarning`` (see :mod:`pytest_verifier._unused`).
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import json
import os
import re
import sys
from types import CodeType
from typing import (
    IO,
    Any,
    Dict,
    FrozenSet,
    Generator,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

import pytest

from . import _unused
from ._descriptors import CheckDescriptor
from ._exceptions import (
    ChecksFailedError,
    failure_header,
    for_terminal,
    format_summary,
    headline,
    junit_property,
)
from ._location import FunctionCode, display_path, split
from ._run import Run, cause_of, recording_verify
from ._stash import check_results_key
from ._summary import MODES, SessionSummary
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
#: Which checks become junit properties: ``"none"``, ``"failed"`` or ``"all"``.
_junit_key = pytest.StashKey[str]()
#: The ``user_properties`` entries the checks of the current attempt added.
_properties_key = pytest.StashKey[List[Tuple[str, object]]]()
#: How many passed checks a summary lists below ``-vv`` (all when ``None``).
_show_passed_key = pytest.StashKey[Optional[int]]()

#: Whether the terminal gets the ASCII form of summaries (``--verify-ascii``). Read when a
#: report is printed, which may be outside any hook that has the config (``--pdb``).
_ASCII_TERMINAL: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "pytest_verifier_ascii_terminal", default=False
)


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


_JUNIT_CHOICES = ("none", "failed", "all")

_JUNIT_HELP = (
    "Write checks to the junit XML report (--junitxml) as <property> elements of their test: "
    "none, failed (failed checks only) or all. Default: none."
)

_JSON_HELP = (
    "Write every check to PATH as JSON Lines: one object per check, with the test's node ID, "
    "the phase that judged it, the report's outcome, its index in the test and the check."
)

_SHOW_PASSED_HELP = (
    "How many passed checks a failure summary lists: a number, all or none (default 10). "
    "With -vv every passed check is listed."
)

_ASCII_HELP = (
    "Print failure summaries in the terminal with ASCII markers (x, ok) and escapes for other "
    "characters, as on a terminal that cannot show them. Reports such as junitxml keep the "
    "Unicode text."
)

_SUMMARY_HELP = (
    "Add a terminal section that groups the checks of every test by name: off (default), "
    "failed (names with a failed check), all, or stats (all, with the range of numeric values "
    "and the smallest margin to a limit)."
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
    group.addoption(
        "--verify-show-passed",
        action="store",
        dest="verify_show_passed",
        metavar="N|all|none",
        default=None,
        help=_SHOW_PASSED_HELP + " Overrides the verify_show_passed ini setting.",
    )
    group.addoption(
        "--verify-ascii",
        action="store_true",
        default=None,
        dest="verify_ascii",
        help=_ASCII_HELP + " Default: the verify_ascii ini setting (false).",
    )
    group.addoption(
        "--verify-summary",
        action="store",
        dest="verify_summary",
        metavar="off|failed|all|stats",
        default=None,
        help=_SUMMARY_HELP + " Overrides the verify_summary ini setting.",
    )
    group.addoption(
        "--verify-json",
        action="store",
        dest="verify_json",
        metavar="PATH",
        default=None,
        help=_JSON_HELP,
    )
    parser.addini("verify_fail_fast", _FAIL_FAST_HELP, type="bool", default=False)
    parser.addini("verify_show_passed", _SHOW_PASSED_HELP, default="10")
    parser.addini("verify_ascii", _ASCII_HELP, type="bool", default=False)
    parser.addini("verify_summary", _SUMMARY_HELP, default="off")
    parser.addini("verify_junit_properties", _JUNIT_HELP, default="none")


def _fail_fast(config: pytest.Config) -> bool:
    return bool(_setting(config, "verify_fail_fast"))


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
    _add_properties(item, start, pending)
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
    run.report_sections[when] = format_summary(
        pending, start=start, stopped_at=stopped_at, **options
    )
    return None


def _add_properties(item: pytest.Item, start: int, pending: List[CheckDescriptor]) -> None:
    """Add the judged checks to the test's ``user_properties`` for junitxml, as
    ``record_property`` does, when ``verify_junit_properties`` asks for them.

    pytest copies the item's properties into each report it makes afterwards, and junitxml
    writes the teardown report's, so the properties also reach an xdist controller.
    """
    which = item.config.stash.get(_junit_key, "none")
    if which == "none":
        return
    added = item.stash.setdefault(_properties_key, [])
    for index, record in enumerate(pending, start):
        if which == "failed" and record.get("passed") is True:
            continue
        entry: Tuple[str, object] = junit_property(index, record)
        item.user_properties.append(entry)
        added.append(entry)


def _drop_properties(item: pytest.Item) -> None:
    """Take out the properties of an earlier attempt (pytest-rerunfailures reuses the item)."""
    added = item.stash.get(_properties_key, None)
    if not added:
        return
    del item.stash[_properties_key]
    ids = {id(entry) for entry in added}
    item.user_properties[:] = [entry for entry in item.user_properties if id(entry) not in ids]


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
    if isinstance(verbosity, int) and verbosity >= 2:
        return {"max_passed": None}
    return {"max_passed": config.stash.get(_show_passed_key, _PASSED_SHOWN)}


def _setting(config: pytest.Config, name: str) -> Any:
    """The command-line option *name*, else the ini setting of the same name."""
    option = config.getoption(name, None)
    return config.getini(name) if option is None else option


def _show_passed(config: pytest.Config) -> Optional[int]:
    value = str(_setting(config, "verify_show_passed")).strip()
    if value == "all":
        return None
    if value == "none":
        return 0
    if re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise pytest.UsageError(
        f"verify_show_passed (--verify-show-passed) must be a number, all or none, not {value!r}"
    )


def _summary_mode(config: pytest.Config) -> str:
    value = str(_setting(config, "verify_summary")).strip()
    if value not in MODES:
        raise pytest.UsageError(
            f"verify_summary (--verify-summary) must be off, failed, all or stats, not {value!r}"
        )
    return value


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


def _take_place(
    run: Run,
    error: ChecksFailedError,
    exc: BaseException,
    later: Sequence[ChecksFailedError] = (),
) -> Optional[BaseException]:
    """Prepare *error*, raised in place of *exc*; returns what it chains to.

    A skip stays visible as the cause. *error* lists the checks of a stop error that *exc* is
    or is chained to (or of a *later* one a ``TestCase`` recorded), so it takes that error's
    place: out of the chain, instead of repeating its summary, and with its traceback, so that
    ``--pdb`` still opens in the test.
    """
    stops = _linked_stops(run, exc) + list(later)
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
    item: pytest.Item,
    run: Run,
    error: ChecksFailedError,
    recorded: BaseException,
    later: Sequence[ChecksFailedError],
) -> None:
    """Raise *error* and report it in place of the skip or stop the ``TestCase`` recorded."""
    try:
        raise error from _take_place(run, error, recorded, later)
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


#: What a ``TestCase`` runs to clean up after the test method, in pytest's call phase.
#: unittest's ``run()`` calls ``tearDown`` (and ``asyncTearDown``) through ``_callTearDown``,
#: however they are defined; frameworks with their own ``run()`` (twisted.trial, testtools)
#: call ``tearDown`` directly.
_CLEANUP_METHODS = ("_callTearDown", "tearDown", "asyncTearDown", "doCleanups")


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
    if inspect.iscoroutinefunction(method):  # IsolatedAsyncioTestCase wants a coroutine function

        @functools.wraps(method)
        async def clean_up_async(*args: Any, **kwargs: Any) -> Any:
            cleaning, run.cleaning = run.cleaning, True
            try:
                return await method(*args, **kwargs)
            finally:
                run.cleaning = cleaning

        return clean_up_async

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
                _replace_unittest_outcome(item, run, error, recorded, later)
            raise error
    return result


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, Any, Any]:
    """Start a fresh run for every attempt (pytest-rerunfailures reuses the item)."""
    _finish_tracking(item, warn=False)  # left over by an attempt that did not reach teardown
    _drop_properties(item)
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
    junit = config.stash[_junit_key] = _junit_setting(config)
    config.stash[_show_passed_key] = _show_passed(config)
    mode = _summary_mode(config)
    ascii_only = _ascii_setting(config)
    worker = hasattr(config, "workerinput")  # an xdist controller writes and prints
    path = config.getoption("verify_json", None)
    if path and not worker:
        manager.register(_JsonLines(_json_path(path)), _JSON_PLUGIN)
    if mode != "off" and not worker:
        manager.register(_Summary(SessionSummary(mode)), _SUMMARY_PLUGIN)
    if junit != "none" and not worker:
        manager.register(_JunitAttempts(), _JUNIT_PLUGIN)
    token = _ASCII_TERMINAL.set(ascii_only)
    config.add_cleanup(functools.partial(_reset, _ASCII_TERMINAL, token))


def _ascii_setting(config: pytest.Config) -> bool:
    try:
        return bool(_setting(config, "verify_ascii"))
    except ValueError as exc:  # getini of a bool setting: "invalid truth value 'maybe'"
        raise pytest.UsageError(f"verify_ascii must be true or false: {exc}") from exc


def _reset(variable: contextvars.ContextVar[Any], token: contextvars.Token[Any]) -> None:
    with contextlib.suppress(ValueError):  # cleaned up in another context
        variable.reset(token)


def _junit_setting(config: pytest.Config) -> str:
    value = str(config.getini("verify_junit_properties")).strip()
    if value not in _JUNIT_CHOICES:
        raise pytest.UsageError(
            f"verify_junit_properties must be none, failed or all, not {value!r}"
        )
    return value


#: The junit families whose schema allows properties on a test case.
_JUNIT_FAMILIES = ("xunit1", "legacy")

_JSON_PLUGIN = "pytest_verifier_json"
_JUNIT_PLUGIN = "pytest_verifier_junit"


def _json_path(path: str) -> str:
    """The ``--verify-json`` file, as ``--junitxml`` takes its path (``~`` and ``$VARS``
    expanded, relative to the folder pytest was started in), checked to be writable: its
    folders are created, but the file is emptied only when the session starts, so
    ``pytest --help`` keeps the last results."""
    path = os.path.normpath(os.path.abspath(os.path.expanduser(os.path.expandvars(path))))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8"):
            pass
    except OSError as exc:
        raise pytest.UsageError(f"--verify-json: cannot write {path}: {exc}") from exc
    return path


class _Attempts:
    """Each test's current attempt, as its reports arrive (on an xdist controller too): a new
    one starts at each setup report, and pytest-rerunfailures repeats an attempt after a report
    with the outcome ``"rerun"``. Tests are keyed by node ID and xdist worker, as one test runs
    on each worker with ``--dist each``."""

    def __init__(self) -> None:
        #: ``[attempt number, whether it is repeated]`` by test.
        self._tests: Dict[Tuple[str, Any], List[Any]] = {}

    def see(self, report: pytest.TestReport) -> Tuple[Tuple[str, Any], int, bool]:
        """``(test, attempt number from 1, whether this attempt is repeated)`` for *report*."""
        key = (report.nodeid, getattr(report, "node", None))
        state = self._tests.setdefault(key, [0, False])
        if report.when == "setup" or state[0] == 0:
            state[0] += 1
            state[1] = False
        outcome: str = report.outcome
        if outcome == "rerun":
            state[1] = True
        return key, state[0], state[1]


#: A junit property this plugin added: ``verify[3] 5V0 › Ripple``.
_OUR_PROPERTY = re.compile(r"verify\[\d+\] ")


class _JunitAttempts:
    """``verify_junit_properties`` with pytest-rerunfailures: junitxml writes a test case for
    each attempt whose teardown report it gets (pytest-rerunfailures 16.6.1 and later log the
    teardown of a repeated attempt too). Take this plugin's properties out of the reports of a
    repeated attempt, so only the last attempt's checks are in the report."""

    def __init__(self) -> None:
        self._attempts = _Attempts()

    @pytest.hookimpl(tryfirst=True)  # before junitxml reads the report
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        _, _, repeated = self._attempts.see(report)
        if repeated and report.user_properties:
            report.user_properties = [
                entry
                for entry in report.user_properties
                if not (isinstance(entry[0], str) and _OUR_PROPERTY.match(entry[0]))
            ]

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        """Warn when the junit report's family does not allow properties. Checked when the
        session starts, as a conftest may turn the report on in its ``pytest_configure``."""
        config = session.config
        if not config.getoption("xmlpath", None):
            return
        family = str(config.getini("junit_family"))
        if family not in _JUNIT_FAMILIES:
            config.issue_config_time_warning(
                pytest.PytestConfigWarning(
                    f"verify_junit_properties writes <property> elements, which junit_family "
                    f"{family!r} does not allow: tools that validate the report against its "
                    f"schema reject it. Set junit_family = xunit1 for them."
                ),
                stacklevel=2,
            )


_SUMMARY_PLUGIN = "pytest_verifier_summary"


class _Summary:
    """``--verify-summary``: collects the checks of every report, then prints the section."""

    def __init__(self, summary: SessionSummary) -> None:
        self._summary = summary
        self._attempts = _Attempts()
        #: The checks of each test's current attempt, counted when it ends without a rerun.
        self._pending: Dict[Tuple[str, Any], List[Any]] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        key, _, repeated = self._attempts.see(report)
        if report.when == "setup":
            self._pending[key] = []
        pending = self._pending.setdefault(key, [])
        pending.extend(getattr(report, "verify_checks", None) or [])
        if report.when == "teardown":
            del self._pending[key]
            if not repeated:  # pytest-rerunfailures runs the test again: only the last counts
                self._summary.add(report.nodeid, pending)

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        for (nodeid, _), pending in self._pending.items():  # attempts without a teardown
            self._summary.add(nodeid, pending)
        self._pending.clear()
        lines = self._summary.lines()
        if not lines:
            return
        encoding = _terminal_encoding(terminalreporter._tw)
        terminalreporter.write_sep("=", "pytest-verifier: checks by name")
        for line in lines:
            terminalreporter.write_line(for_terminal(line, encoding))


def _terminal_encoding(tw: Any) -> Optional[str]:
    """The encoding summaries are adapted to when *tw* prints them: ``"ascii"`` with
    ``--verify-ascii``, else its stream's. ``None`` for a writer without an encoding, such
    as the one that renders a report as text for junitxml: reports keep the Unicode text."""
    encoding = getattr(getattr(tw, "_file", None), "encoding", None)
    if not isinstance(encoding, str):
        return None
    return "ascii" if _ASCII_TERMINAL.get() else encoding


class _JsonLines:
    """``--verify-json``: one JSON object per judged check, from the reports, so it works on an
    xdist controller too. Each line holds the test's node ID, its attempt (from 1; more than
    one when pytest-rerunfailures repeats it), the phase that judged the check (``when``), the
    report's outcome, the check's index in the test, as summaries number it, and the check."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._file: Optional[IO[str]] = None
        self._attempts = _Attempts()
        #: Checks written so far in each test's current attempt.
        self._counts: Dict[Tuple[str, Any], int] = {}

    def pytest_sessionstart(self) -> None:
        # Lone surrogates cannot reach a record (snapshots escape them); the error handler is
        # a last guard, as one line must never stop the run.
        self._file = open(
            self._path, "w", encoding="utf-8", errors="backslashreplace", buffering=1
        )

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if self._file is None:
            return
        key, attempt, _ = self._attempts.see(report)
        if report.when == "setup":
            self._counts[key] = 0
        index = self._counts.get(key, 0)
        for check in getattr(report, "verify_checks", None) or []:
            line = {
                "nodeid": report.nodeid,
                "attempt": attempt,
                "when": report.when,
                "outcome": report.outcome,
                "index": index,
                "check": check,
            }
            self._file.write(_json_line(line))
            index += 1
        self._counts[key] = index

    def pytest_unconfigure(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


#: Characters JSON leaves as they are but ``str.splitlines()`` splits on.
_LINE_SEPARATORS = str.maketrans({"\x85": "\\u0085", "\u2028": "\\u2028", "\u2029": "\\u2029"})


def _json_line(line: Dict[str, Any]) -> str:
    """*line* as one line of JSON. A check that ``json`` cannot write as it is (a hand-built
    record with a key that is not text) is written with its keys as text."""
    try:
        text = json.dumps(line, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = json.dumps(_text_keys(line), ensure_ascii=False, default=str)
    return text.translate(_LINE_SEPARATORS) + "\n"


def _text_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _text_keys(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_text_keys(item) for item in value]
    return value


def pytest_report_header(config: pytest.Config) -> List[str]:
    """Say when the project's agent skill was installed by another pytest-verifier version."""
    from ._installer import AGENTS_SKILLS, compare_versions, stale_skills

    root = config.rootpath
    # The command installs in the current folder: name the rootdir when pytest ran elsewhere.
    where = "" if config.invocation_params.dir == root else f" (run it in {root})"
    lines = []
    for label, found, version in stale_skills(root):
        flag = "--agents" if label.startswith(AGENTS_SKILLS[0]) else "--claude"
        command = f"pytest-verifier skill install {flag}"
        if found and version and compare_versions(found, version) == 1:
            lines.append(
                f"pytest-verifier {version}: the agent skill in {label} is for {found}, a newer "
                f"pytest-verifier; upgrade pytest-verifier, or match the skill to {version} "
                f"with: {command}{where}"
            )
        else:
            lines.append(
                f"pytest-verifier {version}: the agent skill in {label} is for "
                f"{found or 'another version'}; update it with: {command}{where}"
            )
    return lines


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
        _name_failures_in_xfail(report, checks)
    section = run.report_sections.pop(call.when, None)
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


def _name_failures_in_xfail(report: pytest.TestReport, checks: List[Any]) -> None:
    """Add the first line of the summary to the reason of an xfailed phase whose checks
    failed: ``-rx`` and junitxml show only the reason, and the failures must not vanish."""
    reason = getattr(report, "wasxfail", None)
    if not report.skipped or not isinstance(reason, str):
        return
    failed = [(i, check) for i, check in enumerate(checks) if check.get("passed") is not True]
    if failed:
        header = failure_header(failed, len(checks))
        report.wasxfail = f"{reason} [{header}]" if reason else f"[{header}]"


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
        encoding = _terminal_encoding(tw)
        if encoding is None:
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
            for holder, attribute, message in saved:
                setattr(holder, attribute, message)

    return printer


def _crash_lines_for_terminal(terminalreporter: Any) -> List[Any]:
    """Adapt the crash messages of soft failures, and the reasons of xfailed tests that name
    failed checks; returns ``(object, attribute, original text)`` triples."""
    encoding = _terminal_encoding(terminalreporter._tw)
    if encoding is None:
        return []
    saved = []
    for report in terminalreporter.stats.get("xfailed", ()):
        reason = getattr(report, "wasxfail", None)
        if getattr(report, "verify_checks", None) and isinstance(reason, str):
            adapted = for_terminal(reason, encoding)
            if adapted != reason:
                saved.append((report, "wasxfail", reason))
                report.wasxfail = adapted
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
                    saved.append((crash, "message", message))
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
