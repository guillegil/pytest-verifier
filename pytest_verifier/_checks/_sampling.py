"""Checks that sample a value over time: ``eventually`` and ``stable``.

Both call a *sample*, a zero-argument callable that makes one check (``lambda:
verify.less(read_temp(), 40, name="T")``), again and again, and keep one try as their only
child: ``eventually`` the first that passed, or the last; ``stable`` the first that failed, or
the passing one closest to its limit. Each call is a try, taken by a :class:`Sampler`: the
sinks settle or record the check it made when it is made, and drop a try with every check it
made once it can no longer be kept (see :class:`pytest_verifier._run.Recorder`). A try passes
when its check passed and so did every other check it recorded. A bounded ``trace`` keeps when
each try started, its value and its verdict.

They wait with :func:`time.sleep`, in the thread that calls them.
"""
from __future__ import annotations

import asyncio
import datetime
import inspect
import math
import re
import sys
import time
import warnings
from fractions import Fraction
from types import TracebackType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

from .._descriptors import CheckDescriptor, is_descriptor, is_real, not_a_check, require_name
from .._location import ours
from .._render import VALUE_LIMIT, describe_error, render_text, safe_repr, shorten
from ._base import CompositeType, child_detail, judge, lookup, margin, passes_through, register

#: The clock and the sleep of the sampling checks (tests replace them).
_clock: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep

#: A sample: makes one check each time it is called.
Sample = Callable[[], CheckDescriptor]

#: A duration: seconds, or a ``timedelta``.
Seconds = Union[float, datetime.timedelta]

#: How many tries the trace keeps from the start and from the end of the sampling.
TRACE_ENDS = 50

#: The values a trace keeps (anything else is ``None``).
_SCALARS = (bool, int, float, str, type(None))

#: How a record keeps a float that is not finite (see :func:`~pytest_verifier._render.snapshot`).
_NOT_FINITE = ("nan", "inf", "-inf")


class Try:
    """One call of a sample.

    ``check`` is the check it made (``None`` when it made none: ``error`` says why), ``passed``
    its verdict: it passed when that check passed and so did every other check it made
    (``others`` names those that failed). ``usage`` marks a try that no later try can fix: the
    sample returned something that is not a check, the same check again, or a usage error of
    this package. ``scope`` belongs to the sampler that took it.
    """

    __slots__ = ("check", "passed", "error", "usage", "margin", "scope", "start", "others")

    def __init__(self, start: float) -> None:
        self.check: Optional[Any] = None
        self.passed = False
        self.others: List[str] = []
        self.error: Optional[str] = None
        self.usage = False
        self.margin: Optional[float] = None
        self.scope: Any = None
        #: When the try started, by :data:`_clock`.
        self.start = start


class Sampler:
    """Takes the tries of one sampling check.

    This one calls the sample and judges the check it returns. A sink's sampler also settles or
    records the check (:meth:`call`), finds the other checks of a try that failed
    (:meth:`failed_beside`), forgets the tries the check drops (:meth:`drop`) and says where a
    sample's error was raised (:meth:`origin`).
    """

    def __init__(self) -> None:
        #: What the last try's sample returned.
        self._last: Any = None

    def take(self, sample: Sample) -> Try:
        """Call *sample* once. Never raises, except for what must go on: an exception that is
        not an ``Exception`` (``pytest.skip``, ``KeyboardInterrupt``) or a stop error."""
        attempt = Try(_clock())
        try:
            returned = self.call(sample, attempt)
        except Exception as exc:
            if passes_through(exc):
                raise
            where = self.origin(exc.__traceback__)
            attempt.error = f"raised {describe_error(exc)}" + (f" (at {where})" if where else "")
            attempt.usage = _raised_here(exc.__traceback__) or _wrong_call(exc)
            return attempt
        if not is_descriptor(returned):
            attempt.error = f"returned {not_a_check(returned)}"
            attempt.usage = True
            if inspect.iscoroutine(returned):
                returned.close()  # an async sample: no "never awaited" warning
            return attempt
        if returned is self._last:
            attempt.error = (
                "returned the same check twice, so nothing was measured again: make the check "
                "inside the sample, such as lambda: verify.less(read(), 40, name=...)"
            )
            attempt.usage = True
            return attempt
        self._last = returned
        check = self.keep(returned, attempt)
        attempt.check = check
        attempt.others = self.failed_beside(check, attempt)
        attempt.passed = judge(check)[0] and not attempt.others
        if attempt.passed:
            attempt.margin = margin(check)
        return attempt

    def call(self, sample: Sample, attempt: Try) -> Any:
        """Call *sample* for *attempt* and return what it returned."""
        return sample()

    def keep(self, check: Any, attempt: Try) -> Any:
        """The check *attempt* made, as the sampling check keeps it."""
        return check

    def failed_beside(self, check: Any, attempt: Try) -> List[str]:
        """The names of the checks *attempt* made besides *check* that failed (the try fails
        with them). Here none: only a sink knows the other checks."""
        return []

    def drop(self, attempt: Try) -> None:
        """*attempt* will not be kept: forget the checks it made."""

    def origin(self, traceback: Optional[TracebackType]) -> Optional[str]:
        """Where an exception a sample raised was raised (``"path:line"``), if known."""
        return None


