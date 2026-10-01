"""Behaviour added in 0.4.0 alongside the bug fixes.

* Usage errors (a bad ``name``, tolerance, range, branch or case) raise ``TypeError`` or
  ``ValueError`` when the check is built (IMP-2, IMP-3, IMP-4).
* Problems with the checked data never raise: the check fails with an ``error`` note.
* ``evaluate()`` and ``evaluate_detailed()`` reject a list and judge hand-built or JSON-loaded
  descriptors without raising (IMP-5, IMP-6).
* Recorded results are JSON-safe snapshots with a private verdict (IMP-18) and a verdict per
  evaluated child (IMP-20); a fixture used after its test ends refuses checks (IMP-8).
"""
from __future__ import annotations

import enum
import json
import math
import os
import pickle
from decimal import Decimal
from fractions import Fraction
from typing import Any, Iterator

import pytest

from pytest_verify import ChecksFailedError, get_check_results
from pytest_verify import verify as mverify
from pytest_verify._descriptors import _NO_CASE, select_case
from pytest_verify._exceptions import format_summary, render_detail
from pytest_verify._render import bounded_format, safe_format, safe_repr, safe_str, snapshot
from pytest_verify._settle import settle


def _dumps(value: Any) -> str:
    return json.dumps(value, allow_nan=False)


# ── Usage errors raise when the check is built ──


