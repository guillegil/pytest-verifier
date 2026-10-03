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
teardown and a unittest ``TestCase``'s cleanup), stops the test when it fails: :meth:`Run.stop`
raises ``ChecksFailedError`` for the checks made so far. It does not judge them, so the end of
the phase still does: a test that catches the error still fails. The error is marked
``stops_test``, so that a composite building a lazy child lets it through instead of taking it
for an error of that child, and the check that stopped the test stays at the top level.

This module's frames hide themselves from tracebacks of such errors (``__tracebackhide__``), so
``--pdb`` opens in the test.

``verify.section`` blocks are kept in a context variable, so that each asyncio task and thread
has its own: a check is in the sections of the code that records it.
"""
from __future__ import annotations

import contextvars
import functools
import os
import sys
import threading
from types import TracebackType
from typing import (
    AbstractSet,
    Any,
    Callable,
    ContextManager,
    Dict,
    Iterable,
    List,
    NoReturn,
    Optional,
    Sequence,
    Tuple,
)

from . import _unused
from ._checks import chosen_checks, child_checks
from ._checks._base import CALLER
from ._checks._sampling import Sampler, Try
from ._descriptors import CheckDescriptor, loose_children, require_descriptor
from ._exceptions import ChecksFailedError, hide_stop_frames
from ._location import NOWHERE, FunctionCode, Site, locate, raised_at
from ._render import utf8_safe
from ._settle import settle
from ._verify import Raises, Sink, Verify

_CLOSED = (
    "pytest-verifier: this 'verify' fixture belongs to a test that has already finished, so the "
    "check would be lost. Request the 'verify' fixture in the test that makes the check."
)

_FORKED = (
    "pytest-verifier: this check was made in a child process (for example a multiprocessing "
    "worker), so it could never reach the test's results. Make the check in the test's own "
    "process, for example on a value the worker returns."
)

_SECTION_CLOSED = (
    "pytest-verifier: this 'verify' fixture belongs to a test that has already finished, so a "
    "section opened with it could hold no check. Request the 'verify' fixture in the test that "
    "opens the section."
)

_SECTION_FORKED = (
    "pytest-verifier: this section was opened in a child process (for example a multiprocessing "
    "worker), where no check can reach the test's results. Make the checks in the test's own "
    "process, for example on values the worker returns."
)


_NO_TEST = FunctionCode()

#: The ``verify.section`` blocks the code running now is in: the key of their run, and an
#: ``(owner, title)`` entry per block, outermost first. Keyed by run, so sections never carry
#: over to another test. Each block leaves by removing its own entry (see :class:`_Section`).
_SECTIONS: contextvars.ContextVar[Tuple[object, Tuple[Tuple[object, str], ...]]] = (
    contextvars.ContextVar("pytest_verifier_sections", default=(None, ()))
)


class _TryScope:
    """One try of a sampling check (``eventually``, ``stable``): the checks recorded while its
    sample ran, and the stops they would have made. A try that is dropped takes its checks
    (and its ``verify.raises`` blocks) with it; the stops of the try that is kept are made once
    the sampling check is recorded."""

    __slots__ = ("key", "parent", "records", "deferred", "blocks")

    def __init__(self, key: object, parent: Optional[_TryScope]) -> None:
        self.key = key
        #: The try this one runs in, when a sampling check is made inside a sample.
        self.parent = parent
        self.records: List[CheckDescriptor] = []
        #: ``(record, hard)`` of each failed check that would have stopped the test.
        self.deferred: List[Tuple[CheckDescriptor, bool]] = []
        #: The ``verify.raises`` blocks made while its sample ran.
        self.blocks: List[Any] = []


#: The try the code running now is in, if any. Keyed by run (:attr:`_TryScope.key`), so tries
#: never reach another test; a thread starts outside it.
_TRYING: contextvars.ContextVar[Optional[_TryScope]] = contextvars.ContextVar(
    "pytest_verifier_trying", default=None
)


class _Call:
    """A call of user code that takes what the code raises as a failure: a sample of a
    sampling check, or a lazy child, guard condition or ``all_satisfy`` factory of a composite.

    If the code raises, the ``verify.raises`` blocks it made go (it never reached their
    ``with``), and the stops of the required blocks whose unexpected exception went on wait
    for the check that takes the error (see :meth:`Recorder._wait`). If it returns, the code
    caught those exceptions itself: they stop nothing, as at the top level of the test.
    """

    __slots__ = ("key", "parent", "blocks", "waiting")

    def __init__(self, key: object, parent: Optional[_Call]) -> None:
        self.key = key
        self.parent = parent
        #: The ``verify.raises`` blocks made during the call.
        self.blocks: List[Any] = []
        #: ``(record, hard)`` of each failed required block whose exception went on.
        self.waiting: List[Tuple[CheckDescriptor, bool]] = []


#: The innermost call of user code that a composite or a sampling check is making now. Keyed by
#: run (:attr:`_Call.key`); a thread starts outside it.
_CALLING: contextvars.ContextVar[Optional[_Call]] = contextvars.ContextVar(
    "pytest_verifier_calling", default=None
)


class _Block:
    """A ``verify.raises`` block not entered yet, with the phase, sections and order in which
    it was made: the check of a block never entered keeps them (see
    :meth:`Run.take_unjudged`)."""

    __slots__ = ("raises", "phase", "section", "order")

    def __init__(self, raises: Any, phase: str, section: Tuple[str, ...], order: int) -> None:
        self.raises = raises
        self.phase = phase
        self.section = section
        self.order = order


class _Entry:
    """What the run knows about one recorded descriptor."""

    __slots__ = (
        "record", "passed", "order", "top_level", "judged", "counted", "pinned", "parent", "copy"
    )

    def __init__(self, record: CheckDescriptor, passed: bool, order: int) -> None:
        self.record = record
        self.passed = passed
        #: When it was made, among the run's records and blocks (see :attr:`Run.made`).
        self.order = order
        #: Whether the record is in :attr:`Run.records`, that is, no composite absorbed it.
        self.top_level = True
        self.judged = False
        #: Whether the composite that absorbed it selected it, so that it counts there.
        self.counted = False
        #: Whether ``record()`` asked for it on its own: a composite then leaves it there.
        self.pinned = False
        #: The entry of the composite that absorbed it.
        self.parent: Optional[_Entry] = None
        #: The copy ``record()`` made of it, when it counted nowhere.
        self.copy: Optional[CheckDescriptor] = None


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
        self.report_sections: Dict[str, str] = {}
        #: Checks judged at the end of each phase, waiting to be attached to its report.
        self.judged: Dict[str, List[CheckDescriptor]] = {}
        #: Where locations are relative to, and the code of the test function.
        self.rootdir = rootdir
        self.test = test if test is not None else FunctionCode()
        #: Whether every failed check stops the test, as ``verify.require`` does.
        self.fail_fast = fail_fast
        #: Whether a unittest ``TestCase`` is cleaning up (``tearDown``, cleanups): unittest
        #: runs that in the call phase, and fail-fast leaves it soft, as in fixture teardown.
        self.cleaning = False
        #: How many passed checks a summary lists.
        self.max_passed = max_passed
        #: The errors :meth:`stop` raised, each with the check that made it stop.
        self._stops: List[Tuple[ChecksFailedError, CheckDescriptor]] = []
        self._entries: Dict[int, _Entry] = {}
        #: The ``verify.raises`` blocks made and not entered yet (see :meth:`take_unjudged`).
        self.blocks: List[_Block] = []
        #: How many records and blocks were made: the order of the next one.
        self.made = 0
        #: Identifies this run's sections in :data:`_SECTIONS` without keeping the run alive.
        self.key = object()

    def section_path(self) -> Tuple[str, ...]:
        """The titles of this run's sections the code running now is in, outermost first."""
        key, entries = _SECTIONS.get()
        return tuple(title for _, title in entries) if key is self.key else ()

    def known(self, descriptor: Any) -> Optional[Tuple[bool, CheckDescriptor]]:
        entry = self._entries.get(id(descriptor))
        if entry is None or entry.record is not descriptor:
            return None
        return entry.passed, entry.record

    def ensure_open(self, closed: str = _CLOSED, forked: str = _FORKED) -> None:
        if self.closed:
            raise RuntimeError(closed)
        if os.getpid() != self.pid:
            raise RuntimeError(forked)

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
        chosen: Iterable[Any] = (),
    ) -> None:
        """Record a check at the top level, first absorbing the children of a composite (see
        :meth:`absorb`)."""
        with self.lock:
            self.ensure_open()
            entry = _Entry(record, passed, self.made)
            self.made += 1
            self.absorb(absorb, chosen, entry)
            _stamp(record, site, self.phase, self.section_path())
            self._entries[id(record)] = entry
            self.records.append(record)
            scope = self.trying()
            while scope is not None:
                scope.records.append(record)
                scope = scope.parent

    def add_block(self, raises: Any) -> None:
        """Keep the ``verify.raises`` block made now until it is entered (see
        :meth:`take_unjudged`). One made in a try goes with that try, and one made by user code
        that a composite calls goes when that code raises."""
        with self.lock:
            self.ensure_open()
            self.blocks.append(_Block(raises, self.phase, self.section_path(), self.made))
            self.made += 1
            scope = self.trying()
            while scope is not None:
                scope.blocks.append(raises)
                scope = scope.parent
            call = self.calling()
            while call is not None:
                call.blocks.append(raises)
                call = call.parent

    def drop_blocks(self, blocks: Iterable[Any]) -> None:
        """Forget these ``verify.raises`` blocks: entered, or made by code that will never reach
        their ``with`` (a dropped try, user code that raised)."""
        with self.lock:
            ids = {id(raises) for raises in blocks}
            if ids:
                self.blocks = [block for block in self.blocks if id(block.raises) not in ids]

    def calling(self) -> Optional[_Call]:
        """The innermost call of user code that this run's composite or sampling check is
        making now, if any."""
        call = _CALLING.get()
        return call if call is not None and call.key is self.key else None

    def trying(self) -> Optional[_TryScope]:
        """The try of this run's sampling check the code running now is in, if any."""
        scope = _TRYING.get()
        return scope if scope is not None and scope.key is self.key else None

    def absorb(
        self, children: Iterable[Any], chosen: Iterable[Any] = (), parent: Optional[_Entry] = None
    ) -> None:
        """Take the recorded checks passed to a composite as children out of the top level.

        A child belongs to the composite whenever it was recorded, so building ``cases`` or
        ``branches`` in a variable before the call works like building them inline. To keep a
        check on its own as well, pass a copy (``dict(check)``): only the recorded object
        itself is absorbed.

        A check that stopped the test stays at the top level too: the end of the phase must
        judge it, and the summary names it as the check the test stopped at. So does a check
        an earlier phase judged (a teardown composite of checks made in the test body): it was
        reported, and numbered, with that phase. And so does a check ``record()`` pinned.

        *chosen* are the children the composite selected: they count there (see
        :meth:`counted`).
        """
        with self.lock:
            ids = set()
            triggers = {id(trigger) for _, trigger in self._stops}
            selected = {id(child) for child in chosen}
            for child in children:
                entry = self._entries.get(id(child))
                if (
                    entry is None
                    or entry.record is not child
                    or not entry.top_level
                    or entry.judged
                    or entry.pinned
                    or id(child) in triggers
                ):
                    continue
                entry.top_level = False
                entry.counted = id(child) in selected
                entry.parent = parent
                ids.add(id(child))
            self._remove(ids)

    def discard(self, records: Iterable[Any]) -> None:
        """Forget *records* (the checks of a dropped try): out of the top level and out of the
        run, as if never made."""
        with self.lock:
            ids = set()
            for record in records:
                entry = self._entries.get(id(record))
                if entry is None or entry.record is not record:
                    continue
                del self._entries[id(record)]
                if entry.top_level:
                    ids.add(id(record))
            self._remove(ids)

    def _remove(self, ids: AbstractSet[int]) -> None:
        """Take the records with these ids out of :attr:`records`."""
        with self.lock:
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

    def take_unjudged(
        self, exc: Optional[BaseException] = None
    ) -> Tuple[int, List[CheckDescriptor]]:
        """Top-level checks not judged by an earlier phase, each with its private verdict.

        Returns the index of the first of them in :attr:`records` too. They always come last:
        records are appended, and a phase judges every record made before its end.

        A ``verify.raises`` block that was never entered is recorded first, as a failed check,
        when the phase ended without an exception (*exc*, the one it ended with): else the code
        may never have reached its ``with`` statement (a stop, an error, a skip). The check
        keeps the phase, sections and site where the block was made, and its place among the
        records made around it.
        """
        with self.lock:
            blocks, self.blocks = self.blocks, []
            if exc is None:
                for block in blocks:
                    self._add_unentered(block)
            return self._unjudged(take=True)

    def _add_unentered(self, block: _Block) -> None:
        """Record the failed check of *block* if it was never entered. Also once the run is
        closed: the end of fixture teardown judges the checks after closing it."""
        unentered = block.raises.unentered()
        if unentered is None:
            return
        descriptor, site = unentered
        record, passed = settle(descriptor, self.known)
        _stamp(record, site, block.phase, block.section)
        self._entries[id(record)] = _Entry(record, passed, block.order)
        # Before the records made after the block, never before a judged one.
        records, index = self.records, len(self.records)
        while index > 0:
            before = self._entries[id(records[index - 1])]
            if before.judged or before.order < block.order:
                break
            index -= 1
        records.insert(index, record)

    def _unjudged(self, take: bool) -> Tuple[int, List[CheckDescriptor]]:
        with self.lock:
            pending = []
            for record in self.records:
                entry = self._entries[id(record)]
                if not entry.judged:
                    entry.judged = take
                    pending.append(dict(record, passed=entry.passed))
            return len(self.records) - len(pending), pending  # type: ignore[return-value]

    def uncounted(self, record: CheckDescriptor) -> bool:
        """Whether *record* is a recorded check that counts nowhere: a composite took it in
        without selecting it (an unselected case), or selected it but counts nowhere itself."""
        with self.lock:
            entry = self._entries.get(id(record))
            if entry is None or entry.record is not record:
                return False
            while not entry.top_level:
                parent = entry.parent
                if not entry.counted or parent is None:
                    return True
                if self._entries.get(id(parent.record)) is not parent:
                    return True  # its composite was dropped with a try
                entry = parent
            return False

    def copy_of(self, record: CheckDescriptor) -> Optional[CheckDescriptor]:
        """The copy :meth:`Recorder.record` made of *record*, if it is still recorded."""
        with self.lock:
            entry = self._entries.get(id(record))
            copy = None if entry is None or entry.record is not record else entry.copy
            return copy if copy is not None and self.known(copy) is not None else None

    def keep_copy(self, record: CheckDescriptor, copy: CheckDescriptor) -> None:
        with self.lock:
            entry = self._entries.get(id(record))
            if entry is not None and entry.record is record:
                entry.copy = copy

    def pin(self, record: CheckDescriptor) -> None:
        """Keep the top-level *record* at the top level when a composite takes it in."""
        with self.lock:
            entry = self._entries.get(id(record))
            if entry is not None and entry.record is record and entry.top_level:
                entry.pinned = True

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


