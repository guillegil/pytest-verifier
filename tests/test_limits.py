"""``verify.limits`` and ``pytest_verifier.load_limits``: limits tables.

* ``verify.limits``: one check per row, in table order, returned by name; how a row without
  ``"check"`` says which check it is; strict validation of every row before anything is
  recorded (errors name the row); measurement keys; ``on_missing``; ``require.limits`` and
  ``--verify-fail-fast`` stopping only once the whole table is recorded; ``checks.limits``;
  ``source`` kept on records as ``limit_source``; the table is never changed.
* ``load_limits``: delimiters, decimal commas, headers, comments, encodings, ``columns=``,
  ``select=``, number and boolean cells, CSV text cells (``CellText``) that take the
  measurement's type when checked, and errors that name ``path:line``.
"""
from __future__ import annotations

import array
import collections
import collections.abc
import copy
import enum
import json
import math
import re
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from pytest_verifier import ChecksFailedError, LimitRow, Verify, checks, load_limits
from pytest_verifier._run import Run, recording_verify


def _recording(**kwargs: Any) -> Tuple[Run, Any]:
    run = Run(**kwargs)
    run.phase = "call"
    return run, recording_verify(run)


def _line() -> int:
    """The line number of the caller."""
    return sys._getframe(1).f_lineno


def _round_trip(record: Any) -> Dict[str, Any]:
    return json.loads(json.dumps(record, allow_nan=False))


def _names(run: Run) -> List[str]:
    return [record["name"] for record in run.records]


def _csv(directory: Path, text: str, name: str = "limits.csv", encoding: str = "utf-8") -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


class Rail(str, enum.Enum):
    V3 = "3V3"
    V5 = "5V0"


class Channel(enum.Enum):
    VOUT = "Vout"
    SEVEN = 7


class Pin(enum.IntEnum):
    SEVEN = 7


VALID_ROW = {"low": 0}


# ---------------------------------------------------------------------------
# One check per row
# ---------------------------------------------------------------------------


def test_one_check_per_row_in_table_order_returned_by_name():
    run, verify = _recording()
    table = {
        "Vout": {"expected": 3.3, "abs_tol": 0.05, "units": "V"},
        "Ripple": {"high": 0.05, "units": "V"},
        "FW": {"check": "matches", "pattern": "^v2"},
    }
    measured = {"FW": "v2.4", "Ripple": 0.02, "Vout": 3.31}  # another order
    out = verify.limits(measured, table)
    assert list(out) == ["Vout", "Ripple", "FW"]
    assert _names(run) == ["Vout", "Ripple", "FW"]
    assert [out[name] for name in out] == run.records
    assert all(out[record["name"]] is record for record in run.records)
    assert [record["check_type"] for record in run.records] == ["approx", "less_equal", "matches"]
    assert all(record["passed"] is True for record in run.records)
    assert out["Vout"]["description"] == "Verify 'Vout' == 3.3V ± 0.05V"
    assert out["Vout"]["detail"] == "3.31V == 3.3V ± 0.05V"
    assert out["Ripple"]["description"] == "Verify 'Ripple' <= 0.05V"
    assert out["FW"]["detail"] == "matches /^v2/"


def test_a_measurement_without_a_row_is_not_checked():
    run, verify = _recording()
    out = verify.limits({"Vout": 3.3, "Extra": 99}, {"Vout": {"low": 3}})
    assert list(out) == ["Vout"]
    assert _names(run) == ["Vout"]


def test_rows_are_recorded_at_the_limits_call():
    run, verify = _recording()
    line = _line() + 1
    verify.limits({"A": 1, "B": 2}, {"A": {"low": 0}, "B": {"high": 1}})
    assert [record["location"].rsplit(":", 1)[1] for record in run.records] == [str(line)] * 2
    assert [record["phase"] for record in run.records] == ["call", "call"]


def test_records_are_json_safe_and_evaluate_as_recorded():
    run, verify = _recording()
    table = {
        "Vout": {"expected": 3.3, "abs_tol": 0.05, "units": "V"},
        "I": {"low": 0.1, "high": 0.5, "units": "A"},
        "Mode": {"expected": 3},
        "FW": {"check": "matches", "pattern": "^v2"},
        "Rows": {"check": "length", "expected": 2},
    }
    measured = {"Vout": 3.5, "I": 0.3, "Mode": 4, "FW": "v2.4", "Rows": [1, 2]}
    verify.limits(measured, table)
    verdicts = [record["passed"] for record in run.records]
    assert verdicts == [False, True, False, True, True]
    for record in run.records:
        text = json.dumps(record, allow_nan=False)
        assert checks.evaluate(record) is record["passed"]
        assert checks.evaluate(json.loads(text)) is record["passed"]
    assert run.records[0]["detail"] == "expected 3.3V ± 0.05V, got 3.5V"
    assert run.records[2]["detail"] == "expected 3, got 4"


def test_rows_in_a_section_get_its_title():
    run, verify = _recording()
    with verify.section("3V3 rail"):
        verify.limits({"A": 1, "B": 2}, {"A": {"low": 0}, "B": {"low": 0}})
    assert [record["section"] for record in run.records] == [["3V3 rail"], ["3V3 rail"]]


def test_a_str_enum_row_name_is_plain_text():
    run, verify = _recording()
    out = verify.limits({"3V3": 3.3}, {Rail.V3: {"low": 3}})
    (name,) = out
    assert type(name) is str and name == "3V3"
    assert type(run.records[0]["name"]) is str
    assert run.records[0]["description"] == "Verify '3V3' >= 3"


# ---------------------------------------------------------------------------
# Which check a row makes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row, value, check_type, description",
    [
        ({"low": 0.1, "high": 0.5, "units": "A"}, 0.3, "between", "Verify 'X' ∈ [0.1A, 0.5A]"),
        ({"low": 0.1, "high": 0.5, "inclusive": False}, 0.3, "between", "Verify 'X' ∈ (0.1, 0.5)"),
        ({"low": 10}, 10, "greater_equal", "Verify 'X' >= 10"),
        ({"high": 10}, 10, "less_equal", "Verify 'X' <= 10"),
        ({"low": 10, "inclusive": True}, 10, "greater_equal", "Verify 'X' >= 10"),
        ({"low": 10, "inclusive": False}, 11, "greater", "Verify 'X' > 10"),
        ({"high": 10, "inclusive": False}, 9, "less", "Verify 'X' < 10"),
        ({"expected": 3.3, "abs_tol": 0.05}, 3.31, "approx", "Verify 'X' == 3.3 ± 0.05"),
        ({"expected": 50, "rel_tol": 0.02}, 50.5, "approx", None),
        ({"expected": 3}, 3, "equal", "Verify 'X' == 3"),
        ({"expected": "OK"}, "OK", "equal", "Verify 'X' == 'OK'"),
        ({"expected": True}, True, "equal", None),
        ({"expected": Decimal("3.3")}, Decimal("3.3"), "equal", None),
        ({"expected": 3.3, "check": "equal"}, 3.3, "equal", "Verify 'X' == 3.3"),
    ],
)
def test_a_row_without_check_is_inferred_from_its_limits(row, value, check_type, description):
    run, verify = _recording()
    record = verify.limits({"X": value}, {"X": row})["X"]
    assert record["check_type"] == check_type
    assert record["passed"] is True, record["detail"]
    if description is not None:
        assert record["description"] == description


def test_one_bound_with_inclusive_false_fails_at_the_bound():
    _, verify = _recording()
    table = {"L": {"low": 10, "inclusive": False}, "H": {"high": 10, "inclusive": False}}
    out = verify.limits({"L": 10, "H": 10}, table)
    assert (out["L"]["passed"], out["H"]["passed"]) == (False, False)
    assert out["L"]["detail"] == "expected > 10, got 10"
    assert "inclusive" not in out["L"]


@pytest.mark.parametrize("expected", [3.3, 3.0, -0.5])
def test_a_float_expected_without_a_tolerance_is_a_value_error(expected):
    run, verify = _recording()
    message = r"limits\(\) row 'Vout': expected .* no tolerance"
    with pytest.raises(ValueError, match=message) as info:
        verify.limits({"Vout": 3.3}, {"Vout": {"expected": expected}})
    assert "abs_tol or rel_tol" in str(info.value)
    assert "check='equal'" in str(info.value)
    assert run.records == []


@pytest.mark.parametrize(
    "row, value, check_type",
    [
        ({"check": "equal", "expected": 3}, 3, "equal"),
        ({"check": "not_equal", "expected": 3}, 4, "not_equal"),
        ({"check": "approx", "expected": 3, "abs_tol": 0.5}, 3.2, "approx"),
        ({"check": "greater", "threshold": 3}, 4, "greater"),
        ({"check": "greater_equal", "threshold": 3}, 3, "greater_equal"),
        ({"check": "less", "threshold": 3}, 2, "less"),
        ({"check": "less_equal", "threshold": 3}, 3, "less_equal"),
        ({"check": "between", "low": 1, "high": 3}, 2, "between"),
        ({"check": "is_true"}, 1, "true"),
        ({"check": "is_false"}, 0, "false"),
        ({"check": "true"}, 1, "true"),
        ({"check": "false"}, "", "false"),
        ({"check": "is_none"}, None, "is_none"),
        ({"check": "is_not_none"}, 0, "is_not_none"),
        ({"check": "contains", "needle": "b"}, "abc", "contains"),
        ({"check": "not_contains", "needle": "z"}, "abc", "not_contains"),
        ({"check": "matches", "pattern": r"^v\d"}, "v2", "matches"),
        ({"check": "matches", "pattern": re.compile("^V", re.I)}, "v2", "matches"),
        ({"check": "length", "expected": 2}, [1, 2], "length"),
        ({"check": "is_instance", "expected_type": int}, 3, "is_instance"),
        ({"check": "is_instance", "expected_type": (int, str)}, "3", "is_instance"),
    ],
)
def test_an_explicit_check_makes_that_check(row, value, check_type):
    _, verify = _recording()
    record = verify.limits({"X": value}, {"X": row})["X"]
    assert record["check_type"] == check_type
    assert record["passed"] is True, record["detail"]
    assert checks.evaluate(_round_trip(record)) is True


