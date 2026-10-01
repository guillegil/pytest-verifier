from __future__ import annotations

from typing import Any, Iterable, Mapping

import pytest

from ._descriptors import approx_tolerance
from ._evaluator import judge, selected_child
from ._render import describe_error, safe_format, safe_repr, safe_str

# Comparison operators rendered for the ordering checks.
_ORDER_OPS = {"greater": ">", "greater_equal": ">=", "less": "<", "less_equal": "<="}


def _value(value: object, units: str | None) -> str:
    """Render a value with its optional unit suffix (e.g. ``3.3V``)."""
    return f"{safe_format(value)}{units or ''}"


def _range(result: Mapping[str, Any], units: str | None) -> str:
    """Render a ``between`` range with inclusive ``[]`` or exclusive ``()`` brackets."""
    low = _value(result["low"], units)
    high = _value(result["high"], units)
    return f"[{low}, {high}]" if result.get("inclusive", True) else f"({low}, {high})"


def _child_detail(child: Mapping[str, Any], passed: bool) -> str:
    stored = child.get("detail")
    return stored if isinstance(stored, str) else render_detail(child, passed)


def _detail(result: Mapping[str, Any], passed: bool) -> str:
    """Render the per-type ``expected … got …`` (failed) or compact (passed) clause.

    Follows the spec §7 table. ``passed`` selects which rendering to produce; for
    composite checks (``conditional``) it is the parent verdict, which equals the
    matched child's verdict.
    """
    check_type = result.get("check_type")
    units = result.get("units")

    if check_type == "equal":
        actual, expected = _value(result["actual"], units), _value(result["expected"], units)
        return f"{actual} == {expected}" if passed else f"expected {expected}, got {actual}"

    if check_type == "not_equal":
        actual, expected = _value(result["actual"], units), _value(result["expected"], units)
        return f"{actual} ≠ {expected}" if passed else f"expected ≠ {expected}, got {actual}"

    if check_type == "approx":
        actual = _value(result["actual"], units)
        expected = _value(result["expected"], units)
        tol = approx_tolerance(result.get("abs_tol"), result.get("rel_tol"), units)
        target = f"{expected} {tol}"
        return f"{actual} == {target}" if passed else f"expected {target}, got {actual}"

    if check_type in _ORDER_OPS:
        op = _ORDER_OPS[check_type]
        actual = _value(result["actual"], units)
        threshold = _value(result["threshold"], units)
        return f"{actual} {op} {threshold}" if passed else f"expected {op} {threshold}, got {actual}"

    if check_type == "between":
        actual = _value(result["actual"], units)
        rng = _range(result, units)
        return f"{actual} ∈ {rng}" if passed else f"expected {rng}, got {actual}"

    if check_type == "true":
        return "True" if passed else f"expected True, got {bool(result['actual'])}"

    if check_type == "false":
        return "False" if passed else f"expected False, got {bool(result['actual'])}"

    if check_type == "is_none":
        return "None" if passed else f"expected None, got {safe_repr(result['actual'])}"

    if check_type == "is_not_none":
        return "not None" if passed else f"expected not None, got {safe_repr(result['actual'])}"

    if check_type == "contains":
        needle = safe_repr(result["needle"])
        haystack = safe_repr(result["haystack"])
        return f"contains {needle}" if passed else f"expected to contain {needle}, got {haystack}"

    if check_type == "not_contains":
        needle = safe_repr(result["needle"])
        return (
            f"does not contain {needle}"
            if passed
            else f"expected to not contain {needle}, got {safe_repr(result['haystack'])}"
        )

    if check_type == "matches":
        pattern = safe_format(result["pattern"])
        actual = safe_repr(result["actual"])
        return f"matches /{pattern}/" if passed else f"expected to match /{pattern}/, got {actual}"

    if check_type == "is_instance":
        expected_type = result["expected_type"]
        if passed:
            return f"instance of {expected_type}"
        return f"expected instance of {expected_type}, got {type(result['actual']).__name__}"

    if check_type == "length":
        expected = result["expected"]
        if passed:
            return f"length {expected}"
        return f"expected length {expected}, got length {result.get('actual_length')}"

    if check_type == "all_satisfy":
        children = result.get("child_checks") or []
        total = len(children)
        if passed:
            return f"all {total} items pass"
        failed = sum(1 for child in children if judge(child)[0] is not True)
        return f"expected all {total} to pass, got {failed} failed"

    if check_type == "conditional":
        mode = f"mode={result.get('switch_label', safe_str(result.get('switch_value')))}"
        selected = selected_child(result)
        if selected is None:
            return f"[{mode} → no match]"
        child = selected[1]
        return f"[{mode} → {child.get('name', '')}] — {_child_detail(child, passed)}"

    if check_type == "guard":
        selected = selected_child(result)
        if selected is None:
            return "[→ no match]"
        label, child = selected
        return f"[→ {label}] — {_child_detail(child, passed)}"

    if check_type == "fail":
        return f"FAIL: {safe_format(result.get('msg', ''))}"

    # Fallback for any unknown check type: the canonical description, prefix-stripped.
    name = safe_str(result.get("name", ""))
    description = safe_str(result.get("description", ""))
    prefix = f"Verify '{name}' "
    return description[len(prefix):] if description.startswith(prefix) else description


