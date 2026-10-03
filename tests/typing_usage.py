"""Documented usage that must type-check under ``mypy --strict`` (M-16).

CI runs ``mypy --strict pytest_verifier tests/typing_usage.py``. The ``# type: ignore[...]``
lines are misuse that mypy must keep rejecting: with ``--strict`` an ignore that is no longer
needed is itself an error. ``tests/test_typing.py`` also runs these functions, so the
documented examples are known to work at runtime too.
"""
from __future__ import annotations

import datetime
import enum
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import pytest

import pytest_verifier
from pytest_verifier import (
    CheckDescriptor,
    ChecksFailedError,
    GuardBranch,
    LimitRow,
    Raises,
    Require,
    Verify,
    checks,
    get_check_results,
    load_limits,
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
    # Sections group the checks recorded in a block; records carry the titles.
    with fixture.section("3V3"), fixture.require.section("Load"):
        grouped = fixture.approx(3.31, 3.3, abs_tol=0.05, name="Vout", units="V")
    titles: List[str] | None = grouped.get("section")
    assert titles == ["3V3", "Load"]


class SetpointError(ValueError):
    """A DUT error with a field of its own."""

    def __init__(self, volts: float) -> None:
        super().__init__(f"{volts} V is out of range")
        self.volts = volts


def _set_voltage(volts: float) -> None:
    raise SetpointError(volts)


#: A limits table typed with the exported row type.
LIMITS: Dict[str, LimitRow] = {
    "Vout": {"expected": 3.3, "abs_tol": 0.05, "units": "V"},
    "Ripple": {"high": 0.05, "units": "V"},
    "Iq": {"low": 0.1, "high": 0.5, "inclusive": False, "units": "A"},
    "FW": {"check": "matches", "pattern": "^v2"},
}


def lab_api(fixture: Verify, folder: Path) -> None:
    """``verify.raises``, ``eventually``/``stable``, ``limits`` and ``load_limits`` (0.10.0)."""
    # raises: the block's exception is typed as the expected class.
    with fixture.raises(SetpointError, match="out of range", name="Reject 7 V") as raised:
        _set_voltage(7)
    block: Raises[SetpointError] = raised
    error: Optional[SetpointError] = block.value
    assert error is not None and error.volts == 7
    kind: Optional[Type[SetpointError]] = raised.type
    assert kind is SetpointError
    rejected: CheckDescriptor = raised.check
    assert rejected.get("passed") is True
    readings: Dict[str, float] = {}
    with fixture.raises((KeyError, IndexError), name="Lookup") as lookup:
        readings["missing"]  # noqa: B018
    found: Optional[LookupError] = lookup.value
    assert isinstance(found, KeyError)

    # eventually/stable: a lambda sample, durations in seconds or as a timedelta.
    settled: CheckDescriptor = fixture.eventually(
        lambda: fixture.less(31.5, 40, name="Temperature", units="C"),
        timeout=datetime.timedelta(seconds=0),
        interval=0.05,
        name="Cools down",
    )
    steady: CheckDescriptor = fixture.stable(
        lambda: fixture.approx(3.31, 3.3, abs_tol=0.05, name="Vout", units="V"),
        duration=0,
        interval=datetime.timedelta(milliseconds=50),
        name="Vout steady",
    )
    assert settled.get("passed") is True and steady.get("passed") is True
    built: CheckDescriptor = checks.eventually(
        lambda: checks.is_true(True, name="Ready"), timeout=0, name="Boots"
    )
    assert checks.evaluate(built)

    # limits: a typed table in, the checks by name out.
    made: Dict[str, CheckDescriptor] = fixture.limits(
        {"Vout": 3.31, "Ripple": 0.02, "Iq": 0.3, "FW": "v2.4"}, LIMITS
    )
    assert [check.get("passed") for check in made.values()] == [True] * 4
    partial = fixture.limits({"Vout": 3.31}, LIMITS, on_missing="ignore")
    assert list(partial) == ["Vout"]
    one_row: Dict[str, LimitRow] = {"Ripple": LIMITS["Ripple"]}
    required: Dict[str, CheckDescriptor] = fixture.require.limits({"Ripple": 0.01}, one_row)
    assert required["Ripple"].get("passed") is True

    # load_limits: a CSV file, selected by corner, with a column skipped.
    path = folder / "limits.csv"
    path.write_text(
        "name,corner,low,high,units,notes\n"
        "Vout,hot,3.2,3.4,V,wide\n"
        "Vout,,3.25,3.35,V,\n"
        "Ripple,cold,,0.05,V,\n",
        encoding="utf-8",
    )
    rows: Dict[str, LimitRow] = load_limits(path, select={"corner": "hot"}, columns={"notes": None})
    assert list(rows) == ["Vout"]
    row: LimitRow = rows["Vout"]
    source: Optional[str] = row.get("source")
    assert row.get("low") == 3.2 and source is not None and source.endswith("limits.csv:2")
    # Another corner gets the line that leaves it empty.
    cold = load_limits(str(path), select={"corner": "cold"}, columns={"Notes": None})
    loaded = fixture.limits({"Vout": 3.3, "Ripple": 0.01}, cold)
    assert loaded["Vout"].get("passed") is True and loaded["Vout"].get("low") == 3.25
    assert loaded["Ripple"].get("check_type") == "less_equal"


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
    checks.section(["3V3"])  # type: ignore[arg-type]
    checks.eventually(checks.less(1, 2, name="T"), timeout=1, name="E")  # type: ignore[arg-type]
    checks.stable(lambda: checks.fail("x"), duration="2", name="S")  # type: ignore[arg-type]
    checks.stable(lambda: checks.less(1, 2, name="T"), duration=1)  # type: ignore[call-arg]
    checks.limits({"Vout": 3.3}, LIMITS, on_missing="skip")  # type: ignore[arg-type]
    typo: LimitRow = {"hi": 0.05}  # type: ignore[typeddict-unknown-key]
    text_limit: LimitRow = {"abs_tol": "0.05"}  # type: ignore[typeddict-item]
    load_limits(5)  # type: ignore[arg-type]
    load_limits("limits.csv", columns={"Min": 0})  # type: ignore[dict-item]
    with checks.raises(ValueError, name="R") as raised:
        pass
    wrong: Optional[KeyError] = raised.value  # type: ignore[assignment]
    wrong_type: Optional[Type[KeyError]] = raised.type  # type: ignore[assignment]
    raised.value.args  # type: ignore[union-attr]  # noqa: B018
    checks.raises("ValueError", name="R")  # type: ignore[arg-type]
    checks.raises(ValueError)  # type: ignore[call-arg]
    from pytest_verifier import chekcs  # type: ignore[attr-defined]  # noqa: F401
    pytest_verifier.get_check_result  # type: ignore[attr-defined]  # noqa: B018


def deprecated_alias() -> CheckDescriptor:
    """``pytest_verifier.verify`` keeps the types of ``checks``; it warns at runtime."""
    from pytest_verifier import verify as alias

    return alias.equal(1, 1, name="alias")