def test_true_and_false_aliases_record_like_is_true_and_is_false():
    _, verify = _recording()
    out = verify.limits({"On": 1, "Off": 1}, {"On": {"check": "true"}, "Off": {"check": "false"}})
    assert out["On"]["description"] == "Verify 'On' is True"
    assert out["Off"]["description"] == "Verify 'Off' is False"
    assert (out["On"]["passed"], out["Off"]["passed"]) == (True, False)


@pytest.mark.parametrize(
    "row, value, passed, description",
    [
        ({"check": "greater", "low": 5}, 6, True, "Verify 'X' > 5"),
        ({"check": "greater", "low": 5}, 5, False, "Verify 'X' > 5"),
        ({"check": "greater_equal", "low": 5}, 5, True, "Verify 'X' >= 5"),
        ({"check": "less", "high": 5}, 5, False, "Verify 'X' < 5"),
        ({"check": "less_equal", "high": 5}, 5, True, "Verify 'X' <= 5"),
    ],
)
def test_low_and_high_stand_for_the_threshold(row, value, passed, description):
    _, verify = _recording()
    record = verify.limits({"X": value}, {"X": row})["X"]
    assert record["threshold"] == 5
    assert "low" not in record and "high" not in record
    assert record["passed"] is passed
    assert record["description"] == description


@pytest.mark.parametrize(
    "row, alias",
    [
        ({"check": "greater", "low": 5, "threshold": 1}, "low"),
        ({"check": "greater_equal", "low": 5, "threshold": 1}, "low"),
        ({"check": "less", "high": 5, "threshold": 1}, "high"),
        ({"low": 5, "threshold": 1}, "low"),
        ({"high": 5, "threshold": 1}, "high"),
    ],
)
def test_threshold_and_its_alias_together_are_an_error(row, alias):
    run, verify = _recording()
    with pytest.raises(TypeError, match=rf"row 'X': give threshold or {alias}, not both"):
        verify.limits({"X": 3}, {"X": row})
    assert run.records == []


def test_the_bound_an_ordering_check_does_not_take_is_an_unknown_argument():
    _, verify = _recording()
    with pytest.raises(TypeError, match=r"greater\(\) takes threshold \(or low\), units; not: hi"):
        verify.limits({"X": 3}, {"X": {"check": "greater", "high": 5}})
    with pytest.raises(TypeError, match=r"less_equal\(\) takes threshold \(or high\).*not: low"):
        verify.limits({"X": 3}, {"X": {"check": "less_equal", "low": 5}})


def test_none_counts_as_not_given():
    _, verify = _recording()
    out = verify.limits(
        {"A": 3, "B": 3},
        {
            "A": {"low": None, "high": 5, "units": None, "check": None},
            "B": {"low": 1, "high": 2, "inclusive": None, "source": None},
        },
    )
    assert out["A"]["check_type"] == "less_equal"
    assert out["B"]["check_type"] == "between" and out["B"]["inclusive"] is True
    assert "limit_source" not in out["B"]


@pytest.mark.parametrize("row, got", [({}, "nothing"), ({"units": "V"}, "units")])
def test_a_row_that_says_no_check_and_no_limit_is_an_error(row, got):
    _, verify = _recording()
    with pytest.raises(ValueError, match=rf"row 'X' says neither which check .*; got {got}$"):
        verify.limits({"X": 3}, {"X": row})


# ---------------------------------------------------------------------------
# Strict validation: nothing is recorded when a row is wrong
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row, error, message",
    [
        # text limits
        ({"low": "1"}, TypeError, r"must be a finite number, got '1' \(text: write the number"),
        ({"high": "3.3V"}, TypeError, r"must be a finite number, got '3.3V'"),
        ({"expected": "3.3", "abs_tol": 0.1}, TypeError, r"expected must be a finite number"),
        ({"expected": 3.3, "abs_tol": "0.1"}, TypeError, r"abs_tol must be a finite number"),
        ({"expected": 3.3, "rel_tol": "2%"}, TypeError, r"rel_tol must be a finite number"),
        # bool limits
        ({"low": True}, TypeError, r"must be a finite number, got True"),
        ({"low": 0, "high": False}, TypeError, r"high must be a finite number, got False"),
        ({"expected": True, "abs_tol": 0.1}, TypeError, r"expected must be a finite number"),
        ({"expected": 1, "abs_tol": True}, TypeError, r"abs_tol must be a finite number"),
        # tolerances
        ({"expected": 3, "rel_tol": 1}, ValueError, r"rel_tol .* below 1; got 1$"),
        ({"expected": 3, "rel_tol": 1.5}, ValueError, r"rel_tol .* below 1; got 1\.5$"),
        ({"expected": 3, "rel_tol": -0.1}, ValueError, r"rel_tol .* at least 0"),
        ({"expected": 3, "abs_tol": -0.1}, ValueError, r"abs_tol must be a non-negative number"),
        ({"check": "approx", "expected": 3}, ValueError, r"at least one of abs_tol or rel_tol"),
        # inclusive
        ({"low": 1, "high": 2, "inclusive": 1}, TypeError, r"inclusive must be True or Fal.*1"),
        ({"low": 1, "inclusive": "false"}, TypeError, r"inclusive must be True or False"),
        ({"low": 1, "high": 2, "inclusive": 0}, TypeError, r"inclusive must be True or False"),
        # length
        ({"check": "length", "expected": 1.0}, TypeError, r"whole number of items, got 1\.0"),
        ({"check": "length", "expected": 1.5}, TypeError, r"whole number of items, got 1\.5"),
        ({"check": "length", "expected": -1}, TypeError, r"whole number of items, got -1"),
        ({"check": "length", "expected": True}, TypeError, r"whole number of items, got True"),
        ({"check": "length", "expected": "2"}, TypeError, r"whole number of items, got '2'"),
        # unknown check
        ({"check": "approxx", "expected": 3}, ValueError, r"unknown check 'approxx'; one of: eq"),
        ({"check": "fail"}, ValueError, r"unknown check 'fail'"),
        ({"check": "all_satisfy"}, ValueError, r"unknown check 'all_satisfy'"),
        ({"check": "raises"}, ValueError, r"unknown check 'raises'"),
        ({"check": "eventually"}, ValueError, r"unknown check 'eventually'"),
        ({"check": "TRUE"}, ValueError, r"unknown check 'TRUE'"),
        ({"check": 5}, ValueError, r"unknown check 5"),
        # unknown or missing argument
        ({"expected": 3, "abs_tool": 1}, TypeError, r"equal\(\) takes expected, units; not: abs_"),
        ({"expected": 3, "abs_tol": 1, "inclusive": True}, TypeError, r"approx\(\) .*not: inclus"),
        ({"check": "is_true", "low": 1}, TypeError, r"is_true\(\) takes no limits; not: low"),
        ({"low": 1, "expected": 2}, TypeError, r"greater_equal\(\) .*not: expected"),
        (
            {"check": "between", "low": 1, "high": 5, "threshold": 3},
            TypeError,
            r"between\(\) takes low, high, inclusive, units; not: threshold",
        ),
        ({"check": "between", "low": 1}, TypeError, r"between\(\) needs high"),
        ({"check": "contains"}, TypeError, r"contains\(\) needs needle"),
        ({"check": "matches"}, TypeError, r"matches\(\) needs pattern"),
        ({"check": "approx"}, TypeError, r"approx\(\) needs expected"),
        ({"check": "is_instance"}, TypeError, r"is_instance\(\) needs expected_type"),
        # argument values the check itself refuses
        ({"check": "matches", "pattern": "("}, ValueError, r"pattern is not a valid regular exp"),
        ({"check": "matches", "pattern": "[a-"}, ValueError, r"not a valid regular expression"),
        ({"low": 5, "high": 1}, ValueError, r"between\(\) low must not exceed high"),
        ({"check": "is_instance", "expected_type": "int"}, TypeError, r"must be a class"),
        # malformed rows
        ([("low", 1)], TypeError, r"row 'X' must be a mapping of arguments, got list"),
        ({1: 2}, TypeError, r"argument names must be str, got 1"),
        ({"low": 1, "source": 12}, TypeError, r"source must be text, got 12"),
    ],
)
def test_a_wrong_row_raises_naming_it_and_records_nothing(row, error, message):
    run, verify = _recording()
    table = {"Good": VALID_ROW, "X": row, "Later": VALID_ROW}
    with pytest.raises(error) as info:
        verify.limits({"Good": 1, "X": 3, "Later": 1}, table)
    text = str(info.value)
    assert text.startswith("limits() row 'X'"), text
    assert re.search(message, text), text
    assert run.records == []


