"""Tests for descriptor builder functions and CheckDescriptor shape."""
from __future__ import annotations

import pytest

from tests._builders import (
    build_all_satisfy,
    build_approx,
    build_between,
    build_conditional,
    build_contains,
    build_equal,
    build_fail,
    build_greater,
    build_greater_equal,
    build_guard,
    build_is_false,
    build_is_instance,
    build_is_none,
    build_is_not_none,
    build_is_true,
    build_length,
    build_less,
    build_less_equal,
    build_matches,
    build_not_contains,
    build_not_equal,
)


# ── Equality & Approximation ────────────────────────────────────────


class TestBuildEqual:
    def test_check_type(self):
        d = build_equal(1, 1, name="X")
        assert d["check_type"] == "equal"

    def test_description_without_units(self):
        d = build_equal(5, 5, name="Vout")
        assert d["description"] == "Verify 'Vout' == 5"

    def test_description_with_units(self):
        d = build_equal(5, 5, name="Vout", units="V")
        assert d["description"] == "Verify 'Vout' == 5V"

    def test_fields(self):
        d = build_equal(3, 4, name="X", units="A")
        assert d["actual"] == 3
        assert d["expected"] == 4
        assert d["units"] == "A"

    def test_no_passed_field(self):
        d = build_equal(1, 1, name="X")
        assert "passed" not in d


class TestBuildNotEqual:
    def test_check_type(self):
        d = build_not_equal(1, 2, name="X")
        assert d["check_type"] == "not_equal"

    def test_description(self):
        d = build_not_equal(1, 2, name="Y", units="V")
        assert d["description"] == "Verify 'Y' != 2V"


class TestBuildApprox:
    def test_check_type(self):
        d = build_approx(3.3, 3.3, abs_tol=0.1, name="V")
        assert d["check_type"] == "approx"

    def test_raises_without_tolerance(self):
        with pytest.raises(ValueError, match="at least one"):
            build_approx(3.3, 3.3, name="V")

    def test_description_abs_tol_with_units(self):
        d = build_approx(3.28, 3.3, abs_tol=0.05, name="Vout", units="V")
        assert d["description"] == "Verify 'Vout' == 3.3V \u00b1 0.05V"

    def test_description_rel_tol(self):
        d = build_approx(100, 100, rel_tol=0.01, name="T")
        assert d["description"] == "Verify 'T' == 100 \u00b1 1%"

    def test_description_both_tol(self):
        d = build_approx(5, 5, abs_tol=0.1, rel_tol=0.02, name="X", units="A")
        assert d["description"] == "Verify 'X' == 5A \u00b1 0.1A (abs) \u00b1 2% (rel)"

    def test_description_non_round_rel_tol_has_no_float_noise(self):
        # 0.007 * 100 == 0.7000000000000001 in IEEE 754; the rendered percent
        # must not leak that artifact.
        d = build_approx(100, 100, rel_tol=0.007, name="T")
        assert d["description"] == "Verify 'T' == 100 \u00b1 0.7%"

    def test_fields(self):
        d = build_approx(3.28, 3.3, abs_tol=0.05, name="V")
        assert d["abs_tol"] == 0.05
        assert d["rel_tol"] is None


# ── Ordering & Range ─────────────────────────────────────────────────


class TestBuildGreater:
    def test_check_type(self):
        d = build_greater(10, 5, name="T")
        assert d["check_type"] == "greater"

    def test_description(self):
        d = build_greater(10, 100, name="T", units="Mbps")
        assert d["description"] == "Verify 'T' > 100Mbps"

    def test_fields(self):
        d = build_greater(10, 5, name="T")
        assert d["actual"] == 10
        assert d["threshold"] == 5


class TestBuildGreaterEqual:
    def test_check_type(self):
        assert build_greater_equal(5, 5, name="X")["check_type"] == "greater_equal"

    def test_description(self):
        d = build_greater_equal(5, 5, name="X", units="V")
        assert d["description"] == "Verify 'X' >= 5V"


class TestBuildLess:
    def test_check_type(self):
        assert build_less(3, 5, name="X")["check_type"] == "less"

    def test_description(self):
        d = build_less(3, 5, name="X", units="A")
        assert d["description"] == "Verify 'X' < 5A"