def _raised_here(traceback: Optional[TracebackType]) -> bool:
    """Whether an exception was raised by this package: a usage error (a missing tolerance, a
    ``checks.record()`` call), since no check raises because of the value it checks."""
    last = None
    while traceback is not None:
        last = traceback
        traceback = traceback.tb_next
    return last is not None and ours(last.tb_frame)


#: The message of the ``TypeError`` of a call with the wrong arguments (``name=`` left out, a
#: misspelt keyword): no later try can fix it.
_WRONG_CALL = re.compile(
    r"\(\) (?:missing \d+ required|got an unexpected keyword argument|got multiple values "
    r"for argument|takes (?:from \d+ to \d+|\d+|no|exactly \w+|at (?:most|least) \w+) "
    r"(?:positional |keyword )?argument)"
)


def _wrong_call(exc: BaseException) -> bool:
    """Whether *exc* says a function was called with the wrong arguments."""
    return isinstance(exc, TypeError) and bool(_WRONG_CALL.search(safe_repr(exc)))


def _seconds_argument(value: object, label: str, check: str, positive: bool = False) -> float:
    wanted = "more than 0" if positive else "0 or more"
    if isinstance(value, datetime.timedelta):
        value = value.total_seconds()
    if not is_real(value):
        raise TypeError(
            f"{check}() {label} must be a number of seconds or a timedelta, got "
            f"{type(value).__name__}"
        )
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (OverflowError, ValueError):
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0 or (positive and seconds == 0):
        raise ValueError(
            f"{check}() {label} must be a finite number of seconds, {wanted}; got "
            f"{render_text(safe_repr(value))}"
        )
    return abs(seconds)  # -0.0 is 0


def seconds(value: Any) -> str:
    """A duration as ``"2 s"``, ``"0.25 s"`` or ``"86400 s"``."""
    try:
        number = float(value)
        if abs(number) >= 1e4 and abs(number) < 1e15:
            return f"{number:.0f} s"
        return f"{number:.4g} s"
    except Exception:
        return f"{render_text(safe_repr(value))} s"


def _validate(
    check: str, name: object, sample: object, span: object, span_label: str, interval: object
) -> Tuple[str, float, float]:
    resolved = require_name(name, check)
    if not callable(sample) or is_descriptor(sample):
        raise TypeError(
            f"{check}() sample must be a callable with no arguments that makes the check, such "
            f"as lambda: verify.less(read(), 40, name=...); got {type(sample).__name__}"
        )
    limit = _seconds_argument(span, span_label, check)
    every = _seconds_argument(interval, "interval", check, positive=True)
    if _loop_running():
        warnings.warn(
            f"verify.{check}() waits with time.sleep, which blocks the event loop of this "
            "async test: tasks cannot run while it waits. Run the code that changes the value "
            "elsewhere, or call it from a thread: "
            f"await asyncio.to_thread(lambda: verify.{check}(...)).",
            RuntimeWarning,
            stacklevel=_caller_level(),
        )
    return resolved, limit, every


def _caller_level() -> int:
    """The ``stacklevel`` of a warning that points at the first frame outside this package,
    seen from the function that calls this one."""
    level, frame = 1, sys._getframe(1)
    while frame is not None and ours(frame):
        frame = frame.f_back  # type: ignore[assignment]
        level += 1
    return level


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _wait(last_start: float, interval: float, end: float) -> None:
    """Sleep until the next try is due: *interval* after the start of the last one (at once
    when the last one took longer), and never past *end*."""
    pause = min(last_start + interval, end) - _clock()
    if pause > 0:
        _sleep(pause)


def _closer(new: Try, old: Try) -> bool:
    """Whether the passing try *new* is closer to its limit than *old*, the one kept so far.

    Margins compare only between checks of one type and name; without margins, the later try
    wins (the last passing one is kept).
    """
    if old.margin is None:
        return True
    if new.margin is None or not _alike(new.check, old.check):
        return False
    return new.margin < old.margin


def _alike(a: Any, b: Any) -> bool:
    try:
        return bool(a.get("check_type") == b.get("check_type") and a.get("name") == b.get("name"))
    except Exception:
        return False


