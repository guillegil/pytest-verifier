"""The checks of one attempt to run one test item, and the sink that records them.

Each attempt (pytest-rerunfailures runs the same item several times) gets a fresh :class:`Run`.
The fixture's ``Verify`` sends its checks to a :class:`Recorder`, which judges each one once,
snapshots it and adds it to the run.

A composite's children belong to it, not to the top level of the run. Every check is recorded
at the top level when it is made, including the ones a child callable (a lazy
``conditional``/``guard`` child, or an ``all_satisfy`` factory) makes. When the composite is
recorded, it absorbs its children: they are taken out of the top level, by identity. So a check
a callable made but did not return stays on its own, and if building the composite is
interrupted (``pytest.skip`` in an ``all_satisfy`` factory), the checks made so far still count.

A check made through ``verify.require``, or any check when fail-fast is on (except in
teardown), stops the test when it fails: :meth:`Run.stop` raises ``ChecksFailedError`` for the
checks made so far. It does not judge them, so the end of the phase still does: a test that
catches the error still fails. The error is marked ``stops_test``, so that a composite building
a lazy child lets it through instead of taking it for an error of that child.

This module's frames hide themselves from tracebacks of such errors (``__tracebackhide__``), so
``--pdb`` opens in the test.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    NoReturn,
    Optional,
    Sequence,
    Tuple,
)

from . import _unused
from ._checks import child_checks
from ._descriptors import CheckDescriptor, loose_children, require_descriptor
from ._exceptions import ChecksFailedError, hide_stop_frames
from ._location import NOWHERE, FunctionCode, Site, locate
from ._settle import settle
from ._verify import Sink, Verify

_CLOSED = (
    "pytest-verifier: this 'verify' fixture belongs to a test that has already finished, so the "
    "check would be lost. Request the 'verify' fixture in the test that makes the check."
)

_FORKED = (
    "pytest-verifier: this check was made in a child process (for example a multiprocessing "
    "worker), so it could never reach the test's results. Make the check in the test's own "
    "process, for example on a value the worker returns."
)


_NO_TEST = FunctionCode()


class _Entry:
    """What the run knows about one recorded descriptor."""

    __slots__ = ("record", "passed", "top_level", "judged")

    def __init__(self, record: CheckDescriptor, passed: bool) -> None:
        self.record = record
        self.passed = passed
        #: Whether the record is in :attr:`Run.records`, that is, no composite absorbed it.
        self.top_level = True
        self.judged = False


class Run:
    """The checks of one attempt to run one test item. Thread-safe."""

    def __init__(
        self,
        rootdir: Optional[str] = None,
        test: Optional[FunctionCode] = None,
        *,
        fail_fast: bool = False,
        max_passed: Optional[int] = None,
    ) -> None:
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
        #: Where locations are relative to, and the code of the test function.
        self.rootdir = rootdir
        self.test = test if test is not None else FunctionCode()
        #: Whether every failed check stops the test, as ``verify.require`` does.
        self.fail_fast = fail_fast
        #: How many passed checks a summary lists.
        self.max_passed = max_passed
        #: The errors :meth:`stop` raised, each with the check that made it stop.
        self._stops: List[Tuple[ChecksFailedError, CheckDescriptor]] = []
        self._entries: Dict[int, _Entry] = {}

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

    def locate(self) -> Site:
        """Where the check being recorded now was made."""
        # The test function runs only in the call phase: in setup and teardown, searching the
        # stack for it would walk every frame for nothing.
        return locate(self.rootdir, self.test if self.phase == "call" else _NO_TEST)

    def add(
        self,
        record: CheckDescriptor,
        passed: bool,
        absorb: Iterable[Any] = (),
        site: Site = NOWHERE,
    ) -> None:
        """Record a check at the top level, first absorbing the children of a composite (see
        :meth:`absorb`)."""
        location, called_from = site
        with self.lock:
            self.ensure_open()
            self.absorb(absorb)
            record["phase"] = self.phase
            # A copy of another record brings that record's site; this one may have none.
            record.pop("location", None)
            record.pop("called_from", None)
            if location is not None:
                record["location"] = location
            if called_from is not None:
                record["called_from"] = called_from
            self._entries[id(record)] = _Entry(record, passed)
            self.records.append(record)

    def absorb(self, children: Iterable[Any]) -> None:
        """Take the recorded checks passed to a composite as children out of the top level.

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
            if not ids:
                return
            # Children are usually the most recent records: find the oldest one from the end,
            # then rebuild only the tail, in one pass. The list object is the results stash, so
            # it is changed in place.
            records, remaining, start = self.records, len(ids), len(self.records)
            while remaining and start > 0:
                start -= 1
                if id(records[start]) in ids:
                    remaining -= 1
            records[start:] = [record for record in records[start:] if id(record) not in ids]

    def take_unjudged(self) -> Tuple[int, List[CheckDescriptor]]:
        """Top-level checks not judged by an earlier phase, each with its private verdict.

        Returns the index of the first of them in :attr:`records` too. They always come last:
        records are appended, and a phase judges every record made before its end.
        """
        return self._unjudged(take=True)

    def _unjudged(self, take: bool) -> Tuple[int, List[CheckDescriptor]]:
        with self.lock:
            pending = []
            for record in self.records:
                entry = self._entries[id(record)]
                if not entry.judged:
                    entry.judged = take
                    pending.append(dict(record, passed=entry.passed))
            return len(self.records) - len(pending), pending  # type: ignore[return-value]

    def pending(self, record: CheckDescriptor) -> bool:
        """Whether *record* is a top-level check that the end of this phase will judge."""
        with self.lock:
            entry = self._entries.get(id(record))
            return (
                entry is not None
                and entry.record is record
                and entry.top_level
                and not entry.judged
            )

    def stop(self, trigger: CheckDescriptor) -> NoReturn:
        """Stop the test at the failed check *trigger*: raise ``ChecksFailedError`` for the
        checks not judged yet.

        They stay unjudged, so the end of the phase judges them as usual (see
        :meth:`raised`).
        """
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        with self.lock:
            start, pending = self._unjudged(take=False)
            error = ChecksFailedError(
                pending,
                start=start,
                max_passed=self.max_passed,
                stopped_at=self._index(trigger),
            )
            error.stops_test = True
            handled = sys.exc_info()[1]
            if self.raised(handled):
                # Raised while an earlier stop unwinds (a check in ``finally``): this error
                # lists its checks too, so chain to what it was chained to instead of
                # repeating its summary.
                error.__cause__ = cause_of(handled)  # type: ignore[arg-type]
                error.__suppress_context__ = True
            self._stops.append((error, trigger))
        raise error

    def raised(self, exc: Optional[BaseException]) -> bool:
        """Whether *exc* is an error :meth:`stop` raised."""
        return exc is not None and any(exc is error for error, _ in self._stops)

    def stopped_at(self, exc: BaseException) -> Optional[int]:
        """The index now of the check that made :meth:`stop` raise *exc*, if it is still at
        the top level (a composite made later can absorb it, and the checks after it move)."""
        with self.lock:
            for error, trigger in self._stops:
                if error is exc:
                    return self._index(trigger)
            return None

    def _index(self, record: CheckDescriptor) -> Optional[int]:
        """The index of *record* in :attr:`records`; it is usually one of the last."""
        records = self.records
        for index in range(len(records) - 1, -1, -1):
            if records[index] is record:
                return index
        return None


