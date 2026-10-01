"""Tests for the evaluator module — evaluate() and evaluate_detailed()."""
from __future__ import annotations

from pytest_verify._descriptors import (
    build_approx,
    build_between,
    build_contains,
    build_equal,
    build_fail,
    build_greater,
    build_greater_equal,
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
    build_all_satisfy,
    build_conditional,
    build_guard,
)
from pytest_verify._evaluator import evaluate, evaluate_detailed


# ── Per-check-type pass/fail ─────────────────────────────────────────


class TestEqualEval:
    def test_pass(self):
        assert evaluate(build_equal(5, 5, name="X")) is True

    def test_fail(self):
        assert evaluate(build_equal(5, 6, name="X")) is False


class TestNotEqualEval:
    def test_pass(self):
        assert evaluate(build_not_equal(5, 6, name="X")) is True

    def test_fail(self):
        assert evaluate(build_not_equal(5, 5, name="X")) is False


class TestApproxEval:
    def test_pass_abs_tol(self):
        assert evaluate(build_approx(3.28, 3.3, abs_tol=0.05, name="V")) is True

    def test_fail_abs_tol(self):
        assert evaluate(build_approx(3.0, 3.3, abs_tol=0.05, name="V")) is False

    def test_pass_rel_tol(self):
        assert evaluate(build_approx(101, 100, rel_tol=0.02, name="V")) is True

    def test_fail_rel_tol(self):
        assert evaluate(build_approx(120, 100, rel_tol=0.02, name="V")) is False

    def test_pass_if_either_tolerance_satisfied(self):
        # abs_tol fails (diff=5 > 0.01) but rel_tol passes (diff=5% < 10%)
        assert evaluate(build_approx(105, 100, abs_tol=0.01, rel_tol=0.1, name="V")) is True


class TestGreaterEval:
    def test_pass(self):
        assert evaluate(build_greater(10, 5, name="X")) is True

    def test_fail_equal(self):
        assert evaluate(build_greater(5, 5, name="X")) is False

    def test_fail_less(self):
        assert evaluate(build_greater(3, 5, name="X")) is False


class TestGreaterEqualEval:
    def test_pass_greater(self):
        assert evaluate(build_greater_equal(10, 5, name="X")) is True

    def test_pass_equal(self):
        assert evaluate(build_greater_equal(5, 5, name="X")) is True

    def test_fail(self):
        assert evaluate(build_greater_equal(3, 5, name="X")) is False


class TestLessEval:
    def test_pass(self):
        assert evaluate(build_less(3, 5, name="X")) is True

    def test_fail_equal(self):
        assert evaluate(build_less(5, 5, name="X")) is False

    def test_fail_greater(self):
        assert evaluate(build_less(10, 5, name="X")) is False


class TestLessEqualEval:
    def test_pass_less(self):
        assert evaluate(build_less_equal(3, 5, name="X")) is True

    def test_pass_equal(self):
        assert evaluate(build_less_equal(5, 5, name="X")) is True

    def test_fail(self):
        assert evaluate(build_less_equal(10, 5, name="X")) is False


class TestBetweenEval:
    def test_pass_inclusive(self):
        assert evaluate(build_between(0.3, 0.1, 0.5, name="I")) is True

    def test_pass_inclusive_boundary_low(self):
        assert evaluate(build_between(0.1, 0.1, 0.5, name="I")) is True

    def test_pass_inclusive_boundary_high(self):
        assert evaluate(build_between(0.5, 0.1, 0.5, name="I")) is True

    def test_fail_inclusive(self):
        assert evaluate(build_between(0.6, 0.1, 0.5, name="I")) is False

    def test_pass_exclusive(self):
        assert evaluate(build_between(0.3, 0.1, 0.5, inclusive=False, name="I")) is True

    def test_fail_exclusive_boundary(self):
        assert evaluate(build_between(0.1, 0.1, 0.5, inclusive=False, name="I")) is False
        assert evaluate(build_between(0.5, 0.1, 0.5, inclusive=False, name="I")) is False