class _Trace:
    """``[seconds since the start, value, passed]`` of the first and the last
    :data:`TRACE_ENDS` tries. The value is the check's ``actual`` when it is a plain scalar:
    as recorded (a snapshot: ``"nan"`` for a NaN), text cut to :data:`VALUE_LIMIT` characters."""

    def __init__(self, start: float) -> None:
        self.start = start
        self.head: List[List[Any]] = []
        self.tail: List[List[Any]] = []
        #: The first try's value, and whether a later try's value differed (over every try,
        #: not only the traced ones; whole values, not the cut text).
        self.first: Any = None
        self.changed = False

    def add(self, attempt: Try) -> None:
        value = None
        if attempt.check is not None:
            try:
                actual = attempt.check.get("actual")
            except Exception:
                actual = None
            if isinstance(actual, _SCALARS):
                value = actual
        key = (type(value), value)
        if not self.head:
            self.first = key
        elif key != self.first:
            self.changed = True
        if isinstance(value, str):
            value = shorten(value, VALUE_LIMIT)
        entry = [round(attempt.start - self.start, 3), value, attempt.passed]
        if len(self.head) < TRACE_ENDS:
            self.head.append(entry)
            return
        self.tail.append(entry)
        if len(self.tail) > TRACE_ENDS:
            del self.tail[0]

    def entries(self) -> List[List[Any]]:
        return self.head + self.tail


class _Sampling(CompositeType):
    child_fields = ("child_checks",)

    def children(self, d: Mapping[str, Any]) -> List[Any]:
        return list(d.get("child_checks") or [])

    def chosen(self, d: Mapping[str, Any]) -> List[Any]:
        return self.children(d)

    def map_children(self, d: Mapping[str, Any], fn: Callable[[Any], Any]) -> Dict[str, Any]:
        return {"child_checks": [fn(child) for child in d.get("child_checks") or []]}

    def vetoed(self, d: Mapping[str, Any]) -> bool:
        """The kept try made another check that failed: the try failed, and so does this."""
        others = d.get("also_failed")
        return isinstance(others, list) and len(others) > 0

    def margin(self, d: Mapping[str, Any]) -> Optional[Union[float, Fraction]]:
        """The margin of the kept try."""
        children = self.children(d)
        if not children or not is_descriptor(children[-1]):
            return None
        check = lookup(children[-1])
        return None if check is None else check.margin(children[-1])

    def units(self, d: Mapping[str, Any]) -> Any:
        """The units of the kept try."""
        children = self.children(d)
        if not children or not is_descriptor(children[-1]):
            return None
        check = lookup(children[-1])
        return None if check is None else check.units(children[-1])

    @staticmethod
    def _describe(
        d: Dict[str, Any], kept: Optional[Try], trace: _Trace, start: float
    ) -> CheckDescriptor:
        """*d* with what both sampling checks add: the trace, the kept try, the other checks
        that failed it, and why it made no check."""
        d["elapsed"] = round(_clock() - start, 3)
        d["trace"] = trace.entries()
        d["value_changed"] = trace.changed
        d["child_checks"] = [] if kept is None or kept.check is None else [kept.check]
        if kept is not None and kept.others:
            d["also_failed"] = list(kept.others)
        if kept is not None and kept.error is not None:
            if kept.usage:
                d["error"] = f"sample {kept.error}"
            else:
                d["sample_error"] = kept.error
        return d  # type: ignore[return-value]

    @staticmethod
    def _tries(d: Mapping[str, Any]) -> str:
        tries = d.get("tries")
        if isinstance(tries, int) and not isinstance(tries, bool):
            return f"{tries} {'try' if tries == 1 else 'tries'}"
        return f"{render_text(safe_repr(tries))} tries"

    @staticmethod
    def _kept(d: Mapping[str, Any]) -> str:
        """The kept try: its check's name and detail, or why it made none."""
        children = d.get("child_checks") or []
        if children and is_descriptor(children[-1]):
            child = children[-1]
            label = render_text(child.get("name", ""))
            return f"{label} — {child_detail(child, judge(child)[0])}"
        failure = d.get("sample_error")
        return f"the sample {render_text(failure)}" if failure else "no check"

    @staticmethod
    def _also(d: Mapping[str, Any]) -> str:
        """The other checks of the kept try that failed it, if any."""
        others = d.get("also_failed")
        if not isinstance(others, list) or not others:
            return ""
        more = f" (+{len(others) - 1} more)" if len(others) > 1 else ""
        return f"; also failed in that try: {render_text(others[0])}{more}"