@pytest.mark.parametrize("limit", [float("nan"), float("inf"), float("-inf"), Decimal("NaN")])
@pytest.mark.parametrize("argument", ["low", "high", "abs_tol"])
def test_a_limit_that_is_not_finite_is_refused_naming_the_row(argument, limit):
    run, verify = _recording()
    row = {"expected": 3, "abs_tol": 0.1} if argument == "abs_tol" else {}
    row[argument] = limit
    with pytest.raises((TypeError, ValueError)) as info:
        verify.limits({"Good": 1, "X": 3}, {"Good": VALID_ROW, "X": row})
    assert str(info.value).startswith("limits() row 'X': ")
    assert "must be a finite number" in str(info.value)
    assert "leave the limit out for no limit" in str(info.value)
    assert run.records == []


@pytest.mark.parametrize("row", [{"low": float("nan")}, {"expected": 3, "abs_tol": float("nan")}])
def test_a_limit_that_is_not_finite_is_a_value_error(row):
    _, verify = _recording()
    with pytest.raises(ValueError, match=r"row 'X'"):
        verify.limits({"X": 3}, {"X": row})


def test_the_error_names_low_not_threshold():
    _, verify = _recording()
    with pytest.raises(TypeError) as info:
        verify.limits({"X": 3}, {"X": {"low": "1"}})
    assert "low must be a finite number" in str(info.value)


def test_a_pattern_that_is_not_text_is_refused():
    run, verify = _recording()
    with pytest.raises(TypeError, match=r"row 'FW'.*pattern"):
        verify.limits({"FW": "v2"}, {"FW": {"check": "matches", "pattern": 5}})
    assert run.records == []


@pytest.mark.parametrize(
    "name, error, message",
    [
        (5, TypeError, r"limits\(\) row names must be str, got 5"),
        ("", ValueError, r"limits\(\) row names must not be empty"),
        ("   ", ValueError, r"limits\(\) row names must not be empty"),
    ],
)
def test_a_wrong_row_name_raises_and_records_nothing(name, error, message):
    run, verify = _recording()
    with pytest.raises(error, match=message):
        verify.limits({"Good": 1}, {"Good": VALID_ROW, name: VALID_ROW})
    assert run.records == []


@pytest.mark.parametrize(
    "measurements, table, on_missing, error, message",
    [
        ([("V", 3)], {"V": VALID_ROW}, "fail", TypeError, r"measurements must be a mapping.*list"),
        ({"V": 3}, [("V", VALID_ROW)], "fail", TypeError, r"table must be a mapping.*got list"),
        ({"V": 3}, None, "fail", TypeError, r"table must be a mapping.*got NoneType"),
        ({"V": 3}, {"V": VALID_ROW}, "skip", ValueError, r"on_missing must be 'fail' or 'ignore'"),
        ({"V": 3}, {}, "fail", ValueError, r"limits\(\) table has no rows"),
        ({"V": 3}, {}, "ignore", ValueError, r"limits\(\) table has no rows"),
    ],
)
def test_wrong_arguments_raise_and_record_nothing(measurements, table, on_missing, error, message):
    run, verify = _recording()
    with pytest.raises(error, match=message):
        verify.limits(measurements, table, on_missing=on_missing)
    assert run.records == []


def test_a_wrong_row_after_a_failed_one_still_records_nothing():
    run, verify = _recording(fail_fast=True)
    table = {"Fails": {"low": 10}, "Bad": {"low": 1, "hgh": 2}}
    with pytest.raises(TypeError, match=r"row 'Bad'.*not: hgh"):
        verify.limits({"Fails": 1, "Bad": 1}, table)
    assert run.records == []


# ---------------------------------------------------------------------------
# The table is never changed
# ---------------------------------------------------------------------------


def test_the_table_is_never_changed():
    _, verify = _recording()
    table = {
        "A": {"low": 1, "inclusive": False, "units": "V"},
        "B": {"check": "greater", "low": 1},
        "C": {"check": "true"},
        "D": {"expected": 3.3, "abs_tol": -0.0, "source": "bench.py:3"},
        "E": {"low": None, "high": 2},
        "F": {"check": " less ", "high": 3},
    }
    rows = {name: row for name, row in table.items()}
    before = copy.deepcopy(table)
    verify.limits({"A": 2, "B": 2, "C": 1, "D": 3.3, "F": 2}, table)
    verify.limits({"A": 2}, table, on_missing="ignore")
    assert table == before
    assert all(table[name] is rows[name] for name in table)
    assert list(table["A"]) == ["low", "inclusive", "units"]
    assert math.copysign(1.0, table["D"]["abs_tol"]) == -1.0


def test_a_failed_validation_leaves_the_table_unchanged():
    _, verify = _recording()
    table = {"A": {"low": 1, "inclusive": False}, "B": {"low": 3, "threshold": 1}}
    before = copy.deepcopy(table)
    with pytest.raises(TypeError):
        verify.limits({"A": 2, "B": 2}, table)
    assert table == before


# ---------------------------------------------------------------------------
# Measurement keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, row_name",
    [
        (7, "7"),
        (Channel.VOUT, "Vout"),
        (Channel.SEVEN, "7"),
        (Pin.SEVEN, "7"),
        (Rail.V3, "3V3"),
        ("Vout", "Vout"),
    ],
)
def test_a_measurement_key_matches_its_row_as_text(key, row_name):
    run, verify = _recording()
    out = verify.limits({key: 3}, {row_name: {"low": 1}})
    assert list(out) == [row_name]
    assert out[row_name]["actual"] == 3 and out[row_name]["passed"] is True
    assert type(run.records[0]["name"]) is str


@pytest.mark.parametrize(
    "measurements, shown",
    [
        ({7: 3, "7": 4}, r"7 and '7' both stand for '7'"),
        ({Channel.VOUT: 3, "Vout": 4}, r"<Channel.VOUT: 'Vout'> and 'Vout' both stand for 'Vout'"),
        ({Channel.SEVEN: 3, 7: 4}, r"<Channel.SEVEN: 7> and 7 both stand for '7'"),
    ],
)
def test_measurement_keys_that_stand_for_the_same_row_are_an_error(measurements, shown):
    run, verify = _recording()
    with pytest.raises(ValueError, match=re.escape("limits() measurements ") + shown):
        verify.limits(measurements, {"Vout": VALID_ROW, "7": VALID_ROW})
    assert run.records == []


@pytest.mark.parametrize("key", [7.0, True, (1, 2), None, b"Vout", Decimal(7)])
def test_an_unsupported_measurement_key_is_a_type_error(key):
    run, verify = _recording()
    with pytest.raises(TypeError, match=r"measurement names must be str, int or enum members"):
        verify.limits({"Vout": 1, key: 3}, {"Vout": VALID_ROW})
    assert run.records == []


# ---------------------------------------------------------------------------
# on_missing
# ---------------------------------------------------------------------------


def test_a_missing_measurement_fails_with_the_rows_own_check_and_limits():
    run, verify = _recording()
    table = {
        "Vout": {"expected": 3.3, "abs_tol": 0.05, "units": "V", "source": "limits.csv:2"},
        "Other": {"low": 0},
    }
    out = verify.limits({"Other": 1}, table)
    record = out["Vout"]
    assert _names(run) == ["Vout", "Other"]
    assert record["check_type"] == "approx"
    assert (record["expected"], record["abs_tol"], record["units"]) == (3.3, 0.05, "V")
    assert record["actual"] is None
    assert record["passed"] is False
    assert record["error"] == "not measured: the measurements have no 'Vout'"
    assert record["description"] == "Verify 'Vout' == 3.3V ± 0.05V"
    assert "not measured" in record["detail"]
    assert record["limit_source"] == "limits.csv:2"
    json.dumps(record, allow_nan=False)
    assert checks.evaluate(record) is False
    assert checks.evaluate(_round_trip(record)) is False


@pytest.mark.parametrize(
    "row",
    [
        {"check": "is_none"},
        {"check": "is_false"},
        {"check": "not_equal", "expected": 3},
        {"check": "not_contains", "needle": "x"},
        {"check": "is_instance", "expected_type": type(None)},
        {"check": "matches", "pattern": ""},
        {"low": 1},
    ],
)
def test_a_missing_measurement_fails_even_where_none_would_pass(row):
    _, verify = _recording()
    record = verify.limits({"Other": 1}, {"S": row, "Other": VALID_ROW})["S"]
    assert record["passed"] is False
    assert record["error"].startswith("not measured")
    assert checks.evaluate(record) is False
    assert checks.evaluate(_round_trip(record)) is False


def test_a_missing_measurement_names_a_close_measurement_key():
    _, verify = _recording()
    out = verify.limits({"Vout1": 3.3, "Other": 1}, {"Vout": VALID_ROW, "Other": VALID_ROW})
    assert out["Vout"]["error"] == "not measured: the measurements have no 'Vout'; is it 'Vout1'?"
    assert "is it 'Vout1'?" in out["Vout"]["detail"]


def test_a_key_another_row_uses_is_not_suggested():
    _, verify = _recording()
    out = verify.limits({"Vin": 1}, {"Vin": VALID_ROW, "Vinn": VALID_ROW})
    assert out["Vinn"]["error"] == "not measured: the measurements have no 'Vinn'"