class TestBuildLessEqual:
    def test_check_type(self):
        assert build_less_equal(5, 5, name="X")["check_type"] == "less_equal"

    def test_description(self):
        d = build_less_equal(5, 5, name="X")
        assert d["description"] == "Verify 'X' <= 5"


class TestBuildBetween:
    def test_check_type(self):
        d = build_between(0.3, 0.1, 0.5, name="I")
        assert d["check_type"] == "between"

    def test_description_inclusive_with_units(self):
        d = build_between(0.3, 0.1, 0.5, name="I", units="A")
        assert d["description"] == "Verify 'I' \u2208 [0.1A, 0.5A]"

    def test_description_exclusive_without_units(self):
        d = build_between(0.3, 0.1, 0.5, inclusive=False, name="I")
        assert d["description"] == "Verify 'I' \u2208 (0.1, 0.5)"

    def test_fields(self):
        d = build_between(0.3, 0.1, 0.5, inclusive=False, name="I")
        assert d["low"] == 0.1
        assert d["high"] == 0.5
        assert d["inclusive"] is False


# ── Boolean & Identity ───────────────────────────────────────────────


class TestBuildIsTrue:
    def test_check_type(self):
        assert build_is_true(True, name="Alive")["check_type"] == "true"

    def test_description(self):
        d = build_is_true(True, name="Alive")
        assert d["description"] == "Verify 'Alive' is True"


class TestBuildIsFalse:
    def test_check_type(self):
        assert build_is_false(False, name="Dead")["check_type"] == "false"

    def test_description(self):
        d = build_is_false(False, name="Dead")
        assert d["description"] == "Verify 'Dead' is False"


class TestBuildIsNone:
    def test_check_type(self):
        assert build_is_none(None, name="X")["check_type"] == "is_none"

    def test_description(self):
        assert build_is_none(None, name="X")["description"] == "Verify 'X' is None"


class TestBuildIsNotNone:
    def test_check_type(self):
        assert build_is_not_none(42, name="X")["check_type"] == "is_not_none"

    def test_description(self):
        assert build_is_not_none(42, name="X")["description"] == "Verify 'X' is not None"


# ── String & Container ───────────────────────────────────────────────


class TestBuildContains:
    def test_check_type(self):
        assert build_contains("hello world", "world", name="Msg")["check_type"] == "contains"

    def test_description(self):
        d = build_contains("hello", "ell", name="Msg")
        assert d["description"] == "Verify 'Msg' contains 'ell'"

    def test_fields(self):
        d = build_contains([1, 2, 3], 2, name="List")
        assert d["haystack"] == [1, 2, 3]
        assert d["needle"] == 2


class TestBuildNotContains:
    def test_check_type(self):
        assert build_not_contains("hello", "xyz", name="Msg")["check_type"] == "not_contains"

    def test_description(self):
        d = build_not_contains("hello", "xyz", name="Msg")
        assert d["description"] == "Verify 'Msg' does not contain 'xyz'"


class TestBuildMatches:
    def test_check_type(self):
        assert build_matches("abc123", r"\d+", name="Code")["check_type"] == "matches"

    def test_description(self):
        d = build_matches("abc", r"\d+", name="Code")
        assert d["description"] == r"Verify 'Code' matches /\d+/"

    def test_fields(self):
        d = build_matches("abc", r"\d+", name="Code")
        assert d["pattern"] == r"\d+"


# ── Type / Collection / Conditional ──────────────────────────────────


class TestBuildIsInstance:
    def test_check_type(self):
        assert build_is_instance({}, dict, name="Data")["check_type"] == "is_instance"

    def test_stores_type_as_string(self):
        d = build_is_instance({}, dict, name="Data")
        assert d["expected_type"] == "dict"
        assert isinstance(d["expected_type"], str)

    def test_description(self):
        d = build_is_instance([], list, name="Items")
        assert d["description"] == "Verify 'Items' is instance of list"


class TestBuildLength:
    def test_check_type(self):
        assert build_length([1, 2, 3], 3, name="Items")["check_type"] == "length"

    def test_stores_actual_length(self):
        d = build_length([1, 2], 3, name="Items")
        assert d["actual_length"] == 2
        assert d["expected"] == 3

    def test_description(self):
        d = build_length("abc", 3, name="Str")
        assert d["description"] == "Verify 'Str' has length 3"


