"""Check types that compare values: everything except the composites."""
from __future__ import annotations

import numbers
import operator
import re
from decimal import Decimal
from typing import Any, Callable, ClassVar, Mapping, Optional

from .._descriptors import (
    CheckDescriptor,
    ClassInfo,
    approx_tolerance,
    flatten_classes,
    is_real,
    qualified_type_name,
    require_name,
    type_display,
    units_suffix,
    validate_tolerance,
)
from .._render import bounded_format, bounded_repr, describe_error, safe_format, safe_repr, safe_str
from ._base import CheckType, register, value

# ---------------------------------------------------------------------------
# Equality & approximation
# ---------------------------------------------------------------------------


class Equal(CheckType):
    check_type = "equal"

    @staticmethod
    def build(
        actual: Any, expected: Any, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        require_name(name, "equal")
        return {
            "check_type": "equal",
            "name": name,
            "description": f"Verify '{name}' == {safe_format(expected)}{units_suffix(units)}",
            "actual": actual,
            "expected": expected,
            "units": units,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return d["actual"] == d["expected"]

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        units = d.get("units")
        actual, expected = value(d["actual"], units), value(d["expected"], units)
        return f"{actual} == {expected}" if passed else f"expected {expected}, got {actual}"


class NotEqual(CheckType):
    check_type = "not_equal"

    @staticmethod
    def build(
        actual: Any, expected: Any, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        require_name(name, "not_equal")
        return {
            "check_type": "not_equal",
            "name": name,
            "description": f"Verify '{name}' != {safe_format(expected)}{units_suffix(units)}",
            "actual": actual,
            "expected": expected,
            "units": units,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return d["actual"] != d["expected"]

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        units = d.get("units")
        actual, expected = value(d["actual"], units), value(d["expected"], units)
        return f"{actual} ≠ {expected}" if passed else f"expected ≠ {expected}, got {actual}"


def _number(number: Any) -> Any:
    """*number* if it is a real number, else its ``float()`` if it has one, like ``math.isclose``.

    Anything else, such as ``None``, a string or a list, raises ``TypeError`` instead of being
    compared with ``==``.
    """
    if isinstance(number, (numbers.Real, Decimal)):
        return number
    if hasattr(type(number), "__float__"):  # e.g. a numpy 0-d array
        return float(number)
    raise TypeError(f"approx compares numbers, got {type(number).__name__}")


def _approx(actual: Any, expected: Any, abs_tol: Any, rel_tol: Any) -> bool:
    """``|actual - expected|`` within ``abs_tol``, or within ``rel_tol * |expected|``.

    Exact types (``int``, ``Decimal``, ``Fraction``) are compared exactly; as soon as a
    ``float`` is involved the comparison is done in floats, like ``math.isclose``.
    """
    if abs_tol is None and rel_tol is None:
        raise ValueError("approx requires at least one of abs_tol or rel_tol")
    actual, expected = _number(actual), _number(expected)
    operands = [actual, expected, abs_tol, rel_tol]
    if any(isinstance(v, float) for v in operands):
        actual, expected = float(actual), float(expected)
        abs_tol = None if abs_tol is None else float(abs_tol)
        rel_tol = None if rel_tol is None else float(rel_tol)
    if actual == expected:  # also makes inf match inf
        return True
    try:
        diff = abs(actual - expected)
    except TypeError:  # e.g. Decimal and Fraction: fall back to floats
        return _approx(float(actual), float(expected), abs_tol, rel_tol)
    if abs_tol is not None and diff <= abs_tol:
        return True
    return rel_tol is not None and diff <= rel_tol * abs(expected)


class Approx(CheckType):
    check_type = "approx"

    @staticmethod
    def build(
        actual: Any,
        expected: Any,
        *,
        abs_tol: Optional[float] = None,
        rel_tol: Optional[float] = None,
        name: str,
        units: Optional[str] = None,
    ) -> CheckDescriptor:
        require_name(name, "approx")
        if abs_tol is None and rel_tol is None:
            raise ValueError("approx requires at least one of abs_tol or rel_tol")
        validate_tolerance(abs_tol, "abs_tol")
        validate_tolerance(rel_tol, "rel_tol")
        tolerance = approx_tolerance(abs_tol, rel_tol, units)
        return {
            "check_type": "approx",
            "name": name,
            "description": (
                f"Verify '{name}' == {safe_format(expected)}{units_suffix(units)} {tolerance}"
            ),
            "actual": actual,
            "expected": expected,
            "abs_tol": abs_tol,
            "rel_tol": rel_tol,
            "units": units,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return _approx(d["actual"], d["expected"], d.get("abs_tol"), d.get("rel_tol"))

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        units = d.get("units")
        actual = value(d["actual"], units)
        expected = value(d["expected"], units)
        target = f"{expected} {approx_tolerance(d.get('abs_tol'), d.get('rel_tol'), units)}"
        return f"{actual} == {target}" if passed else f"expected {target}, got {actual}"


# ---------------------------------------------------------------------------
# Ordering & range
# ---------------------------------------------------------------------------


class _Ordering(CheckType):
    symbol: ClassVar[str]
    compare_with: ClassVar[Callable[[Any, Any], Any]]

    @classmethod
    def _build(
        cls, actual: Any, threshold: Any, name: str, units: Optional[str]
    ) -> CheckDescriptor:
        require_name(name, cls.check_type)
        return {
            "check_type": cls.check_type,
            "name": name,
            "description": (
                f"Verify '{name}' {cls.symbol} {safe_format(threshold)}{units_suffix(units)}"
            ),
            "actual": actual,
            "threshold": threshold,
            "units": units,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return type(self).compare_with(d["actual"], d["threshold"])

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        units = d.get("units")
        actual, threshold = value(d["actual"], units), value(d["threshold"], units)
        if passed:
            return f"{actual} {self.symbol} {threshold}"
        return f"expected {self.symbol} {threshold}, got {actual}"


class Greater(_Ordering):
    check_type = "greater"
    symbol = ">"
    compare_with = operator.gt

    @staticmethod
    def build(
        actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        return Greater._build(actual, threshold, name, units)


class GreaterEqual(_Ordering):
    check_type = "greater_equal"
    symbol = ">="
    compare_with = operator.ge

    @staticmethod
    def build(
        actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        return GreaterEqual._build(actual, threshold, name, units)


class Less(_Ordering):
    check_type = "less"
    symbol = "<"
    compare_with = operator.lt

    @staticmethod
    def build(
        actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        return Less._build(actual, threshold, name, units)


class LessEqual(_Ordering):
    check_type = "less_equal"
    symbol = "<="
    compare_with = operator.le

    @staticmethod
    def build(
        actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        return LessEqual._build(actual, threshold, name, units)


class Between(CheckType):
    check_type = "between"

    @staticmethod
    def build(
        actual: Any,
        low: float,
        high: float,
        *,
        inclusive: bool = True,
        name: str,
        units: Optional[str] = None,
    ) -> CheckDescriptor:
        require_name(name, "between")
        if is_real(low) and is_real(high):
            try:
                inverted = bool(low > high)
            except Exception:  # Decimal('NaN') refuses ordering; the check itself will then fail
                inverted = False
            if inverted:
                raise ValueError(
                    "between() low must not exceed high, "
                    f"got low={safe_repr(low)}, high={safe_repr(high)}"
                )
        u = units_suffix(units)
        lo, hi = safe_format(low), safe_format(high)
        bounds = f"[{lo}{u}, {hi}{u}]" if inclusive else f"({lo}{u}, {hi}{u})"
        return {
            "check_type": "between",
            "name": name,
            "description": f"Verify '{name}' ∈ {bounds}",
            "actual": actual,
            "low": low,
            "high": high,
            "inclusive": inclusive,
            "units": units,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        actual, low, high = d["actual"], d["low"], d["high"]
        if d.get("inclusive", True):
            return low <= actual <= high
        return low < actual < high

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        units = d.get("units")
        actual = value(d["actual"], units)
        low, high = value(d["low"], units), value(d["high"], units)
        bounds = f"[{low}, {high}]" if d.get("inclusive", True) else f"({low}, {high})"
        return f"{actual} ∈ {bounds}" if passed else f"expected {bounds}, got {actual}"


# ---------------------------------------------------------------------------
# Boolean & identity
# ---------------------------------------------------------------------------


class _Unary(CheckType):
    """A check on one value: ``build(actual, *, name)``."""

    method: ClassVar[str]
    statement: ClassVar[str]

    @classmethod
    def _build(cls, actual: Any, name: str) -> CheckDescriptor:
        require_name(name, cls.method)
        return {
            "check_type": cls.check_type,
            "name": name,
            "description": f"Verify '{name}' {cls.statement}",
            "actual": actual,
        }


class IsTrue(_Unary):
    check_type = "true"
    method = "is_true"
    statement = "is True"

    @staticmethod
    def build(actual: Any, *, name: str) -> CheckDescriptor:
        return IsTrue._build(actual, name)

    def compare(self, d: Mapping[str, Any]) -> Any:
        return bool(d["actual"]) is True

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        return "True" if passed else f"expected True, got {bool(d['actual'])}"


class IsFalse(_Unary):
    check_type = "false"
    method = "is_false"
    statement = "is False"

    @staticmethod
    def build(actual: Any, *, name: str) -> CheckDescriptor:
        return IsFalse._build(actual, name)

    def compare(self, d: Mapping[str, Any]) -> Any:
        return bool(d["actual"]) is False

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        return "False" if passed else f"expected False, got {bool(d['actual'])}"


class IsNone(_Unary):
    check_type = "is_none"
    method = "is_none"
    statement = "is None"

    @staticmethod
    def build(actual: Any, *, name: str) -> CheckDescriptor:
        return IsNone._build(actual, name)

    def compare(self, d: Mapping[str, Any]) -> Any:
        return d["actual"] is None

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        return "None" if passed else f"expected None, got {bounded_repr(d['actual'])}"


class IsNotNone(_Unary):
    check_type = "is_not_none"
    method = "is_not_none"
    statement = "is not None"

    @staticmethod
    def build(actual: Any, *, name: str) -> CheckDescriptor:
        return IsNotNone._build(actual, name)

    def compare(self, d: Mapping[str, Any]) -> Any:
        return d["actual"] is not None

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        return "not None" if passed else f"expected not None, got {bounded_repr(d['actual'])}"


# ---------------------------------------------------------------------------
# String & container
# ---------------------------------------------------------------------------


class Contains(CheckType):
    check_type = "contains"

    @staticmethod
    def build(haystack: Any, needle: Any, *, name: str) -> CheckDescriptor:
        require_name(name, "contains")
        return {
            "check_type": "contains",
            "name": name,
            "description": f"Verify '{name}' contains {safe_repr(needle)}",
            "haystack": haystack,
            "needle": needle,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return d["needle"] in d["haystack"]

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        needle = bounded_repr(d["needle"])
        if passed:
            return f"contains {needle}"
        return f"expected to contain {needle}, got {bounded_repr(d['haystack'])}"


class NotContains(CheckType):
    check_type = "not_contains"

    @staticmethod
    def build(haystack: Any, needle: Any, *, name: str) -> CheckDescriptor:
        require_name(name, "not_contains")
        return {
            "check_type": "not_contains",
            "name": name,
            "description": f"Verify '{name}' does not contain {safe_repr(needle)}",
            "haystack": haystack,
            "needle": needle,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return d["needle"] not in d["haystack"]

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        needle = bounded_repr(d["needle"])
        if passed:
            return f"does not contain {needle}"
        return f"expected to not contain {needle}, got {bounded_repr(d['haystack'])}"


class Matches(CheckType):
    check_type = "matches"

    @staticmethod
    def build(actual: Any, pattern: str, *, name: str) -> CheckDescriptor:
        require_name(name, "matches")
        return {
            "check_type": "matches",
            "name": name,
            "description": f"Verify '{name}' matches /{safe_format(pattern)}/",
            "actual": actual,
            "pattern": pattern,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return re.search(d["pattern"], d["actual"]) is not None

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        pattern = bounded_format(d["pattern"])
        if passed:
            return f"matches /{pattern}/"
        return f"expected to match /{pattern}/, got {bounded_repr(d['actual'])}"


# ---------------------------------------------------------------------------
# Type & collection
# ---------------------------------------------------------------------------


class IsInstance(CheckType):
    check_type = "is_instance"

    @staticmethod
    def build(actual: Any, expected_type: ClassInfo, *, name: str) -> CheckDescriptor:
        require_name(name, "is_instance")
        classes = flatten_classes(expected_type)
        if not classes:
            raise TypeError("is_instance() expected_type must name at least one class")
        error: Optional[str] = None
        try:
            instance_check = isinstance(actual, classes)
        except Exception as exc:  # a misbehaving __instancecheck__
            instance_check, error = False, describe_error(exc)
        display = " | ".join(type_display(cls) for cls in classes)
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

    def compare(self, d: Mapping[str, Any]) -> Any:
        stored = d.get("instance_check")
        if isinstance(stored, bool):
            return stored
        # Hand-built descriptor without the build-time verdict: match names along the MRO.
        mro = type(d.get("actual")).__mro__
        qualified = d.get("expected_types")
        if qualified:
            names = {qualified_type_name(cls) for cls in mro}
            return any(name in names for name in qualified)
        expected_name = d["expected_type"]
        return any(cls.__name__ == expected_name for cls in mro)

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        expected_type = d["expected_type"]
        if passed:
            return f"instance of {expected_type}"
        return f"expected instance of {expected_type}, got {type(d['actual']).__name__}"


class Length(CheckType):
    check_type = "length"

    @staticmethod
    def build(actual: Any, expected: int, *, name: str) -> CheckDescriptor:
        require_name(name, "length")
        try:
            actual_length: Optional[int] = len(actual)
        except Exception:
            actual_length = None  # evaluation calls len() again and records the error
        return {
            "check_type": "length",
            "name": name,
            "description": f"Verify '{name}' has length {safe_format(expected)}",
            "actual": actual,
            "expected": expected,
            "actual_length": actual_length,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        length = d.get("actual_length")
        if length is None:
            length = len(d["actual"])
        return length == d["expected"]

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        expected = d["expected"]
        if passed:
            return f"length {expected}"
        return f"expected length {expected}, got length {d.get('actual_length')}"


class Fail(CheckType):
    check_type = "fail"

    @staticmethod
    def build(msg: str, *, name: Optional[str] = None) -> CheckDescriptor:
        resolved_name = name if name is not None else safe_str(msg)
        require_name(resolved_name, "fail")
        return {
            "check_type": "fail",
            "name": resolved_name,
            "description": f"FAIL: {safe_format(msg)}",
            "msg": msg,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return False

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        return f"FAIL: {bounded_format(d.get('msg', ''))}"


EQUAL = register(Equal())
NOT_EQUAL = register(NotEqual())
APPROX = register(Approx())
GREATER = register(Greater())
GREATER_EQUAL = register(GreaterEqual())
LESS = register(Less())
LESS_EQUAL = register(LessEqual())
BETWEEN = register(Between())
IS_TRUE = register(IsTrue())
IS_FALSE = register(IsFalse())
IS_NONE = register(IsNone())
IS_NOT_NONE = register(IsNotNone())
CONTAINS = register(Contains())
NOT_CONTAINS = register(NotContains())
MATCHES = register(Matches())
IS_INSTANCE = register(IsInstance())
LENGTH = register(Length())
FAIL = register(Fail())
