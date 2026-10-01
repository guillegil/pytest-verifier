"""The descriptor types and the helpers that check types share.

A check descriptor is a plain dict. :class:`CheckDescriptor` types it; the check types that
build and judge descriptors live in :mod:`pytest_verifier._checks`.
"""
from __future__ import annotations

import enum
import numbers
import sys
import typing
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from ._render import escape, render_value, safe_format, safe_repr, safe_str, shorten, units_text

if sys.version_info >= (3, 10):
    from types import UnionType

    #: What ``isinstance`` accepts as its second argument.
    ClassInfo = Union[type, UnionType, Tuple[Any, ...]]
else:  # pragma: no cover - exercised by the Python 3.9 CI job
    ClassInfo = Union[type, Tuple[Any, ...]]


class _CheckIdentity(typing.TypedDict):
    """The keys every check descriptor has."""

    check_type: str
    name: str
    description: str


class CheckDescriptor(_CheckIdentity, total=False):
    """Plain-data descriptor returned by every ``verify.*`` call.

    ``check_type``, ``name`` and ``description`` are always present. All other fields are
    check-type-specific and may or may not be present depending on the check that produced
    the descriptor.

    Descriptors built by ``pytest_verifier.checks`` hold the values they were given, so they
    can be evaluated later. Descriptors recorded by the ``verify`` fixture are already judged:
    they carry ``passed``, a rendered ``detail``, an ``error`` when the check could not be
    evaluated, the test ``phase`` they were made in (``"setup"``, ``"call"`` or
    ``"teardown"``), and JSON-safe snapshots of the checked values.
    """

    # --- set when recorded (fixture path) ---
    passed: bool
    detail: str
    error: Optional[str]
    phase: str

    # --- check-type-specific ---
    actual: Any
    expected: Any
    abs_tol: Optional[float]
    rel_tol: Optional[float]
    units: Optional[str]
    threshold: float
    low: float
    high: float
    inclusive: bool
    haystack: Any
    needle: Any
    pattern: str
    flags: int
    expected_type: str
    expected_types: List[str]
    instance_check: bool
    actual_length: Optional[int]
    child_checks: List[CheckDescriptor]
    switch_value: Any
    switch_label: str
    cases: Dict[str, Optional[CheckDescriptor]]
    default: Optional[CheckDescriptor]
    matched_case: Optional[str]
    branches: List[GuardBranch]
    matched_index: Optional[int]
    msg: str


class GuardBranch(typing.TypedDict):
    """One branch of a ``guard`` check: a condition, its label, and the check to run.

    ``condition`` is ``None`` for a callable condition that was not called because an earlier
    branch matched, and ``check`` is ``None`` for a lazy check that was not selected.
    """

    condition: Optional[bool]
    label: str
    check: Optional[CheckDescriptor]


#: A child check passed to a composite: a descriptor, or a zero-argument callable that builds
#: it, so that only the selected child is built.
Child = Union[CheckDescriptor, Callable[[], CheckDescriptor]]


# ---------------------------------------------------------------------------
# Argument validation (usage errors raise; problems with the checked data never do)
# ---------------------------------------------------------------------------


def require_name(name: object, check: str) -> str:
    if not isinstance(name, str):
        raise TypeError(f"{check}() name must be a str, got {type(name).__name__}")
    return name


def is_descriptor(value: object) -> bool:
    """Whether ``value`` looks like a check descriptor (a mapping with a ``check_type``)."""
    return isinstance(value, Mapping) and "check_type" in value


def require_descriptor(value: object, where: str, alternative: str = "") -> CheckDescriptor:
    if not is_descriptor(value):
        raise TypeError(
            f"{where} must be a check descriptor (the result of a verify.* call){alternative}, "
            f"got {type(value).__name__}"
        )
    return value  # type: ignore[return-value]


def require_child(value: object, where: str) -> Any:
    """A child check: a descriptor, or a zero-argument callable that returns one."""
    if callable(value) and not is_descriptor(value):
        return value
    return require_descriptor(value, where, " or a callable that returns one")


def loose_children(*containers: Any) -> List[Any]:
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


def is_real(value: object) -> bool:
    return isinstance(value, (numbers.Real, Decimal)) and not isinstance(value, bool)


def validate_tolerance(value: object, label: str) -> None:
    if value is None:
        return
    if not is_real(value):
        raise TypeError(f"approx() {label} must be a real number, got {type(value).__name__}")
    try:
        invalid = bool(value != value or value < 0)  # type: ignore[operator]
    except Exception:  # Decimal('sNaN') refuses every comparison
        invalid = True
    if invalid:
        raise ValueError(f"approx() {label} must be a non-negative number, got {safe_repr(value)}")


# ---------------------------------------------------------------------------
# Formatting shared by descriptions and details
# ---------------------------------------------------------------------------