class Eventually(_Sampling):
    check_type = "eventually"

    @staticmethod
    def build(
        sample: Sample,
        *,
        timeout: Seconds,
        interval: Seconds = 0.1,
        name: str,
        sampler: Optional[Sampler] = None,
    ) -> CheckDescriptor:
        """Try *sample* until a check it makes passes, or *timeout* seconds have passed (a try
        that starts before the timeout counts; the last starts at the timeout)."""
        name, limit, every = _validate("eventually", name, sample, timeout, "timeout", interval)
        sampler = sampler or Sampler()
        start = _clock()
        end = start + limit
        trace = _Trace(start)
        count = 0
        kept: Optional[Try] = None
        while True:
            attempt = sampler.take(sample)
            count += 1
            trace.add(attempt)
            if kept is not None:
                sampler.drop(kept)
            kept = attempt
            if attempt.passed or attempt.usage or _clock() >= end:
                break
            _wait(attempt.start, every, end)
        d: Dict[str, Any] = {
            "check_type": "eventually",
            "name": name,
            "description": (
                f"Verify '{render_text(name)}' passes within {seconds(limit)} "
                f"(every {seconds(every)})"
            ),
            "timeout": limit,
            "interval": every,
            "tries": count,
            "settled_at": round(kept.start - start, 3) if kept.passed else None,
        }
        return Eventually._describe(d, kept, trace, start)

    def combine(self, verdicts: List[bool]) -> bool:
        return any(verdicts)

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        if passed:
            settled = d.get("settled_at")
            at = seconds(settled if settled is not None else d.get("elapsed"))
            return f"passed at try {d.get('tries')} ({at}): {self._kept(d)}"
        if d.get("error") is not None:
            return f"stopped at try {d.get('tries')} ({seconds(d.get('elapsed'))})"
        return (
            f"never passed in {seconds(d.get('timeout'))} ({self._tries(d)}), "
            f"last: {self._kept(d)}{self._also(d)}{_unchanged(d)}"
        )


def _unchanged(d: Mapping[str, Any]) -> str:
    """A note for a failed check whose value was the same in every try (at least 3): it was
    probably read once, outside the sample."""
    trace, tries = d.get("trace"), d.get("tries")
    if d.get("value_changed") is not False or not isinstance(tries, int) or tries < 3:
        return ""
    try:
        first = trace[0][1]  # type: ignore[index]
        if first is None or first in _NOT_FINITE:
            # No value to compare (the tries made no plain check, or raised), or a reading
            # that is not a number (a disconnected input reads NaN however it is read).
            return ""
        children = d.get("child_checks") or []
        if children and judge(children[-1])[0]:
            return ""  # the value passed: other checks failed the tries
    except Exception:
        return ""
    return f"; the value never changed in {tries} tries: is it read inside the sample?"


class Stable(_Sampling):
    check_type = "stable"

    @staticmethod
    def build(
        sample: Sample,
        *,
        duration: Seconds,
        interval: Seconds = 0.1,
        name: str,
        sampler: Optional[Sampler] = None,
    ) -> CheckDescriptor:
        """Try *sample* until a try starts at or after *duration* seconds, and at least twice
        when *duration* is more than 0; every check it makes must pass. Stops at the first
        try that fails."""
        name, limit, every = _validate("stable", name, sample, duration, "duration", interval)
        sampler = sampler or Sampler()
        start = _clock()
        end = start + limit
        trace = _Trace(start)
        count = 0
        kept: Optional[Try] = None
        while True:
            attempt = sampler.take(sample)
            count += 1
            trace.add(attempt)
            if not attempt.passed:
                if kept is not None:
                    sampler.drop(kept)
                kept = attempt
                break
            if kept is None:
                kept = attempt
            elif _closer(attempt, kept):
                sampler.drop(kept)
                kept = attempt
            else:
                sampler.drop(attempt)
            # Twice at least, even when the first try started late (the clock is read before
            # it, and a coarse clock ticks between reads).
            if attempt.start >= end and (count > 1 or limit == 0):
                break
            _wait(attempt.start, every, end)
        d: Dict[str, Any] = {
            "check_type": "stable",
            "name": name,
            "description": (
                f"Verify '{render_text(name)}' holds for {seconds(limit)} "
                f"(every {seconds(every)})"
            ),
            "duration": limit,
            "interval": every,
            "tries": count,
        }
        return Stable._describe(d, kept, trace, start)

    def combine(self, verdicts: List[bool]) -> bool:
        return bool(verdicts) and all(verdicts)

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        if passed:
            return (
                f"held for {seconds(d.get('duration'))} ({self._tries(d)}), "
                f"closest: {self._kept(d)}"
            )
        elapsed = seconds(d.get("elapsed"))
        if d.get("error") is not None:
            return f"stopped at try {d.get('tries')} ({elapsed})"
        return f"failed at try {d.get('tries')} ({_failed_at(d)}): {self._kept(d)}{self._also(d)}"


def _failed_at(d: Mapping[str, Any]) -> str:
    """When the failed try started, from the trace; else the elapsed time."""
    trace = d.get("trace")
    if isinstance(trace, list) and trace and isinstance(trace[-1], list) and trace[-1]:
        return seconds(trace[-1][0])
    return seconds(d.get("elapsed"))


EVENTUALLY = register(Eventually())
STABLE = register(Stable())
