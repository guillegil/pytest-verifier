from __future__ import annotations

import enum
import numbers
import sys
import typing
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from ._render import describe_error, safe_format, safe_repr, safe_str, snapshot

if sys.version_info >= (3, 10):
    from types import UnionType

    #: What ``isinstance`` accepts as its second argument.
    ClassInfo = Union[type, UnionType, Tuple[Any, ...]]
else:  # pragma: no cover - exercised by the Python 3.9 CI job
    ClassInfo = Union[type, Tuple[Any, ...]]


class CheckDescriptor(typing.TypedDict, total=False):
    """Plain-data descriptor returned by every ``verify.*`` call.

    All fields except ``check_type``, ``name``, and ``description`` are
    check-type-specific and may or may not be present depending on the
    check that produced the descriptor.

    Descriptors built by the module-level ``verify`` hold the values they were given, so they
    can be evaluated later. Descriptors recorded by the ``verify`` fixture are already judged:
    they carry ``passed``, a rendered ``detail``, an ``error`` when the check could not be
    evaluated, and JSON-safe snapshots of the checked values.
    """

    # --- always present ---
    check_type: str
    name: str
    description: str

    # --- set after evaluation (fixture path) ---
    passed: bool
    detail: str
    error: Optional[str]

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
    expected_type: str
    expected_types: List[str]
    instance_check: bool
    actual_length: Optional[int]
    child_checks: List[CheckDescriptor]
    switch_value: Any
    switch_label: str
    cases: Dict[str, CheckDescriptor]
    default: Optional[CheckDescriptor]
    matched_case: Optional[str]
    branches: List[GuardBranch]
    matched_index: Optional[int]
    msg: str


class GuardBranch(typing.TypedDict):
    """One branch of a ``guard`` check: a condition, its label, and the check to run."""

    condition: bool
    label: str
    check: CheckDescriptor


#: Check types whose verdict comes from child checks.
COMPOSITE_TYPES = frozenset({"all_satisfy", "conditional", "guard"})


# ---------------------------------------------------------------------------
# Argument validation (usage errors raise; problems with the checked data never do)
# ---------------------------------------------------------------------------


def _require_name(name: object, check: str) -> str:
    if not isinstance(name, str):
        raise TypeError(f"{check}() name must be a str, got {type(name).__name__}")
    return name


def is_descriptor(value: object) -> bool:
    """Whether ``value`` looks like a check descriptor (a mapping with a ``check_type``)."""
    return isinstance(value, Mapping) and "check_type" in value


def _require_descriptor(value: object, where: str) -> CheckDescriptor:
    if not is_descriptor(value):
        raise TypeError(
            f"{where} must be a check descriptor (the result of a verify.* call), "
            f"got {type(value).__name__}"
        )
    return value  # type: ignore[return-value]


def _is_real(value: object) -> bool:
    return isinstance(value, (numbers.Real, Decimal)) and not isinstance(value, bool)


def _validate_tolerance(value: object, label: str) -> None:
    if value is None:
        return
    if not _is_real(value):
        raise TypeError(f"approx() {label} must be a real number, got {type(value).__name__}")
    try:
        invalid = bool(value != value or value < 0)  # type: ignore[operator]
    except Exception:  # Decimal('sNaN') refuses every comparison
        invalid = True
    if invalid:
        raise ValueError(f"approx() {label} must be a non-negative number, got {safe_repr(value)}")


# ---------------------------------------------------------------------------
# Descriptor builder helpers
# ---------------------------------------------------------------------------

def _units_suffix(units: str | None) -> str:
    return units if units else ""


def _format_percent(rel_tol: Any) -> str:
    """Render a relative tolerance as a percent string, dropping a trailing ``.0``.

    ``0.01`` -> ``"1"``, ``0.015`` -> ``"1.5"``. The ``g`` format also rounds away
    IEEE 754 artifacts from the ``* 100`` (e.g. ``0.007 * 100`` -> ``"0.7"``).
    """
    try:
        return f"{rel_tol * 100:.10g}"
    except Exception:
        return safe_format(rel_tol * 100)


