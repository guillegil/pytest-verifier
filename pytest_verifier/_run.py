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

A check made through ``verify.require``, or any check when fail-fast is on, stops the test when
it fails: :meth:`Run.stop` raises ``ChecksFailedError`` for the checks made so far. It does not
judge them, so the end of the phase still does: a test that catches the error still fails.
"""
from __future__ import annotations

import os
import threading
from types import CodeType
from typing import (
    AbstractSet,
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
from ._exceptions import ChecksFailedError
from ._location import NOWHERE, Site, locate
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
        test_codes: AbstractSet[CodeType] = frozenset(),
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
        self.test_codes = test_codes
        #: Whether every failed check stops the test, as ``verify.require`` does.
        self.fail_fast = fail_fast
        #: How many passed checks a summary lists.
        self.max_passed = max_passed
        #: The errors :meth:`stop` raised.
        self.stops: List[ChecksFailedError] = []
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
        return locate(self.rootdir, self.test_codes)

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
            record.pop("location", None)  # a copy of another record brings its own
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

    def stop(self) -> NoReturn:
        """Stop the test: raise ``ChecksFailedError`` for the checks not judged yet.

        They stay unjudged, so the end of the phase judges them as usual (see
        :meth:`raised`).
        """
        with self.lock:
            start, pending = self._unjudged(take=False)
            error = ChecksFailedError(pending, start=start, max_passed=self.max_passed)
            self.stops.append(error)
        raise error

    def raised(self, exc: Optional[BaseException]) -> bool:
        """Whether *exc* is an error :meth:`stop` raised."""
        return exc is not None and any(exc is error for error in self.stops)


class Recorder(Sink):
    """The fixture's sink: judges, snapshots and records every check in a :class:`Run`.

    A *hard* recorder (``verify.require``) stops the test when a check fails; with fail-fast
    on, every recorder does.
    """

    def __init__(self, run: Run, hard: bool = False) -> None:
        self._run = run
        self._hard = hard

    def hard(self) -> Sink:
        return self if self._hard else Recorder(self._run, hard=True)

    def _enforce(self, passed: bool) -> None:
        if not passed and (self._hard or self._run.fail_fast):
            self._run.stop()

    def check(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        run = self._run
        run.ensure_open()
        record, passed = settle(descriptor, run.known)
        run.add(record, passed, site=run.locate())
        self._enforce(passed)
        return record

    def composite(
        self, build: Callable[[], CheckDescriptor], arguments: Sequence[Any]
    ) -> CheckDescriptor:
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
        self._enforce(passed)
        return record

    def record(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        require_descriptor(descriptor, "record() argument")
        run = self._run
        run.ensure_open()
        hit = run.known(descriptor)
        if hit is not None:
            passed, record = hit
            if self._hard and not passed:  # fail-fast already stopped when it was recorded
                run.stop()
            return record
        _unused.used(descriptor)
        record, passed = settle(descriptor, run.known)
        run.add(record, passed, absorb=child_checks(descriptor), site=run.locate())
        self._enforce(passed)
        return record


def recording_verify(run: Run) -> Verify:
    """A ``Verify`` whose checks are judged and recorded in *run*."""
    verify = Verify()
    verify._sink = Recorder(run)
    return verify