class TestBuildAllSatisfy:
    def test_check_type(self):
        factory = lambda x: build_greater(x, 0, name=f"item_{x}")
        d = build_all_satisfy([1, 2, 3], factory, name="Positives")
        assert d["check_type"] == "all_satisfy"

    def test_invokes_factory_at_call_time(self):
        call_count = 0

        def factory(x):
            nonlocal call_count
            call_count += 1
            return build_greater(x, 0, name=f"item_{x}")

        d = build_all_satisfy([1, 2, 3], factory, name="Positives")
        assert call_count == 3
        assert len(d["child_checks"]) == 3

    def test_empty_list(self):
        factory = lambda x: build_greater(x, 0, name="X")
        d = build_all_satisfy([], factory, name="Empty")
        assert d["child_checks"] == []

    def test_description_includes_item_count(self):
        factory = lambda x: build_greater(x, 0, name=f"item_{x}")
        d = build_all_satisfy([1, 2, 3, 4], factory, name="All channels within spec")
        assert d["description"] == "Verify all items in 'All channels within spec' satisfy condition (4 items)"


class TestBuildConditional:
    def test_check_type(self):
        cases = {"1": build_equal(1, 1, name="case1")}
        d = build_conditional(1, cases=cases, name="M")
        assert d["check_type"] == "conditional"

    def test_stringifies_switch_value(self):
        cases = {"1": build_equal(1, 1, name="case1")}
        d = build_conditional(1, cases=cases, name="M")
        assert d["matched_case"] == "1"

    def test_no_match(self):
        cases = {"1": build_equal(1, 1, name="case1")}
        d = build_conditional(99, cases=cases, name="M")
        assert d["matched_case"] is None

    def test_int_keys_are_normalized_and_match(self):
        # Natural usage passes the case keys as the same type as switch_value
        # (ints here). They must be normalized to str so the lookup matches.
        cases = {0: build_equal(1, 2, name="c0"), 1: build_equal(1, 1, name="c1")}
        d = build_conditional(1, cases=cases, name="M")
        assert d["matched_case"] == "1"
        assert set(d["cases"].keys()) == {"0", "1"}

    def test_description(self):
        cases = {"1": build_equal(1, 1, name="case1")}
        d = build_conditional(1, cases=cases, name="M")
        assert d["description"] == "Verify 'M' [mode=1]"


class TestBuildGuard:
    def test_check_type(self):
        d = build_guard([(True, "a", build_equal(1, 1, name="a"))], name="G")
        assert d["check_type"] == "guard"

    def test_matched_index_is_first_true(self):
        d = build_guard(
            [
                (False, "a", build_equal(1, 1, name="a")),
                (True, "b", build_equal(1, 1, name="b")),
                (True, "c", build_equal(1, 1, name="c")),
            ],
            name="G",
        )
        assert d["matched_index"] == 1

    def test_matched_index_none_when_all_false(self):
        d = build_guard([(False, "a", build_equal(1, 1, name="a"))], name="G")
        assert d["matched_index"] is None

    def test_branches_normalized_to_bool_and_structure(self):
        d = build_guard([(1, "a", build_equal(1, 1, name="a"))], name="G")
        branch = d["branches"][0]
        assert branch["condition"] is True  # truthy normalized to bool
        assert branch["label"] == "a"
        assert branch["check"]["check_type"] == "equal"

    def test_description(self):
        d = build_guard([(True, "a", build_equal(1, 1, name="a"))], name="Sensor")
        assert d["description"] == "Verify 'Sensor' [guarded]"


class TestBuildFail:
    def test_check_type(self):
        assert build_fail("oops")["check_type"] == "fail"

    def test_name_defaults_to_msg(self):
        d = build_fail("something broke")
        assert d["name"] == "something broke"

    def test_name_override(self):
        d = build_fail("something broke", name="CustomName")
        assert d["name"] == "CustomName"

    def test_description(self):
        d = build_fail("power rail failed")
        assert d["description"] == "FAIL: power rail failed"
