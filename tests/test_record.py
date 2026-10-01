"""``verify.record()``: record a check built elsewhere with the fixture."""
from __future__ import annotations

import json

import pytest

from pytest_verify import verify as checks
from pytest_verify._run import Run, recording_verify


def _recording():
    run = Run()
    run.phase = "call"
    return run, recording_verify(run)


def test_module_level_verify_cannot_record():
    with pytest.raises(RuntimeError, match="Request the 'verify' fixture"):
        checks.record(checks.equal(1, 1, name="a"))


@pytest.mark.parametrize("value", [None, 5, "equal", {"name": "no type"}, [1]])
def test_record_rejects_what_is_not_a_check(value):
    _, verify = _recording()
    with pytest.raises(TypeError, match="record\\(\\) argument must be a check descriptor"):
        verify.record(value)


def test_a_built_check_is_judged_and_recorded():
    run, verify = _recording()
    built = checks.greater(3, 5, name="speed", units="m/s")
    record = verify.record(built)
    assert "passed" not in built
    assert record is not built
    assert record["passed"] is False
    assert record["detail"] == "expected > 5m/s, got 3m/s"
    assert record["phase"] == "call"
    assert run.records == [record]


def test_a_recorded_check_is_returned_as_is():
    run, verify = _recording()
    record = verify.equal(1, 2, name="a")
    assert verify.record(record) is record
    assert run.records == [record]


def test_the_record_is_a_snapshot():
    run, verify = _recording()
    values = [1, 2]
    record = verify.record(checks.length(values, 2, name="n"))
    values.append(3)
    assert record["passed"] is True
    assert record["actual"] == [1, 2]
    json.dumps(run.records, allow_nan=False)


def test_a_built_composite_absorbs_its_recorded_children():
    run, verify = _recording()
    child = verify.equal(1, 2, name="child")
    record = verify.record(checks.conditional(1, cases={1: child}, name="Mode"))
    assert run.records == [record]
    assert record["passed"] is False
    assert record["cases"]["1"]["passed"] is False


def test_a_hand_built_descriptor_with_an_unknown_type_fails():
    _, verify = _recording()
    record = verify.record({"check_type": "custom", "name": "x", "description": "Verify 'x'"})
    assert record["passed"] is False
    assert record["error"] == "ValueError: Unknown check_type: 'custom'"


def test_recorded_helper_checks_fail_the_test(pytester):
    pytester.makepyfile(helpers="""
        from pytest_verify import verify

        def rail_ok(voltage):
            return verify.between(voltage, 3.2, 3.4, name="rail", units="V")
    """)
    pytester.makepyfile("""
        from helpers import rail_ok

        def test_rails(verify):
            assert verify.record(rail_ok(3.3))["passed"]
            verify.record(rail_ok(3.6))
    """)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*1 of 2 checks failed*", "*rail — expected [[]3.2V, 3.4V[]], got 3.6V"]
    )
