"""Documented usage that must type-check under ``mypy --strict`` (M-16).

CI runs ``mypy --strict pytest_verify tests/typing_usage.py``. The ``# type: ignore[...]``
lines are misuse that mypy must keep rejecting: with ``--strict`` an ignore that is no longer
needed is itself an error. ``tests/test_typing.py`` also runs these functions, so the
documented examples are known to work at runtime too.
"""
from __future__ import annotations

import enum
from typing import Any, List

import pytest

from pytest_verify import (
    CheckDescriptor,
    ChecksFailedError,
    GuardBranch,
    Verify,
    get_check_results,
    verify,
)


class Mode(enum.Enum):
    IDLE = 0
    ACTIVE = 1


def module_api() -> None:
    vout: CheckDescriptor = verify.approx(3.28, 3.3, abs_tol=0.05, name="Vout", units="V")
    ok: bool = verify.evaluate(vout, verify.greater(120, 100, name="Throughput", units="Mbps"))
    details: List[dict[str, Any]] = verify.evaluate_detailed(vout)
    assert ok and details[0]["passed"] is True

    verify.between(0.3, 0.1, 0.5, inclusive=False, name="I", units="A")
    verify.is_true(True, name="Alive")
    verify.contains("hello world", "world", name="Greeting")
    verify.matches("v1.2.3", r"^v\d+\.\d+\.\d+$", name="Version")
    verify.length([1, 2, 3], 3, name="Items")
    verify.fail("not implemented")

    # is_instance takes what isinstance() takes: a class or a tuple of classes.
    verify.is_instance({}, dict, name="Payload")
    verify.is_instance(1.5, (int, float), name="Number")

    # all_satisfy takes any iterable, generators included.
    verify.all_satisfy(
        (n for n in range(3)), lambda n: verify.greater_equal(n, 0, name=f"n={n}"), name="Gen"
    )

    # conditional cases may use int, str or enum keys.
    verify.conditional(
        1,
        cases={0: verify.equal(1, 1, name="Zero"), 1: verify.equal(2, 2, name="One")},
        name="IntKeys",
    )
    verify.conditional(
        Mode.ACTIVE,
        cases={Mode.IDLE: verify.is_true(True, name="Idle"), Mode.ACTIVE: verify.is_true(True, name="On")},
        default=verify.fail("unknown mode"),
        name="EnumKeys",
    )

    # guard conditions are truthy values, not only bools.
    readings: list[float] = []
    verify.guard(
        branches=[
            (readings, "has readings", verify.length(readings, 1, name="Readings")),
            (1, "fallback", verify.is_true(True, name="Fallback")),
        ],
        name="Guard",
    )

    # The recorded shape of a guard's branches.
    guarded = verify.guard(branches=[(True, "only", verify.is_none(None, name="N"))], name="G")
    branches: List[GuardBranch] = guarded.get("branches", [])
    assert branches[0]["label"] == "only"

    # Children (and guard conditions) can be callables, so only the selected one is built.
    lazy = verify.conditional(
        "fast",
        cases={"fast": lambda: verify.less(12, 20, name="Latency", units="ms")},
        default=lambda: verify.fail("unknown speed"),
        name="Speed",
    )
    verify.guard(
        branches=[(lambda: True, "powered", lambda: verify.is_true(True, name="Power"))],
        default=verify.fail("no power"),
        name="LazyGuard",
    )

    # check_type, name and description are always present.
    check_type: str = lazy["check_type"]
    description: str = lazy["description"]
    assert check_type == "conditional" and description == "Verify 'Speed' [mode=fast]"


def fixture_api(fixture: Verify, request: pytest.FixtureRequest) -> None:
    result = fixture.equal(200, 200, name="Status")
    passed: bool | None = result.get("passed")
    assert passed is True
    # A check built by a helper with the module-level verify can be recorded too.
    helper = fixture.record(verify.greater(5, 1, name="Helper"))
    assert helper.get("passed") is True
    recorded: List[CheckDescriptor] = get_check_results(request.node)
    assert [r["name"] for r in recorded][-2:] == ["Status", "Helper"]
    phase: str | None = recorded[-1].get("phase")
    assert phase == "call"


def error_api(error: ChecksFailedError) -> AssertionError:
    results: List[Any] = error.results
    assert results
    return error


def misuse() -> None:
    """Never called: each line must stay a type error."""
    verify.equal(1, 1)  # type: ignore[call-arg]
    verify.approx(1.0, 1.0, abs_tol="0.1", name="Tol")  # type: ignore[arg-type]
    verify.greater(1, 0, "positional name")  # type: ignore[call-arg]
    verify.guard(branches=[(True, verify.is_true(True, name="T"))], name="G")  # type: ignore[list-item]
    verify.conditional(1, cases={1: 5}, name="C")  # type: ignore[dict-item]
    verify.record("not a check")  # type: ignore[arg-type]
