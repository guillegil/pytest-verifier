"""Regression tests: problems the 0.9.0 release report listed as found but not fixed.

* A ``str`` enum used as ``name`` (or a ``fail`` message, or a guard label) was stored as its
  repr (``<Rail.V3: '3V3'>``).
* ``all_satisfy`` whose factory raised said "expected all 0 to pass, got 0 failed".
* ``verify.record(check)`` of a check a composite had taken in without selecting it did
  nothing, and the check counted nowhere.
* ``abs_tol=-0.0`` rendered as ``± -0.0``; ``name=''`` was accepted.
* Snapshot strings were not length-capped.
"""
from __future__ import annotations

import enum
import json
from decimal import Decimal

import pytest

from pytest_verifier import checks
from pytest_verifier._render import SNAPSHOT_TEXT_LIMIT, snapshot
from pytest_verifier._run import Run, recording_verify


class Rail(str, enum.Enum):
    V3 = "3V3"
    V5 = "5V0"


def _recording():
    run = Run()
    run.phase = "call"
    return run, recording_verify(run)


# ---------------------------------------------------------------------------
# str enum names
# ---------------------------------------------------------------------------


def test_a_str_enum_name_is_stored_as_its_text():
    _, verify = _recording()
    record = verify.equal(1, 2, name=Rail.V3)
    assert type(record["name"]) is str and record["name"] == "3V3"
    assert record["description"] == "Verify '3V3' == 2"
    json.dumps(record)


def test_a_str_subclass_name_is_plain_text_in_built_checks_too():
    class Label(str):
        def __str__(self) -> str:
            return "something else"

    built = checks.less(1, 2, name=Label("Ripple"))
    assert type(built["name"]) is str and built["name"] == "Ripple"


def test_a_str_enum_fail_message_and_guard_label_are_plain_text():
    _, verify = _recording()
    failed = verify.fail(Rail.V5)
    assert failed["name"] == "5V0" and failed["msg"] == "5V0"
    assert failed["description"] == "FAIL: 5V0"
    guard = verify.guard([(True, Rail.V3, lambda: verify.is_true(1, name="on"))], name="g")
    assert guard["branches"][0]["label"] == "3V3"
    assert guard["detail"].startswith("[→ 3V3]")


def test_a_composite_name_is_plain_text():
    _, verify = _recording()
    record = verify.all_satisfy([1], lambda x: verify.is_true(x, name="x"), name=Rail.V3)
    assert record["name"] == "3V3" and "'3V3'" in record["description"]


# ---------------------------------------------------------------------------
# Empty names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["", "   ", "\n\t"])
def test_an_empty_name_is_a_usage_error(name):
    with pytest.raises(ValueError, match=r"equal\(\) name must not be empty"):
        checks.equal(1, 1, name=name)
    with pytest.raises(ValueError, match=r"all_satisfy\(\) name must not be empty"):
        checks.all_satisfy([], lambda x: x, name=name)


def test_a_blank_fail_message_is_named_fail_instead_of_raising():
    _, verify = _recording()
    try:
        raise TimeoutError()
    except TimeoutError as exc:
        record = verify.fail(str(exc))  # str() of a bare TimeoutError is ""
    assert record["name"] == "fail" and record["passed"] is False
    assert record["description"] == "FAIL: (no message)"
    assert record["detail"] == "FAIL: (no message)"
    assert checks.fail("", name="Mode")["name"] == "Mode"
    with pytest.raises(ValueError, match=r"fail\(\) name must not be empty"):
        checks.fail("message", name=" ")


def test_an_empty_name_in_a_factory_fails_the_composite():
    _, verify = _recording()
    record = verify.all_satisfy([1], lambda x: verify.equal(x, 1, name=""), name="all")
    assert record["passed"] is False
    assert "name must not be empty" in record["error"]


def test_a_long_name_is_bounded_in_the_description():
    built = checks.equal(1, 1, name="n" * 10_000)
    assert len(built["description"]) < 300
    assert built["name"] == "n" * 10_000  # the name itself is kept


# ---------------------------------------------------------------------------
# all_satisfy whose items could not all be checked
# ---------------------------------------------------------------------------


def test_a_factory_that_raises_at_once_says_no_item_was_checked():
    _, verify = _recording()
    record = verify.all_satisfy([1, 2], lambda x: 1 / 0, name="all")
    assert record["detail"] == (
        "no item checked (descriptor_factory raised ZeroDivisionError: division by zero "
        "for item 0)"
    )


