"""The check-type registry and the two functions that dispatch through it.

Everything specific to one ``check_type`` lives in one :class:`CheckType` subclass: building the
descriptor (``build``), comparing the user's values (``compare``), rendering the detail clause
(``detail``) and, for composites, finding and rebuilding the child checks. :func:`judge` and
:func:`render_detail` look the class up in :data:`REGISTRY`, so no other module branches on
``check_type``.
"""
from __future__ import annotations

import bdb
import contextvars
import math
import unittest
from fractions import Fraction
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Mapping,
    Optional,
    Tuple,
    TypeVar,
    Union,
)

import pytest

from .._descriptors import is_descriptor
from .._render import describe_error, escape, render_value, safe_repr, safe_str

#: ``(passed, error)``: the verdict and, when the check could not be evaluated, why.
Verdict = Tuple[bool, Optional[str]]

#: ``Exception`` subclasses that end the test or the session, not a check: ``pytest.exit``
#: (also quitting the debugger), ``bdb.BdbQuit`` and ``unittest.SkipTest``.
_ALWAYS_ON: Tuple[type, ...] = (pytest.exit.Exception, bdb.BdbQuit, unittest.SkipTest)


def stops_test(exc: BaseException) -> bool:
    """Whether *exc* is, or groups, the error of a check that stopped the test
    (``verify.require``, fail-fast)."""
    queue: List[BaseException] = [exc]
    seen = set()
    while queue:
        link = queue.pop()
        if id(link) in seen:
            continue
        seen.add(id(link))
        if getattr(link, "stops_test", False) is True:
            return True
        members = getattr(link, "exceptions", None)
        if isinstance(members, tuple):  # an exception group
            queue.extend(member for member in members if isinstance(member, BaseException))
    return False


def passes_through(exc: BaseException, expected: Tuple[type, ...] = ()) -> bool:
    """Whether *exc* goes on through code that takes errors as a failed check (a lazy child, a
    condition, a factory, a sample, a ``raises`` block): a stop error, an exception that is not
    an ``Exception`` (``pytest.skip``, ``KeyboardInterrupt``), or one of :data:`_ALWAYS_ON`.

    The last two are taken when an *expected* class names them: their own class or a base of
    it other than ``Exception`` and ``BaseException``.
    """
    if stops_test(exc):
        return True
    if isinstance(exc, Exception) and not isinstance(exc, _ALWAYS_ON):
        return False
    try:
        return not any(
            isinstance(exc, cls) and cls not in (Exception, BaseException) for cls in expected
        )
    except Exception:  # pragma: no cover - a misbehaving __instancecheck__
        return True


#: How the user code a composite calls is called, when the sink building the composite
#: watches it (see :func:`call_user`): ``caller(function, args, check)``.
CALLER: contextvars.ContextVar[Optional[Callable[..., Any]]] = contextvars.ContextVar(
    "pytest_verifier_caller", default=None
)


def call_user(function: Callable[..., Any], *args: Any, check: bool = True) -> Any:
    """Call *function* with *args*: user code a composite calls, a lazy child or an
    ``all_satisfy`` factory (*check*: it must return a check) or a guard condition.

    The sink building the composite may watch the call (:data:`CALLER`): the fixture's forgets
    the ``verify.raises`` blocks the code made when it raises (it never reached their
    ``with``), and a block it returns in place of a check (the composite's error says so).
    """
    caller = CALLER.get()
    return function(*args) if caller is None else caller(function, args, check)