def test_on_missing_ignore_makes_no_check_for_the_row():
    run, verify = _recording()
    out = verify.limits({"B": 1}, {"A": VALID_ROW, "B": VALID_ROW}, on_missing="ignore")
    assert list(out) == ["B"]
    assert _names(run) == ["B"]


def test_on_missing_fail_with_nothing_measured_records_every_row():
    run, verify = _recording()
    out = verify.limits({}, {"A": VALID_ROW, "B": {"high": 1}})
    assert _names(run) == ["A", "B"]
    assert all(record["error"].startswith("not measured") for record in out.values())


def test_on_missing_ignore_with_nothing_measured_is_an_error():
    run, verify = _recording()
    with pytest.raises(ValueError, match=r"no row of the table has a measurement") as info:
        verify.limits({"Vout1": 1}, {"Vout": VALID_ROW}, on_missing="ignore")
    assert "rows: 'Vout'; measurements: 'Vout1'" in str(info.value)
    with pytest.raises(ValueError, match=r"measurements: none\)"):
        verify.limits({}, {"Vout": VALID_ROW}, on_missing="ignore")
    assert run.records == []


# ---------------------------------------------------------------------------
# require.limits and fail-fast
# ---------------------------------------------------------------------------

STOP_TABLE = {"A": {"low": 1}, "B": {"low": 10}, "C": {"high": 5}, "D": {"low": 100}}
STOP_MEASURED = {"A": 5, "B": 1, "C": 3, "D": 1}


def test_require_limits_stops_only_after_the_whole_table_is_recorded():
    run, verify = _recording()
    with pytest.raises(ChecksFailedError) as info:
        verify.require.limits(STOP_MEASURED, STOP_TABLE)
    assert _names(run) == ["A", "B", "C", "D"]
    text = str(info.value)
    assert text.startswith("2 of 4 checks failed, stopped at [1]: B")
    assert "[3] D" in text


def test_fail_fast_limits_stops_only_after_the_whole_table_is_recorded():
    run, verify = _recording(fail_fast=True)
    with pytest.raises(ChecksFailedError, match=r"^2 of 4 checks failed, stopped at \[1\]: B"):
        verify.limits(STOP_MEASURED, STOP_TABLE)
    assert _names(run) == ["A", "B", "C", "D"]


def test_require_limits_that_pass_do_not_stop():
    run, verify = _recording()
    out = verify.require.limits({"A": 5}, {"A": {"low": 1}})
    assert out["A"]["passed"] is True
    assert _names(run) == ["A"]


def test_a_missing_measurement_stops_a_required_table():
    run, verify = _recording()
    with pytest.raises(ChecksFailedError, match=r"stopped at \[0\]: A"):
        verify.require.limits({"B": 5}, {"A": {"low": 1}, "B": {"low": 1}})
    assert _names(run) == ["A", "B"]


def test_fail_fast_leaves_limits_in_teardown_soft():
    run, verify = _recording(fail_fast=True)
    run.phase = "teardown"
    out = verify.limits(STOP_MEASURED, STOP_TABLE)
    assert [record["passed"] for record in out.values()] == [True, False, True, False]
    assert _names(run) == ["A", "B", "C", "D"]


STOP_TEST = """
    LIMITS = {"A": {"low": 1}, "B": {"low": 10}, "C": {"high": 5}, "D": {"low": 100}}

    def test_limits(verify):
        verify.%s({"A": 5, "B": 1, "C": 3, "D": 1}, LIMITS)
        print("went on")
"""


@pytest.mark.parametrize(
    "method, args",
    [("require.limits", ()), ("limits", ("--verify-fail-fast",))],
    ids=["require", "fail-fast"],
)
def test_a_stopped_table_lists_every_row_in_a_session(pytester: pytest.Pytester, method, args):
    pytester.makepyfile(STOP_TEST % method)
    result = pytester.runpytest("-s", "-p", "no:cacheprovider", *args)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*2 of 4 checks failed, stopped at [[]1[]]: B — expected >= 10, got 1 (+1 more)*",
            "*✗ [[]1[]] B (test_a_stopped_table_lists_every_row_in_a_session.py:4)*",
            "*✗ [[]3[]] D (*",
            "*✓ [[]0[]] A — 5 >= 1*",
            "*✓ [[]2[]] C — 3 <= 5*",
        ]
    )
    assert "went on" not in result.stdout.str()


def test_soft_limits_go_on_in_a_session(pytester: pytest.Pytester):
    pytester.makepyfile(STOP_TEST % "limits")
    result = pytester.runpytest("-s", "-p", "no:cacheprovider")
    result.assert_outcomes(failed=1)
    assert "went on" in result.stdout.str()
    result.stdout.fnmatch_lines(["*2 of 4 checks failed: B — expected >= 10, got 1 (+1 more)*"])


# ---------------------------------------------------------------------------
# checks.limits
# ---------------------------------------------------------------------------


def test_checks_limits_builds_unevaluated_checks_by_name():
    out = checks.limits({"A": 5, "B": 1}, {"A": {"low": 1}, "B": {"low": 3}, "C": {"high": 1}})
    assert list(out) == ["A", "B", "C"]
    assert all("passed" not in descriptor for descriptor in out.values())
    assert [checks.evaluate(descriptor) for descriptor in out.values()] == [True, False, False]
    assert out["C"]["error"].startswith("not measured")
    assert out["C"]["actual"] is None
    assert [checks.evaluate(_round_trip(d)) for d in out.values()] == [True, False, False]


def test_checks_limits_validates_the_table_and_keeps_the_source():
    with pytest.raises(TypeError, match=r"row 'A'.*not: hgh"):
        checks.limits({"A": 5}, {"A": {"low": 1, "hgh": 2}})
    out = checks.limits({"A": 5}, {"A": {"low": 1, "source": "bench.csv:3"}})
    assert out["A"]["limit_source"] == "bench.csv:3"


def test_checks_limits_records_into_the_fixture():
    run, verify = _recording()
    built = checks.limits({"A": 5}, {"A": {"low": 10}})
    record = verify.record(built["A"])
    assert record["passed"] is False and _names(run) == ["A"]


def test_checks_require_limits_needs_the_fixture():
    with pytest.raises(RuntimeError, match=r"checks.require cannot stop a test"):
        checks.require.limits({"A": 5}, {"A": {"low": 1}})


# ---------------------------------------------------------------------------
# source -> limit_source
# ---------------------------------------------------------------------------


def test_a_rows_source_is_kept_as_limit_source():
    run, verify = _recording()
    out = verify.limits(
        {"A": 1, "B": 1}, {"A": {"low": 0, "source": "bench.py:12"}, "B": {"low": 0}}
    )
    assert out["A"]["limit_source"] == "bench.py:12"
    assert "limit_source" not in out["B"] and "source" not in out["A"]
    assert _round_trip(run.records[0])["limit_source"] == "bench.py:12"


