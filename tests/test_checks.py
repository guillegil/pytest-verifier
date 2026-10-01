"""End-to-end tests for all 21 check functions.

Pass-path tests use the ``verify`` fixture (proving fixture integration).
Fail-path tests use the module-level ``Verify`` class + evaluator to avoid
the expected ``ChecksFailedError`` at teardown.
"""
from __future__ import annotations

import pytest

from tests._builders import build_equal, build_greater
from pytest_verify._evaluator import evaluate
from pytest_verify._verify import Verify

# Module-level instance for fail-path tests (no teardown side effects)
v = Verify()


# ── Equality & Approximation ────────────────────────────────────────


class TestEqual:
    def test_pass(self, verify):
        d = verify.equal(42, 42, name="Answer")
        assert d["passed"] is True
        assert d["check_type"] == "equal"

    def test_fail(self):
        d = v.equal(42, 99, name="Wrong")
        assert evaluate(d) is False

    def test_with_units(self, verify):
        d = verify.equal(5, 5, name="V", units="V")
        assert "5V" in d["description"]


class TestNotEqual:
    def test_pass(self, verify):
        d = verify.not_equal(1, 2, name="Diff")
        assert d["passed"] is True

    def test_fail(self):
        d = v.not_equal(1, 1, name="Same")
        assert evaluate(d) is False


class TestApprox:
    def test_pass_abs_tol(self, verify):
        d = verify.approx(3.28, 3.3, abs_tol=0.05, name="Vout", units="V")
        assert d["passed"] is True
        assert "\u00b1" in d["description"]

    def test_fail_abs_tol(self):
        d = v.approx(3.0, 3.3, abs_tol=0.05, name="Vout")
        assert evaluate(d) is False

    def test_pass_rel_tol(self, verify):
        d = verify.approx(101, 100, rel_tol=0.02, name="Power")
        assert d["passed"] is True

    def test_raises_without_tolerance(self):
        with pytest.raises(ValueError, match="at least one"):
            v.approx(3.3, 3.3, name="V")


# ── Ordering & Range ─────────────────────────────────────────────────


class TestGreater:
    def test_pass(self, verify):
        d = verify.greater(200, 100, name="Throughput", units="Mbps")
        assert d["passed"] is True
        assert "> 100Mbps" in d["description"]

    def test_fail_at_boundary(self):
        d = v.greater(100, 100, name="T")
        assert evaluate(d) is False


class TestGreaterEqual:
    def test_pass_at_boundary(self, verify):
        d = verify.greater_equal(100, 100, name="T")
        assert d["passed"] is True

    def test_fail(self):
        d = v.greater_equal(99, 100, name="T")
        assert evaluate(d) is False


class TestLess:
    def test_pass(self, verify):
        d = verify.less(3, 10, name="Latency", units="ms")
        assert d["passed"] is True

    def test_fail_at_boundary(self):
        d = v.less(10, 10, name="L")
        assert evaluate(d) is False


class TestLessEqual:
    def test_pass_at_boundary(self, verify):
        d = verify.less_equal(10, 10, name="L")
        assert d["passed"] is True

    def test_fail(self):
        d = v.less_equal(11, 10, name="L")
        assert evaluate(d) is False


class TestBetween:
    def test_pass_inclusive(self, verify):
        d = verify.between(0.3, 0.1, 0.5, name="I", units="A")
        assert d["passed"] is True
        assert "\u2208" in d["description"]
        assert "[0.1A, 0.5A]" in d["description"]

    def test_pass_at_inclusive_boundary(self, verify):
        d = verify.between(0.1, 0.1, 0.5, name="I")
        assert d["passed"] is True

    def test_fail_outside_range(self):
        d = v.between(0.6, 0.1, 0.5, name="I")
        assert evaluate(d) is False

    def test_exclusive_boundaries(self):
        d = v.between(0.1, 0.1, 0.5, inclusive=False, name="I")
        assert evaluate(d) is False
        assert "(0.1, 0.5)" in d["description"]


# ── Boolean & Identity ───────────────────────────────────────────────


class TestIsTrue:
    def test_pass(self, verify):
        d = verify.is_true(True, name="Alive")
        assert d["passed"] is True
        assert d["description"] == "Verify 'Alive' is True"

    def test_pass_truthy(self, verify):
        assert verify.is_true(1, name="X")["passed"] is True
        assert verify.is_true([1], name="X")["passed"] is True

    def test_fail(self):
        assert evaluate(v.is_true(False, name="X")) is False
        assert evaluate(v.is_true(0, name="X")) is False
        assert evaluate(v.is_true(None, name="X")) is False


class TestIsFalse:
    def test_pass(self, verify):
        d = verify.is_false(False, name="Dead")
        assert d["passed"] is True

    def test_pass_falsy(self, verify):
        assert verify.is_false(0, name="X")["passed"] is True
        assert verify.is_false("", name="X")["passed"] is True
        assert verify.is_false([], name="X")["passed"] is True

    def test_fail(self):
        assert evaluate(v.is_false(True, name="X")) is False