class TestIsTrueEval:
    def test_pass(self):
        assert evaluate(build_is_true(True, name="Alive")) is True

    def test_pass_truthy(self):
        assert evaluate(build_is_true(1, name="X")) is True
        assert evaluate(build_is_true("nonempty", name="X")) is True

    def test_fail(self):
        assert evaluate(build_is_true(False, name="X")) is False

    def test_fail_falsy(self):
        assert evaluate(build_is_true(0, name="X")) is False
        assert evaluate(build_is_true("", name="X")) is False


class TestIsFalseEval:
    def test_pass(self):
        assert evaluate(build_is_false(False, name="X")) is True

    def test_pass_falsy(self):
        assert evaluate(build_is_false(0, name="X")) is True
        assert evaluate(build_is_false("", name="X")) is True

    def test_fail(self):
        assert evaluate(build_is_false(True, name="X")) is False


class TestIsNoneEval:
    def test_pass(self):
        assert evaluate(build_is_none(None, name="X")) is True

    def test_fail(self):
        assert evaluate(build_is_none(0, name="X")) is False
        assert evaluate(build_is_none("", name="X")) is False


class TestIsNotNoneEval:
    def test_pass(self):
        assert evaluate(build_is_not_none(42, name="X")) is True
        assert evaluate(build_is_not_none(0, name="X")) is True

    def test_fail(self):
        assert evaluate(build_is_not_none(None, name="X")) is False


class TestContainsEval:
    def test_pass_string(self):
        assert evaluate(build_contains("hello world", "world", name="Msg")) is True

    def test_fail_string(self):
        assert evaluate(build_contains("hello", "xyz", name="Msg")) is False

    def test_pass_list(self):
        assert evaluate(build_contains([1, 2, 3], 2, name="L")) is True

    def test_fail_list(self):
        assert evaluate(build_contains([1, 2, 3], 4, name="L")) is False


class TestNotContainsEval:
    def test_pass(self):
        assert evaluate(build_not_contains("hello", "xyz", name="Msg")) is True

    def test_fail(self):
        assert evaluate(build_not_contains("hello", "ell", name="Msg")) is False


class TestMatchesEval:
    def test_pass(self):
        assert evaluate(build_matches("abc123", r"\d+", name="Code")) is True

    def test_fail(self):
        assert evaluate(build_matches("abcdef", r"\d+", name="Code")) is False


class TestIsInstanceEval:
    def test_pass(self):
        assert evaluate(build_is_instance({}, dict, name="Data")) is True

    def test_fail(self):
        assert evaluate(build_is_instance([], dict, name="Data")) is False

    def test_concrete_subclass_passes(self):
        # bool is a subclass of int — isinstance(True, int) is True.
        assert evaluate(build_is_instance(True, int, name="Flag")) is True

    def test_exact_type_passes(self):
        assert evaluate(build_is_instance(True, bool, name="Flag")) is True

    def test_unrelated_type_fails(self):
        assert evaluate(build_is_instance(5, str, name="Value")) is False

    def test_superclass_instance_does_not_match_subclass(self):
        # An int is not a bool, even though bool subclasses int.
        assert evaluate(build_is_instance(1, bool, name="Value")) is False

    def test_abc_virtual_subclass_matches(self):
        # The builder runs the real isinstance (list is a virtual subclass of
        # Sequence), so the module path agrees with the fixture path.
        from collections.abc import Sequence

        assert evaluate(build_is_instance([1, 2, 3], Sequence, name="S")) is True


class TestLengthEval:
    def test_pass(self):
        assert evaluate(build_length([1, 2, 3], 3, name="Items")) is True

    def test_fail(self):
        assert evaluate(build_length([1, 2], 3, name="Items")) is False


class TestAllSatisfyEval:
    def test_all_pass(self):
        factory = lambda x: build_greater(x, 0, name=f"item_{x}")
        assert evaluate(build_all_satisfy([1, 2, 3], factory, name="Pos")) is True

    def test_some_fail(self):
        factory = lambda x: build_greater(x, 0, name=f"item_{x}")
        assert evaluate(build_all_satisfy([1, -1, 3], factory, name="Pos")) is False

    def test_empty_list_passes(self):
        factory = lambda x: build_greater(x, 0, name="X")
        assert evaluate(build_all_satisfy([], factory, name="Empty")) is True


