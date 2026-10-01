"""The checks of one attempt to run one test item, and the sink that records them.

Each attempt (pytest-rerunfailures runs the same item several times) gets a fresh :class:`Run`.
The fixture's ``Verify`` sends its checks to a :class:`Recorder`, which judges each one once,
snapshots it and adds it to the run.

A composite's children belong to it, not to the top level of the run. Every check is placed
in a list when it is recorded: the run's top level, or the collector of the composite whose
child callable (a lazy ``conditional``/``guard`` child, or an ``all_satisfy`` factory) this
thread is running. When the composite is recorded, its children are taken out of whichever list
holds them, by identity. Checks a child callable recorded but did not return are put back where
the composite itself goes.
"""
from __future__ import annotations

import contextlib
import os
import threading
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from ._checks import RunChild, child_checks
from ._descriptors import CheckDescriptor, is_descriptor, require_descriptor
from ._settle import settle
from ._verify import Sink, Verify

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

    __slots__ = ("record", "passed", "home", "judged")

    def __init__(self, record: CheckDescriptor, passed: bool) -> None:
        self.record = record
        self.passed = passed
        #: The list that holds the record (the top level or a collector); ``None`` once a
        #: composite has absorbed it.
        self.home: Optional[List[CheckDescriptor]] = None
        self.judged = False


class Run:
    """The checks of one attempt to run one test item. Thread-safe."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        #: Top-level records, in order. The same list object is the results stash.
        self.records: List[CheckDescriptor] = []
        self.closed = False
        self.pid = os.getpid()
        #: The test phase running now; recorded on every check.
        self.phase = "setup"
        #: Soft summaries waiting to be attached to the report of a phase that also errored.
        self.sections: Dict[str, str] = {}
        #: Checks judged at the end of each phase, waiting to be attached to its report.
        self.judged: Dict[str, List[CheckDescriptor]] = {}
        self._entries: Dict[int, _Entry] = {}
        self._local = threading.local()

    def known(self, descriptor: Any) -> Optional[Tuple[bool, CheckDescriptor]]:
        entry = self._entries.get(id(descriptor))
        if entry is None or entry.record is not descriptor:
            return None
        return entry.passed, entry.record

    def ensure_open(self) -> None:
        if self.closed:
            raise RuntimeError(_CLOSED)
        if os.getpid() != self.pid:
            raise RuntimeError(_FORKED)

    # ------------------------------------------------------------------
    # Where a new record goes
    # ------------------------------------------------------------------

    def _stack(self) -> List[List[CheckDescriptor]]:
        stack: Optional[List[List[CheckDescriptor]]] = getattr(self._local, "stack", None)
        if stack is None:
            stack = self._local.stack = []
        return stack

    @contextlib.contextmanager
    def collecting(self, collected: List[CheckDescriptor]) -> Iterator[None]:
        """Send the checks this thread records into *collected* instead of the top level."""
        stack = self._stack()
        stack.append(collected)
        try:
            yield
        finally:
            stack.pop()

    def _place(self, record: CheckDescriptor) -> None:
        stack = self._stack()
        home = stack[-1] if stack else self.records
        home.append(record)
        self._entries[id(record)].home = home

    def add(self, record: CheckDescriptor, passed: bool, absorb: Iterable[Any] = ()) -> None:
        """Record a check, first absorbing the eager children of a composite (see
        :meth:`absorb`). It goes to the collector of the composite being built in this thread,
        if any, else to the top level."""
        with self.lock:
            self.ensure_open()
            self.absorb(absorb)
            record["phase"] = self.phase
            self._entries[id(record)] = _Entry(record, passed)
            self._place(record)

    def release(self, collected: List[CheckDescriptor], keep: Any) -> None:
        """Put the checks left in a finished collector, except *keep* (the child its callable
        returned), where the composite itself goes."""
        with self.lock:
            for record in list(collected):
                if record is not keep:
                    self._place(record)
            collected.clear()

    def absorb(self, children: Iterable[Any]) -> None:
        """Take the recorded checks passed to a composite as children out of the list that
        holds them.

        A child belongs to the composite whenever it was recorded, so building ``cases`` or
        ``branches`` in a variable before the call works like building them inline. To keep a
        check on its own as well, pass a copy (``dict(check)``): only the recorded object
        itself is absorbed.
        """
        with self.lock:
            for child in children:
                entry = self._entries.get(id(child))
                if entry is None or entry.record is not child or entry.home is None:
                    continue
                home, entry.home = entry.home, None
                # Children are usually the most recent records, so search from the end.
                for index in range(len(home) - 1, -1, -1):
                    if home[index] is child:
                        del home[index]
                        break

    def take_unjudged(self) -> Tuple[int, List[CheckDescriptor]]:
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


def _loose_children(*containers: Any) -> List[Any]:
    """Best-effort list of the descriptors passed to a composite whose arguments were invalid."""
    found: List[Any] = []

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


class Recorder(Sink):
    """The fixture's sink: judges, snapshots and records every check in a :class:`Run`."""

    def __init__(self, run: Run) -> None:
        self._run = run

    def check(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        self._run.ensure_open()
        record, passed = settle(descriptor, self._run.known)
        self._run.add(record, passed)
        return record

    def composite(
        self, build: Callable[[RunChild], CheckDescriptor], arguments: Sequence[Any]
    ) -> CheckDescriptor:
        run = self._run
        run.ensure_open()

        def run_child(thunk: Callable[[], Any]) -> Any:
            child: Any = None
            collected: List[CheckDescriptor] = []
            try:
                with run.collecting(collected):
                    child = thunk()
            finally:
                run.release(collected, keep=child)
            return child

        try:
            descriptor = build(run_child)
        except Exception:
            # Invalid arguments: the eager children built for this call must not linger as
            # standalone checks.
            run.absorb(_loose_children(*arguments))
            raise
        record, passed = settle(descriptor, run.known)
        run.add(record, passed, absorb=child_checks(descriptor))
        return record

    def record(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        require_descriptor(descriptor, "record() argument")
        run = self._run
        run.ensure_open()
        hit = run.known(descriptor)
        if hit is not None:
            return hit[1]
        record, passed = settle(descriptor, run.known)
        run.add(record, passed, absorb=child_checks(descriptor))
        return record


def recording_verify(run: Run) -> Verify:
    """A ``Verify`` whose checks are judged and recorded in *run*."""
    verify = Verify()
    verify._sink = Recorder(run)
    return verify
