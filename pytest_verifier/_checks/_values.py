"""Check types that compare values: everything except the composites."""
from __future__ import annotations

import numbers
import operator
import re
from decimal import Decimal
from fractions import Fraction
from typing import Any, Callable, ClassVar, Dict, Mapping, Optional, Tuple, Union

from .._descriptors import (
    CheckDescriptor,
    ClassInfo,
    approx_tolerance,
    flatten_classes,
    is_real,
    qualified_type_name,
    require_name,
    type_display,
    validate_tolerance,
)
from .._render import (
    describe_error,
    escape,
    render_text,
    render_value,
    safe_repr,
    safe_str,
)
from ._base import CheckType, exact_number, plain_number, register, value, value_pair

#: Longest ``fail()`` message shown in a description or detail.
_MESSAGE_LIMIT = 1000

_NAN_EQUALITY = " (NaN never compares equal)"
_NAN_ORDERING = " (NaN fails every comparison)"


def _subject(name: str) -> str:
    """``Verify 'name'``, the start of most descriptions."""
    return f"Verify '{escape(name)}'"


def _is_nan(number: Any) -> bool:
    """Whether *number* is a NaN (``float``, ``Decimal``, numpy and other numbers). Never raises."""
    try:
        if isinstance(number, Decimal):
            return number.is_nan()
        return (
            isinstance(number, numbers.Number)
            and not isinstance(number, bool)
            and bool(number != number)
        )
    except Exception:
        return False


def _nan_note(note: str, *numbers_: Any) -> str:
    return note if any(_is_nan(number) for number in numbers_) else ""

# ---------------------------------------------------------------------------
# Equality & approximation
# ---------------------------------------------------------------------------

#: How deep :func:`_difference` follows nested containers.
_DIFFERENCE_DEPTH = 10

#: Characters shown on each side of the first difference between two long texts.
_WINDOW = 20

#: One step into two containers: ``(path, actual item, expected item)``.
_Step = Tuple[str, Any, Any]


def _same(a: Any, b: Any) -> bool:
    """``a == b`` as a list compares its items (identity first)."""
    return a is b or bool(a == b)


def _step(actual: Any, expected: Any) -> Union[str, _Step]:
    """Where two containers first differ: the step to the differing items, or a sentence when
    they differ in their keys, items or length (``""`` when they are not containers)."""
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        for index, (a, e) in enumerate(zip(actual, expected)):
            if not _same(a, e):
                return f"[{index}]", a, e
        if len(actual) != len(expected):
            return f"expected {len(expected)} items, got {len(actual)}"
    elif isinstance(actual, Mapping) and isinstance(expected, Mapping):
        missing = [key for key in expected if key not in actual]
        if missing:
            return f"key {render_value(missing[0])} is missing"
        extra = [key for key in actual if key not in expected]
        if extra:
            return f"unexpected key {render_value(extra[0])}"
        for key in expected:
            if not _same(actual[key], expected[key]):
                return f"[{render_value(key)}]", actual[key], expected[key]
    elif isinstance(actual, (set, frozenset)) and isinstance(expected, (set, frozenset)):
        missing, extra = list(expected - actual), list(actual - expected)
        parts = [f"missing {render_value(missing[0])}"] if missing else []
        parts += [f"unexpected {render_value(extra[0])}"] if extra else []
        return ", ".join(parts)
    return ""


def _difference(actual: Any, expected: Any, units: Optional[str]) -> str:
    """Where two values that look the same in a detail first differ, such as
    ``first difference at [25]: expected 3.3, got 3.9``, or ``""``. Never raises."""
    try:
        return _find_difference(actual, expected, units)
    except Exception:  # a container whose items cannot be compared or looked up
        return ""