def test_a_factory_that_raises_later_says_how_far_it_got():
    _, verify = _recording()

    def factory(x):
        if x == 3:
            raise RuntimeError("probe lost")
        return verify.greater(x, 1, name="x")

    record = verify.all_satisfy([2, 1, 3], factory, name="all")
    assert record["detail"] == (
        "2 items checked, 1 failed: [1] expected > 1, got 1 "
        "(descriptor_factory raised RuntimeError: probe lost for item 2)"
    )
    passing = verify.all_satisfy([2, 3, None], lambda x: 1 / 0 if x is None else
                                 verify.greater(x, 1, name="x"), name="all")
    assert passing["detail"].startswith("2 items checked, all passed (descriptor_factory raised")


def test_items_that_are_not_iterable_say_no_item_was_checked():
    _, verify = _recording()
    record = verify.all_satisfy(5, lambda x: x, name="all")  # type: ignore[arg-type]
    assert record["detail"].startswith("no item checked (items are not iterable: TypeError")


# ---------------------------------------------------------------------------
# verify.record of a check a composite took in
# ---------------------------------------------------------------------------


def _unselected(verify, check):
    """A guard that takes *check* in without selecting it."""
    return verify.guard(
        [(False, "never", check)], default=lambda: verify.is_true(1, name="d"), name="g"
    )


def test_recording_an_unselected_absorbed_check_records_a_copy():
    run, verify = _recording()
    check = verify.equal(1, 2, name="c")
    group = _unselected(verify, check)
    assert run.records == [group]  # c counts nowhere: the guard did not select it
    copy = verify.record(check)
    assert copy is not check and copy == {**check, "phase": "call", "location": copy["location"]}
    assert run.records == [group, copy]
    assert verify.record(copy) is copy  # the copy is a top-level check: returned as is
    assert len(run.records) == 2


def test_recording_a_selected_absorbed_check_returns_it_as_is():
    run, verify = _recording()
    check = verify.equal(1, 2, name="c")
    group = verify.all_satisfy([check], lambda c: c, name="g")
    assert verify.record(check) is check  # it counts in the composite: no second count
    assert run.records == [group]


def test_require_of_an_unselected_passing_check_records_a_copy_and_goes_on():
    run, verify = _recording()
    check = verify.is_true(1, name="c")
    _unselected(verify, check)
    copy = verify.require(check)
    assert copy is not check and copy["passed"] is True
    assert len(run.records) == 2


def test_recording_a_check_before_a_composite_keeps_it_on_its_own():
    run, verify = _recording()
    check = verify.equal(1, 2, name="c")
    assert verify.record(check) is check  # pinned
    group = _unselected(verify, check)
    assert run.records == [check, group]  # still failing the test


def test_a_top_level_check_is_still_returned_as_is():
    run, verify = _recording()
    check = verify.equal(1, 2, name="c")
    assert verify.record(check) is check
    assert run.records == [check]


# ---------------------------------------------------------------------------
# Negative zero tolerances
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("zero", [-0.0, Decimal("-0")])
def test_a_negative_zero_tolerance_is_stored_as_zero(zero):
    built = checks.approx(1, 2, abs_tol=zero, name="a")
    assert str(built["abs_tol"]) in ("0.0", "0")
    assert "-0" not in built["description"]
    built = checks.approx(1, 2, rel_tol=zero, name="a")
    assert "-0" not in built["description"]


# ---------------------------------------------------------------------------
# Snapshot strings
# ---------------------------------------------------------------------------


def test_a_long_string_is_cut_in_a_record():
    _, verify = _recording()
    record = verify.equal("x" * 50_000, "y", name="reply")
    assert len(record["actual"]) == SNAPSHOT_TEXT_LIMIT
    assert record["actual"].endswith("...")
    assert record["passed"] is False


def test_long_text_counts_toward_the_snapshot_budget():
    value = ["y" * SNAPSHOT_TEXT_LIMIT] * 500  # 5 million characters
    copied = snapshot(value)
    assert isinstance(copied, str)  # over budget: a bounded repr instead of a copy
    assert len(copied) < 200_000
    assert snapshot(["short"] * 1000) == ["short"] * 1000


def test_long_dict_keys_are_cut_too():
    copied = snapshot({"k" * 20_000: 1})
    [key] = copied
    assert len(key) == SNAPSHOT_TEXT_LIMIT
