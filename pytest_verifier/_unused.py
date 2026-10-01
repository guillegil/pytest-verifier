"""Warn about checks built with ``checks.*`` in a test body and never used.

``checks.equal(...)`` only describes a check; nothing judges it unless it is recorded with the
fixture, evaluated, or used in a composite. A test that imports the builder instead of requesting
the ``verify`` fixture therefore passes whatever it checks. While a test body runs, the plugin
keeps a :class:`Tracker`: the builder reports each check it builds, the places that use a check
report it as used, and the checks left over when the body ends produce one
:class:`UnusedCheckWarning`.

Only the test body is tracked: a fixture may build checks that a later test records.
"""
from __future__ import annotations

import os
import sys
import threading
import warnings
from types import FrameType
from typing import Any, Dict, List, Optional, Tuple

import pytest

from ._checks import child_checks
from ._render import safe_repr, safe_str

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__)) + os.sep

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


class Tracker:
    """The checks built with ``checks.*`` during one test body and not used yet. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        #: ``id(check) -> (check, filename, lineno)``; the check is kept so its id stays unique.
        self._built: Dict[int, Tuple[Any, str, int]] = {}

    def built(self, check: Any) -> None:
        filename, lineno = _call_site()
        with self._lock:
            self._built[id(check)] = (check, filename, lineno)

    def used(self, check: Any) -> None:
        with self._lock:
            entry = self._built.get(id(check))
            if entry is not None and entry[0] is check:
                del self._built[id(check)]

    def unused(self) -> List[Tuple[Any, str, int]]:
        """The checks never used, in the order they were built."""
        with self._lock:
            return list(self._built.values())


_active: List[Tracker] = []
_active_lock = threading.Lock()


def start() -> Tracker:
    """Start tracking a test body. Trackers nest (``pytester`` runs a session inside a test)."""
    tracker = Tracker()
    with _active_lock:
        _active.append(tracker)
    return tracker


def stop(tracker: Tracker) -> None:
    with _active_lock:
        if tracker in _active:
            _active.remove(tracker)


def _current() -> Optional[Tracker]:
    try:
        return _active[-1]
    except IndexError:
        return None


def built(check: Any) -> None:
    """Report a check the module-level builder made."""
    tracker = _current()
    if tracker is not None:
        tracker.built(check)


def used(*checks: Any) -> None:
    """Report checks as used, with every check nested in them."""
    tracker = _current()
    if tracker is None:
        return
    pending = list(checks)
    seen = 0
    while pending and seen < 100_000:  # a bound against self-referencing hand-built dicts
        check = pending.pop()
        seen += 1
        tracker.used(check)
        try:
            pending.extend(child_checks(check))
        except Exception:  # not a descriptor, or a malformed hand-built composite
            pass


def _call_site() -> Tuple[str, int]:
    """File and line of the first caller outside this package."""
    frame: Optional[FrameType] = sys._getframe(2)
    while frame is not None and frame.f_code.co_filename.startswith(_PACKAGE_DIR):
        frame = frame.f_back
    if frame is None:
        return "<unknown>", 0
    return frame.f_code.co_filename, frame.f_lineno


def warn_unused(tracker: Tracker) -> None:
    """Emit one :class:`UnusedCheckWarning` for the checks *tracker* saw built but never used."""
    unused = tracker.unused()
    if not unused:
        return
    names = ", ".join(_label(check) for check, _, _ in unused[:_NAMED])
    if len(unused) > _NAMED:
        names += f" and {len(unused) - _NAMED} more"
    count = "1 check was" if len(unused) == 1 else f"{len(unused)} checks were"
    message = (
        f"{count} built with pytest_verifier.checks in this test but never recorded or "
        f"evaluated, so they cannot fail it: {names}. Use the 'verify' fixture to make checks "
        f"that count, or pass a built check to verify.record()."
    )
    _, filename, lineno = unused[0]
    warnings.warn_explicit(UnusedCheckWarning(message), UnusedCheckWarning, filename, lineno)


def _label(check: Any) -> str:
    try:
        return f"'{safe_str(check.get('name', ''))}' ({safe_str(check.get('check_type', ''))})"
    except Exception:
        return safe_repr(check)