def approx_tolerance(abs_tol: Any, rel_tol: Any, units: str | None = None) -> str:
    """Render the ``± …`` tolerance clause for an approx check (spec §5.1).

    Labels ``(abs)``/``(rel)`` appear only when both tolerances are present.
    Shared by ``build_approx`` and the ``ChecksFailedError`` message formatter.
    """
    u = _units_suffix(units)
    if abs_tol is not None and rel_tol is not None:
        return f"± {safe_format(abs_tol)}{u} (abs) ± {_format_percent(rel_tol)}% (rel)"
    if abs_tol is not None:
        return f"± {safe_format(abs_tol)}{u}"
    return f"± {_format_percent(rel_tol)}%"


def build_equal(actual: Any, expected: Any, *, name: str, units: str | None = None) -> CheckDescriptor:
    _require_name(name, "equal")
    u = _units_suffix(units)
    desc: CheckDescriptor = {
        "check_type": "equal",
        "name": name,
        "description": f"Verify '{name}' == {safe_format(expected)}{u}",
        "actual": actual,
        "expected": expected,
        "units": units,
    }
    return desc


def build_not_equal(actual: Any, expected: Any, *, name: str, units: str | None = None) -> CheckDescriptor:
    _require_name(name, "not_equal")
    u = _units_suffix(units)
    desc: CheckDescriptor = {
        "check_type": "not_equal",
        "name": name,
        "description": f"Verify '{name}' != {safe_format(expected)}{u}",
        "actual": actual,
        "expected": expected,
        "units": units,
    }
    return desc


def build_approx(
    actual: Any,
    expected: Any,
    *,
    abs_tol: float | None = None,
    rel_tol: float | None = None,
    name: str,
    units: str | None = None,
) -> CheckDescriptor:
    _require_name(name, "approx")
    if abs_tol is None and rel_tol is None:
        raise ValueError("approx requires at least one of abs_tol or rel_tol")
    _validate_tolerance(abs_tol, "abs_tol")
    _validate_tolerance(rel_tol, "rel_tol")
    u = _units_suffix(units)
    tol_str = approx_tolerance(abs_tol, rel_tol, units)
    desc: CheckDescriptor = {
        "check_type": "approx",
        "name": name,
        "description": f"Verify '{name}' == {safe_format(expected)}{u} {tol_str}",
        "actual": actual,
        "expected": expected,
        "abs_tol": abs_tol,
        "rel_tol": rel_tol,
        "units": units,
    }
    return desc


def _build_ordering(
    check_type: str, op: str, actual: Any, threshold: Any, name: str, units: str | None
) -> CheckDescriptor:
    _require_name(name, check_type)
    u = _units_suffix(units)
    desc: CheckDescriptor = {
        "check_type": check_type,
        "name": name,
        "description": f"Verify '{name}' {op} {safe_format(threshold)}{u}",
        "actual": actual,
        "threshold": threshold,
        "units": units,
    }
    return desc


def build_greater(actual: Any, threshold: float, *, name: str, units: str | None = None) -> CheckDescriptor:
    return _build_ordering("greater", ">", actual, threshold, name, units)


def build_greater_equal(actual: Any, threshold: float, *, name: str, units: str | None = None) -> CheckDescriptor:
    return _build_ordering("greater_equal", ">=", actual, threshold, name, units)


def build_less(actual: Any, threshold: float, *, name: str, units: str | None = None) -> CheckDescriptor:
    return _build_ordering("less", "<", actual, threshold, name, units)


def build_less_equal(actual: Any, threshold: float, *, name: str, units: str | None = None) -> CheckDescriptor:
    return _build_ordering("less_equal", "<=", actual, threshold, name, units)


def build_between(
    actual: Any,
    low: float,
    high: float,
    *,
    inclusive: bool = True,
    name: str,
    units: str | None = None,
) -> CheckDescriptor:
    _require_name(name, "between")
    if _is_real(low) and _is_real(high):
        try:
            inverted = bool(low > high)
        except Exception:  # Decimal('NaN') refuses ordering; the check itself will then fail
            inverted = False
        if inverted:
            raise ValueError(
                f"between() low must not exceed high, got low={safe_repr(low)}, high={safe_repr(high)}"
            )
    u = _units_suffix(units)
    lo, hi = safe_format(low), safe_format(high)
    if inclusive:
        description = f"Verify '{name}' ∈ [{lo}{u}, {hi}{u}]"
    else:
        description = f"Verify '{name}' ∈ ({lo}{u}, {hi}{u})"
    desc: CheckDescriptor = {
        "check_type": "between",
        "name": name,
        "description": description,
        "actual": actual,
        "low": low,
        "high": high,
        "inclusive": inclusive,
        "units": units,
    }
    return desc