class TestUsageErrors:
    @pytest.mark.parametrize(
        "call",
        [
            lambda: mverify.equal(1, 1, name=None),
            lambda: mverify.greater(1, 0, name=3),
            lambda: mverify.is_true(True, name=b"bytes"),
            lambda: mverify.all_satisfy([], lambda x: x, name=None),
            lambda: mverify.guard([], name=None),
            lambda: mverify.fail("msg", name=1),
        ],
    )
    def test_name_must_be_a_str(self, call: Any) -> None:
        with pytest.raises(TypeError, match="name must be a str"):
            call()

    @pytest.mark.parametrize(
        "kwargs, error, match",
        [
            ({}, ValueError, "at least one of abs_tol or rel_tol"),
            ({"abs_tol": -0.1}, ValueError, "abs_tol must be a non-negative number"),
            ({"rel_tol": float("nan")}, ValueError, "rel_tol must be a non-negative number"),
            ({"abs_tol": "0.1"}, TypeError, "abs_tol must be a real number"),
            ({"rel_tol": True}, TypeError, "rel_tol must be a real number"),
        ],
    )
    def test_approx_tolerances_are_validated(self, kwargs: Any, error: Any, match: str) -> None:
        with pytest.raises(error, match=match):
            mverify.approx(1.0, 1.0, name="V", **kwargs)

    @pytest.mark.parametrize("tol", [0, 0.0, Decimal("0.1"), Fraction(1, 10)])
    def test_approx_accepts_zero_and_exact_tolerances(self, tol: Any) -> None:
        assert mverify.evaluate(mverify.approx(1, 1, abs_tol=tol, name="V"))

    def test_between_rejects_low_above_high(self) -> None:
        with pytest.raises(ValueError, match="low must not exceed high"):
            mverify.between(1, 5, 2, name="R")

    def test_between_allows_an_empty_exclusive_range_and_non_numbers(self) -> None:
        assert not mverify.evaluate(mverify.between(2, 2, 2, inclusive=False, name="R"))
        assert mverify.evaluate(mverify.between("b", "a", "c", name="S"))

    @pytest.mark.parametrize("expected_type", [list[int], ()])
    def test_is_instance_rejects_what_isinstance_cannot_check(self, expected_type: Any) -> None:
        with pytest.raises(TypeError, match="is_instance"):
            mverify.is_instance([], expected_type, name="T")

    def test_conditional_validates_cases_and_default(self) -> None:
        with pytest.raises(TypeError, match="cases must be a mapping"):
            mverify.conditional(1, cases=[1], name="C")  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="case 1 must be a check descriptor"):
            mverify.conditional(1, cases={1: 5}, name="C")  # type: ignore[dict-item]
        with pytest.raises(TypeError, match="default must be a check descriptor"):
            mverify.conditional(1, cases={}, default=5, name="C")  # type: ignore[arg-type]

    def test_guard_validates_branches(self) -> None:
        check = mverify.is_true(True, name="T")
        with pytest.raises(TypeError, match=r"branch 0 must be a \(condition, label, check\)"):
            mverify.guard([(True, "x")], name="G")  # type: ignore[list-item]
        with pytest.raises(TypeError, match="branch 0 check must be a check descriptor"):
            mverify.guard([(True, "x", 5)], name="G")  # type: ignore[list-item]
        with pytest.raises(TypeError, match="default must be a check descriptor"):
            mverify.guard([], default=5, name="G")  # type: ignore[arg-type]

    def test_guard_rejects_a_descriptor_used_as_condition(self) -> None:
        check = mverify.is_true(False, name="T")
        with pytest.raises(TypeError, match="condition is a check descriptor, which is always truthy"):
            mverify.guard([(check, "x", check)], name="G")

    def test_fixture_usage_error_stops_the_test_and_leaves_no_orphans(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makepyfile(
            """
            import pytest
            from pytest_verify import get_check_results

            def test_bad_range(verify, request):
                with pytest.raises(ValueError):
                    verify.between(1, 5, 2, name="R")
                with pytest.raises(TypeError):
                    verify.guard([(True, "x", verify.fail("unused"))], default=5, name="G")
                assert get_check_results(request.node) == []
            """
        )
        pytester.runpytest().assert_outcomes(passed=1)


# ── Problems with the data never raise ──


class _RaisingBool:
    def __bool__(self) -> bool:
        raise RuntimeError("no truth value")


class _RaisingMeta(type):
    def __instancecheck__(cls, instance: object) -> bool:
        raise RuntimeError("cannot tell")


class _Unknowable(metaclass=_RaisingMeta):
    pass


def _items_then_error() -> Iterator[int]:
    yield 1
    raise RuntimeError("stream closed")


class _FloatLike:
    def __float__(self) -> float:
        return 3.31


class TestDataErrorsFailTheCheck:
    @pytest.mark.parametrize(
        "build, error",
        [
            (
                lambda: mverify.all_satisfy(5, lambda x: mverify.is_true(x, name="T"), name="A"),
                "items are not iterable: TypeError",
            ),
            (
                lambda: mverify.all_satisfy(
                    _items_then_error(), lambda x: mverify.is_true(x, name="T"), name="A"
                ),
                "iterating items raised RuntimeError: stream closed",
            ),
            (
                lambda: mverify.all_satisfy([1], lambda x: 1 / 0, name="A"),
                "descriptor_factory raised ZeroDivisionError",
            ),
            (
                lambda: mverify.all_satisfy([1], lambda x: None, name="A"),
                "did you forget `return`?",
            ),
            (
                lambda: mverify.guard(
                    [(_RaisingBool(), "x", mverify.is_true(True, name="T"))], name="G"
                ),
                "condition of branch 0 (x) raised RuntimeError: no truth value",
            ),
            (
                lambda: mverify.is_instance(1, _Unknowable, name="I"),
                "RuntimeError: cannot tell",
            ),
            (
                lambda: mverify.length(5, 1, name="L"),
                "TypeError",
            ),
            (
                lambda: mverify.approx(None, None, abs_tol=1, name="A"),
                "TypeError: approx compares numbers, got NoneType",
            ),
            (
                lambda: mverify.approx("3.3", 3.3, abs_tol=0.1, name="A"),
                "TypeError: approx compares numbers, got str",
            ),
            (
                lambda: mverify.approx([1, 2], [1, 2], rel_tol=1, name="A"),
                "TypeError: approx compares numbers, got list",
            ),
        ],
    )
    def test_check_builds_and_fails_with_an_error_note(self, build: Any, error: str) -> None:
        descriptor = build()
        assert mverify.evaluate(descriptor) is False
        [result] = mverify.evaluate_detailed(descriptor)
        assert result["passed"] is False
        assert error in result["error"]

    def test_approx_converts_values_that_have_a_float(self) -> None:
        assert mverify.evaluate(mverify.approx(_FloatLike(), 3.3, abs_tol=0.05, name="A"))
        assert not mverify.evaluate(mverify.approx(_FloatLike(), 3.0, abs_tol=0.05, name="A"))

    def test_fixture_records_data_errors_and_keeps_going(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_data(verify):
                verify.all_satisfy([1], lambda x: None, name="Forgot return")
                verify.greater(None, 1, name="Missing reading")
                verify.equal(1, 1, name="Still runs")
            """
        )
        result = pytester.runpytest()
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            [
                "*2 of 3 checks failed*",
                "*Forgot return*did you forget `return`?*",
                "*Missing reading — expected > 1, got None (TypeError:*",
                "*Still runs — 1 == 1",
            ]
        )


# ── evaluate() and evaluate_detailed() ──


class TestEvaluate:
    def test_a_list_argument_gets_a_helpful_error(self) -> None:
        checks = [mverify.is_true(True, name="T")]
        for function in (mverify.evaluate, mverify.evaluate_detailed):
            with pytest.raises(TypeError, match=r"use verify\.evaluate(_detailed)?\(\*checks\)"):
                function(checks)  # type: ignore[arg-type]

    def test_a_non_descriptor_argument_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="argument 1 is not a check descriptor: int"):
            mverify.evaluate(mverify.is_true(True, name="T"), 5)  # type: ignore[arg-type]

    def test_no_descriptors_is_vacuously_true(self) -> None:
        assert mverify.evaluate() is True
        assert mverify.evaluate_detailed() == []

    def test_every_descriptor_is_evaluated_even_after_a_failure(self) -> None:
        results = mverify.evaluate_detailed(
            mverify.fail("first"), mverify.greater(None, 1, name="Second")
        )
        assert [r["seq"] for r in results] == [0, 1]
        assert all(r["passed"] is False for r in results)
        assert "error" not in results[0]
        assert results[1]["error"].startswith("TypeError:")

    def test_unknown_check_type_fails_with_an_error(self) -> None:
        [result] = mverify.evaluate_detailed({"check_type": "nope", "name": "N", "description": ""})
        assert result["passed"] is False
        assert result["error"] == "ValueError: Unknown check_type: 'nope'"


# ── Hand-built and JSON-loaded descriptors ──


def _round_trip(descriptor: Any) -> Any:
    return json.loads(json.dumps(descriptor))


class TestHandBuiltDescriptors:
    def test_guard_without_matched_index_uses_the_first_true_condition(self) -> None:
        guard = {
            "check_type": "guard",
            "name": "G",
            "description": "",
            "branches": [
                {"condition": False, "label": "a", "check": mverify.fail("unselected")},
                {"condition": True, "label": "b", "check": mverify.equal(1, 1, name="E")},
            ],
            "default": None,
        }
        assert mverify.evaluate(guard) is True

    def test_conditional_without_matched_case_selects_by_switch_value(self) -> None:
        conditional = {
            "check_type": "conditional",
            "name": "C",
            "description": "",
            "switch_value": 2,
            "cases": {"1": mverify.fail("one"), "2": mverify.equal(1, 1, name="two")},
            "default": None,
        }
        assert mverify.evaluate(conditional) is True
        assert "[mode=2 → two]" in render_detail(conditional, True)

    def test_is_instance_without_stored_verdict_matches_qualified_names(self) -> None:
        stored = _round_trip(mverify.is_instance(True, int, name="I"))
        stored.pop("instance_check")
        stored["actual"] = True  # json round-trip keeps it, but be explicit
        assert mverify.evaluate(stored) is True  # bool's MRO contains builtins.int
        stored["expected_types"] = ["other_module.int"]
        assert mverify.evaluate(stored) is False

    def test_is_instance_with_only_a_type_name_matches_the_bare_name(self) -> None:
        legacy = {"check_type": "is_instance", "name": "I", "description": "", "actual": {},
                  "expected_type": "dict"}
        assert mverify.evaluate(legacy) is True

    def test_length_without_actual_length_measures_actual(self) -> None:
        legacy = {"check_type": "length", "name": "L", "description": "", "actual": [1, 2],
                  "expected": 2}
        assert mverify.evaluate(legacy) is True

    def test_approx_without_tolerance_fails_with_an_error(self) -> None:
        legacy = {"check_type": "approx", "name": "A", "description": "", "actual": 1,
                  "expected": 1}
        [result] = mverify.evaluate_detailed(legacy)
        assert result["passed"] is False and "abs_tol or rel_tol" in result["error"]

    def test_approx_mixing_decimal_and_fraction(self) -> None:
        check = mverify.approx(Decimal("1.0"), Fraction(11, 10), abs_tol=Fraction(1, 5), name="A")
        assert mverify.evaluate(check) is True

    def test_a_recorded_verdict_is_trusted(self) -> None:
        recorded = dict(mverify.equal(1, 2, name="E"), passed=True)
        assert mverify.evaluate(recorded) is True


# ── select_case ──


class _Mode(enum.Enum):
    OFF = 0
    ON = 1


class _Label(str, enum.Enum):
    FAST = "fast"


class TestSelectCase:
    @pytest.mark.parametrize(
        "switch, keys, expected",
        [
            (1, ["0", "1"], "1"),
            ("1", [0, 1], 1),
            (1.0, [1], 1),
            (True, [1], 1),
            (_Mode.ON, ["0", "1"], "1"),
            (1, [_Mode.OFF, _Mode.ON], _Mode.ON),
            (_Label.FAST, ["fast"], "fast"),
            ("fast", [_Label.FAST], _Label.FAST),
            (None, [None, "None"], None),
        ],
    )
    def test_matches(self, switch: Any, keys: list[Any], expected: Any) -> None:
        assert select_case(switch, keys) == expected

    @pytest.mark.parametrize(
        "switch, keys",
        [(None, ["None"]), (True, ["True"]), (1.5, ["1.5"]), ("01", [1]), (2, ["1"])],
    )
    def test_does_not_match(self, switch: Any, keys: list[Any]) -> None:
        assert select_case(switch, keys) is _NO_CASE

    def test_no_match_without_default_fails_and_says_so(self) -> None:
        check = mverify.conditional(3, cases={1: mverify.equal(1, 1, name="one")}, name="Mode")
        assert check["matched_case"] is None
        [result] = mverify.evaluate_detailed(check)
        assert result["passed"] is False
        assert render_detail(check, False) == "[mode=3 → no match]"


# ── Safe rendering and snapshots ──


class _Hostile:
    def __str__(self) -> str:
        raise RuntimeError("str")

    def __repr__(self) -> str:
        raise RuntimeError("repr")

    def __format__(self, spec: str) -> str:
        raise RuntimeError("format")

    def __eq__(self, other: object) -> bool:
        return False

    __hash__ = object.__hash__


class _HostileStr(str):
    def __str__(self) -> str:
        raise RuntimeError("str")


class TestRendering:
    def test_safe_helpers_never_raise(self) -> None:
        value = _Hostile()
        assert safe_str(value) == "<_Hostile object: str() raised RuntimeError>"
        assert safe_repr(value) == "<_Hostile object: repr() raised RuntimeError>"
        assert safe_format(value) == "<_Hostile object: format() raised RuntimeError>"

    def test_hostile_values_are_recorded_and_reported(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            class Hostile:
                def __str__(self): raise RuntimeError("str")
                def __repr__(self): raise RuntimeError("repr")
                def __format__(self, spec): raise RuntimeError("format")
                def __eq__(self, other): return False
                __hash__ = object.__hash__

            def test_hostile(verify):
                verify.equal(Hostile(), 1, name="Hostile")
                verify.contains([Hostile()], 1, name="In list")
            """
        )
        result = pytester.runpytest()
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*2 of 2 checks failed*", "*format() raised RuntimeError*"])

    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, None),
            (True, True),
            (3, 3),
            (2.5, 2.5),
            (float("nan"), "nan"),
            (float("inf"), "inf"),
            (-math.inf, "-inf"),
            ("text", "text"),
            ((1, [2, (3,)]), [1, [2, [3]]]),
            ({"a": {"b": 1}}, {"a": {"b": 1}}),
            ({1: "a"}, "{1: 'a'}"),
            ({3}, "{3}"),
            (b"raw", "b'raw'"),
            (Decimal("1.10"), "Decimal('1.10')"),
            (_Mode.ON, "<_Mode.ON: 1>"),
        ],
    )
    def test_snapshot_values(self, value: Any, expected: Any) -> None:
        assert snapshot(value) == expected
        _dumps(snapshot(value))

    def test_snapshot_is_a_copy(self) -> None:
        original = {"readings": [1, 2]}
        copy = snapshot(original)
        original["readings"].append(3)
        assert copy == {"readings": [1, 2]}

    def test_snapshot_of_hostile_values_is_still_json(self) -> None:
        for value in (_Hostile(), _HostileStr("x"), [_Hostile()], {"k": _Hostile()}):
            _dumps(snapshot(value))

    def test_snapshot_bounds_deep_and_huge_values(self) -> None:
        deep: list[Any] = []
        node = deep
        for _ in range(200):
            node.append([])
            node = node[0]
        assert isinstance(snapshot(deep), str)
        huge = list(range(50_000))
        shown = snapshot(huge)
        assert isinstance(shown, str) and len(shown) < 2_000

    def test_detail_of_an_unknown_check_type_uses_its_description(self) -> None:
        custom = {"check_type": "custom", "name": "X", "description": "Verify 'X' looks right"}
        assert render_detail(custom, True) == "looks right"

    def test_detail_that_cannot_be_rendered_says_so(self) -> None:
        broken = {"check_type": "equal", "name": "E", "description": ""}  # no actual/expected
        assert render_detail(broken, False).startswith("<detail unavailable: KeyError")
        assert render_detail(broken, False, "boom") == "error: boom"

    def test_summary_and_error_repr(self) -> None:
        results = [
            dict(mverify.equal(1, 2, name="Bad"), passed=False),
            dict(mverify.equal(1, 1, name="Good"), passed=True),
        ]
        summary = format_summary(results)
        assert summary.splitlines()[0] == "1 of 2 checks failed"
        assert summary.index("✗ [0] Bad") < summary.index("✓ [1] Good")
        error = ChecksFailedError(results)
        assert str(error) == summary
        assert repr(error) == "ChecksFailedError(1 of 2 checks failed)"
        assert isinstance(error, AssertionError)

    def test_summary_numbers_checks_from_start(self) -> None:
        results = [
            dict(mverify.equal(1, 2, name="Bad"), passed=False),
            dict(mverify.equal(1, 1, name="Good"), passed=True),
        ]
        error = ChecksFailedError(results, start=2)
        assert "✗ [2] Bad" in str(error)
        assert "✓ [3] Good" in str(error)
        clone = pickle.loads(pickle.dumps(error))
        assert (str(clone), clone.args, clone.start) == (str(error), error.args, 2)

    def test_small_values_render_like_format(self) -> None:
        for value in (3.3, 42, "text", [1, 2], (1,), {"a": 1}, None, Decimal("1.10")):
            assert bounded_format(value) == format(value)

    def test_large_values_render_a_bounded_detail(self) -> None:
        big_list = list(range(1_000_000))
        big_text = "x" * 1_000_000
        checks = [
            mverify.equal(big_list, [], name="List"),
            mverify.equal(big_text, "y", name="Text"),
            mverify.contains(big_list, -1, name="Haystack"),
            mverify.is_none(big_text, name="Not none"),
            mverify.matches(big_text, "z", name="Pattern"),
            mverify.fail(big_text, name="Message"),
            mverify.equal(big_list, big_list, name="Passing"),
        ]
        records = [settle(check)[0] for check in checks]
        for record in records:
            assert len(record["detail"]) < 2500, record["name"]
        assert records[0]["detail"].startswith("expected [], got [0, 1, 2, ")
        assert records[0]["detail"].endswith(", ...]")
        assert records[1]["detail"].endswith("x...")
        assert len(format_summary(records)) < 20_000