class CheckType:
    """One kind of check. Subclasses set ``check_type`` and implement ``compare`` and ``detail``.

    ``build`` is a static method with the signature of the matching ``Verify`` method.
    """

    check_type: ClassVar[str]

    #: Put between the name and the detail in a summary line.
    separator: ClassVar[str] = " — "

    #: Fields a record keeps only a preview of: at most this many nodes, strings cut at
    #: :data:`~pytest_verifier._render.VALUE_LIMIT` characters. Others are copied in full.
    snapshot_limits: ClassVar[Dict[str, int]] = {}

    def compare(self, d: Mapping[str, Any]) -> Any:
        """Compare the descriptor's values and return the raw result. May raise."""
        raise NotImplementedError

    def verdict(self, d: Mapping[str, Any]) -> Verdict:
        """The verdict of a descriptor that has no recorded verdict or error. Never raises."""
        try:
            result = self.compare(d)
        except Exception as exc:
            return False, describe_error(exc)
        return truth(result)

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        """The ``expected … got …`` clause (failed) or the compact restatement (passed)."""
        raise NotImplementedError

    def margin(self, d: Mapping[str, Any]) -> Optional[Union[float, Fraction]]:
        """How far the value is inside its limit, in its units (negative: past it; ``0`` at
        the limit), or ``None`` for a check without a numeric limit. Computed as the verdict
        is: exactly, unless the check compares in floats. May raise."""
        return None


class CompositeType(CheckType):
    """A check whose verdict comes from child checks."""

    #: Descriptor fields that hold child checks. A record rebuilds them instead of copying.
    child_fields: ClassVar[Tuple[str, ...]]

    def children(self, d: Mapping[str, Any]) -> List[Any]:
        """Every child check, selected or not (``None`` for an empty slot)."""
        raise NotImplementedError

    def chosen(self, d: Mapping[str, Any]) -> List[Any]:
        """The children that count toward the verdict. May raise on a malformed descriptor."""
        raise NotImplementedError

    def combine(self, verdicts: List[bool]) -> bool:
        """The composite's verdict from the verdicts of :meth:`chosen`."""
        raise NotImplementedError

    def map_children(self, d: Mapping[str, Any], fn: Callable[[Any], Any]) -> Dict[str, Any]:
        """The descriptor's child fields, rebuilt with *fn* applied to every child check."""
        raise NotImplementedError

    def verdict(self, d: Mapping[str, Any]) -> Verdict:
        try:
            chosen = self.chosen(d)
        except Exception as exc:  # a malformed hand-built descriptor
            return False, describe_error(exc)
        return self.combine([judge(child)[0] for child in chosen]), None


#: Every check type, by ``check_type``.
REGISTRY: Dict[str, CheckType] = {}

_T = TypeVar("_T", bound=CheckType)


def register(check: _T) -> _T:
    """Add *check* to :data:`REGISTRY` and return it."""
    REGISTRY[check.check_type] = check
    return check


def lookup(descriptor: Mapping[str, Any]) -> Optional[CheckType]:
    """The check type of *descriptor*, or ``None`` when it is unknown."""
    try:
        return REGISTRY.get(descriptor.get("check_type"))  # type: ignore[arg-type]
    except TypeError:  # an unhashable check_type in a hand-built descriptor
        return None


def margin(descriptor: Mapping[str, Any]) -> Optional[float]:
    """The margin of a check whose values are plain numbers (see :meth:`CheckType.margin`),
    else ``None``. Never raises."""
    check = lookup(descriptor)
    if check is None:
        return None
    try:
        result = check.margin(descriptor)
        if result is None:
            return None
        number = float(result)  # an exact margin too large for a float: OverflowError
    except Exception:
        return None
    return number if math.isfinite(number) else None