class _Section:
    """A ``verify.section`` block: see :meth:`Verify.section`.

    Reusable, also nested and by several tasks at once: entering adds an entry owned by this
    object to the current context's sections, and leaving removes the innermost one, so each
    context leaves only what it entered. The entry holds a bare owner object, not the run.
    """

    __slots__ = ("_run", "_title", "_owner")

    def __init__(self, run: Run, title: str) -> None:
        self._run = run
        # Plain text: a str subclass (a str Enum member) could not be pickled or sent by xdist.
        self._title = utf8_safe(str.__str__(title))
        self._owner = object()

    def __enter__(self) -> None:
        run = self._run
        run.ensure_open(_SECTION_CLOSED, _SECTION_FORKED)
        key, entries = _SECTIONS.get()
        if key is not run.key:
            entries = ()
        _SECTIONS.set((run.key, entries + ((self._owner, self._title),)))

    def __exit__(self, *exc_info: Any) -> None:
        key, entries = _SECTIONS.get()
        if key is not self._run.key:
            return
        for index in range(len(entries) - 1, -1, -1):
            if entries[index][0] is self._owner:
                _SECTIONS.set((key, entries[:index] + entries[index + 1:]))
                return
        # Not entered in this context (an async fixture resumed in another task): nothing to
        # leave here.