# ── Recorded results ──


class TestRecordedResults:
    def test_settle_marks_a_non_descriptor_invalid(self) -> None:
        record, passed = settle(42)
        assert passed is False
        assert record["check_type"] == "invalid"
        assert record["error"] == "not a check descriptor: 42"

    def test_changing_a_returned_verdict_does_not_change_the_outcome(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makepyfile(
            """
            def test_tamper(verify):
                check = verify.equal(1, 2, name="Bad")
                check["passed"] = True
            """
        )
        pytester.runpytest().assert_outcomes(failed=1)

    def test_every_evaluated_child_has_a_verdict_and_unselected_ones_have_none(
        self, verify: Any, request: pytest.FixtureRequest
    ) -> None:
        verify.all_satisfy([1, 2], lambda x: verify.greater(x, 0, name=f"n{x}"), name="All")
        verify.conditional(
            "a",
            cases={
                "a": verify.equal(1, 1, name="Selected"),
                "b": verify.all_satisfy([0], lambda x: verify.greater(x, 1, name="no"), name="Nested"),
                "c": verify.guard([(True, "t", verify.fail("never"))], name="Inner guard"),
                "d": verify.conditional(0, cases={0: verify.fail("never")}, name="Inner cond"),
            },
            name="Outer",
        )
        all_record, outer = get_check_results(request.node)
        assert [child["passed"] for child in all_record["child_checks"]] == [True, True]
        cases = outer["cases"]
        assert cases["a"]["passed"] is True
        for key in ("b", "c", "d"):
            text = _dumps(cases[key])
            assert '"passed"' not in text and '"detail"' not in text and '"error"' not in text

    def test_a_fixture_used_after_its_test_refuses_checks(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import pytest

            saved = []

            def test_first(verify):
                saved.append(verify)
                verify.equal(1, 1, name="Mine")

            def test_second():
                with pytest.raises(RuntimeError, match="already finished"):
                    saved[0].equal(1, 2, name="Lost")
            """
        )
        pytester.runpytest().assert_outcomes(passed=2)

    @pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
    def test_a_check_made_in_a_forked_child_is_refused(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            import os

            def test_forked(verify):
                read_end, write_end = os.pipe()
                pid = os.fork()
                if pid == 0:
                    try:
                        verify.equal(1, 2, name="In child")
                        os.write(write_end, b"recorded")
                    except RuntimeError as exc:
                        os.write(write_end, b"refused" if "child process" in str(exc) else b"?")
                    os._exit(0)
                os.waitpid(pid, 0)
                assert os.read(read_end, 100) == b"refused"
                verify.equal(1, 1, name="In parent")
            """
        )
        pytester.runpytest("-p", "no:cacheprovider", "-W", "ignore::DeprecationWarning").assert_outcomes(passed=1)

    def test_a_failing_teardown_keeps_its_error_and_adds_the_soft_summary(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makepyfile(
            """
            import pytest

            @pytest.fixture
            def device(verify):
                yield
                verify.equal(0, 1, name="Teardown reading")
                raise RuntimeError("cleanup failed")

            def test_body(device):
                pass
            """
        )
        result = pytester.runpytest()
        result.assert_outcomes(passed=1, errors=1)
        result.stdout.fnmatch_lines(
            ["*RuntimeError: cleanup failed*", "*Soft assertion failures*", "*Teardown reading*"]
        )
