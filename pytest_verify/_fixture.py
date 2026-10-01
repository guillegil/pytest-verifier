"""The ``verify`` fixture and the hooks that turn failed soft checks into a failed test.

Each attempt to run a test item (pytest-rerunfailures runs the same item several times) gets a
fresh :class:`_Run`, created when its setup starts. Checks are judged when they are recorded.
At the end of each phase the checks recorded so far that have not been judged yet are
examined, and if any failed, :class:`ChecksFailedError` is raised from that phase:

- after the call phase, for checks made in setup and in the test body;
- after teardown, for checks made while fixtures are torn down;
- after setup, only when setup itself is skipped (a skip never hides a failed check).

When the phase already failed with another exception, that exception is kept and the soft
summary is added to its report as a "Soft assertion failures" section. A unittest ``TestCase``
does not raise its failures and skips: pytest records them on the item and reports them
afterwards, so the call phase reads that record too.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Any, Callable, Generator, Iterable, Mapping, Optional, Sequence

import pytest

from ._descriptors import (
    CheckDescriptor,
    ClassInfo,
    build_all_satisfy,
    build_approx,
    build_between,
    build_conditional,
    build_contains,
    build_equal,
    build_fail,
    build_greater,
    build_greater_equal,
    build_guard,
    build_is_false,
    build_is_instance,
    build_is_none,
    build_is_not_none,
    build_is_true,
    build_length,
    build_less,
    build_less_equal,
    build_matches,
    build_not_contains,
    build_not_equal,
    child_checks,
    is_descriptor,
)
from ._exceptions import ChecksFailedError, format_summary
from ._settle import settle
from ._stash import check_results_key
from ._verify import Verify

_NOT_ACTIVE = (
    "pytest-verify: the 'verify' fixture was requested, but the pytest-verify plugin is not "
    "active in this session, so failed checks could never fail the test. Load the plugin "
    "(remove '-p no:verify', or add '-p pytest_verify._fixture' when "
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD is set) instead of importing the fixture on its own."
)

_CLOSED = (
    "pytest-verify: this 'verify' fixture belongs to a test that has already finished, so the "
    "check would be lost. Request the 'verify' fixture in the test that makes the check."
)

_FORKED = (
    "pytest-verify: this check was made in a child process (for example a multiprocessing "
    "worker), so it could never reach the test's results. Make the check in the test's own "
    "process, for example on a value the worker returns."
)


class _Entry:
    """What the run knows about one recorded descriptor."""

    __slots__ = ("record", "passed", "top_level", "judged")

    def __init__(self, record: CheckDescriptor, passed: bool) -> None:
        self.record = record
        self.passed = passed
        self.top_level = True
        self.judged = False


class _Run:
    """The checks of one attempt to run one test item. Thread-safe."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        #: Top-level records, in order. The same list object is the results stash.
        self.records: list[CheckDescriptor] = []
        self.closed = False
        self.pid = os.getpid()
        #: Soft summaries waiting to be attached to the report of a phase that also errored.
        self.sections: dict[str, str] = {}
        self._entries: dict[int, _Entry] = {}

    def known(self, descriptor: Any) -> Optional[tuple[bool, CheckDescriptor]]:
        entry = self._entries.get(id(descriptor))
        if entry is None or entry.record is not descriptor:
            return None
        return entry.passed, entry.record

    def ensure_open(self) -> None:
        if self.closed:
            raise RuntimeError(_CLOSED)
        if os.getpid() != self.pid:
            raise RuntimeError(_FORKED)

    def add(
        self,
        record: CheckDescriptor,
        passed: bool,
        absorb: Iterable[Any] = (),
    ) -> None:
        """Record a top-level check, first absorbing its children (see :meth:`absorb`)."""
        with self.lock:
            self.ensure_open()
            self.absorb(absorb)
            entry = _Entry(record, passed)
            self._entries[id(record)] = entry
            self.records.append(record)

    def absorb(self, children: Iterable[Any]) -> None:
        """Remove from the top level the recorded checks passed to a composite as children.

        A child belongs to the composite whenever it was recorded, so building ``cases`` or
        ``branches`` in a variable before the call works like building them inline. To keep a
        check on its own as well, pass a copy (``dict(check)``): only the recorded object
        itself is absorbed.
        """
        with self.lock:
            ids = set()
            for child in children:
                entry = self._entries.get(id(child))
                if entry is None or entry.record is not child or not entry.top_level:
                    continue
                entry.top_level = False
                ids.add(id(child))
            # Children are usually the most recent records, so search from the end.
            index = len(self.records)
            while ids and index > 0:
                index -= 1
                if id(self.records[index]) in ids:
                    ids.discard(id(self.records.pop(index)))

    def take_unjudged(self) -> tuple[int, list[CheckDescriptor]]:
        """Top-level checks not judged by an earlier phase, each with its private verdict.

        Returns the index of the first of them in :attr:`records` too. They always come last:
        records are appended, and a phase judges every record made before its end.
        """
        with self.lock:
            pending = []
            for record in self.records:
                entry = self._entries[id(record)]
                if not entry.judged:
                    entry.judged = True
                    pending.append(dict(record, passed=entry.passed))
            return len(self.records) - len(pending), pending  # type: ignore[return-value]