def _format_percent(rel_tol: Any) -> str:
    """Render a relative tolerance as a percent string, dropping a trailing ``.0``.

    ``0.01`` -> ``"1"``, ``0.015`` -> ``"1.5"``. The ``g`` format also rounds away
    IEEE 754 artifacts from the ``* 100`` (e.g. ``0.007 * 100`` -> ``"0.7"``).
    """
    try:
        return f"{rel_tol * 100:.10g}"
    except Exception:
        try:
            return shorten(escape(safe_format(rel_tol * 100)))
        except Exception:  # a hand-built descriptor's rel_tol that cannot be multiplied
            return render_value(rel_tol)


def approx_tolerance(abs_tol: Any, rel_tol: Any, units: Optional[str] = None) -> str:
    """Render the ``± …`` tolerance clause for an approx check (spec §5.1).

    Labels ``(abs)``/``(rel)`` appear when both tolerances are present, and when the units end
    with ``%``: there ``± 2%`` could be either. Shared by the ``approx`` description and its
    detail.
    """
    if abs_tol is not None and rel_tol is not None:
        return f"± {render_value(abs_tol, units)} (abs) ± {_format_percent(rel_tol)}% (rel)"
    ambiguous = units_text(units).endswith("%")
    if abs_tol is not None:
        return f"± {render_value(abs_tol, units)}" + (" (abs)" if ambiguous else "")
    return f"± {_format_percent(rel_tol)}%" + (" (rel)" if ambiguous else "")


# ---------------------------------------------------------------------------
# is_instance
# ---------------------------------------------------------------------------


def flatten_classes(expected_type: object) -> tuple[type, ...]:
    """Flatten what ``isinstance`` accepts (a class, a tuple, a union) into a tuple of classes.

    ``typing.Union``/``Optional`` and PEP 604 unions become their member classes, so they work
    the same way on every supported Python. Subscripted generics such as ``list[int]`` cannot be
    checked at runtime and are rejected with ``TypeError``.
    """
    if isinstance(expected_type, type) and typing.get_origin(expected_type) is None:
        return (expected_type,)
    if isinstance(expected_type, tuple):
        return tuple(cls for member in expected_type for cls in flatten_classes(member))
    origin = typing.get_origin(expected_type)
    if origin is Union or (sys.version_info >= (3, 10) and isinstance(expected_type, UnionType)):
        return tuple(
            cls for member in typing.get_args(expected_type) for cls in flatten_classes(member)
        )
    if isinstance(origin, type) and not typing.get_args(expected_type):
        return (origin,)  # a bare alias such as typing.List
    raise TypeError(
        "is_instance() expected_type must be a class, a tuple of classes or a union of "
        f"classes, got {safe_repr(expected_type)}"
    )


def type_display(cls: type) -> str:
    if cls is type(None):
        return "None"
    return safe_str(getattr(cls, "__qualname__", None) or getattr(cls, "__name__", None) or cls)


def qualified_type_name(cls: type) -> str:
    """``module.qualname`` of a class, used to tell apart same-name classes."""
    module = getattr(cls, "__module__", None)
    qualname = getattr(cls, "__qualname__", None) or getattr(cls, "__name__", "?")
    return f"{module}.{qualname}" if module else safe_str(qualname)


# ---------------------------------------------------------------------------
# conditional case matching
# ---------------------------------------------------------------------------


def unwrap(value: object) -> object:
    """An enum member compares and is stored as its value."""
    while isinstance(value, enum.Enum):
        value = value.value
    return value


def case_key(key: object) -> str:
    """The version-stable string a ``conditional`` case key is stored under.

    Enum members are stored under their value, so ``Mode.ACTIVE`` (value 1) and ``1`` share
    the key ``"1"`` on every Python version.
    """
    return safe_str(unwrap(key))


def _same(a: object, b: object) -> bool:
    try:
        return bool(a == b)
    except Exception:
        return False


def _int_or_str(value: object) -> bool:
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def select_case(switch_value: object, keys: Iterable[Any]) -> Any:
    """Return the case key that *switch_value* selects, or ``_NO_CASE``.

    A key matches when it equals the switch value (enum members compare by value). As the only
    exception to Python equality, an ``int`` and its decimal string are the same key, so
    ``1`` selects ``"1"`` and ``"1"`` selects ``1``. ``None`` never matches the string
    ``"None"``.
    """
    keys = list(keys)
    switch = unwrap(switch_value)
    for key in keys:
        if _same(unwrap(key), switch):
            return key
    if _int_or_str(switch):
        wanted = safe_str(switch)
        for key in keys:
            unwrapped = unwrap(key)
            if _int_or_str(unwrapped) and type(unwrapped) is not type(switch):
                if safe_str(unwrapped) == wanted:
                    return key
    return _NO_CASE


class _NoCase:
    def __repr__(self) -> str:
        return "<no matching case>"


_NO_CASE: Any = _NoCase()