def build_is_true(actual: Any, *, name: str) -> CheckDescriptor:
    _require_name(name, "is_true")
    desc: CheckDescriptor = {
        "check_type": "true",
        "name": name,
        "description": f"Verify '{name}' is True",
        "actual": actual,
    }
    return desc


def build_is_false(actual: Any, *, name: str) -> CheckDescriptor:
    _require_name(name, "is_false")
    desc: CheckDescriptor = {
        "check_type": "false",
        "name": name,
        "description": f"Verify '{name}' is False",
        "actual": actual,
    }
    return desc


def build_is_none(actual: Any, *, name: str) -> CheckDescriptor:
    _require_name(name, "is_none")
    desc: CheckDescriptor = {
        "check_type": "is_none",
        "name": name,
        "description": f"Verify '{name}' is None",
        "actual": actual,
    }
    return desc


def build_is_not_none(actual: Any, *, name: str) -> CheckDescriptor:
    _require_name(name, "is_not_none")
    desc: CheckDescriptor = {
        "check_type": "is_not_none",
        "name": name,
        "description": f"Verify '{name}' is not None",
        "actual": actual,
    }
    return desc


def build_contains(haystack: Any, needle: Any, *, name: str) -> CheckDescriptor:
    _require_name(name, "contains")
    desc: CheckDescriptor = {
        "check_type": "contains",
        "name": name,
        "description": f"Verify '{name}' contains {safe_repr(needle)}",
        "haystack": haystack,
        "needle": needle,
    }
    return desc


def build_not_contains(haystack: Any, needle: Any, *, name: str) -> CheckDescriptor:
    _require_name(name, "not_contains")
    desc: CheckDescriptor = {
        "check_type": "not_contains",
        "name": name,
        "description": f"Verify '{name}' does not contain {safe_repr(needle)}",
        "haystack": haystack,
        "needle": needle,
    }
    return desc


def build_matches(actual: Any, pattern: str, *, name: str) -> CheckDescriptor:
    _require_name(name, "matches")
    desc: CheckDescriptor = {
        "check_type": "matches",
        "name": name,
        "description": f"Verify '{name}' matches /{safe_format(pattern)}/",
        "actual": actual,
        "pattern": pattern,
    }
    return desc


# ---------------------------------------------------------------------------
# is_instance
# ---------------------------------------------------------------------------


def _classes(expected_type: object) -> tuple[type, ...]:
    """Flatten what ``isinstance`` accepts (a class, a tuple, a union) into a tuple of classes.

    ``typing.Union``/``Optional`` and PEP 604 unions become their member classes, so they work
    the same way on every supported Python. Subscripted generics such as ``list[int]`` cannot be
    checked at runtime and are rejected with ``TypeError``.
    """
    if isinstance(expected_type, type) and typing.get_origin(expected_type) is None:
        return (expected_type,)
    if isinstance(expected_type, tuple):
        return tuple(cls for member in expected_type for cls in _classes(member))
    origin = typing.get_origin(expected_type)
    if origin is Union or (sys.version_info >= (3, 10) and isinstance(expected_type, UnionType)):
        return tuple(
            cls for member in typing.get_args(expected_type) for cls in _classes(member)
        )
    if isinstance(origin, type) and not typing.get_args(expected_type):
        return (origin,)  # a bare alias such as typing.List
    raise TypeError(
        "is_instance() expected_type must be a class, a tuple of classes or a union of "
        f"classes, got {safe_repr(expected_type)}"
    )


def _type_display(cls: type) -> str:
    if cls is type(None):
        return "None"
    return safe_str(getattr(cls, "__qualname__", None) or getattr(cls, "__name__", None) or cls)


def qualified_type_name(cls: type) -> str:
    """``module.qualname`` of a class, used to tell apart same-name classes."""
    module = getattr(cls, "__module__", None)
    qualname = getattr(cls, "__qualname__", None) or getattr(cls, "__name__", "?")
    return f"{module}.{qualname}" if module else safe_str(qualname)