class TestConditionalEval:
    def test_matched_case_pass(self):
        cases = {"1": build_equal(10, 10, name="case1")}
        assert evaluate(build_conditional(1, cases=cases, name="M")) is True

    def test_matched_case_fail(self):
        cases = {"1": build_equal(10, 99, name="case1")}
        assert evaluate(build_conditional(1, cases=cases, name="M")) is False

    def test_default_fallback(self):
        cases = {"1": build_equal(10, 10, name="case1")}
        default = build_equal(5, 5, name="default")
        assert evaluate(build_conditional(99, cases=cases, default=default, name="M")) is True

    def test_no_match_no_default_fails(self):
        cases = {"1": build_equal(10, 10, name="case1")}
        assert evaluate(build_conditional(99, cases=cases, name="M")) is False

    def test_int_keys_match(self):
        cases = {0: build_equal(1, 2, name="c0"), 1: build_equal(10, 10, name="c1")}
        assert evaluate(build_conditional(1, cases=cases, name="M")) is True


class TestGuardEval:
    def test_first_true_branch_passes(self):
        d = build_guard(
            [(False, "a", build_equal(1, 2, name="a")), (True, "b", build_equal(1, 1, name="b"))],
            name="G",
        )
        assert evaluate(d) is True

    def test_first_true_branch_can_fail(self):
        d = build_guard(
            [(True, "a", build_equal(1, 2, name="a")), (True, "b", build_equal(1, 1, name="b"))],
            name="G",
        )
        # Earlier True wins even though a later branch would pass.
        assert evaluate(d) is False

    def test_default_used_when_no_branch_matches(self):
        d = build_guard(
            [(False, "a", build_equal(1, 2, name="a"))],
            default=build_equal(5, 5, name="def"),
            name="G",
        )
        assert evaluate(d) is True

    def test_no_match_no_default_fails(self):
        d = build_guard([(False, "a", build_equal(1, 1, name="a"))], name="G")
        assert evaluate(d) is False


class TestFailEval:
    def test_always_fails(self):
        assert evaluate(build_fail("oops")) is False


# ── Multi-descriptor evaluate ────────────────────────────────────────


class TestEvaluateMultiple:
    def test_all_pass(self):
        d1 = build_equal(1, 1, name="A")
        d2 = build_greater(10, 5, name="B")
        assert evaluate(d1, d2) is True

    def test_any_fail(self):
        d1 = build_equal(1, 1, name="A")
        d2 = build_equal(1, 2, name="B")
        assert evaluate(d1, d2) is False

    def test_all_fail(self):
        d1 = build_equal(1, 2, name="A")
        d2 = build_equal(3, 4, name="B")
        assert evaluate(d1, d2) is False


# ── evaluate_detailed ────────────────────────────────────────────────


class TestEvaluateDetailed:
    def test_schema(self):
        d = build_equal(1, 1, name="X")
        results = evaluate_detailed(d)
        assert len(results) == 1
        r = results[0]
        assert "passed" in r
        assert "details" in r
        assert "seq" in r
        assert "t" in r

    def test_passed_field(self):
        results = evaluate_detailed(
            build_equal(1, 1, name="A"),
            build_equal(1, 2, name="B"),
        )
        assert results[0]["passed"] is True
        assert results[1]["passed"] is False

    def test_seq_ordering(self):
        results = evaluate_detailed(
            build_equal(1, 1, name="A"),
            build_greater(10, 5, name="B"),
            build_fail("oops"),
        )
        assert [r["seq"] for r in results] == [0, 1, 2]

    def test_timestamp_is_numeric(self):
        results = evaluate_detailed(build_equal(1, 1, name="A"))
        assert isinstance(results[0]["t"], float)

    def test_details_is_original_descriptor(self):
        d = build_equal(42, 42, name="Answer")
        results = evaluate_detailed(d)
        assert results[0]["details"] is d