def test_limit_source_reaches_the_reports(pytester: pytest.Pytester):
    pytester.makefile(".csv", limits="name,low,high,units\nVout,3.2,3.4,V\nIq,,0.002,A\n")
    pytester.makepyfile(
        """
        from pathlib import Path
        from pytest_verifier import load_limits

        def test_limits(verify):
            table = load_limits(Path(__file__).with_name("limits.csv"))
            verify.limits({"Vout": 3.3, "Iq": 0.005}, table)
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "--verify-json", "out.jsonl")
    result.assert_outcomes(failed=1)
    lines = [json.loads(line) for line in (pytester.path / "out.jsonl").read_text().splitlines()]
    sources = [line["check"]["limit_source"] for line in lines]
    assert [Path(source).name for source in sources] == ["limits.csv:2", "limits.csv:3"]
    result.stdout.fnmatch_lines(["*1 of 2 checks failed: Iq — expected <= 0.002A, got 0.005A*"])


# ---------------------------------------------------------------------------
# load_limits: file format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_load_limits_reads_commas_semicolons_and_tabs(tmp_path, monkeypatch, delimiter):
    monkeypatch.chdir(tmp_path)
    lines = [["name", "low", "high", "units"], ["Vout", "3.2", "3.4", "V"], ["Mode", "", "7", ""]]
    _csv(tmp_path, "\n".join(delimiter.join(cells) for cells in lines) + "\n")
    table = load_limits("limits.csv")
    assert table == {
        "Vout": {"low": 3.2, "high": 3.4, "units": "V", "source": "limits.csv:2"},
        "Mode": {"high": 7, "source": "limits.csv:3"},
    }
    assert type(table["Mode"]["high"]) is int


@pytest.mark.parametrize(
    "text, pattern",
    [
        ("name;check;pattern\nFW;matches;^v1,2\n", "^v1,2"),
        ("name,check,pattern\nFW,matches,a;b\n", "a;b"),
        ("name\tcheck\tpattern\nFW\tmatches\ta,b;c\n", "a,b;c"),
        ('name,check,pattern\nFW,matches,"x\ty"\n', "x\ty"),
    ],
)
def test_the_delimiter_is_the_one_that_gives_a_name_column(tmp_path, text, pattern):
    table = load_limits(_csv(tmp_path, text))
    assert table["FW"]["pattern"] == pattern


def test_a_decimal_comma_is_a_number_with_semicolons(tmp_path):
    path = _csv(tmp_path, "name;expected;abs_tol;low;high\nVout;3,3;0,05;;\nI;;;0,1;1,5e1\n")
    table = load_limits(path)
    assert (table["Vout"]["expected"], table["Vout"]["abs_tol"]) == (3.3, 0.05)
    assert (table["I"]["low"], table["I"]["high"]) == (0.1, 15.0)


def test_a_decimal_comma_with_commas_between_columns_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, 'name,low\nVout,"3,2"\n')
    with pytest.raises(ValueError) as info:
        load_limits("limits.csv")
    assert str(info.value) == (
        "load_limits(): limits.csv:2, column 'low': must be a number, got '3,2' "
        "(a decimal comma needs ';' between columns)"
    )


def test_headers_match_in_any_case_and_cells_are_stripped(tmp_path):
    path = _csv(tmp_path, "  NAME , Low ,HIGH,Units,  Check \n Vout , 3.2 , 3.4 , V , Between \n")
    row = load_limits(path)["Vout"]
    assert {key: row[key] for key in ("check", "low", "high", "units")} == {
        "check": "between", "low": 3.2, "high": 3.4, "units": "V"
    }


def test_comment_and_blank_lines_are_skipped_and_lines_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, "# limits of board A\n\nname,low\n# Vout first\n\n,,\nVout,1\n  \n#x,2\nI,2\n")
    table = load_limits("limits.csv")
    assert list(table) == ["Vout", "I"]
    assert table["Vout"]["source"] == "limits.csv:7"
    assert table["I"]["source"] == "limits.csv:10"


def test_utf8_with_a_bom_is_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, "\ufeffname,low,units\nI,1,\u00b5A\n")
    table = load_limits("limits.csv")
    assert table == {"I": {"low": 1, "units": "\u00b5A", "source": "limits.csv:2"}}


def test_an_encoding_can_be_given(tmp_path):
    path = _csv(tmp_path, "name,low,units\nI,1,µA\n", encoding="cp1252")
    assert load_limits(path, encoding="cp1252")["I"]["units"] == "µA"


def test_a_wrong_encoding_names_the_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, "name,low,units\nV,1,V\nI,1,µA\n", encoding="cp1252")
    with pytest.raises(ValueError) as info:
        load_limits("limits.csv")
    text = str(info.value)
    assert text.startswith("load_limits(): limits.csv:3 is not utf-8-sig text")
    assert 'encoding="cp1252"' in text


def test_a_wrong_encoding_far_into_a_file_names_its_line(tmp_path):
    lines = ["name,low,units"] + [f"R{index},1,V" for index in range(2000)] + ["I,1,\u00b5A"]
    path = _csv(tmp_path, "\n".join(lines) + "\n", encoding="cp1252")
    with pytest.raises(ValueError, match=r"limits.csv:2002 is not utf-8-sig text"):
        load_limits(path)


def test_columns_renames_and_skips_columns(tmp_path):
    path = _csv(tmp_path, "Signal,Min,Max,Notes\nVout,3.2,3.4,see note 4\n")
    columns = {"signal": "name", "MIN": "low", "Max": " High ", "Notes": None}
    row = load_limits(path, columns=columns)["Vout"]
    assert {key: row[key] for key in ("low", "high")} == {"low": 3.2, "high": 3.4}
    assert "Notes" not in row and "notes" not in row


def test_columns_must_map_names_to_names_or_none(tmp_path):
    path = _csv(tmp_path, "name,Min\nVout,1\n")
    with pytest.raises(TypeError, match=r"columns maps a file's column name .*'Min': 5"):
        load_limits(path, columns={"Min": 5})


def test_an_unknown_column_is_an_error_that_suggests_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, "name,low,hi\nVout,1,2\n")
    with pytest.raises(ValueError) as info:
        load_limits("limits.csv")
    text = str(info.value)
    assert text.startswith("load_limits(): limits.csv has columns that are not limits: hi.")
    assert "columns={'hi': None}" in text and "select=" in text


@pytest.mark.parametrize(
    "text, column",
    [("name,low,,high\nVout,1,x,2\n", 3), ("name,low\nVout,1,5\n", 3), ("name,,low\nV,a,1\n", 2)],
)
def test_a_cell_under_an_empty_header_is_an_error(tmp_path, monkeypatch, text, column):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, text)
    with pytest.raises(ValueError, match=rf"^load_limits\(\): limits.csv:2 has '\w' in column "
                       rf"{column}, which has no name in the header$"):
        load_limits("limits.csv")


def test_an_empty_column_under_an_empty_header_is_fine(tmp_path):
    path = _csv(tmp_path, "name,low,,high\nVout,1,,2\n")
    assert load_limits(path)["Vout"]["high"] == 2


@pytest.mark.parametrize(
    "text, columns",
    [
        ("name,low,low\nVout,1,2\n", None),
        ("name,low,LOW \nVout,1,2\n", None),
        ("name,Min,low\nVout,1,2\n", {"Min": "low"}),
    ],
)
def test_a_repeated_column_is_an_error(tmp_path, text, columns):
    path = _csv(tmp_path, text)
    with pytest.raises(ValueError, match=r"has the column low twice"):
        load_limits(path, columns=columns)


def test_a_file_without_a_name_column_is_an_error(tmp_path):
    path = _csv(tmp_path, "signal,low\nVout,1\n")
    with pytest.raises(ValueError, match=r"has no 'name' column \(its header: signal, low\)"):
        load_limits(path)


@pytest.mark.parametrize("text", ["", "# only a comment\n\n"])
def test_a_file_without_a_header_is_an_error(tmp_path, text):
    path = _csv(tmp_path, text)
    with pytest.raises(ValueError, match=r"has no header line"):
        load_limits(path)


def test_a_line_without_a_name_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, "name,low\nVout,1\n,2\n")
    with pytest.raises(ValueError, match=r"^load_limits\(\): limits.csv:3 has no name$"):
        load_limits("limits.csv")


# ---------------------------------------------------------------------------
# load_limits: select
# ---------------------------------------------------------------------------

CORNERS = """\
name,expected,abs_tol,corner
Vout,3.3,0.1,
Iq,0.002,0.001,
Vout,3.0,0.1,cold
Vout,3.6,0.1,hot
"""


@pytest.mark.parametrize("corner, vout", [("hot", 3.6), ("cold", 3.0)])
def test_select_takes_a_line_that_names_the_value_over_a_default_line(
    tmp_path, corner, vout
):
    table = load_limits(_csv(tmp_path, CORNERS), select={"corner": corner})
    assert list(table) == ["Vout", "Iq"]
    assert table["Vout"]["expected"] == vout
    assert table["Iq"]["expected"] == 0.002
    assert "corner" not in table["Vout"]


def test_a_selector_cell_may_list_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, "name,low,sku\nVout,1,\nVout,2,A|C\nVout,3, B \n")
    assert load_limits("limits.csv", select={"sku": "A"})["Vout"]["source"] == "limits.csv:3"
    assert load_limits("limits.csv", select={"sku": "C"})["Vout"]["low"] == 2
    assert load_limits("limits.csv", select={"sku": "B"})["Vout"]["low"] == 3


def test_select_values_compare_as_text(tmp_path):
    path = _csv(tmp_path, "name,low,sku\nVout,1,\nVout,2,7\nVout,3,Vout\n")
    assert load_limits(path, select={"sku": 7})["Vout"]["low"] == 2
    assert load_limits(path, select={"sku": Channel.SEVEN})["Vout"]["low"] == 2
    assert load_limits(path, select={"sku": Channel.VOUT})["Vout"]["low"] == 3
    assert load_limits(path, select={"SKU": "7"})["Vout"]["low"] == 2


def test_a_select_value_no_line_names_is_an_error(tmp_path):
    path = _csv(tmp_path, CORNERS)
    with pytest.raises(ValueError, match=r"has corner = 'warm' \(it has: cold, hot\)"):
        load_limits(path, select={"corner": "warm"})
    with pytest.raises(ValueError, match=r"corner = 'HOT'"):
        load_limits(path, select={"corner": "HOT"})


def test_a_select_value_of_a_column_with_only_empty_cells_is_an_error(tmp_path):
    path = _csv(tmp_path, "name,low,corner\nVout,1,\nIq,2, \n")
    with pytest.raises(ValueError, match=r"has corner = 'hot' \(it has: nothing\)"):
        load_limits(path, select={"corner": "hot"})


def test_a_select_key_that_is_not_a_column_is_an_error(tmp_path):
    path = _csv(tmp_path, CORNERS)
    with pytest.raises(ValueError, match=r"select names corne, but .* has no such column"):
        load_limits(path, select={"corne": "hot"})


def test_select_keys_that_name_the_same_column_are_an_error(tmp_path):
    path = _csv(tmp_path, CORNERS)
    with pytest.raises(ValueError, match=r"corner"):
        load_limits(path, select={"corner": "hot", "CORNER": "cold"})


def test_columns_keys_that_name_the_same_column_are_an_error(tmp_path):
    path = _csv(tmp_path, "name,Min\nVout,1\n")
    with pytest.raises(ValueError, match=r"(?i)min"):
        load_limits(path, columns={"Min": "low", "MIN": "high"})


def test_a_selector_column_without_select_is_an_unknown_column(tmp_path):
    with pytest.raises(ValueError, match=r"columns that are not limits: corner\."):
        load_limits(_csv(tmp_path, CORNERS))


def test_a_selector_column_can_be_skipped(tmp_path):
    path = _csv(tmp_path, "name,low,corner\nVout,1,hot\nIq,2,\n")
    assert list(load_limits(path, columns={"corner": None})) == ["Vout", "Iq"]


@pytest.mark.parametrize(
    "text, select, lines",
    [
        ("name,low\nVout,1\nVout,2\n", None, "2, 3"),
        ("name,low,corner\nVout,1,hot\nVout,2,hot\n", {"corner": "hot"}, "2, 3"),
        ("name,low,corner\nVout,0,\nVout,1,hot\nVout,2,hot\n", {"corner": "hot"}, "3, 4"),
        (
            "name,low,corner,sku\nVout,1,hot,\nVout,2,,A\n",
            {"corner": "hot", "sku": "A"},
            "2, 3",
        ),
    ],
)
def test_lines_that_fit_equally_are_an_error(tmp_path, text, select, lines):
    path = _csv(tmp_path, text)
    with pytest.raises(ValueError, match=rf"2 lines for 'Vout' that fit equally \(lines {lines}\)"):
        load_limits(path, select=select)


def test_the_line_that_fits_best_breaks_a_tie_of_default_lines(tmp_path):
    path = _csv(tmp_path, "name,low,corner,sku\nVout,0,,\nVout,1,,\nVout,2,hot,\nVout,3,hot,A\n")
    assert load_limits(path, select={"corner": "hot", "sku": "A"})["Vout"]["low"] == 3
    path = _csv(tmp_path, "name,low,corner\nVout,0,\nVout,1,\nIq,1,hot\n")
    with pytest.raises(ValueError, match=r"2 lines for 'Vout' that fit equally \(lines 2, 3\)"):
        load_limits(path, select={"corner": "hot"})


# ---------------------------------------------------------------------------
# load_limits: cells
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cell, number",
    [
        ("0x1F", 31),
        ("0X1f", 31),
        ("-0x10", -16),
        ("0b101", 5),
        ("0o17", 15),
        ("007", 7),
        ("+7", 7),
        ("-5", -5),
        ("1e3", 1000.0),
        ("1E-3", 0.001),
        (".5", 0.5),
        ("5.", 5.0),
        (" 3 ", 3),
    ],
)
def test_number_cells(tmp_path, cell, number):
    row = load_limits(_csv(tmp_path, f"name,low\nA,{cell}\n"))["A"]
    assert row["low"] == number and isinstance(row["low"], type(number))
    assert type(row["low"]) is int or type(number) is float  # a float cell keeps its digits


@pytest.mark.parametrize(
    "cell", ["nan", "NaN", "inf", "-inf", "Infinity", "1_000", "1e400", "0x", "abc", "1.2.3", "١٢"]
)
def test_cells_that_are_not_finite_numbers_are_an_error(tmp_path, monkeypatch, cell):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, f"name,low\nA,{cell}\n")
    with pytest.raises(ValueError) as info:
        load_limits("limits.csv")
    assert str(info.value) == (
        f"load_limits(): limits.csv:2, column 'low': must be a number, got {cell!r}"
    )


@pytest.mark.parametrize(
    "cell, flag",
    [("true", True), ("False", False), ("YES", True), ("no", False), ("1", True), ("0", False)],
)
def test_inclusive_cells(tmp_path, cell, flag):
    row = load_limits(_csv(tmp_path, f"name,low,high,inclusive\nA,1,2,{cell}\n"))["A"]
    assert row["inclusive"] is flag


def test_an_inclusive_cell_that_is_not_a_flag_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, "name,low,high,inclusive\nA,1,2,maybe\n")
    with pytest.raises(ValueError, match=r"limits.csv:2, column 'inclusive': must be true or fa"):
        load_limits("limits.csv")


def test_check_cells_match_in_any_case_with_the_aliases(tmp_path):
    path = _csv(tmp_path, "name,check,low\nA,True,\nB,FALSE,\nC,Is_None,\nD,Greater,5\n")
    table = load_limits(path)
    assert [row["check"] for row in table.values()] == ["is_true", "is_false", "is_none", "greater"]


def test_units_and_pattern_stay_text(tmp_path):
    path = _csv(tmp_path, "name,check,pattern,low,units\nFW,matches,10,,\nV,,,1,007\n")
    table = load_limits(path)
    assert table["FW"]["pattern"] == "10" and type(table["FW"]["pattern"]) is str
    assert table["V"]["units"] == "007"


@pytest.mark.parametrize("check", ["is_instance", "IS_INSTANCE", " Is_Instance "])
def test_an_is_instance_line_is_refused(tmp_path, monkeypatch, check):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, f"name,check,expected_type\nA,{check},int\n")
    with pytest.raises(ValueError, match=r"^load_limits\(\): limits.csv:2: an is_instance row"):
        load_limits("limits.csv")


# ---------------------------------------------------------------------------
# load_limits: every line is validated, naming path:line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, message",
    [
        ("Vout,5,1,,,,,", r"row 'Vout': between\(\) low must not exceed high"),
        ("Vout,,,,,,,approxx", r"row 'Vout': unknown check 'approxx'"),
        ("Vout,1,,,,,,", None),
        ("Vout,,,3.3,,,,", r"row 'Vout': expected '3.3' is a float and there is no tolerance"),
        ("Vout,,,3,,1,,", r"row 'Vout': rel_tol .* below 1"),
        ("Vout,,,3,-1,,,", r"row 'Vout': approx\(\) abs_tol must be a non-negative number"),
        ("Vout,,,,,,,between", r"row 'Vout': between\(\) needs low, high"),
        ("Vout,,,1.5,,,,length", r"row 'Vout': expected must be a whole number of items"),
        ("Vout,,,abc,,,,length", r"column 'expected': must be a number, got 'abc'"),
        ("Vout,,,abc,0.1,,,", r"column 'expected': must be a number, got 'abc'"),
        ("Vout,1,,,,,5,", r"row 'Vout': give threshold or low, not both"),
        ("Vout,,,,,,,", r"row 'Vout' says neither which check"),
        ("Vout,,,3,0.1,,,equal", r"row 'Vout': equal\(\) takes expected, units; not: abs_tol"),
    ],
)
def test_every_line_is_validated_naming_its_place(tmp_path, monkeypatch, line, message):
    monkeypatch.chdir(tmp_path)
    header = "name,low,high,expected,abs_tol,rel_tol,threshold,check"
    _csv(tmp_path, f"{header}\nIq,0,1,,,,,\n{line}\n")
    if message is None:
        assert load_limits("limits.csv")["Vout"]["source"] == "limits.csv:3"
        return
    with pytest.raises(ValueError) as info:
        load_limits("limits.csv")
    text = str(info.value)
    assert text.startswith("load_limits(): limits.csv:3"), text
    assert re.search(message, text), text


def test_a_line_that_is_not_selected_is_validated_too(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, "name,low,high,corner\nVout,1,2,\nVout,5,1,cold\nVout,1,3,hot\n")
    with pytest.raises(ValueError, match=r"^load_limits\(\): limits.csv:3: row 'Vout': between"):
        load_limits("limits.csv", select={"corner": "hot"})


def test_the_source_names_the_file_as_given_relative_to_the_working_directory(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, "name,low\n\nVout,1\n", name="sub/limits.csv")
    assert load_limits("sub/limits.csv")["Vout"]["source"] == "sub/limits.csv:3"
    assert load_limits(tmp_path / "sub" / "limits.csv")["Vout"]["source"] == "sub/limits.csv:3"


def test_a_file_outside_the_working_directory_is_named_by_its_absolute_path(
    tmp_path, monkeypatch
):
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    path = _csv(tmp_path, "name,low\nVout,1\n")
    assert load_limits(path)["Vout"]["source"] == f"{path}:2"


def test_load_limits_returns_plain_rows(tmp_path):
    table = load_limits(_csv(tmp_path, "name,low\nVout,1\n"))
    row: LimitRow = table["Vout"]
    assert type(table) is dict and type(row) is dict
    json.dumps(table)


# ---------------------------------------------------------------------------
# CSV text cells: equal/not_equal expected and contains/not_contains needle
# ---------------------------------------------------------------------------


def _limits_of(tmp_path: Path, text: str, measured: Dict[str, Any]) -> Dict[str, Any]:
    table = load_limits(_csv(tmp_path, text))
    before = copy.deepcopy(table)
    _, verify = _recording()
    out = verify.limits(measured, table)
    assert table == before
    for record in out.values():
        json.dumps(record, allow_nan=False)
        assert checks.evaluate(record) is record["passed"]
        assert checks.evaluate(_round_trip(record)) is record["passed"]
    return out


EQUALS = "name,check,expected\nFW,equal,1.10\nN,equal,007\nB,equal,yes\nX,not_equal,abc\n"


def test_a_text_cell_is_text_against_text(tmp_path):
    out = _limits_of(tmp_path, EQUALS, {"FW": "1.10", "N": "7", "B": "yes", "X": "abd"})
    assert [out[name]["passed"] for name in out] == [True, False, True, True]
    assert out["FW"]["expected"] == "1.10" and type(out["FW"]["expected"]) is str
    assert out["N"]["detail"] == "expected '007', got '7'"
    assert "error" not in out["N"]


def test_a_text_cell_stays_text_against_other_text(tmp_path):
    out = _limits_of(tmp_path, EQUALS, {"FW": "1.1", "N": "007", "B": "yes", "X": "abc"})
    assert out["FW"]["passed"] is False
    assert out["FW"]["detail"] == "expected '1.10', got '1.1'"


def test_a_text_cell_is_a_number_against_a_number(tmp_path):
    out = _limits_of(tmp_path, EQUALS.replace("yes", "1"), {"FW": 1.1, "N": 7, "B": 1, "X": 5})
    assert [out[name]["passed"] for name in ("FW", "N", "B")] == [True, True, True]
    assert out["N"]["expected"] == 7 and type(out["N"]["expected"]) is int
    assert out["N"]["detail"] == "7 == 7"
    assert out["FW"]["expected"] == 1.1


def test_a_text_cell_that_cannot_be_a_number_fails_against_a_number(tmp_path):
    out = _limits_of(tmp_path, "name,check,expected\nX,equal,abc\nY,not_equal,abc\n",
                     {"X": 5, "Y": 5})
    for name in ("X", "Y"):
        assert out[name]["passed"] is False
        assert out[name]["error"] == "the limit 'abc' is text, and the measurement is a number"
        assert out[name]["expected"] == "abc"


def test_a_text_cell_is_a_flag_against_a_bool(tmp_path):
    text = "name,check,expected\nA,equal,yes\nB,not_equal,TRUE\nC,equal,1.10\n"
    out = _limits_of(tmp_path, text, {"A": True, "B": False, "C": True})
    assert (out["A"]["passed"], out["B"]["passed"]) == (True, True)
    assert out["A"]["expected"] is True
    assert out["C"]["passed"] is False
    assert out["C"]["error"] == (
        "the limit '1.10' is not true or false, and the measurement is a bool"
    )


def test_a_text_cell_against_a_measurement_it_cannot_stand_for_fails(tmp_path):
    out = _limits_of(tmp_path, "name,check,expected\nA,equal,1\nB,not_equal,1\n",
                     {"A": [1], "B": b"1"})
    assert out["A"]["passed"] is False and out["B"]["passed"] is False
    assert out["A"]["error"].startswith(
        "a CSV limit is text or a number, and the measurement is list"
    )
    assert "Python table" in out["B"]["error"]


def test_a_decimal_comma_text_cell_is_a_number_with_semicolons(tmp_path):
    text = "name;check;expected\nX;equal;3,3\n"
    assert _limits_of(tmp_path, text, {"X": 3.3})["X"]["passed"] is True
    assert _limits_of(tmp_path, text, {"X": "3,3"})["X"]["expected"] == "3,3"
    out = _limits_of(tmp_path, "name,check,expected\nX,equal,\"3,3\"\n", {"X": 3.3})
    assert out["X"]["passed"] is False and "is text" in out["X"]["error"]


def test_an_inferred_equal_cell_without_a_fraction_is_fine(tmp_path):
    out = _limits_of(tmp_path, "name,expected\nN,007\nFW,abc\n", {"N": 7, "FW": "abc"})
    assert [record["check_type"] for record in out.values()] == ["equal", "equal"]
    assert [record["passed"] for record in out.values()] == [True, True]


def test_a_missing_measurement_keeps_a_text_cell_as_text(tmp_path):
    out = _limits_of(tmp_path, "name,check,expected,low\nN,equal,007,\nI,,,0\n", {"I": 1})
    assert out["N"]["expected"] == "007"
    assert out["N"]["error"].startswith("not measured")


def test_a_text_cell_takes_a_decimal_measurements_type(tmp_path):
    out = _limits_of(tmp_path, "name,check,expected\nV,equal,1.10\n", {"V": Decimal("1.10")})
    assert out["V"]["passed"] is True


NEEDLES = "name,check,needle\nH,contains,3\nS,contains,ab\nT,not_contains,4\n"


def test_a_needle_cell_takes_the_type_of_the_haystack_items(tmp_path):
    out = _limits_of(tmp_path, NEEDLES, {"H": [1, 2, 3], "S": "xaby", "T": (1.5, 2)})
    assert [record["passed"] for record in out.values()] == [True, True, True]
    assert out["H"]["needle"] == 3 and type(out["H"]["needle"]) is int
    assert out["H"]["detail"] == "contains 3"
    assert out["S"]["needle"] == "ab"


def test_a_needle_cell_in_sets_dicts_and_ranges(tmp_path):
    out = _limits_of(tmp_path, NEEDLES, {"H": {3: "x"}, "S": ["ab", "cd"], "T": range(3)})
    assert [record["passed"] for record in out.values()] == [True, True, True]
    out = _limits_of(tmp_path, NEEDLES, {"H": {1, 2}, "S": [], "T": frozenset({4})})
    assert [record["passed"] for record in out.values()] == [False, False, False]
    assert all("error" not in record for record in out.values())


def test_a_needle_cell_in_a_mixed_haystack_fails_with_an_error(tmp_path):
    out = _limits_of(tmp_path, NEEDLES, {"H": [1, "a"], "S": "ab", "T": [True, 4]})
    for name in ("H", "T"):
        assert out[name]["passed"] is False
        assert "or a mix" in out[name]["error"]
        assert "Python table" in out[name]["error"]
    assert out["S"]["passed"] is True


def test_a_needle_cell_that_is_not_a_number_fails_against_numbers(tmp_path):
    out = _limits_of(tmp_path, "name,check,needle\nH,contains,x\n", {"H": [1, 2]})
    assert out["H"]["passed"] is False
    assert out["H"]["error"].startswith("the limit 'x' is text")


def test_a_needle_error_does_not_call_a_list_a_number(tmp_path):
    out = _limits_of(tmp_path, "name,check,needle\nH,contains,x\n", {"H": [1, 2]})
    assert "the measurement is a number" not in out["H"]["error"]


@pytest.mark.parametrize("haystack", [33, b"ab", iter([1, 2])])
def test_a_needle_cell_against_a_haystack_it_cannot_type_fails(tmp_path, haystack):
    out = _limits_of(tmp_path, "name,check,needle\nH,contains,3\n", {"H": haystack})
    assert out["H"]["passed"] is False
    assert out["H"]["error"].startswith("a CSV needle is text or a number")


# ---------------------------------------------------------------------------
# Bug and corner-case hunt
# ---------------------------------------------------------------------------


class Unit(str, enum.Enum):
    VOLT = "V"


EXACT = (
    "name,check,low,high,expected,abs_tol\n"
    "Vmin,,3.2,,,\nVmax,,,3.4,,\nRange,,3.2,3.4,,\nNear,approx,,,3.3,0.1\nCount,,3,,,\n"
)


@pytest.mark.parametrize("kind", [Decimal, Fraction])
def test_csv_number_cells_take_a_decimal_or_fraction_measurements_type(tmp_path, kind):
    measured = {"Vmin": "3.2", "Vmax": "3.4", "Range": "3.4", "Near": "3.4", "Count": "3"}
    out = _limits_of(tmp_path, EXACT, {name: kind(text) for name, text in measured.items()})
    assert [record["passed"] for record in out.values()] == [True] * 5
    assert out["Range"]["detail"].startswith(f"{kind('3.4')} ∈ [{kind('3.2')}, ")
    built = checks.limits({"Near": kind("3.4")}, load_limits(_csv(tmp_path, EXACT)))
    assert (built["Near"]["expected"], built["Near"]["abs_tol"]) == (kind("3.3"), kind("0.1"))
    assert type(built["Near"]["expected"]) is kind
    assert type(built["Count"]["threshold"]) is int


def test_csv_number_cells_with_a_decimal_comma_take_a_decimals_type(tmp_path):
    out = _limits_of(tmp_path, "name;low;high\nV;3,2;3,4\n", {"V": Decimal("3.4")})
    assert out["V"]["passed"] is True


def test_csv_number_cells_are_plain_floats_against_anything_else(tmp_path):
    table = load_limits(_csv(tmp_path, EXACT))
    assert table["Vmin"]["low"] == 3.2 and isinstance(table["Vmin"]["low"], float)
    assert json.loads(json.dumps(table))["Near"] == {
        "check": "approx", "expected": 3.3, "abs_tol": 0.1, "source": table["Near"]["source"]
    }
    for value in (3.3, 3, None, "3.3"):
        built = checks.limits({"Vmin": value, "Near": value}, table)
        assert type(built["Vmin"]["threshold"]) is float
        assert type(built["Near"]["expected"]) is float and type(built["Near"]["abs_tol"]) is float
    out = _limits_of(tmp_path, EXACT, {"Vmin": 3.2, "Vmax": 3.4, "Range": 3.3, "Near": 3.35,
                                       "Count": 3})
    assert all(record["passed"] for record in out.values())
    assert type(out["Vmin"]["threshold"]) is float


def test_a_python_tables_limits_stay_as_given_against_a_decimal():
    _, verify = _recording()
    out = verify.limits({"V": Decimal("3.2")}, {"V": {"low": 3.2}})
    assert out["V"]["threshold"] == 3.2
    assert out["V"]["passed"] is False  # Decimal('3.2') < 3.2 in binary, as with greater_equal()


def test_a_needle_cell_in_any_collection_that_can_be_read_again(tmp_path):
    measured = {
        "H": collections.deque([1, 2, 3]),
        "S": {"ab": 1, "cd": 2}.keys(),
        "T": array.array("i", [1, 2]),
    }
    out = _limits_of(tmp_path, NEEDLES, measured)
    assert [record["passed"] for record in out.values()] == [True, True, True]
    assert all("error" not in record for record in out.values())
    text = "name,check,needle\nV,contains,ab\nL,not_contains,x\n"
    out = _limits_of(tmp_path, text, {"V": {1: "ab"}.values(), "L": collections.UserList("ab")})
    assert [record["passed"] for record in out.values()] == [True, True]


class _Unreadable(collections.abc.Collection):
    """A collection whose items cannot be read, like a 0-d numpy array."""

    def __len__(self) -> int:
        return 1

    def __iter__(self) -> Any:
        raise TypeError("iteration over a 0-d array")

    def __contains__(self, item: object) -> bool:
        return False


def test_a_needle_cell_against_a_collection_that_cannot_be_read_fails(tmp_path):
    out = _limits_of(tmp_path, "name,check,needle\nH,contains,3\n", {"H": _Unreadable()})
    assert out["H"]["passed"] is False
    assert out["H"]["error"].startswith("a CSV needle is text or a number, and _Unreadable")


def test_a_needle_cell_against_an_iterator_says_it_is_read_once(tmp_path):
    out = _limits_of(tmp_path, "name,check,needle\nH,contains,3\n", {"H": iter([1, 2, 3])})
    assert out["H"]["passed"] is False
    assert out["H"]["error"] == (
        "a CSV needle is text or a number, and a list_iterator can be read only once: "
        "pass a list"
    )


@pytest.mark.parametrize(
    "method, args",
    [
        ("equal", (3.3, 3.3)),
        ("not_equal", (3.3, 3.2)),
        ("greater", (3.3, 3.2)),
        ("greater_equal", (3.3, 3.2)),
        ("less", (3.1, 3.2)),
        ("less_equal", (3.1, 3.2)),
    ],
)
def test_str_enum_units_are_stored_and_shown_as_text(method, args):
    _, verify = _recording()
    record = getattr(verify, method)(*args, name="V", units=Unit.VOLT)
    built = getattr(checks, method)(*args, name="V", units=Unit.VOLT)
    for check in (record, built):
        assert check["units"] == "V" and type(check["units"]) is str
        assert "3.2V" in check["description"] or "3.3V" in check["description"]
        assert "Unit" not in check["description"]
    assert "Unit" not in record["detail"] and "V" in record["detail"]


def test_str_enum_units_of_approx_between_and_limits_are_text():
    _, verify = _recording()
    approx = verify.approx(3.31, 3.3, abs_tol=0.05, name="A", units=Unit.VOLT)
    between = verify.between(3.3, 3.2, 3.4, name="B", units=Unit.VOLT)
    out = verify.limits({"L": 3.3}, {"L": {"low": 3.2, "units": Unit.VOLT}})
    assert approx["description"] == "Verify 'A' == 3.3V ± 0.05V"
    assert between["description"] == "Verify 'B' ∈ [3.2V, 3.4V]"
    assert out["L"]["description"] == "Verify 'L' >= 3.2V"
    assert out["L"]["detail"] == "3.3V >= 3.2V"
    assert [approx["units"], between["units"], out["L"]["units"]] == ["V"] * 3


def test_str_enum_units_of_a_hand_built_check_are_shown_as_text():
    _, verify = _recording()
    check = {"check_type": "greater", "name": "V", "description": "V > 2V", "actual": 3,
             "threshold": 2, "units": Unit.VOLT}
    assert verify.record(check)["detail"] == "3V > 2V"


def test_big_ints_fractions_and_finite_decimals_are_valid_limits(tmp_path):
    _, verify = _recording()
    table = {
        "N": {"low": 10**320},
        "D": {"low": Decimal("1E+400")},
        "F": {"high": Fraction(10**401, 3)},
    }
    out = verify.limits({"N": 10**330, "D": Decimal("1E+401"), "F": Fraction(10**400, 3)}, table)
    assert [record["passed"] for record in out.values()] == [True, True, True]
    json.dumps(out, allow_nan=False)
    row = load_limits(_csv(tmp_path, f"name,low\nN,1{'0' * 320}\n"))["N"]
    assert row["low"] == 10**320


@pytest.mark.parametrize("limit", [Decimal("Infinity"), Decimal("-Infinity"), Decimal("sNaN")])
def test_decimals_that_are_not_finite_are_still_refused(limit):
    _, verify = _recording()
    with pytest.raises(ValueError, match=r"row 'X': low must be a finite number"):
        verify.limits({"X": 1}, {"X": {"low": limit}})


@pytest.mark.parametrize("first", ["name,low,high,units", "low,name,high,units"])
def test_a_bom_is_skipped_whatever_the_encoding(tmp_path, monkeypatch, first):
    monkeypatch.chdir(tmp_path)
    cells = {"name": "Vout", "low": "3.2", "high": "3.4", "units": "V"}
    line = ",".join(cells[column] for column in first.split(","))
    _csv(tmp_path, f"﻿{first}\r\n{line}\r\n")
    for encoding in ("utf-8", "utf-8-sig"):
        assert load_limits("limits.csv", encoding=encoding) == {
            "Vout": {"low": 3.2, "high": 3.4, "units": "V", "source": "limits.csv:2"}
        }


def test_a_thousands_separator_gets_its_own_hint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, "name;low;high;units\nPower;1.000,5;2000;W\n")
    with pytest.raises(ValueError) as info:
        load_limits("limits.csv")
    assert str(info.value) == (
        "load_limits(): limits.csv:2, column 'low': must be a number, got '1.000,5' "
        "(remove the thousands separator: 1000,5)"
    )
    _csv(tmp_path, "name;low\nPower;1,5.3\n")
    with pytest.raises(ValueError, match=r"got '1,5.3'$"):
        load_limits("limits.csv")


def test_a_misspelt_columns_target_names_the_mapping(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _csv(tmp_path, "Test,Min,Max\nVout,3.2,3.4\n")
    with pytest.raises(ValueError) as info:
        load_limits("limits.csv", columns={"Test": "name", "Min": "lo", "Max": "high"})
    text = str(info.value)
    assert text.startswith(
        "load_limits(): columns= maps the column 'Min' of limits.csv to 'lo', which is not a "
        "limit (did you mean 'low'?). Limit columns: name, check, "
    ), text
    assert "None" not in text


@pytest.mark.parametrize("target", ["", "  "])
def test_an_empty_columns_target_is_an_error(tmp_path, target):
    path = _csv(tmp_path, "name,Min,Max\nVout,3.2,3.4\n")
    with pytest.raises(ValueError, match=r"columns maps 'Max' to an empty name; skip a column "):
        load_limits(path, columns={"Min": "low", "Max": target})


def test_the_limits_docstring_says_when_a_value_error_is_raised():
    doc = " ".join((Verify.limits.__doc__ or "").split())
    assert 'on_missing is "ignore" and no row has a measurement' in doc
    assert "``low`` can stand for the ``threshold`` of ``greater``/``greater_equal``" in doc
    assert "and ``high`` for that of ``less``/``less_equal``" in doc


@pytest.mark.parametrize(
    "row, message",
    [
        ({"expected": 3.0}, r"row 'V': expected 3\.0 is a float and there is no tolerance: "),
        ({"expected": 1e3}, r"row 'V': expected 1000\.0 is a float and there is no tolerance"),
        ({"check": "greater"}, r"row 'V': greater\(\) needs threshold \(or low\)$"),
        ({"check": "less_equal"}, r"row 'V': less_equal\(\) needs threshold \(or high\)$"),
        ({"check": "between", "low": 1}, r"row 'V': between\(\) needs high$"),
    ],
)
def test_row_errors_say_what_is_wrong(row, message):
    _, verify = _recording()
    with pytest.raises((TypeError, ValueError), match=message):
        verify.limits({"V": 3}, {"V": row})


def test_the_checks_are_returned_by_the_tables_keys():
    run, verify = _recording()
    table = {"V\ud800": VALID_ROW, "V\\ud800": VALID_ROW, Rail.V3: VALID_ROW}
    out = verify.limits({"V\ud800": 1, "V\\ud800": 1, "3V3": 1}, table)
    assert list(out) == ["V\ud800", "V\\ud800", "3V3"]
    assert list(out.values()) == run.records
    assert [record["name"] for record in run.records] == ["V\\ud800", "V\\ud800", "3V3"]


def test_a_not_measured_error_is_bounded():
    _, verify = _recording()
    name = "V" * 3000
    record = verify.limits({"Other": 1}, {name: VALID_ROW, "Other": VALID_ROW})[name]
    assert record["error"].startswith("not measured: the measurements have no 'VVV")
    assert len(record["error"]) < 300 and len(record["detail"]) < 350


def test_a_missing_measurement_names_a_key_that_differs_in_case():
    _, verify = _recording()
    table = {"VOUT": VALID_ROW, "FW": VALID_ROW, "Iq": VALID_ROW}
    out = verify.limits({"Vout": 3.3, "fw": 2, "IQ_max": 1, "iq": 1}, table)
    assert out["VOUT"]["error"] == "not measured: the measurements have no 'VOUT'; is it 'Vout'?"
    assert out["FW"]["error"] == "not measured: the measurements have no 'FW'; is it 'fw'?"
    assert out["Iq"]["error"] == "not measured: the measurements have no 'Iq'; is it 'iq'?"