def _find_difference(actual: Any, expected: Any, units: Optional[str]) -> str:
    path = ""
    for _ in range(_DIFFERENCE_DEPTH):
        step = _step(actual, expected)
        if isinstance(step, str):
            if step:
                return f"at {path}: {step}" if path else step
            break
        key, actual, expected = step
        path += key
    where = [path] if path else []
    shown_actual, shown_expected = value_pair(actual, expected, units)
    if shown_actual == shown_expected:  # long text or a long number: show a window
        if isinstance(actual, int) and isinstance(expected, int):
            actual, expected, kind = safe_str(actual), safe_str(expected), "digit"
        elif isinstance(actual, (str, bytes)) and type(actual) is type(expected):
            kind = "index"
        elif path:  # items that look alike but differ, such as two NaNs
            kind = ""
        else:
            return ""
        if kind:
            index = next(
                (i for i, (a, e) in enumerate(zip(actual, expected)) if a != e),
                min(len(actual), len(expected)),
            )
            low, high = max(index - _WINDOW, 0), index + _WINDOW
            if kind == "digit":
                shown_actual, shown_expected = actual[low:high], expected[low:high]
            else:
                shown_actual = render_value(actual[low:high])
                shown_expected = render_value(expected[low:high])
            where.append(f"{kind} {index}")
    note = _nan_note(_NAN_EQUALITY, actual, expected)
    location = f" at {', '.join(where)}" if where else ""
    return f"first difference{location}: expected {shown_expected}, got {shown_actual}{note}"


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
            "description": f"{_subject(name)} == {render_value(expected, units)}",
            "actual": actual,
            "expected": expected,
            "units": units,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return d["actual"] == d["expected"]

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        units = d.get("units")
        if passed:
            return f"{value(d['actual'], units)} == {value(d['expected'], units)}"
        actual, expected = value_pair(d["actual"], d["expected"], units)
        note = _nan_note(_NAN_EQUALITY, d["actual"], d["expected"])
        if actual == expected:  # long values that differ past what is shown
            difference = _difference(d["actual"], d["expected"], units)
            note += f"; {difference}" if difference else ""
        return f"expected {expected}, got {actual}{note}"


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
            "description": f"{_subject(name)} != {render_value(expected, units)}",
            "actual": actual,
            "expected": expected,
            "units": units,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return d["actual"] != d["expected"]

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        units = d.get("units")
        actual, expected = value_pair(d["actual"], d["expected"], units)
        if passed:
            return f"{actual} ≠ {expected}{_nan_note(_NAN_EQUALITY, d['actual'], d['expected'])}"
        return f"expected ≠ {expected}, got {actual}"


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
            "description": f"{_subject(name)} == {render_value(expected, units)} {tolerance}",
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
        if passed:
            return f"{actual} == {target}"
        note = _nan_note(_NAN_EQUALITY, d["actual"], d["expected"])
        return f"expected {target}, got {actual}{note}"

    def margin(self, d: Mapping[str, Any]) -> Optional[Union[float, Fraction]]:
        """The tolerance left: the larger one given, less the distance to ``expected``. In
        floats when one of the numbers is a float, as the verdict is, else exact."""
        given = {
            key: plain_number(d[key])
            for key in ("actual", "expected", "abs_tol", "rel_tol")
            if d.get(key) is not None
        }
        in_floats = any(isinstance(each, float) for each in given.values())
        values: Dict[str, Any] = {
            key: float(each) if in_floats else Fraction(each) for key, each in given.items()
        }
        distance = abs(values["actual"] - values["expected"])
        tolerances = []
        if "abs_tol" in values:
            tolerances.append(values["abs_tol"])
        if "rel_tol" in values:
            tolerances.append(values["rel_tol"] * abs(values["expected"]))
        result: Union[float, Fraction] = max(tolerances) - distance
        return result


# ---------------------------------------------------------------------------
# Ordering & range
# ---------------------------------------------------------------------------


_TEXT = (str, bytes, bytearray)


def _refuse_text(actual: Any, *limits: Any) -> None:
    """Refuse to order text by text: ``"100" > "20"`` is False, because strings compare letter
    by letter. Text against any other type compares on that type's terms (a version object
    parses the string) or raises, so it is left to the comparison."""
    if isinstance(actual, _TEXT) and any(isinstance(limit, _TEXT) for limit in limits):
        raise TypeError(
            f"{type(actual).__name__} values are compared as text, not as numbers; "
            "convert readings with float() first"
        )


def _ordered(compare: Callable[[], Any], *operands: Any) -> Any:
    """Run *compare*; when text cannot be ordered against a number, say how to fix it."""
    try:
        return compare()
    except TypeError as exc:
        if any(isinstance(operand, _TEXT) for operand in operands):
            raise TypeError(f"{exc}; convert text readings with float() first") from exc
        raise


