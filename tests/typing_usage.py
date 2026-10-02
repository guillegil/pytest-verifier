"""Documented usage that must type-check under ``mypy --strict`` (M-16).

CI runs ``mypy --strict pytest_verifier tests/typing_usage.py``. The ``# type: ignore[...]``
lines are misuse that mypy must keep rejecting: with ``--strict`` an ignore that is no longer
needed is itself an error. ``tests/test_typing.py`` also runs these functions, so the
documented examples are known to work at runtime too.
"""
from __future__ import annotations

import enum
import re
from typing import Any, List

import pytest

import pytest_verifier
from pytest_verifier import (
    CheckDescriptor,
    ChecksFailedError,
    GuardBranch,
    Require,
    Verify,
    checks,
    get_check_results,
)


class Mode(enum.Enum):
    IDLE = 0
    ACTIVE = 1


def module_api() -> None:
    vout: CheckDescriptor = checks.approx(3.28, 3.3, abs_tol=0.05, name="Vout", units="V")
    ok: bool = checks.evaluate(vout, checks.greater(120, 100, name="Throughput", units="Mbps"))
    details: List[dict[str, Any]] = checks.evaluate_detailed(vout)
    assert ok and details[0]["passed"] is True

    checks.between(0.3, 0.1, 0.5, inclusive=False, name="I", units="A")
    checks.is_true(True, name="Alive")
    checks.contains("hello world", "world", name="Greeting")
    checks.matches("v1.2.3", r"^v\d+\.\d+\.\d+$", name="Version")
    checks.matches("V1.2", re.compile(r"^v\d", re.IGNORECASE), name="Compiled")
    checks.length([1, 2, 3], 3, name="Items")
    checks.fail("not implemented")

    # is_instance takes what isinstance() takes: a class or a tuple of classes.
    checks.is_instance({}, dict, name="Payload")
    checks.is_instance(1.5, (int, float), name="Number")

    # all_satisfy takes any iterable, generators included.
    checks.all_satisfy(
        (n for n in range(3)), lambda n: checks.greater_equal(n, 0, name=f"n={n}"), name="Gen"
    )

    # conditional cases may use int, str or enum keys.
    checks.conditional(
        1,
        cases={0: checks.equal(1, 1, name="Zero"), 1: checks.equal(2, 2, name="One")},
        name="IntKeys",
    )
    checks.conditional(
        Mode.ACTIVE,
        cases={Mode.IDLE: checks.is_true(True, name="Idle"), Mode.ACTIVE: checks.is_true(True, name="On")},
        default=checks.fail("unknown mode"),
        name="EnumKeys",
    )

    # guard conditions are truthy values, not only bools.
    readings: list[float] = []
    checks.guard(
        branches=[
            (readings, "has readings", checks.length(readings, 1, name="Readings")),
            (1, "fallback", checks.is_true(True, name="Fallback")),
        ],
        name="Guard",
    )

    # The recorded shape of a guard's branches.
    guarded = checks.guard(branches=[(True, "only", checks.is_none(None, name="N"))], name="G")
    branches: List[GuardBranch] = guarded.get("branches", [])
    assert branches[0]["label"] == "only"

    # Children (and guard conditions) can be callables, so only the selected one is built.
    lazy = checks.conditional(
        "fast",
        cases={"fast": lambda: checks.less(12, 20, name="Latency", units="ms")},
        default=lambda: checks.fail("unknown speed"),
        name="Speed",
    )
    checks.guard(
        branches=[(lambda: True, "powered", lambda: checks.is_true(True, name="Power"))],
        default=checks.fail("no power"),
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
    # A check built by a helper with pytest_verifier.checks can be recorded too.
    helper = fixture.record(checks.greater(5, 1, name="Helper"))
    assert helper.get("passed") is True
    recorded: List[CheckDescriptor] = get_check_results(request.node)
    assert [r["name"] for r in recorded][-2:] == ["Status", "Helper"]
    phase: str | None = recorded[-1].get("phase")
    assert phase == "call"
    location: str | None = recorded[-1].get("location")
    assert location is not None and location.startswith("tests/typing_usage.py:")
    # Required checks stop the test when they fail; both forms return the recorded check.
    link: CheckDescriptor = fixture.require.is_not_none(object(), name="Link")
    built: CheckDescriptor = fixture.require(checks.equal(1, 1, name="Built"))
    assert link.get("passed") is True and built.get("passed") is True
    assert _connect(fixture.require).get("passed") is True
    version: str = pytest_verifier.__version__
    assert version


def _connect(require: Require) -> CheckDescriptor:
    """A helper that receives ``verify.require``."""
    require.is_true(True, name="Powered")
    return require(checks.is_true(True, name="Link up"))


def error_api(error: ChecksFailedError) -> AssertionError:
    results: List[Any] = error.results
    assert results
    return error


def _by_name(text: str) -> CheckDescriptor:
    return checks.contains(text, "x", name=text)


def misuse() -> None:
    """Never called: each line must stay a type error."""
    checks.equal(1, 1)  # type: ignore[call-arg]
    checks.approx(1.0, 1.0, abs_tol="0.1", name="Tol")  # type: ignore[arg-type]
    checks.greater(1, 0, "positional name")  # type: ignore[call-arg]
    checks.guard(branches=[(True, checks.is_true(True, name="T"))], name="G")  # type: ignore[list-item]
    checks.conditional(1, cases={1: 5}, name="C")  # type: ignore[dict-item]
    checks.record("not a check")  # type: ignore[arg-type]
    checks.length(5, 1, name="Len")  # type: ignore[arg-type]
    checks.contains(5, 1, name="In")  # type: ignore[arg-type]
    checks.all_satisfy([1, 2], _by_name, name="Names")  # type: ignore[arg-type]
    checks.require("not a check")  # type: ignore[arg-type]
    checks.require.equal(1, 1)  # type: ignore[call-arg]
    from pytest_verifier import chekcs  # type: ignore[attr-defined]  # noqa: F401
    pytest_verifier.get_check_result  # type: ignore[attr-defined]  # noqa: B018


def deprecated_alias() -> CheckDescriptor:
    """``pytest_verifier.verify`` keeps the types of ``checks``; it warns at runtime."""
    from pytest_verifier import verify as alias

    return alias.equal(1, 1, name="alias")
