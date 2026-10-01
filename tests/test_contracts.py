"""Contracts every check type keeps, driven by the check-type registry.

For every registered type there are passing, failing and hostile examples. Each one is built
with the module-level ``verify`` and recorded with the fixture's, and must:

- record a JSON-safe result with the required keys and the phase it was made in;
- get the same verdict from the fixture as from ``verify.evaluate()``, before and after a
  JSON round-trip of the record;
- render its detail, the failure summary and ``ChecksFailedError`` without raising;
- keep its record unchanged by rendering, re-evaluation and later mutation of the values.

A new check type fails ``test_every_check_type_has_examples`` until it gets examples here.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Callable, List, NamedTuple, Optional

import pytest

from pytest_verify import ChecksFailedError
from pytest_verify import verify as checks
from pytest_verify._checks import REGISTRY, render_detail
from pytest_verify._exceptions import format_summary
from pytest_verify._run import Run, recording_verify


class Hostile:
    """Every operation a check might apply to a value raises."""

    def _boom(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("hostile value")

    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = _boom  # type: ignore[assignment]
    __bool__ = __len__ = __iter__ = __contains__ = __float__ = _boom
    __repr__ = __str__ = __format__ = _boom  # type: ignore[assignment]
    __hash__ = None  # type: ignore[assignment]


class Ambiguous:
    """Like a numpy array: comparisons return something with no truth value."""

    def __eq__(self, other: object) -> Any:
        return Ambiguous()

    __ne__ = __eq__  # type: ignore[assignment]

    def __bool__(self) -> bool:
        raise ValueError("The truth value of an array with more than one element is ambiguous")

    def __repr__(self) -> str:
        return "array([1, 2])"


def _recursive() -> List[Any]:
    value: List[Any] = [1]
    value.append(value)
    return value


class Example(NamedTuple):
    check_type: str
    label: str
    build: Callable[[Any], Any]
    #: The expected verdict; ``None`` when only the fixture/evaluate agreement matters.
    passed: Optional[bool]
    #: Whether the check must fail with an ``error`` note.
    error: bool = False


EXAMPLES = [
    Example("equal", "pass", lambda v: v.equal([1, 2], [1, 2], name="eq"), True),
    Example("equal", "fail", lambda v: v.equal(1, 2, name="eq", units="V"), False),
    Example("equal", "ambiguous", lambda v: v.equal(Ambiguous(), 1, name="eq"), False, True),
    Example("equal", "hostile", lambda v: v.equal(Hostile(), 1, name="eq"), False, True),
    Example("equal", "nan", lambda v: v.equal(float("nan"), float("nan"), name="eq"), False),
    Example("equal", "recursive", lambda v: v.equal(_recursive(), [1], name="eq"), None),
    Example("not_equal", "pass", lambda v: v.not_equal("a", "b", name="ne"), True),
    Example("not_equal", "fail", lambda v: v.not_equal(b"x", b"x", name="ne"), False),
    Example("not_equal", "hostile", lambda v: v.not_equal(Hostile(), 1, name="ne"), False, True),
    Example("approx", "pass",
            lambda v: v.approx(3.31, 3.3, abs_tol=0.05, name="ap", units="V"), True),
    Example("approx", "fail", lambda v: v.approx(10, 9, rel_tol=0.01, name="ap"), False),
    Example("approx", "decimal",
            lambda v: v.approx(Decimal("1.05"), Decimal(1), abs_tol=Decimal("0.1"), name="ap"),
            True),
    Example("approx", "nan", lambda v: v.approx(float("nan"), 1.0, abs_tol=1, name="ap"), False),
    Example("approx", "infinite",
            lambda v: v.approx(float("inf"), float("inf"), abs_tol=1, name="ap"), None),
    Example("approx", "hostile",
            lambda v: v.approx(Hostile(), 1.0, abs_tol=1, name="ap"), False, True),
    Example("approx", "string", lambda v: v.approx("1.0", 1.0, abs_tol=1, name="ap"), False, True),
    Example("greater", "pass", lambda v: v.greater(5, 1, name="gt", units="Mbps"), True),
    Example("greater", "fail", lambda v: v.greater(1, 1, name="gt"), False),
    Example("greater", "none", lambda v: v.greater(None, 1, name="gt"), False, True),
    Example("greater", "hostile", lambda v: v.greater(Hostile(), 1, name="gt"), False, True),
    Example("greater_equal", "pass", lambda v: v.greater_equal(1, 1, name="ge"), True),
    Example("greater_equal", "fail", lambda v: v.greater_equal(0.5, 1, name="ge"), False),
    Example("greater_equal", "hostile",
            lambda v: v.greater_equal(Hostile(), 1, name="ge"), False, True),
    Example("less", "pass", lambda v: v.less(-1, 0, name="lt"), True),
    Example("less", "fail", lambda v: v.less(float("inf"), 0, name="lt"), False),
    Example("less", "hostile", lambda v: v.less(Hostile(), 1, name="lt"), False, True),
    Example("less_equal", "pass", lambda v: v.less_equal(0, 0, name="le"), True),
    Example("less_equal", "fail", lambda v: v.less_equal(Decimal(2), 1, name="le"), False),
    Example("less_equal", "hostile", lambda v: v.less_equal(Hostile(), 1, name="le"), False, True),
    Example("between", "pass", lambda v: v.between(0.3, 0.1, 0.5, name="bt", units="A"), True),
    Example("between", "fail",
            lambda v: v.between(0.5, 0.1, 0.5, inclusive=False, name="bt"), False),
    Example("between", "nan", lambda v: v.between(float("nan"), 0, 1, name="bt"), False),
    Example("between", "hostile", lambda v: v.between(Hostile(), 0, 1, name="bt"), False, True),
    Example("true", "pass", lambda v: v.is_true([0], name="t"), True),
    Example("true", "fail", lambda v: v.is_true("", name="t"), False),
    Example("true", "hostile", lambda v: v.is_true(Hostile(), name="t"), False, True),
    Example("false", "pass", lambda v: v.is_false(0, name="f"), True),
    Example("false", "fail", lambda v: v.is_false({1}, name="f"), False),
    Example("false", "ambiguous", lambda v: v.is_false(Ambiguous(), name="f"), False, True),
    Example("is_none", "pass", lambda v: v.is_none(None, name="n"), True),
    Example("is_none", "fail", lambda v: v.is_none(Hostile(), name="n"), False),
    Example("is_not_none", "pass", lambda v: v.is_not_none(Hostile(), name="nn"), True),
    Example("is_not_none", "fail", lambda v: v.is_not_none(None, name="nn"), False),
    Example("contains", "pass", lambda v: v.contains("hello world", "world", name="c"), True),
    Example("contains", "fail", lambda v: v.contains({"a": 1}, "b", name="c"), False),
    Example("contains", "hostile", lambda v: v.contains(Hostile(), 1, name="c"), False, True),
    Example("contains", "not a container", lambda v: v.contains(5, 1, name="c"), False, True),
    Example("not_contains", "pass", lambda v: v.not_contains([1, 2], 3, name="nc"), True),
    Example("not_contains", "fail", lambda v: v.not_contains("abc", "b", name="nc"), False),
    Example("not_contains", "hostile",
            lambda v: v.not_contains(Hostile(), 1, name="nc"), False, True),
    Example("matches", "pass", lambda v: v.matches("v1.2.3", r"\d+\.\d+", name="m"), True),
    Example("matches", "fail", lambda v: v.matches("abc", r"^\d+$", name="m"), False),
    Example("matches", "not a string", lambda v: v.matches(12, r"\d", name="m"), False, True),
    Example("matches", "huge", lambda v: v.matches("x" * 100_000, "y", name="m"), False),
    Example("is_instance", "pass", lambda v: v.is_instance({}, dict, name="i"), True),
    Example("is_instance", "fail", lambda v: v.is_instance(1, (str, bytes), name="i"), False),
    Example("is_instance", "hostile", lambda v: v.is_instance(Hostile(), Hostile, name="i"), True),
    Example("length", "pass", lambda v: v.length([1, 2, 3], 3, name="len"), True),
    Example("length", "fail", lambda v: v.length("ab", 3, name="len"), False),
    Example("length", "no length", lambda v: v.length(5, 1, name="len"), False, True),
    Example("length", "hostile", lambda v: v.length(Hostile(), 1, name="len"), False, True),
    Example("fail", "always", lambda v: v.fail("unreachable state"), False),
    Example("fail", "hostile message", lambda v: v.fail(Hostile(), name="f"), False),
    Example("all_satisfy", "pass",
            lambda v: v.all_satisfy([1, 2], lambda x: v.greater(x, 0, name=f"x{x}"), name="all"),
            True),
    Example("all_satisfy", "fail",
            lambda v: v.all_satisfy([1, -2], lambda x: v.greater(x, 0, name=f"x{x}"), name="all"),
            False),
    Example("all_satisfy", "empty",
            lambda v: v.all_satisfy([], lambda x: v.fail("never"), name="all"), True),
    Example("all_satisfy", "hostile items",
            lambda v: v.all_satisfy(Hostile(), lambda x: v.fail("never"), name="all"), False, True),
    Example("all_satisfy", "factory forgot return",
            lambda v: v.all_satisfy([1], lambda x: None, name="all"), False, True),
    Example("conditional", "pass",
            lambda v: v.conditional(1, cases={1: v.equal(1, 1, name="one")}, name="cd"), True),
    Example("conditional", "lazy fail",
            lambda v: v.conditional("b", cases={"a": lambda: v.fail("a"),
                                                "b": lambda: v.less(5, 1, name="b")},
                                    name="cd"),
            False),
    Example("conditional", "default",
            lambda v: v.conditional(3, cases={}, default=v.is_true(1, name="d"), name="cd"), True),
    Example("conditional", "no match",
            lambda v: v.conditional(Hostile(), cases={1: v.fail("x")}, name="cd"), False),
    Example("conditional", "lazy raises",
            lambda v: v.conditional(1, cases={1: lambda: 1 / 0}, name="cd"), False, True),
    Example("guard", "pass",
            lambda v: v.guard([(False, "a", v.fail("a")), (True, "b", v.is_none(None, name="b"))],
                              name="g"),
            True),
    Example("guard", "lazy fail",
            lambda v: v.guard([(lambda: True, "on", lambda: v.equal(1, 2, name="on"))],
                              default=lambda: v.fail("never"), name="g"),
            False),
    Example("guard", "no match", lambda v: v.guard([(0, "a", v.fail("a"))], name="g"), False),
    Example("guard", "hostile condition",
            lambda v: v.guard([(Hostile(), "a", v.fail("a"))], name="g"), False, True),
]


def _ids(example: Example) -> str:
    return f"{example.check_type}-{example.label}"


def _record(example: Example) -> Any:
    run = Run()
    run.phase = "call"
    record = example.build(recording_verify(run))
    assert run.records == [record], "a check must be recorded once, at the top level"
    return record


def test_every_check_type_has_examples():
    covered = {example.check_type for example in EXAMPLES}
    assert covered == set(REGISTRY)
    for check_type in REGISTRY:
        verdicts = {e.passed for e in EXAMPLES if e.check_type == check_type}
        assert False in verdicts, f"{check_type} needs a failing example"


@pytest.mark.parametrize("example", EXAMPLES, ids=_ids)
def test_recorded_result(example: Example):
    record = _record(example)
    assert record["check_type"] == example.check_type
    assert isinstance(record["name"], str)
    assert isinstance(record["description"], str) and record["description"]
    assert isinstance(record["detail"], str)
    assert record["phase"] == "call"
    assert isinstance(record["passed"], bool)
    if example.passed is not None:
        assert record["passed"] is example.passed
    assert ("error" in record) is example.error
    json.dumps(record, allow_nan=False)


@pytest.mark.parametrize("example", EXAMPLES, ids=_ids)
def test_fixture_and_evaluate_agree(example: Example):
    record = _record(example)
    built = example.build(checks)
    assert checks.evaluate(built) is record["passed"]
    assert checks.evaluate(record) is record["passed"]
    assert checks.evaluate(json.loads(json.dumps(record))) is record["passed"]
    [detailed] = checks.evaluate_detailed(built)
    assert detailed["passed"] is record["passed"]
    assert ("error" in detailed) is example.error


@pytest.mark.parametrize("example", EXAMPLES, ids=_ids)
def test_rendering_never_raises_or_mutates(example: Example):
    record = _record(example)
    frozen = json.dumps(record, sort_keys=True)
    for result in (record, example.build(checks)):
        assert isinstance(render_detail(result, record["passed"]), str)
        assert isinstance(format_summary([result]), str)
        assert isinstance(str(ChecksFailedError([result])), str)
    checks.evaluate(record)
    checks.evaluate_detailed(record)
    assert json.dumps(record, sort_keys=True) == frozen


class Mutable(NamedTuple):
    label: str
    build: Callable[[Any, List[Any]], Any]


MUTABLES = [
    Mutable("equal", lambda v, data: v.equal(data, [1, 2], name="eq")),
    Mutable("contains", lambda v, data: v.contains(data, 2, name="c")),
    Mutable("length", lambda v, data: v.length(data, 2, name="len")),
    Mutable("all_satisfy",
            lambda v, data: v.all_satisfy(data, lambda x: v.greater(x, 0, name="x"), name="all")),
    Mutable("conditional",
            lambda v, data: v.conditional(1, cases={1: v.equal(data, [1, 2], name="eq")},
                                          name="cd")),
    Mutable("guard", lambda v, data: v.guard([(data, "has data", v.length(data, 2, name="n"))],
                                             name="g")),
]


@pytest.mark.parametrize("mutable", MUTABLES, ids=lambda m: m.label)
def test_later_mutation_changes_nothing(mutable: Mutable):
    run = Run()
    run.phase = "call"
    data = [1, 2]
    record = mutable.build(recording_verify(run), data)
    frozen = json.dumps(record, sort_keys=True)
    assert record["passed"] is True
    data.append(-3)
    data[0] = "changed"
    assert json.dumps(record, sort_keys=True) == frozen
    assert checks.evaluate(record) is True
    assert "changed" not in format_summary([record])