def render_detail(result: Mapping[str, Any], passed: bool, error: str | None = None) -> str:
    """The detail clause of one check, never raising.

    A check that could not be evaluated gets its error appended, for example
    ``expected > 100, got None (TypeError: '>' not supported ...)``.
    """
    if error is None:
        error = result.get("error")
    try:
        text = _detail(result, passed)
    except Exception as exc:
        if error is None:
            return f"<detail unavailable: {describe_error(exc)}>"
        return f"error: {error}"
    return text if error is None else f"{text} ({error})"


def _line(marker: str, idx: int, result: Mapping[str, Any], passed: bool) -> str:
    name = safe_str(result.get("name", ""))
    # conditional/guard render `name [… → child] — …`, attaching their
    # bracket clause directly to the name without the `— ` separator.
    sep = " " if result.get("check_type") in ("conditional", "guard") else " — "
    stored = result.get("detail")
    detail = stored if isinstance(stored, str) else render_detail(result, passed)
    return f"  {marker} [{idx}] {name}{sep}{detail}"


def format_summary(results: Iterable[Mapping[str, Any]]) -> str:
    """The ``N of M checks failed`` summary of spec §7. Never raises."""
    results = list(results)
    failed = [(i, r) for i, r in enumerate(results) if r.get("passed") is not True]
    passed = [(i, r) for i, r in enumerate(results) if r.get("passed") is True]

    def line(marker: str, idx: int, result: Mapping[str, Any], is_passed: bool) -> str:
        try:
            return _line(marker, idx, result, is_passed)
        except Exception as exc:
            return f"  {marker} [{idx}] <check could not be rendered: {describe_error(exc)}>"

    lines: list[str] = [f"{len(failed)} of {len(results)} checks failed", ""]
    lines.extend(line("✗", idx, r, False) for idx, r in failed)
    if passed:
        lines.append("")
        lines.extend(line("✓", idx, r, True) for idx, r in passed)
    return "\n".join(lines)


class ChecksFailedError(AssertionError, pytest.fail.Exception):  # type: ignore[misc,name-defined]
    """Raised when one or more soft checks of a test failed.

    The ``verify`` fixture raises it at the end of the phase in which the checks were recorded:
    after the test body for checks made in fixtures' setup and in the test, and after teardown
    for checks made while fixtures are torn down. It is an ``AssertionError``, so
    ``pytest.raises``, ``xfail(raises=AssertionError)`` and rerun filters treat it like a
    failed ``assert``. pytest prints only its message, without a traceback.

    The message follows spec §7: a ``N of M checks failed`` header, then the
    failed checks (``✗``) before the passed checks (``✓``), each prefixed with
    its ``[seq]`` index in evaluation order, its name, and a per-type
    ``expected … got …`` (failed) or compact (passed) detail clause.

    Attributes:
        results: The check descriptors the summary was built from.
    """

    def __init__(self, results: Iterable[Mapping[str, Any]]) -> None:
        self.results = list(results)
        message = format_summary(self.results)
        AssertionError.__init__(self, message)
        # pytest.fail.Exception attributes: show only the message, not a traceback.
        self.msg = message
        self.pytrace = False

    def __str__(self) -> str:
        return self.msg

    def __repr__(self) -> str:
        failed = sum(1 for r in self.results if r.get("passed") is not True)
        return f"{type(self).__name__}({failed} of {len(self.results)} checks failed)"

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.results,), self.__dict__)


ChecksFailedError.__module__ = "pytest_verify"