def _stamp(record: CheckDescriptor, site: Site, phase: str, section: Tuple[str, ...]) -> None:
    """Set where *record* was made: its phase, site and sections."""
    location, called_from = site
    record["phase"] = phase
    # A copy of another record brings that record's site and section; this one may have none.
    record.pop("location", None)
    record.pop("called_from", None)
    record.pop("section", None)
    if location is not None:
        record["location"] = location
    if called_from is not None:
        record["called_from"] = called_from
    if section:
        record["section"] = list(section)


def cause_of(exc: BaseException) -> Optional[BaseException]:
    """What *exc* is chained to, so that an error raised in its place keeps that chain."""
    if exc.__cause__ is not None or exc.__suppress_context__:
        return exc.__cause__
    return exc.__context__


class Recorder(Sink):
    """The fixture's sink: judges, snapshots and records every check in a :class:`Run`.

    A *hard* recorder (``verify.require``) stops the test when a check fails; with fail-fast
    on, every recorder does, except in teardown (and a ``TestCase``'s cleanup): the test is
    over by then, and stopping would only cut the cleanup short. Inside a try of a sampling
    check, the stop waits until that check is recorded, and is dropped with the try.

    A ``verify.raises`` block that gets an unexpected exception lets it go on instead of
    stopping: it stops the test itself. When a try, a lazy child, a guard condition or an
    ``all_satisfy`` factory takes it as a failure, the stop waits until that try's sampling
    check, or that composite, is recorded (see :class:`_Call`); when the user's code catches it,
    nothing stops, as at the top level.
    """

    def __init__(self, run: Run, hard: bool = False) -> None:
        self._run = run
        self._hard = hard

    def hard(self) -> Sink:
        return self if self._hard else Recorder(self._run, hard=True)

    def section(self, title: str) -> ContextManager[None]:
        return _Section(self._run, title)

    def _enforce(self, record: CheckDescriptor, passed: bool, hard: Optional[bool] = None) -> None:
        """Stop the test at the failed *record* if it is required (*hard*: this recorder's
        default) or fail-fast applies."""
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        if passed:
            return
        run = self._run
        hard = self._hard if hard is None else hard
        scope = run.trying()
        if scope is not None:
            scope.deferred.append((record, hard))
            return
        if hard or (run.fail_fast and run.phase != "teardown" and not run.cleaning):
            run.stop(record)

    def block(self, raises: Any) -> Site:
        run = self._run
        run.add_block(raises)
        return run.locate()

    def entered(self, raises: Any) -> None:
        self._run.drop_blocks((raises,))

    def origin(self, traceback: Optional[TracebackType]) -> Optional[str]:
        return raised_at(traceback, self._run.rootdir)

    def check(self, descriptor: CheckDescriptor, site: Optional[Site] = None) -> CheckDescriptor:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        run = self._run
        run.ensure_open()
        record, passed = settle(descriptor, run.known)
        run.add(record, passed, site=run.locate() if site is None else site)
        self._enforce(record, passed)
        return record

    def ended(
        self,
        descriptor: CheckDescriptor,
        site: Any,
        keep: Callable[[CheckDescriptor], None],
        stop: bool,
    ) -> None:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        run = self._run
        run.ensure_open()
        record, passed = settle(descriptor, run.known)
        run.add(record, passed, site=site)
        keep(record)
        if stop:
            self._enforce(record, passed)
        elif not passed:
            self._wait(record, self._hard)

    def _wait(self, record: CheckDescriptor, hard: bool) -> None:
        """Keep the stop of the failed *record*, whose exception goes on, for the call of user
        code running now (see :class:`_Call`): if that call raises, the try or the composite
        that takes the error makes the stop once recorded, unless the record is no longer
        pending then. Outside such a call, the exception stops the test itself."""
        call = self._run.calling()
        if call is not None:
            call.waiting.append((record, hard))

    def batch(self, descriptors: List[CheckDescriptor]) -> List[CheckDescriptor]:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        run = self._run
        run.ensure_open()
        site = run.locate()
        recorded = []
        for descriptor in descriptors:
            record, passed = settle(descriptor, run.known)
            run.add(record, passed, site=site)
            recorded.append((record, passed))
        # Every row first, so that a stop lists the whole table; then the first failed row
        # stops the test if it must.
        for record, passed in recorded:
            if not passed:
                self._enforce(record, passed)
                break
        return [record for record, _ in recorded]

    def composite(
        self, build: Callable[[], CheckDescriptor], arguments: Sequence[Any]
    ) -> CheckDescriptor:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        run = self._run
        run.ensure_open()
        # The stops of the calls of user code that raised (see _Call), made once recorded.
        waiting: List[Tuple[CheckDescriptor, bool]] = []
        token = CALLER.set(functools.partial(self._call, waiting))
        try:
            try:
                descriptor = build()
            finally:
                CALLER.reset(token)
        except BaseException:
            # Invalid arguments, or a lazy child or condition that skipped or failed the test:
            # the checks passed to this call were never selected, so they must not linger as
            # standalone checks.
            loose = loose_children(*arguments)
            _unused.used(*loose)
            run.absorb(loose)
            for each, hard in waiting:  # for the try or composite that takes this error, if any
                self._wait(each, hard)
            raise
        record = self._add_composite(descriptor)
        # A required ``raises`` block whose unexpected exception a lazy child, condition or
        # factory took as a failure stops the test now.
        for each, hard in waiting:
            if run.pending(each):
                self._enforce(each, False, hard)
        return record

    def _call(
        self,
        waiting: List[Tuple[CheckDescriptor, bool]],
        function: Callable[..., Any],
        args: Tuple[Any, ...],
        check: bool,
    ) -> Any:
        """Make a call of user code for the composite being built (see :class:`_Call` and
        :func:`~pytest_verifier._checks._base.call_user`): if it raises, its blocks go and its
        stops go to *waiting*, the composite's. A block it returns in place of a check
        (*check*) is reported by the composite's error, not as never used too."""
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        run = self._run
        call = _Call(run.key, run.calling())
        token = _CALLING.set(call)
        try:
            returned = function(*args)
        except BaseException:
            run.drop_blocks(call.blocks)
            waiting.extend(call.waiting)
            raise
        finally:
            _CALLING.reset(token)
        if check and isinstance(returned, Raises):
            run.drop_blocks((returned,))
        return returned

    def _add_composite(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        run = self._run
        children = child_checks(descriptor)
        _unused.used(*children)
        record, passed = settle(descriptor, run.known)
        run.add(
            record, passed, absorb=children, chosen=chosen_checks(descriptor), site=run.locate()
        )
        self._enforce(record, passed)
        return record

    def sampling(
        self, build: Callable[[Sampler], CheckDescriptor], arguments: Sequence[Any]
    ) -> CheckDescriptor:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        run = self._run
        run.ensure_open()
        sampler = _RecordingSampler(self)
        try:
            descriptor = build(sampler)
        except BaseException:
            # Interrupted (pytest.skip in a sample, Ctrl-C) or invalid arguments: the tries were
            # only tries, so they go, and a skip stays a skip.
            for attempt in sampler.tries:
                sampler.drop(attempt)
            loose = loose_children(*arguments)
            _unused.used(*loose)
            run.absorb(loose)
            raise
        record = self._add_composite(descriptor)
        # The checks the kept try made besides the one it returned stay on their own; those
        # that would have stopped the test stop it now (or wait for an outer try).
        for attempt in sampler.tries:
            if attempt.scope is None:
                continue
            for deferred, hard in attempt.scope.deferred:
                if run.pending(deferred):
                    self._enforce(deferred, False, hard)
        return record

    def record(self, descriptor: CheckDescriptor, call: str = "record()") -> CheckDescriptor:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        require_descriptor(descriptor, f"{call} argument")
        run = self._run
        run.ensure_open()
        hit = run.known(descriptor)
        if hit is not None:
            passed, record = hit
            if run.uncounted(record):
                # It counts nowhere (a composite took it in without selecting it): recording it
                # asks for it on its own, so record a copy, as ``dict(check)`` would have kept.
                # Once: recording it again is recording that copy again.
                copy = run.copy_of(record)
                if copy is None:
                    fresh: CheckDescriptor = dict(record)  # type: ignore[assignment]
                    run.add(fresh, passed, site=run.locate())
                    run.pin(fresh)  # asked for on its own: no later composite takes it
                    run.keep_copy(record, fresh)
                    self._enforce(fresh, passed)
                    return fresh
                record = copy
            run.pin(record)
            # Fail-fast already stopped when it was recorded.
            if self._hard and not passed:
                if not run.pending(record):
                    # An earlier phase judged it, or a composite counts it: record a copy, so
                    # that the error names it and this phase fails too.
                    record = dict(record)  # type: ignore[assignment]
                    run.add(record, False, site=run.locate())
                self._enforce(record, False, True)
            return record
        _unused.used(descriptor)
        record, passed = settle(descriptor, run.known)
        run.add(
            record,
            passed,
            absorb=child_checks(descriptor),
            chosen=chosen_checks(descriptor),
            site=run.locate(),
        )
        self._enforce(record, passed)
        return record


class _RecordingSampler(Sampler):
    """The tries of the fixture's ``eventually``/``stable``: each runs in its own try scope, so
    that dropping it takes every check it recorded."""

    def __init__(self, recorder: Recorder) -> None:
        super().__init__()
        self._recorder = recorder
        self._run = recorder._run
        #: Every try taken so far; a dropped one has no scope.
        self.tries: List[Try] = []

    def call(self, sample: Any, attempt: Try) -> Any:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        run = self._run
        scope = _TryScope(run.key, run.trying())
        attempt.scope = scope
        self.tries.append(attempt)
        call = _Call(run.key, run.calling())
        token = _TRYING.set(scope)
        calling = _CALLING.set(call)
        try:
            returned = sample()
        except BaseException:
            # A failed try (or an interrupted sampling): the sample never reached the with of
            # the blocks it made, so they go, even from the try that is kept; the stops of its
            # required blocks wait with the try's (made if it is kept).
            run.drop_blocks(call.blocks)
            scope.deferred.extend(call.waiting)
            raise
        finally:
            _CALLING.reset(calling)
            _TRYING.reset(token)
        if isinstance(returned, Raises):
            # The sampling check says so (not a check): not reported as never used too.
            self._recorder.entered(returned)
        return returned

    def keep(self, check: Any, attempt: Try) -> Any:
        __tracebackhide__ = hide_stop_frames  # noqa: F841 - read by pytest
        if self._run.known(check) is not None:
            return check
        # Built with ``checks``: recorded in the try, like a check the sample recorded.
        token = _TRYING.set(attempt.scope)
        try:
            return self._recorder.record(check)
        finally:
            _TRYING.reset(token)

    def origin(self, traceback: Optional[TracebackType]) -> Optional[str]:
        return raised_at(traceback, self._run.rootdir)

    def drop(self, attempt: Try) -> None:
        scope = attempt.scope
        if scope is None:
            return
        attempt.scope = None
        self._run.discard(scope.records)
        self._run.drop_blocks(scope.blocks)


def recording_verify(run: Run) -> Verify:
    """A ``Verify`` whose checks are judged and recorded in *run*."""
    verify = Verify()
    verify._sink = Recorder(run)
    return verify
