"""Warn about checks built with ``checks.*`` in a test body and never used.

``checks.equal(...)`` only describes a check; nothing judges it unless it is recorded with the
fixture, evaluated, or used in a composite. A test that imports the builder instead of requesting
the ``verify`` fixture therefore passes whatever it checks. While a test body runs, the plugin
keeps a :class:`Tracker`: the builder reports each check it builds, and the places that use a
check report it as used. The body's checks can still be used until the test's teardown ends
(a fixture may record them), and the ones left over then produce one
:class:`UnusedCheckWarning`.

Only the test body is tracked. Tracking pauses while fixtures are set up, during setup and
teardown, and for a whole session, so that checks built at import time or by fixtures, which a
later test may record, never count. A session that ``pytester`` runs inside a test is paused the
same way, and its own tests get their own trackers.
"""
from __future__ import annotations

import os
import sys
import threading
import warnings
from types import FrameType
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Union

import pytest

from ._checks import CompositeType, lookup
from ._descriptors import is_descriptor, loose_children
from ._location import ours
from ._render import render_text

#: How many unused checks the warning names.
_NAMED = 3


class UnusedCheckWarning(pytest.PytestWarning):
    """A check built with ``pytest_verifier.checks`` in a test was never recorded or evaluated.

    Such a check cannot fail the test. Use the ``verify`` fixture to make checks that count, or
    pass a built check to ``verify.record()``. Where building checks without using them is
    intended, filter the warning, for example with
    ``filterwarnings = ["ignore::pytest_verifier.UnusedCheckWarning"]``.
    """


UnusedCheckWarning.__module__ = "pytest_verifier"


class Site(NamedTuple):
    """Where a check was built: the first caller outside this package."""

    filename: str
    lineno: int
    module: Optional[str]


class Built(NamedTuple):
    """What a warning says about a check that was built. The check itself is not kept, so the
    values it holds can be freed as soon as the test drops it."""

    name: Any
    check_type: Any
    site: Site

    @property
    def label(self) -> str:
        return f"'{render_text(self.name)}' ({render_text(self.check_type)})"


class Tracker:
    """The checks built with ``checks.*`` during one test body and not used yet. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        #: ``id(check) -> what was built``, in the order the checks were built.
        self._built: Dict[int, Built] = {}
        #: Checks that were dropped unused: their id was given to a newer object.
        self._lost: List[Built] = []
        #: Set when the test body failed or skipped: then nothing is reported.
        self.quiet = False

    def built(self, check: Any, site: Site) -> None:
        entry = Built(check.get("name"), check.get("check_type"), site)
        with self._lock:
            earlier = self._built.pop(id(check), None)
            if earlier is not None:  # two live objects never share an id
                self._lost.append(earlier)
            self._built[id(check)] = entry

    def used(self, ids: Iterable[int]) -> None:
        with self._lock:
            for key in ids:
                self._built.pop(key, None)

    def unused(self) -> List[Built]:
        """The checks never used: the dropped ones, then the others in the order they were
        built."""
        with self._lock:
            return [*self._lost, *self._built.values()]


class _Pause:
    """A stack entry under which nothing is tracked."""


#: The newest entry decides where a built check goes: a tracker, or nowhere under a pause.
_stack: List[Union[Tracker, _Pause]] = []
#: Every tracker whose checks can still be used, whether or not it is on the stack.
_listening: List[Tracker] = []
_lock = threading.Lock()


def start() -> Tracker:
    """Start tracking a test body."""
    tracker = Tracker()
    with _lock:
        _stack.append(tracker)
        _listening.append(tracker)
    return tracker


def stop_building(tracker: Tracker) -> None:
    """The test body ended: later checks are not its own, but its checks can still be used."""
    _remove(tracker)


def finish(tracker: Tracker) -> List[Built]:
    """Stop *tracker* and return the checks it saw built and never used."""
    _remove(tracker)
    with _lock:
        if tracker in _listening:
            _listening.remove(tracker)
    return tracker.unused()


def pause() -> _Pause:
    """Track nothing until :func:`resume` is called with the returned token."""
    token = _Pause()
    with _lock:
        _stack.append(token)
    return token


def resume(token: _Pause) -> None:
    _remove(token)


def _remove(entry: Union[Tracker, _Pause]) -> None:
    with _lock:
        for index in range(len(_stack) - 1, -1, -1):
            if _stack[index] is entry:
                del _stack[index]
                return


def _current() -> Optional[Tracker]:
    try:
        top = _stack[-1]
    except IndexError:
        return None
    return top if isinstance(top, Tracker) else None


def built(check: Any) -> None:
    """Report a check that ``pytest_verifier.checks`` built."""
    tracker = _current()
    if tracker is not None:
        tracker.built(check, _call_site())


def used(*checks: Any) -> None:
    """Report checks as used, with every check nested in them."""
    if not _listening:
        return
    ids = _nested_ids(checks)
    with _lock:
        trackers = list(_listening)
    for tracker in trackers:
        tracker.used(ids)


def _nested_ids(checks: Iterable[Any]) -> List[int]:
    """The ids of *checks* and of every check nested in them.

    Only lists, tuples and mappings are looked into, so a hand-built composite whose children
    are an iterator still has them when it is judged.
    """
    pending = list(checks)
    seen: Dict[int, None] = {}
    while pending:
        check = pending.pop()
        if id(check) in seen:
            continue
        if not ("check_type" in check if type(check) is dict else is_descriptor(check)):
            continue
        seen[id(check)] = None
        try:
            kind = lookup(check)
            if isinstance(kind, CompositeType):
                pending.extend(loose_children(*(check.get(field) for field in kind.child_fields)))
        except Exception:  # a malformed hand-built composite
            pass
    return list(seen)


def _call_site() -> Site:
    """The first caller outside this package."""
    frame: Optional[FrameType] = sys._getframe(2)
    while frame is not None and ours(frame):
        frame = frame.f_back
    if frame is None:
        return Site("<unknown>", 0, None)
    module = frame.f_globals.get("__name__")
    module_name = module if isinstance(module, str) else None
    return Site(frame.f_code.co_filename, frame.f_lineno, module_name)


def warn_unused(unused: List[Built]) -> None:
    """Emit one :class:`UnusedCheckWarning` for *unused*, at the place the first one was built."""
    if not unused:
        return
    listed = ", ".join(f"{entry.label} at {_where(entry.site)}" for entry in unused[:_NAMED])
    if len(unused) > _NAMED:
        listed += f" and {len(unused) - _NAMED} more"
    if len(unused) == 1:
        count, consequence = "1 check was", "so it cannot fail the test"
    else:
        count, consequence = f"{len(unused)} checks were", "so they cannot fail the test"
    message = (
        f"{count} built with pytest_verifier.checks in this test but never recorded or "
        f"evaluated, {consequence}: {listed}. Use the 'verify' fixture to make checks that "
        f"count, or pass a built check to verify.record()."
    )
    site = unused[0].site
    warnings.warn_explicit(
        UnusedCheckWarning(message),
        UnusedCheckWarning,
        site.filename,
        site.lineno,
        module=site.module,
    )


def _where(site: Site) -> str:
    """``file:line``, relative to the working directory when the file is under it."""
    filename = site.filename
    try:
        relative = os.path.relpath(filename)
        if not relative.startswith(os.pardir):
            filename = relative
    except ValueError:  # another drive on Windows
        pass
    return f"{filename}:{site.lineno}"