class TestIsNone:
    def test_pass(self, verify):
        d = verify.is_none(None, name="Missing")
        assert d["passed"] is True

    def test_fail(self):
        assert evaluate(v.is_none(0, name="X")) is False
        assert evaluate(v.is_none("", name="X")) is False


class TestIsNotNone:
    def test_pass(self, verify):
        d = verify.is_not_none(42, name="Present")
        assert d["passed"] is True
        assert verify.is_not_none(0, name="X")["passed"] is True

    def test_fail(self):
        assert evaluate(v.is_not_none(None, name="X")) is False


# ── String & Container ───────────────────────────────────────────────


class TestContains:
    def test_pass_string(self, verify):
        d = verify.contains("hello world", "world", name="Msg")
        assert d["passed"] is True

    def test_fail_string(self):
        d = v.contains("hello", "xyz", name="Msg")
        assert evaluate(d) is False

    def test_pass_list(self, verify):
        d = verify.contains([1, 2, 3], 2, name="List")
        assert d["passed"] is True


class TestNotContains:
    def test_pass(self, verify):
        d = verify.not_contains("hello", "xyz", name="Msg")
        assert d["passed"] is True

    def test_fail(self):
        d = v.not_contains("hello", "ell", name="Msg")
        assert evaluate(d) is False


class TestMatches:
    def test_pass(self, verify):
        d = verify.matches("abc123", r"\d+", name="Code")
        assert d["passed"] is True

    def test_fail(self):
        d = v.matches("abcdef", r"\d+", name="Code")
        assert evaluate(d) is False


# ── Type / Collection / Conditional ──────────────────────────────────


class TestIsInstance:
    def test_pass(self, verify):
        d = verify.is_instance({"key": "val"}, dict, name="Data")
        assert d["passed"] is True
        assert d["expected_type"] == "dict"

    def test_fail(self):
        d = v.is_instance([], dict, name="Data")
        assert evaluate(d) is False


class TestLength:
    def test_pass(self, verify):
        d = verify.length([1, 2, 3], 3, name="Items")
        assert d["passed"] is True
        assert d["actual_length"] == 3

    def test_fail(self):
        d = v.length([1, 2], 3, name="Items")
        assert evaluate(d) is False
        assert d["actual_length"] == 2


class TestAllSatisfy:
    def test_all_pass(self, verify):
        factory = lambda x: build_greater(x, 0, name=f"item_{x}")
        d = verify.all_satisfy([1, 2, 3], factory, name="Positives")
        assert d["passed"] is True

    def test_some_fail(self):
        factory = lambda x: build_greater(x, 0, name=f"item_{x}")
        d = v.all_satisfy([1, -1, 3], factory, name="Positives")
        assert evaluate(d) is False

    def test_empty_list(self, verify):
        factory = lambda x: build_greater(x, 0, name="X")
        d = verify.all_satisfy([], factory, name="Empty")
        assert d["passed"] is True


class TestConditional:
    def test_matched_case(self, verify):
        cases = {"1": build_equal(10, 10, name="case1")}
        d = verify.conditional(1, cases=cases, name="Mode")
        assert d["passed"] is True

    def test_default_fallback(self, verify):
        cases = {"1": build_equal(10, 10, name="case1")}
        default = build_equal(5, 5, name="default")
        d = verify.conditional(99, cases=cases, default=default, name="Mode")
        assert d["passed"] is True

    def test_no_match_no_default(self):
        cases = {"1": build_equal(10, 10, name="case1")}
        d = v.conditional(99, cases=cases, name="Mode")
        assert evaluate(d) is False


class TestGuard:
    def test_matched_branch(self, verify):
        d = verify.guard(
            [(False, "lo", build_equal(1, 2, name="lo")), (True, "hi", build_equal(1, 1, name="hi"))],
            name="Sensor",
        )
        assert d["passed"] is True

    def test_default_fallback(self, verify):
        d = verify.guard(
            [(False, "lo", build_equal(1, 2, name="lo"))],
            default=build_equal(5, 5, name="def"),
            name="Sensor",
        )
        assert d["passed"] is True

    def test_no_match_no_default(self):
        d = v.guard([(False, "lo", build_equal(1, 1, name="lo"))], name="Sensor")
        assert evaluate(d) is False


class TestFail:
    def test_always_fails(self):
        d = v.fail("intentional failure")
        assert evaluate(d) is False

    def test_name_defaults_to_msg(self):
        d = v.fail("power rail down")
        assert d["name"] == "power rail down"

    def test_custom_name(self):
        d = v.fail("power rail down", name="PSU Check")
        assert d["name"] == "PSU Check"

    def test_description_format(self):
        d = v.fail("something broke")
        assert d["description"] == "FAIL: something broke"