def build_is_instance(actual: Any, expected_type: ClassInfo, *, name: str) -> CheckDescriptor:
    _require_name(name, "is_instance")
    classes = _classes(expected_type)
    if not classes:
        raise TypeError("is_instance() expected_type must name at least one class")
    error: str | None = None
    try:
        instance_check = isinstance(actual, classes)
    except Exception as exc:  # a misbehaving __instancecheck__
        instance_check, error = False, describe_error(exc)
    display = " | ".join(_type_display(cls) for cls in classes)
    desc: CheckDescriptor = {
        "check_type": "is_instance",
        "name": name,
        "description": f"Verify '{name}' is instance of {display}",
        "actual": actual,
        "expected_type": display,
        "expected_types": [qualified_type_name(cls) for cls in classes],
        "instance_check": instance_check,
    }
    if error is not None:
        desc["error"] = error
    return desc


def build_length(actual: Any, expected: int, *, name: str) -> CheckDescriptor:
    _require_name(name, "length")
    try:
        actual_length: int | None = len(actual)
    except Exception:
        actual_length = None  # evaluation calls len() again and records the error
    desc: CheckDescriptor = {
        "check_type": "length",
        "name": name,
        "description": f"Verify '{name}' has length {safe_format(expected)}",
        "actual": actual,
        "expected": expected,
        "actual_length": actual_length,
    }
    return desc


def build_all_satisfy(
    items: Iterable[Any],
    descriptor_factory: Callable[[Any], CheckDescriptor],
    *,
    name: str,
) -> CheckDescriptor:
    """Build one child check per item by calling *descriptor_factory* right away.

    Problems with the data (``items`` is not iterable, the factory raises or returns something
    that is not a check) do not raise: the descriptor records them in ``error`` and fails.
    """
    _require_name(name, "all_satisfy")
    child_checks: list[CheckDescriptor] = []
    error: str | None = None
    try:
        iterator = iter(items)
    except Exception as exc:
        iterator, error = iter(()), f"items are not iterable: {describe_error(exc)}"
    index = 0
    while error is None:
        try:
            item = next(iterator)
        except StopIteration:
            break
        except Exception as exc:
            error = f"iterating items raised {describe_error(exc)}"
            break
        try:
            child = descriptor_factory(item)
        except Exception as exc:
            error = f"descriptor_factory raised {describe_error(exc)} for item {index}"
            break
        if not is_descriptor(child):
            error = (
                f"descriptor_factory returned {type(child).__name__} for item {index}, "
                "not a check (did you forget `return`?)"
            )
            break
        child_checks.append(child)
        index += 1
    desc: CheckDescriptor = {
        "check_type": "all_satisfy",
        "name": name,
        "description": f"Verify all items in '{name}' satisfy condition ({len(child_checks)} items)",
        "child_checks": child_checks,
    }
    if error is not None:
        desc["error"] = error
    return desc


# ---------------------------------------------------------------------------
# conditional
# ---------------------------------------------------------------------------


def _unwrap(value: object) -> object:
    """An enum member compares and is stored as its value."""
    while isinstance(value, enum.Enum):
        value = value.value
    return value


def case_key(key: object) -> str:
    """The version-stable string a ``conditional`` case key is stored under.

    Enum members are stored under their value, so ``Mode.ACTIVE`` (value 1) and ``1`` share
    the key ``"1"`` on every Python version.
    """
    return safe_str(_unwrap(key))


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
    switch = _unwrap(switch_value)
    for key in keys:
        if _same(_unwrap(key), switch):
            return key
    if _int_or_str(switch):
        wanted = safe_str(switch)
        for key in keys:
            unwrapped = _unwrap(key)
            if _int_or_str(unwrapped) and type(unwrapped) is not type(switch):
                if safe_str(unwrapped) == wanted:
                    return key
    return _NO_CASE


class _NoCase:
    def __repr__(self) -> str:
        return "<no matching case>"


_NO_CASE: Any = _NoCase()