def plain_number(value: Any) -> Union[int, float]:
    """*value*, an ``int`` (not a ``bool``) or a finite ``float``; else raises ``TypeError``,
    so a margin is only computed from numbers that read as such."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"not a plain number: {type(value).__name__}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("not a finite number")
    return value


def exact_number(value: Any) -> Fraction:
    """:func:`plain_number` as an exact :class:`~fractions.Fraction` (Python compares ``int``
    and ``float`` exactly, so ordering margins are exact too)."""
    return Fraction(plain_number(value))


def child_checks(descriptor: Mapping[str, Any]) -> List[Any]:
    """The direct child checks of a composite (``[]`` for any other check)."""
    check = lookup(descriptor)
    if not isinstance(check, CompositeType):
        return []
    return [child for child in check.children(descriptor) if child is not None]


def chosen_checks(descriptor: Mapping[str, Any]) -> List[Any]:
    """The children a composite selected, which count toward its verdict (``[]`` for any other
    check, or a malformed one). Never raises."""
    check = lookup(descriptor)
    if not isinstance(check, CompositeType):
        return []
    try:
        return [child for child in check.chosen(descriptor) if child is not None]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------


def judge(descriptor: Any) -> Verdict:
    """Return the verdict of one descriptor without ever raising.

    A descriptor that already carries a ``bool`` ``passed`` (one recorded by the fixture) keeps
    its recorded verdict: its values are snapshots and are not compared again.
    """
    if not is_descriptor(descriptor):
        return False, f"not a check descriptor: {safe_repr(descriptor)}"
    recorded = descriptor.get("passed")
    if isinstance(recorded, bool):
        return recorded, descriptor.get("error")
    error = descriptor.get("error")
    if error is not None:
        return False, safe_str(error)
    check = lookup(descriptor)
    if check is None:
        unknown = ValueError(f"Unknown check_type: {safe_repr(descriptor.get('check_type'))}")
        return False, describe_error(unknown)
    return check.verdict(descriptor)


def truth(result: Any) -> Verdict:
    """Coerce a comparison result to a ``bool`` verdict, never raising."""
    if result is True or result is False:
        return result, None
    try:
        return bool(result), None
    except Exception as exc:
        return False, (
            f"the comparison returned a {type(result).__name__} whose truth value cannot be "
            f"decided ({describe_error(exc)}); reduce it with .all() or .any()"
        )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def value(value: object, units: Optional[str]) -> str:
    """Render a value with its optional unit suffix (e.g. ``3.3V``); see
    :func:`~pytest_verifier._render.render_value`."""
    return render_value(value, units)


def value_pair(actual: object, expected: object, units: Optional[str]) -> Tuple[str, str]:
    """Render two compared values. When they look the same but their types differ, such as
    ``1`` and ``Decimal('1')``, each gets its type: ``1 (int)`` and ``1 (Decimal)``."""
    shown_actual, shown_expected = value(actual, units), value(expected, units)
    if shown_actual == shown_expected and type(actual) is not type(expected):
        shown_actual = f"{shown_actual} ({type(actual).__name__})"
        shown_expected = f"{shown_expected} ({type(expected).__name__})"
    return shown_actual, shown_expected


def render_detail(result: Mapping[str, Any], passed: bool, error: Optional[str] = None) -> str:
    """The detail clause of one check, never raising.

    A check that could not be evaluated gets its error appended, for example
    ``expected > 100, got None (TypeError: '>' not supported ...)``.
    """
    if error is None:
        error = result.get("error")
    try:
        if error is not None and result.get("error") is None:
            result = {**result, "error": error}  # the detail can see why judging failed
        text = _detail(result, passed)
    except Exception as exc:
        if error is None:
            return f"<detail unavailable: {describe_error(exc)}>"
        return escape(f"error: {error}")
    return escape(text if error is None else f"{text} ({error})")


def _detail(result: Mapping[str, Any], passed: bool) -> str:
    check = lookup(result)
    if check is not None:
        return check.detail(result, passed)
    # An unknown check type: the canonical description, prefix-stripped.
    name = safe_str(result.get("name", ""))
    description = safe_str(result.get("description", ""))
    prefix = f"Verify '{name}' "
    return description[len(prefix):] if description.startswith(prefix) else description


def summary_separator(result: Mapping[str, Any]) -> str:
    """What separates a check's name from its detail in a summary line."""
    check = lookup(result)
    return CheckType.separator if check is None else check.separator


def child_detail(child: Mapping[str, Any], passed: bool) -> str:
    """The detail of a child: the one it was recorded with, else rendered now."""
    stored = child.get("detail")
    return stored if isinstance(stored, str) else render_detail(child, passed)