def cause_of(exc: BaseException) -> Optional[BaseException]:
    """What *exc* is chained to, so that an error raised in its place keeps that chain."""
    if exc.__cause__ is not None or exc.__suppress_context__:
        return exc.__cause__
    return exc.__context__


class Recorder(Sink):
    """The fixture's sink: judges, snapshots and records every check in a :class:`Run`.

    A *hard* recorder (``verify.require``) stops the test when a check fails; with fail-fast
    on, every recorder does, except in teardown: the test is over by then, and stopping would
    only cut a fixture's cleanup short.
    """

    def __init__(self, run: Run, hard: bool = False) -> None:
        self._run = run
        self._hard = hard

    def hard(self) -> Sink:
        return self if self._hard else Recorder(self._run, hard=True)

    def _enforce(self, record: CheckDescriptor, passed: bool) -> None:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        if passed:
            return
        run = self._run
        if self._hard or (run.fail_fast and run.phase != "teardown"):
            run.stop(record)

    def check(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        run = self._run
        run.ensure_open()
        record, passed = settle(descriptor, run.known)
        run.add(record, passed, site=run.locate())
        self._enforce(record, passed)
        return record

    def composite(
        self, build: Callable[[], CheckDescriptor], arguments: Sequence[Any]
    ) -> CheckDescriptor:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        run = self._run
        run.ensure_open()
        try:
            descriptor = build()
        except BaseException:
            # Invalid arguments, or a lazy child or condition that skipped or failed the test:
            # the checks passed to this call were never selected, so they must not linger as
            # standalone checks.
            loose = loose_children(*arguments)
            _unused.used(*loose)
            run.absorb(loose)
            raise
        _unused.used(*child_checks(descriptor))
        record, passed = settle(descriptor, run.known)
        run.add(record, passed, absorb=child_checks(descriptor), site=run.locate())
        self._enforce(record, passed)
        return record

    def record(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        require_descriptor(descriptor, "record() argument")
        run = self._run
        run.ensure_open()
        hit = run.known(descriptor)
        if hit is not None:
            passed, record = hit
            if self._hard and not passed:  # fail-fast already stopped when it was recorded
                if not run.pending(record):
                    # A composite absorbed it, or an earlier phase judged it: record a copy,
                    # so that the error names it and this phase fails too.
                    record = dict(record)  # type: ignore[assignment]
                    run.add(record, False, site=run.locate())
                run.stop(record)
            return record
        _unused.used(descriptor)
        record, passed = settle(descriptor, run.known)
        run.add(record, passed, absorb=child_checks(descriptor), site=run.locate())
        self._enforce(record, passed)
        return record


def recording_verify(run: Run) -> Verify:
    """A ``Verify`` whose checks are judged and recorded in *run*."""
    verify = Verify()
    verify._sink = Recorder(run)
    return verify
