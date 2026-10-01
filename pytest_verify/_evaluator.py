"""Judging descriptors: the only place where user values are compared.

:func:`judge` never raises. Whatever a comparison returns is coerced to a real ``bool``, and an
exception (or a result whose truth value is ambiguous, such as a numpy array) becomes a failed
verdict with an error note.
"""
from __future__ import annotations

import numbers
import operator
import re
import time
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional, Tuple

from ._descriptors import (
    COMPOSITE_TYPES,
    CheckDescriptor,
    is_descriptor,
    qualified_type_name,
    select_case,
    _NO_CASE,
)
from ._render import describe_error, safe_repr

#: ``(passed, error)``: the verdict and, when the check could not be evaluated, why.
Verdict = Tuple[bool, Optional[str]]

_ORDERING: dict[str, Callable[[Any, Any], Any]] = {
    "greater": operator.gt,
    "greater_equal": operator.ge,
    "less": operator.lt,
    "less_equal": operator.le,
}


def judge(descriptor: Mapping[str, Any]) -> Verdict:
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
        return False, str(error)
    if descriptor.get("check_type") in COMPOSITE_TYPES:
        return _judge_composite(descriptor)
    try:
        result = _compare(descriptor)
    except Exception as exc:
        return False, describe_error(exc)
    return truth(result)


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


def selected_child(descriptor: Mapping[str, Any]) -> tuple[str, Any] | None:
    """Return ``(label, child)`` for the branch a guard/conditional selected, or ``None``.

    The stored ``matched_index``/``matched_case`` is authoritative; it is recomputed only when
    the field is missing (a hand-built descriptor).
    """
    check_type = descriptor.get("check_type")
    default = descriptor.get("default")
    if check_type == "guard":
        branches = descriptor.get("branches") or []
        if "matched_index" in descriptor:
            matched = descriptor.get("matched_index")
        else:
            matched = next((i for i, b in enumerate(branches) if b.get("condition")), None)
        if matched is not None:
            branch = branches[matched]
            return str(branch.get("label", "")), branch.get("check")
        return None if default is None else ("default", default)
    if check_type == "conditional":
        cases = descriptor.get("cases") or {}
        if "matched_case" in descriptor:
            key = descriptor.get("matched_case")
        else:
            found = select_case(descriptor.get("switch_value"), cases.keys())
            key = None if found is _NO_CASE else found
        if key is not None and key in cases:
            return str(key), cases[key]
        return None if default is None else ("default", default)
    return None


def _judge_composite(descriptor: Mapping[str, Any]) -> Verdict:
    try:
        if descriptor.get("check_type") == "all_satisfy":
            verdicts = [judge(child)[0] for child in descriptor.get("child_checks") or []]
            return all(verdicts), None
        selected = selected_child(descriptor)
    except Exception as exc:  # a malformed hand-built descriptor
        return False, describe_error(exc)
    if selected is None:
        return False, None
    passed, _child_error = judge(selected[1])
    return passed, None


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


def _number(value: Any) -> Any:
    """*value* if it is a real number, else its ``float()`` if it has one, like ``math.isclose``.

    Anything else, such as ``None``, a string or a list, raises ``TypeError`` instead of being
    compared with ``==``.
    """
    if isinstance(value, (numbers.Real, Decimal)):
        return value
    if hasattr(type(value), "__float__"):  # e.g. a numpy 0-d array
        return float(value)
    raise TypeError(f"approx compares numbers, got {type(value).__name__}")


def _instance_check(descriptor: Mapping[str, Any]) -> bool:
    stored = descriptor.get("instance_check")
    if isinstance(stored, bool):
        return stored
    # Hand-built descriptor without the build-time verdict: match qualified names along the MRO.
    mro = type(descriptor.get("actual")).__mro__
    qualified = descriptor.get("expected_types")
    if qualified:
        names = {qualified_type_name(cls) for cls in mro}
        return any(name in names for name in qualified)
    expected_name = descriptor["expected_type"]
    return any(cls.__name__ == expected_name for cls in mro)


def _compare(d: Mapping[str, Any]) -> Any:
    """Run the comparison for a non-composite descriptor and return its raw result."""
    check_type = d["check_type"]
    if check_type == "equal":
        return d["actual"] == d["expected"]
    if check_type == "not_equal":
        return d["actual"] != d["expected"]
    if check_type == "approx":
        return _approx(d["actual"], d["expected"], d.get("abs_tol"), d.get("rel_tol"))
    if check_type in _ORDERING:
        return _ORDERING[check_type](d["actual"], d["threshold"])
    if check_type == "between":
        actual, low, high = d["actual"], d["low"], d["high"]
        if d.get("inclusive", True):
            return low <= actual <= high
        return low < actual < high
    if check_type == "true":
        return bool(d["actual"]) is True
    if check_type == "false":
        return bool(d["actual"]) is False
    if check_type == "is_none":
        return d["actual"] is None
    if check_type == "is_not_none":
        return d["actual"] is not None
    if check_type == "contains":
        return d["needle"] in d["haystack"]
    if check_type == "not_contains":
        return d["needle"] not in d["haystack"]
    if check_type == "matches":
        return re.search(d["pattern"], d["actual"]) is not None
    if check_type == "is_instance":
        return _instance_check(d)
    if check_type == "length":
        length = d.get("actual_length")
        if length is None:
            length = len(d["actual"])
        return length == d["expected"]
    if check_type == "fail":
        return False
    raise ValueError(f"Unknown check_type: {check_type!r}")


def _require_descriptors(descriptors: tuple[Any, ...], function: str) -> None:
    for index, descriptor in enumerate(descriptors):
        if isinstance(descriptor, (list, tuple)):
            raise TypeError(
                f"{function}() takes descriptors as separate arguments; "
                f"use verify.{function}(*checks) to pass a list"
            )
        if not is_descriptor(descriptor):
            raise TypeError(
                f"{function}() argument {index} is not a check descriptor: "
                f"{type(descriptor).__name__}"
            )


def evaluate(*descriptors: CheckDescriptor) -> bool:
    """Evaluate one or more descriptors, returning ``True`` only if ALL pass.

    This is a pure function with no side effects — it does not mutate the
    descriptors or store results anywhere. Every descriptor is evaluated, and a check that
    cannot be evaluated (its comparison raises) counts as failed.
    """
    _require_descriptors(descriptors, "evaluate")
    verdicts = [judge(d)[0] for d in descriptors]
    return all(verdicts)


def evaluate_detailed(*descriptors: CheckDescriptor) -> list[dict[str, Any]]:
    """Evaluate descriptors and return detailed result dicts.

    Each result dict contains:
    - ``passed``: bool
    - ``details``: the original descriptor
    - ``seq``: 0-based sequence index
    - ``t``: timestamp of evaluation (seconds since epoch)
    - ``error``: why the check could not be evaluated (only present when it could not)
    """
    _require_descriptors(descriptors, "evaluate_detailed")
    results: list[dict[str, Any]] = []
    for seq, d in enumerate(descriptors):
        passed, error = judge(d)
        result: dict[str, Any] = {"passed": passed, "details": d, "seq": seq, "t": time.time()}
        if error is not None:
            result["error"] = error
        results.append(result)
    return results