def build_conditional(
    switch_value: Any,
    *,
    cases: Mapping[Any, CheckDescriptor],
    default: CheckDescriptor | None = None,
    name: str,
) -> CheckDescriptor:
    _require_name(name, "conditional")
    if not isinstance(cases, Mapping):
        raise TypeError(f"conditional() cases must be a mapping, got {type(cases).__name__}")
    normalized: dict[str, CheckDescriptor] = {}
    originals: dict[str, Any] = {}
    for key, check in cases.items():
        stored = case_key(key)
        if stored in normalized:
            raise ValueError(
                f"conditional() case keys {safe_repr(originals[stored])} and {safe_repr(key)} "
                f"are both stored as {stored!r}; use distinct keys"
            )
        normalized[stored] = _require_descriptor(check, f"conditional() case {safe_repr(key)}")
        originals[stored] = key
    if default is not None:
        _require_descriptor(default, "conditional() default")
    selected = select_case(switch_value, cases.keys())
    label = safe_str(switch_value)
    desc: CheckDescriptor = {
        "check_type": "conditional",
        "name": name,
        "description": f"Verify '{name}' [mode={label}]",
        "switch_value": snapshot(_unwrap(switch_value)),
        "switch_label": label,
        "cases": normalized,
        "default": default,
        "matched_case": None if selected is _NO_CASE else case_key(selected),
    }
    return desc


# ---------------------------------------------------------------------------
# guard
# ---------------------------------------------------------------------------


def _unpack_branch(branch: object, index: int) -> tuple[object, str, CheckDescriptor]:
    condition: object
    label: Any
    check: Any
    try:
        condition, label, check = branch  # type: ignore[misc]
    except (TypeError, ValueError):
        raise TypeError(
            f"guard() branch {index} must be a (condition, label, check) tuple, "
            f"got {safe_repr(branch)}"
        ) from None
    if is_descriptor(condition):
        raise TypeError(
            f"guard() branch {index} condition is a check descriptor, which is always truthy; "
            "use its verdict instead, e.g. check['passed']"
        )
    return condition, label, _require_descriptor(check, f"guard() branch {index} check")


def build_guard(
    branches: Sequence[tuple[object, str, CheckDescriptor]],
    *,
    default: CheckDescriptor | None = None,
    name: str,
) -> CheckDescriptor:
    """Build a guard descriptor — an ordered if/elif/else chain.

    Each branch is a ``(condition, label, check)`` tuple. The first branch whose
    condition is truthy is the one evaluated; if none match, ``default`` is used.
    """
    _require_name(name, "guard")
    if default is not None:
        _require_descriptor(default, "guard() default")
    normalized: list[GuardBranch] = []
    matched_index: int | None = None
    error: str | None = None
    for index, branch in enumerate(branches):
        condition, label, check = _unpack_branch(branch, index)
        try:
            truth = bool(condition)
        except Exception as exc:
            truth = False
            if matched_index is None and error is None:
                error = f"condition of branch {index} ({safe_str(label)}) raised {describe_error(exc)}"
        if truth and matched_index is None and error is None:
            matched_index = index
        normalized.append({"condition": truth, "label": label, "check": check})
    desc: CheckDescriptor = {
        "check_type": "guard",
        "name": name,
        "description": f"Verify '{name}' [guarded]",
        "branches": normalized,
        "default": default,
        "matched_index": matched_index,
    }
    if error is not None:
        desc["error"] = error
    return desc


def build_fail(msg: str, *, name: str | None = None) -> CheckDescriptor:
    resolved_name = name if name is not None else safe_str(msg)
    _require_name(resolved_name, "fail")
    desc: CheckDescriptor = {
        "check_type": "fail",
        "name": resolved_name,
        "description": f"FAIL: {safe_format(msg)}",
        "msg": msg,
    }
    return desc


def child_checks(descriptor: Mapping[str, Any]) -> list[Any]:
    """The direct child descriptors of a composite check (``[]`` for any other check)."""
    check_type = descriptor.get("check_type")
    if check_type == "all_satisfy":
        return list(descriptor.get("child_checks") or [])
    if check_type == "guard":
        children = [branch.get("check") for branch in descriptor.get("branches") or []]
    elif check_type == "conditional":
        children = list((descriptor.get("cases") or {}).values())
    else:
        return []
    default = descriptor.get("default")
    if default is not None:
        children.append(default)
    return children