class _Ordering(CheckType):
    symbol: ClassVar[str]
    compare_with: ClassVar[Callable[[Any, Any], Any]]
    #: ``1`` when the value must be above the threshold, ``-1`` when below.
    side: ClassVar[int]

    @classmethod
    def _build(
        cls, actual: Any, threshold: Any, name: str, units: Optional[str]
    ) -> CheckDescriptor:
        require_name(name, cls.check_type)
        return {
            "check_type": cls.check_type,
            "name": name,
            "description": f"{_subject(name)} {cls.symbol} {render_value(threshold, units)}",
            "actual": actual,
            "threshold": threshold,
            "units": units,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        actual, threshold = d["actual"], d["threshold"]
        _refuse_text(actual, threshold)
        compare_with = type(self).compare_with
        return _ordered(lambda: compare_with(actual, threshold), actual, threshold)

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        units = d.get("units")
        actual, threshold = value_pair(d["actual"], d["threshold"], units)
        if passed:
            return f"{actual} {self.symbol} {threshold}"
        note = _nan_note(_NAN_ORDERING, d["actual"], d["threshold"])
        return f"expected {self.symbol} {threshold}, got {actual}{note}"

    def margin(self, d: Mapping[str, Any]) -> Optional[Union[float, Fraction]]:
        return (exact_number(d["actual"]) - exact_number(d["threshold"])) * self.side


class Greater(_Ordering):
    check_type = "greater"
    symbol = ">"
    compare_with = operator.gt
    side = 1

    @staticmethod
    def build(
        actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        return Greater._build(actual, threshold, name, units)


class GreaterEqual(_Ordering):
    check_type = "greater_equal"
    symbol = ">="
    compare_with = operator.ge
    side = 1

    @staticmethod
    def build(
        actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        return GreaterEqual._build(actual, threshold, name, units)


class Less(_Ordering):
    check_type = "less"
    symbol = "<"
    compare_with = operator.lt
    side = -1

    @staticmethod
    def build(
        actual: Any, threshold: float, *, name: str, units: Optional[str] = None
    ) -> CheckDescriptor:
        return Less._build(actual, threshold, name, units)


class LessEqual(_Ordering):
    check_type = "less_equal"
    symbol = "<="
    compare_with = operator.le
    side = -1

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
        lo, hi = render_value(low, units), render_value(high, units)
        bounds = f"[{lo}, {hi}]" if inclusive else f"({lo}, {hi})"
        return {
            "check_type": "between",
            "name": name,
            "description": f"{_subject(name)} ∈ {bounds}",
            "actual": actual,
            "low": low,
            "high": high,
            "inclusive": inclusive,
            "units": units,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        actual, low, high = d["actual"], d["low"], d["high"]
        _refuse_text(actual, low, high)
        if d.get("inclusive", True):
            return _ordered(lambda: low <= actual <= high, actual, low, high)
        return _ordered(lambda: low < actual < high, actual, low, high)

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        units = d.get("units")
        actual, low = value_pair(d["actual"], d["low"], units)
        tagged, high = value_pair(d["actual"], d["high"], units)
        actual = max(actual, tagged, key=len)  # with its type when it looks like either bound
        bounds = f"[{low}, {high}]" if d.get("inclusive", True) else f"({low}, {high})"
        if passed:
            return f"{actual} ∈ {bounds}"
        note = _nan_note(_NAN_ORDERING, d["actual"], d["low"], d["high"])
        return f"expected {bounds}, got {actual}{note}"

    def margin(self, d: Mapping[str, Any]) -> Optional[Union[float, Fraction]]:
        """The distance to the nearer bound."""
        actual = exact_number(d["actual"])
        return min(actual - exact_number(d["low"]), exact_number(d["high"]) - actual)


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
            "description": f"{_subject(name)} {cls.statement}",
            "actual": actual,
        }


def _truth_detail(d: Mapping[str, Any], passed: bool, wanted: bool) -> str:
    """``True``, or the value and how it tests, e.g. ``'0' (truthy)``: a string reply such as
    ``"0"`` is truthy, which ``bool()`` alone would hide. How it tests comes from the verdict,
    so a value whose truth changes between reads is not read again."""
    actual = d["actual"]
    shown = render_value(actual)
    if actual is True or actual is False or d.get("error") is not None:
        truth = ""  # a bool shows itself; an error says why bool() failed
    else:
        truth = " (truthy)" if passed == wanted else " (falsy)"
    if passed:
        return f"{shown}{truth}"
    return f"expected {wanted}, got {shown}{truth}"


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
        return _truth_detail(d, passed, True)


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
        return _truth_detail(d, passed, False)


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
        return "None" if passed else f"expected None, got {render_value(d['actual'])}"


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
        return "not None" if passed else f"expected not None, got {render_value(d['actual'])}"


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
            "description": f"{_subject(name)} contains {render_value(needle)}",
            "haystack": haystack,
            "needle": needle,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return d["needle"] in d["haystack"]

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        needle = render_value(d["needle"])
        if passed:
            return f"contains {needle}"
        return f"expected to contain {needle}, got {render_value(d['haystack'])}"


class NotContains(CheckType):
    check_type = "not_contains"

    @staticmethod
    def build(haystack: Any, needle: Any, *, name: str) -> CheckDescriptor:
        require_name(name, "not_contains")
        return {
            "check_type": "not_contains",
            "name": name,
            "description": f"{_subject(name)} does not contain {render_value(needle)}",
            "haystack": haystack,
            "needle": needle,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return d["needle"] not in d["haystack"]

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        needle = render_value(d["needle"])
        if passed:
            return f"does not contain {needle}"
        return f"expected to not contain {needle}, got {render_value(d['haystack'])}"


#: Flags shown after a pattern, as in ``/v\d+/i``.
_FLAG_LETTERS = (
    (re.ASCII, "a"),
    (re.IGNORECASE, "i"),
    (re.LOCALE, "L"),
    (re.MULTILINE, "m"),
    (re.DOTALL, "s"),
    (re.VERBOSE, "x"),
)


def _regex(pattern: Any, flags: Any) -> str:
    """A pattern as ``/source/flags``."""
    source = render_text(pattern) if isinstance(pattern, str) else render_value(pattern)
    letters = ""
    if isinstance(flags, int):
        letters = "".join(letter for flag, letter in _FLAG_LETTERS if flags & flag)
    return f"/{source}/{letters}"


class Matches(CheckType):
    check_type = "matches"

    @staticmethod
    def build(actual: Any, pattern: Union[str, "re.Pattern[str]"], *, name: str) -> CheckDescriptor:
        require_name(name, "matches")
        source: Any = pattern
        flags = 0
        if isinstance(pattern, re.Pattern):
            source, flags = pattern.pattern, int(pattern.flags)
            if isinstance(source, str):
                flags &= ~int(re.UNICODE)  # implied for a str pattern
        return {
            "check_type": "matches",
            "name": name,
            "description": f"{_subject(name)} matches {_regex(source, flags)}",
            "actual": actual,
            "pattern": source,
            "flags": flags,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        pattern, flags = d["pattern"], d.get("flags") or 0
        if isinstance(pattern, re.Pattern):  # a hand-built descriptor: it has its own flags
            return pattern.search(d["actual"]) is not None
        return re.search(pattern, d["actual"], flags) is not None

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        pattern = _regex(d["pattern"], d.get("flags"))
        if passed:
            return f"matches {pattern}"
        return f"expected to match {pattern}, got {render_value(d['actual'])}"


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
            "description": f"{_subject(name)} is instance of {display}",
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
    # Only the length matters: a record keeps a preview of a large value, not all of it.
    snapshot_limits: ClassVar[Dict[str, int]] = {"actual": 100}

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
            "description": f"{_subject(name)} has length {render_value(expected)}",
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
            return f"length {render_value(expected)}"
        return (
            f"expected length {render_value(expected)}, "
            f"got length {render_value(d.get('actual_length'))}"
        )


class Fail(CheckType):
    check_type = "fail"

    @staticmethod
    def build(msg: str, *, name: Optional[str] = None) -> CheckDescriptor:
        resolved_name = name if name is not None else safe_str(msg)
        require_name(resolved_name, "fail")
        return {
            "check_type": "fail",
            "name": resolved_name,
            "description": f"FAIL: {render_text(msg, _MESSAGE_LIMIT)}",
            "msg": msg,
        }

    def compare(self, d: Mapping[str, Any]) -> Any:
        return False

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        return f"FAIL: {render_text(d.get('msg', ''), _MESSAGE_LIMIT)}"


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