_run_key = pytest.StashKey[_Run]()


def _loose_children(*containers: Any) -> list[Any]:
    """Best-effort list of the descriptors passed to a composite whose arguments were invalid."""
    found: list[Any] = []

    def visit(value: Any, depth: int) -> None:
        if depth > 3:
            return
        if is_descriptor(value):
            found.append(value)
        elif isinstance(value, Mapping):
            for item in value.values():
                visit(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item, depth + 1)

    for container in containers:
        try:
            visit(container, 0)
        except Exception:
            pass
    return found


class _FixtureVerify(Verify):
    """Fixture-scoped verify that judges every check immediately and records it."""

    def __init__(self, run: _Run) -> None:
        self._run = run

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _record(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        """Judge, snapshot, store and return a non-composite check."""
        self._run.ensure_open()
        record, passed = settle(descriptor, self._run.known)
        self._run.add(record, passed)
        return record

    def _record_composite(
        self, build: Callable[[], CheckDescriptor], arguments: Sequence[Any]
    ) -> CheckDescriptor:
        """Build, judge and store a composite, absorbing the children made for it."""
        self._run.ensure_open()
        try:
            descriptor = build()
        except Exception:
            # Invalid arguments: the children built for this call must not linger as
            # standalone checks.
            self._run.absorb(_loose_children(*arguments))
            raise
        record, passed = settle(descriptor, self._run.known)
        self._run.add(record, passed, absorb=child_checks(descriptor))
        return record

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def equal(self, actual: Any, expected: Any, *, name: str, units: Optional[str] = None) -> CheckDescriptor:
        return self._record(build_equal(actual, expected, name=name, units=units))

    def not_equal(self, actual: Any, expected: Any, *, name: str, units: Optional[str] = None) -> CheckDescriptor:
        return self._record(build_not_equal(actual, expected, name=name, units=units))

    def approx(
        self,
        actual: Any,
        expected: Any,
        *,
        abs_tol: Optional[float] = None,
        rel_tol: Optional[float] = None,
        name: str,
        units: Optional[str] = None,
    ) -> CheckDescriptor:
        return self._record(
            build_approx(actual, expected, abs_tol=abs_tol, rel_tol=rel_tol, name=name, units=units)
        )

    def greater(self, actual: Any, threshold: float, *, name: str, units: Optional[str] = None) -> CheckDescriptor:
        return self._record(build_greater(actual, threshold, name=name, units=units))

    def greater_equal(
        self, actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        return self._record(build_greater_equal(actual, threshold, name=name, units=units))

    def less(self, actual: Any, threshold: float, *, name: str, units: Optional[str] = None) -> CheckDescriptor:
        return self._record(build_less(actual, threshold, name=name, units=units))

    def less_equal(
        self, actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        return self._record(build_less_equal(actual, threshold, name=name, units=units))

    def between(
        self,
        actual: Any,
        low: float,
        high: float,
        *,
        inclusive: bool = True,
        name: str,
        units: Optional[str] = None,
    ) -> CheckDescriptor:
        return self._record(
            build_between(actual, low, high, inclusive=inclusive, name=name, units=units)
        )

    def is_true(self, actual: Any, *, name: str) -> CheckDescriptor:
        return self._record(build_is_true(actual, name=name))

    def is_false(self, actual: Any, *, name: str) -> CheckDescriptor:
        return self._record(build_is_false(actual, name=name))

    def is_none(self, actual: Any, *, name: str) -> CheckDescriptor:
        return self._record(build_is_none(actual, name=name))

    def is_not_none(self, actual: Any, *, name: str) -> CheckDescriptor:
        return self._record(build_is_not_none(actual, name=name))

    def contains(self, haystack: Any, needle: Any, *, name: str) -> CheckDescriptor:
        return self._record(build_contains(haystack, needle, name=name))

    def not_contains(self, haystack: Any, needle: Any, *, name: str) -> CheckDescriptor:
        return self._record(build_not_contains(haystack, needle, name=name))

    def matches(self, actual: Any, pattern: str, *, name: str) -> CheckDescriptor:
        return self._record(build_matches(actual, pattern, name=name))

    def is_instance(self, actual: Any, expected_type: ClassInfo, *, name: str) -> CheckDescriptor:
        return self._record(build_is_instance(actual, expected_type, name=name))

    def length(self, actual: Any, expected: int, *, name: str) -> CheckDescriptor:
        return self._record(build_length(actual, expected, name=name))

    def all_satisfy(
        self,
        items: Iterable[Any],
        descriptor_factory: Callable[[Any], CheckDescriptor],
        *,
        name: str,
    ) -> CheckDescriptor:
        return self._record_composite(
            lambda: build_all_satisfy(items, descriptor_factory, name=name), ()
        )

    def conditional(
        self,
        switch_value: Any,
        *,
        cases: Mapping[Any, CheckDescriptor],
        default: Optional[CheckDescriptor] = None,
        name: str,
    ) -> CheckDescriptor:
        return self._record_composite(
            lambda: build_conditional(switch_value, cases=cases, default=default, name=name),
            (cases, default),
        )

    def guard(
        self,
        branches: Sequence[tuple[object, str, CheckDescriptor]],
        *,
        default: Optional[CheckDescriptor] = None,
        name: str,
    ) -> CheckDescriptor:
        return self._record_composite(
            lambda: build_guard(branches, default=default, name=name), (branches, default)
        )

    def fail(self, msg: str, *, name: Optional[str] = None) -> CheckDescriptor:
        return self._record(build_fail(msg, name=name))


# ======================================================================
# Plugin hooks
# ======================================================================


def _close_phase(run: _Run, when: str, exc: Optional[BaseException]) -> Optional[ChecksFailedError]:
    """Judge the checks recorded up to the end of phase *when*.

    Returns the error to raise, or ``None``. If the phase already raised *exc*, the soft
    summary is kept for that phase's report instead, except after a skip: a skip must not
    hide a failed check, so the failure is raised in its place.
    """
    start, pending = run.take_unjudged()
    if all(record.get("passed") is True for record in pending):
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


def _replace_unittest_outcome(item: pytest.Item, error: ChecksFailedError, skip: BaseException) -> None:
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
    try:
        result = yield
    except BaseException as exc:
        error = _close_phase(run, when, exc)
        if when == "teardown":
            run.closed = True
        if error is not None:
            raise error from exc
        raise
    if when == "teardown":
        run.closed = True
    if when != "setup":  # checks made in setup are judged with the test body
        recorded = _unittest_outcome(item) if when == "call" else None
        error = _close_phase(run, when, recorded)
        if error is not None:
            if recorded is not None:
                _replace_unittest_outcome(item, error, recorded)
            raise error
    return result


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, Any, Any]:
    """Start a fresh run for every attempt (pytest-rerunfailures reuses the item)."""
    run = _Run()
    item.stash[_run_key] = run
    item.stash[check_results_key] = run.records
    return (yield from _run_phase(item, "setup"))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, Any]:
    """Raise ``ChecksFailedError`` after the test body if a check made so far failed."""
    return (yield from _run_phase(item, "call"))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: Optional[pytest.Item]) -> Generator[None, Any, Any]:
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


def _decorate_report(item: pytest.Item, call: pytest.CallInfo[None], report: pytest.TestReport) -> None:
    run = item.stash.get(_run_key, None)
    if run is None:
        return
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
    return _FixtureVerify(run)
