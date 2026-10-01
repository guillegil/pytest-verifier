"""How checks read in descriptions, details and the failure summary (0.7.0).

* Text operands of ordering checks fail instead of comparing letter by letter (IMP-1).
* Strings are quoted, and types are shown when two values look the same (IMP-9).
* ``is_true``/``is_false`` show the value, not ``bool(value)`` (IMP-10).
* Composites say which children failed and which cases or branches were considered (IMP-11).
* User text is escaped onto one line and bounded; passed checks are capped (IMP-12, IMP-13).
* The summary stays readable on terminals that cannot show its symbols (IMP-14).
* ``± 2%`` says whether it is absolute or relative (IMP-15), and NaN is explained (IMP-16).
* Compiled patterns are stored as their source and flags (IMP-17).
"""
from __future__ import annotations

import enum
import json
import math
import re
from decimal import Decimal
from fractions import Fraction
from typing import Any, List

import pytest

from pytest_verifier import ChecksFailedError, checks
from pytest_verifier._checks import render_detail
from pytest_verifier._exceptions import for_terminal, format_summary
from pytest_verifier._run import Run, recording_verify


def _record(build: Any) -> Any:
    """Record the check *build* makes with a fixture-like ``verify``, without its location
    (tests of locations are in tests/test_locations.py)."""
    run = Run()
    run.phase = "call"
    record = build(recording_verify(run))
    record.pop("location", None)
    record.pop("called_from", None)
    return record


def _detail(check: Any) -> str:
    [result] = checks.evaluate_detailed(check)
    return render_detail(check, result["passed"])


class Mode(enum.Enum):
    IDLE = 0
    ACTIVE = 1


class Level(enum.IntEnum):
    LOW = 1


# ── IMP-1: ordering checks refuse text ──


class _Version:
    """Compares with version strings on its own terms, like ``semver.Version``."""

    def __init__(self, text: str) -> None:
        self.parts = tuple(int(part) for part in text.split("."))

    def _other(self, other: Any) -> Any:
        return _Version(other).parts if isinstance(other, str) else NotImplemented

    def __lt__(self, other: Any) -> Any:
        parts = self._other(other)
        return parts if parts is NotImplemented else self.parts < parts

    def __le__(self, other: Any) -> Any:
        parts = self._other(other)
        return parts if parts is NotImplemented else self.parts <= parts

    def __gt__(self, other: Any) -> Any:
        parts = self._other(other)
        return parts if parts is NotImplemented else self.parts > parts

    def __ge__(self, other: Any) -> Any:
        parts = self._other(other)
        return parts if parts is NotImplemented else self.parts >= parts


class TestTextOperands:
    @pytest.mark.parametrize(
        "build",
        [
            lambda v: v.greater("100", "20", name="ripple"),
            lambda v: v.less_equal(b"5", b"20", name="ripple"),
            lambda v: v.greater_equal(bytearray(b"5"), "1", name="ripple"),
            lambda v: v.between("10.5", "9.0", "11.0", name="freq"),
            lambda v: v.between("10.5", 9.0, "11.0", name="freq"),
        ],
    )
    def test_text_against_text_fails_with_an_error_that_says_to_convert(
        self, build: Any
    ) -> None:
        record = _record(build)
        assert record["passed"] is False
        assert "compared as text, not as numbers" in record["error"]
        assert "float()" in record["error"]
        assert checks.evaluate(build(checks)) is False

    @pytest.mark.parametrize(
        "build",
        [
            lambda v: v.less(5, "20", name="ripple"),
            lambda v: v.less("100", 20, name="ripple"),
            lambda v: v.between(10.5, "9.0", 11.0, name="freq"),
            lambda v: v.between("10.5", 9.0, 11.0, name="freq"),
        ],
    )
    def test_text_against_a_number_keeps_python_s_error_and_says_to_convert(
        self, build: Any
    ) -> None:
        record = _record(build)
        assert record["passed"] is False
        assert "not supported between instances of" in record["error"]
        assert "compared as text" not in record["error"]
        assert record["error"].endswith("convert text readings with float() first")

    def test_types_that_compare_with_text_on_their_own_terms_still_work(self) -> None:
        firmware = _Version("1.10.0")
        assert checks.evaluate(checks.greater_equal(firmware, "1.9.0", name="firmware"))
        assert checks.evaluate(checks.between(firmware, "1.2.0", "2.0.0", name="window"))
        assert not checks.evaluate(checks.less(firmware, "1.9.0", name="firmware"))
        assert checks.evaluate(checks.less("1.9.0", firmware, name="older"))

    def test_other_type_errors_are_unchanged(self) -> None:
        record = _record(lambda v: v.greater(None, 1, name="reading"))
        assert record["error"] == (
            "TypeError: '>' not supported between instances of 'NoneType' and 'int'"
        )

    def test_the_letter_by_letter_answer_is_not_given(self) -> None:
        # "100" > "20" is False and "10.5" is outside ["9.0", "11.0"] as text.
        assert checks.evaluate(checks.greater("100", "20", name="r")) is False
        assert checks.evaluate(checks.between("10.5", "9.0", "11.0", name="f")) is False

    @pytest.mark.parametrize(
        "check",
        [
            checks.greater(Decimal("1.5"), 1, name="decimal"),
            checks.less(Fraction(1, 3), 0.5, name="fraction"),
            checks.greater((1, 2), (1, 1), name="version tuple"),
            checks.between(5, 1, 10, name="int"),
        ],
    )
    def test_numbers_and_other_comparables_still_work(self, check: Any) -> None:
        assert checks.evaluate(check) is True

    def test_text_still_works_where_text_is_the_point(self) -> None:
        assert checks.evaluate(checks.equal("OK", "OK", name="reply"))
        assert checks.evaluate(checks.contains("hello", "ell", name="greeting"))


# ── IMP-9: quoted strings, types when values look the same ──


class TestValues:
    def test_strings_are_quoted_and_take_no_units(self) -> None:
        check = checks.equal("3.3", 3.3, name="Vout", units="V")
        assert check["description"] == "Verify 'Vout' == 3.3V"
        assert _detail(check) == "expected 3.3V, got '3.3'"

    def test_a_string_expected_value_is_quoted_in_the_description(self) -> None:
        assert checks.equal("x", "OK", name="reply")["description"] == "Verify 'reply' == 'OK'"
        assert _detail(checks.equal("OK", "OK", name="r")) == "'OK' == 'OK'"

    def test_an_int_and_its_string_read_differently(self) -> None:
        assert _detail(checks.equal(1, "1", name="n")) == "expected '1', got 1"

    def test_types_are_shown_when_the_values_look_the_same(self) -> None:
        # Decimal("0.1") is exactly 0.1; the float 0.1 is not.
        detail = _detail(checks.equal(Decimal("0.1"), 0.1, name="n"))
        assert detail == "expected 0.1 (float), got 0.1 (Decimal)"

    def test_ordering_and_range_details_show_types_too(self) -> None:
        # Decimal("0.3") is above the float 0.3, which is 0.29999...
        assert _detail(checks.less_equal(Decimal("0.3"), 0.3, name="ripple")) == (
            "expected <= 0.3 (float), got 0.3 (Decimal)"
        )
        assert _detail(checks.between(Decimal("0.3"), 0.1, 0.3, name="window")) == (
            "expected [0.1, 0.3 (float)], got 0.3 (Decimal)"
        )
        assert _detail(checks.greater(2, 1, name="g")) == "2 > 1"

    def test_types_are_not_shown_when_they_are_the_same(self) -> None:
        class Same:
            def __repr__(self) -> str:
                return "Same()"

        assert _detail(checks.equal(Same(), Same(), name="s")) == "expected Same(), got Same()"

    def test_enum_members_show_their_class_and_name(self) -> None:
        check = checks.equal(Mode.IDLE, Mode.ACTIVE, name="mode")
        assert check["description"] == "Verify 'mode' == Mode.ACTIVE"
        assert _detail(check) == "expected Mode.ACTIVE, got Mode.IDLE"
        assert _detail(checks.equal(Level.LOW, 2, name="l", units="V")) == (
            "expected 2V, got Level.LOW (1V)"
        )

    def test_numeric_enum_members_add_their_value(self) -> None:
        class Status(enum.IntFlag):
            READY = 1
            FAULT = 4

        assert _detail(checks.greater_equal(Level.LOW, 50, name="g", units="dB")) == (
            "expected >= 50dB, got Level.LOW (1dB)"
        )
        detail = _detail(checks.equal(Status.READY | Status.FAULT, 3, name="status"))
        assert detail.startswith("expected 3, got ")
        assert detail.endswith(" (5)")

    def test_numbers_keep_their_natural_form_and_units(self) -> None:
        check = checks.between(3.55, 3.2, 3.4, name="rail", units="V")
        assert check["description"] == "Verify 'rail' ∈ [3.2V, 3.4V]"
        assert _detail(check) == "expected [3.2V, 3.4V], got 3.55V"
        assert _detail(checks.greater(Decimal("1.10"), 2, name="d")) == "expected > 2, got 1.10"

    def test_a_value_whose_type_check_raises_is_still_shown(self) -> None:
        class Lazy:
            """A lazy proxy whose wrapped object cannot be made, like Django's
            SimpleLazyObject."""

            @property  # type: ignore[misc]
            def __class__(self) -> type:
                raise RuntimeError("settings are not configured")

            def __repr__(self) -> str:
                return "<Lazy>"

        record = _record(lambda v: v.contains(["admin"], Lazy(), name="role"))
        assert record["passed"] is False
        assert record["description"] == "Verify 'role' contains <Lazy>"
        assert record["detail"] == "expected to contain <Lazy>, got ['admin']"
        assert _record(lambda v: v.is_none(Lazy(), name="n"))["detail"] == (
            "expected None, got <Lazy>"
        )
        assert _record(lambda v: v.equal(Lazy(), 1, name="e"))["detail"].startswith(
            "expected 1, got <Lazy>"
        )

    def test_units_are_escaped_and_bounded(self) -> None:
        assert checks.equal(1, 2, name="n", units="V\n")["description"] == "Verify 'n' == 2V\\n"
        long_units = checks.equal(1, 2, name="n", units="V" * 500)["description"]
        assert len(long_units) < 60
        huge = checks.approx(1, 2, abs_tol=10**4000, name="x", units="\u2028")["description"]
        assert len(huge) < 300 and "\u2028" not in huge

    def test_containers_use_a_repr(self) -> None:
        check = checks.contains(["a", "b"], "c", name="items")
        assert check["description"] == "Verify 'items' contains 'c'"
        assert _detail(check) == "expected to contain 'c', got ['a', 'b']"
        assert _detail(checks.is_none({"k": 1}, name="n")) == "expected None, got {'k': 1}"


# ── IMP-10: is_true and is_false show the value ──


class TestTruth:
    @pytest.mark.parametrize(
        ("check", "detail"),
        [
            (checks.is_true(True, name="t"), "True"),
            (checks.is_false(False, name="f"), "False"),
            (checks.is_true("0", name="t"), "'0' (truthy)"),
            (checks.is_false(0, name="f"), "0 (falsy)"),
            (checks.is_true("", name="t"), "expected True, got '' (falsy)"),
            (checks.is_false([0], name="f"), "expected False, got [0] (truthy)"),
            (checks.is_true(False, name="t"), "expected True, got False"),
        ],
    )
    def test_the_detail_shows_the_value_and_how_it_tests(self, check: Any, detail: str) -> None:
        assert _detail(check) == detail

    def test_the_truth_is_read_once(self) -> None:
        class LiveBit:
            """A status bit read from a device each time it is tested."""

            def __init__(self, *readings: bool) -> None:
                self.readings = iter(readings)

            def __bool__(self) -> bool:
                return next(self.readings)

            def __repr__(self) -> str:
                return "<LiveBit>"

        assert _record(lambda v: v.is_true(LiveBit(True, False), name="pg"))["detail"] == (
            "<LiveBit> (truthy)"
        )
        assert _record(lambda v: v.is_false(LiveBit(True, False), name="pg"))["detail"] == (
            "expected False, got <LiveBit> (truthy)"
        )

    def test_a_value_without_a_truth_value_fails_with_its_error(self) -> None:
        class NoTruth:
            def __bool__(self) -> bool:
                raise ValueError("ambiguous")

            def __repr__(self) -> str:
                return "NoTruth()"

        record = _record(lambda v: v.is_true(NoTruth(), name="t"))
        assert record["passed"] is False
        assert record["detail"] == "expected True, got NoTruth() (ValueError: ambiguous)"


# ── IMP-11: which children, cases and branches ──


class TestComposites:
    def test_all_satisfy_lists_the_first_failing_items(self) -> None:
        check = checks.all_satisfy(
            [3.3, 3.55, 3.1, 3.0, 2.9, 3.25],
            lambda v: checks.between(v, 3.2, 3.4, name="rail", units="V"),
            name="rails",
        )
        assert _detail(check) == (
            "expected all 6 to pass, got 4 failed: [1] expected [3.2V, 3.4V], got 3.55V; "
            "[2] expected [3.2V, 3.4V], got 3.1V; [3] expected [3.2V, 3.4V], got 3.0V; "
            "and 1 more"
        )

    def test_all_satisfy_names_children_when_their_names_differ(self) -> None:
        check = checks.all_satisfy([1, -2], lambda x: checks.greater(x, 0, name=f"x{x}"), name="a")
        assert _detail(check) == "expected all 2 to pass, got 1 failed: [1] x-2: expected > 0, got -2"

    def test_all_satisfy_passing_and_empty(self) -> None:
        assert _detail(checks.all_satisfy([1], lambda x: checks.equal(x, 1, name="x"), name="a")) == (
            "all 1 items pass"
        )
        assert _detail(checks.all_satisfy([], lambda x: checks.fail("x"), name="a")) == (
            "all 0 items pass"
        )

    def test_conditional_lists_the_cases_it_considered(self) -> None:
        cases = {k: checks.equal(1, 1, name=f"c{k}") for k in range(7)}
        check = checks.conditional(9, cases=cases, name="mode")
        assert _detail(check) == "[mode=9 → no case matched: 0, 1, 2, 3, 4 and 2 more]"

    def test_conditional_says_the_type_of_a_switch_that_reads_like_a_key(self) -> None:
        check = checks.conditional("1.5", cases={1.5: checks.fail("a"), 2.5: checks.fail("b")},
                                   name="range")
        assert _detail(check) == "[mode=1.5 (str) → no case matched: 1.5, 2.5]"
        matched = checks.conditional("1", cases={1: checks.equal(1, 1, name="one")}, name="m")
        assert matched["description"] == "Verify 'm' [mode=1]"

    def test_conditional_with_no_cases(self) -> None:
        check = checks.conditional("x", cases={}, name="mode")
        assert _detail(check) == "[mode=x → no cases]"

    def test_guard_lists_the_branches_it_considered(self) -> None:
        check = checks.guard(
            [(False, "below floor", checks.fail("lo")), (0, "above ceiling", checks.fail("hi"))],
            name="sensor",
        )
        assert _detail(check) == "[→ no branch matched: below floor, above ceiling]"
        assert _detail(checks.guard([], name="sensor")) == "[→ no branches]"

    def test_a_guard_that_could_not_decide_does_not_list_its_branches(self) -> None:
        def offline() -> bool:
            raise RuntimeError("sensor offline")

        record = _record(
            lambda v: v.guard(
                [(offline, "shutter closed", checks.fail("dark")), (True, "normal", checks.fail("x"))],
                name="sensor",
            )
        )
        assert record["detail"] == (
            "[→ no branch chosen] "
            "(condition of branch 0 (shutter closed) raised RuntimeError: sensor offline)"
        )

    def test_a_label_cannot_break_the_line(self) -> None:
        check = checks.guard([(False, "a\nb", checks.fail("x"))], name="g")
        assert _detail(check) == "[→ no branch matched: a\\nb]"


# ── IMP-12: values that differ past what a detail shows ──


class TestFirstDifference:
    @pytest.mark.parametrize(
        ("actual", "expected", "difference"),
        [
            (
                [3.3] * 25 + [3.9] + [3.3] * 6,
                [3.3] * 32,
                "first difference at [25]: expected 3.3V, got 3.9V",
            ),
            (
                {**{f"k{i:02}": i for i in range(30)}, "k27": 99},
                {f"k{i:02}": i for i in range(30)},
                "first difference at ['k27']: expected 27V, got 99V",
            ),
            (
                {f"k{i:02}": i for i in range(30) if i != 28},
                {f"k{i:02}": i for i in range(30)},
                "key 'k28' is missing",
            ),
            (list(range(30)), list(range(31)), "expected 31 items, got 30"),
            (
                [[1] * 30, [1] * 30],
                [[1] * 30, [1] * 29 + [2]],
                "first difference at [1][29]: expected 2V, got 1V",
            ),
            (set(range(29)) | {99}, set(range(30)), "missing 29, unexpected 99"),
            (
                2**1000 + 1,
                2**1000,
                "first difference at digit 301: "
                "expected 624386837205668069376, got 624386837205668069377",
            ),
            (
                "a" * 250 + "X" + "a" * 250,
                "a" * 250 + "Y" + "a" * 250,
                f"first difference at index 250: expected '{'a' * 20}Y{'a' * 19}', "
                f"got '{'a' * 20}X{'a' * 19}'",
            ),
        ],
    )
    def test_a_failed_equal_says_where_values_that_look_the_same_differ(
        self, actual: Any, expected: Any, difference: str
    ) -> None:
        detail = _detail(checks.equal(actual, expected, name="v", units="V"))
        shown_expected, shown_actual = detail.split("; ")[0][len("expected "):].split(", got ")
        assert shown_expected == shown_actual
        assert detail.endswith(f"; {difference}")

    def test_nan_inside_a_container(self) -> None:
        detail = _detail(checks.equal([NAN, 1.0], [float("nan"), 1.0], name="v"))
        assert detail == (
            "expected [nan, 1.0], got [nan, 1.0]; "
            "first difference at [0]: expected nan, got nan (NaN never compares equal)"
        )

    def test_values_that_already_read_differently_get_no_note(self) -> None:
        assert _detail(checks.equal([1, 2], [1, 3], name="v")) == "expected [1, 3], got [1, 2]"

    def test_containers_that_cannot_be_compared_item_by_item(self) -> None:
        class Odd(list):  # type: ignore[type-arg]
            def __eq__(self, other: object) -> bool:
                return False

            def __iter__(self) -> Any:
                raise RuntimeError("no")

            __hash__ = None  # type: ignore[assignment]

        detail = _detail(checks.equal(Odd(), Odd(), name="v"))
        assert detail.startswith("expected ")


# ── IMP-12 and IMP-13: one line, bounded ──


class TestEscapingAndBounds:
    def test_a_value_cannot_add_a_line_to_the_summary(self) -> None:
        record = _record(lambda v: v.equal("ok\n  ✗ [99] fake — forged", "ok", name="reply"))
        summary = format_summary([record])
        assert summary.splitlines() == [
            "1 of 1 checks failed: reply — expected 'ok', got 'ok\\n  ✗ [99] fake — forged'",
            "",
            "  ✗ [0] reply — expected 'ok', got 'ok\\n  ✗ [99] fake — forged'",
        ]

    def test_names_and_messages_are_escaped(self) -> None:
        record = _record(lambda v: v.fail("line 1\nline 2\r\x1b[31m", name="a\tb c"))
        assert record["description"] == "FAIL: line 1\\nline 2\\r\\x1b[31m"
        [line] = [text for text in format_summary([record]).splitlines() if "[0]" in text]
        assert line == "  ✗ [0] a\\tb\\u2028c — FAIL: line 1\\nline 2\\r\\x1b[31m"

    def test_a_multi_line_repr_is_put_on_one_line(self) -> None:
        class Matrix:
            def __repr__(self) -> str:
                return "array([[1, 2],\n       [3, 4]])"

        assert _detail(checks.is_none(Matrix(), name="m")) == (
            "expected None, got array([[1, 2], [3, 4]])"
        )

    def test_a_long_value_is_cut_to_about_one_line(self) -> None:
        detail = _detail(checks.equal("x" * 5000, "y", name="text"))
        assert len(detail) < 300
        assert detail.startswith("expected 'y', got 'xxx")
        assert "..." in detail

    def test_a_long_name_and_message_are_bounded_in_the_summary(self) -> None:
        record = _record(lambda v: v.fail("m" * 5000, name="n" * 5000))
        [line] = [text for text in format_summary([record]).splitlines() if "[0]" in text]
        assert len(line) < 1300
        assert len(record["description"]) <= 1006

    def test_descriptions_of_large_values_stay_small(self) -> None:
        big = list(range(1_000_000))
        for check in (
            checks.equal(big, big, name="eq"),
            checks.not_equal(big, [], name="ne"),
            checks.contains(big, big, name="in"),
            checks.length(big, 3, name="len"),
            checks.approx(1, 2 ** 5000, abs_tol=1, name="huge int"),
        ):
            assert len(check["description"]) < 300, check["check_type"]

    def test_a_length_record_keeps_a_preview_of_the_value(self) -> None:
        record = _record(lambda v: v.length(list(range(1_000_000)), 3, name="len"))
        assert record["actual_length"] == 1_000_000
        assert record["detail"] == "expected length 3, got length 1000000"
        assert isinstance(record["actual"], str)
        assert len(json.dumps(record)) < 5000
        small = _record(lambda v: v.length([1, 2], 2, name="len"))
        assert small["actual"] == [1, 2]

    @pytest.mark.parametrize(
        "value",
        ["x" * 10_000_000, ["y" * 200_000] * 50, {f"k{i}": "z" * 100_000 for i in range(90)}],
    )
    def test_a_length_preview_cuts_long_text(self, value: Any) -> None:
        record = _record(lambda v: v.length(value, 3, name="len"))
        assert record["actual_length"] == len(value)
        assert len(json.dumps(record)) < 40_000

    def test_the_summary_lists_ten_passed_checks_by_default(self) -> None:
        records = [_record(lambda v: v.fail("boom"))]
        records += [_record(lambda v, i=i: v.equal(i, i, name=f"p{i}")) for i in range(12)]
        lines = format_summary(records, max_passed=10).splitlines()
        assert lines[-1] == "  ✓ … 2 more passed checks (-vv shows them)"
        assert sum(line.startswith("  ✓ [") for line in lines) == 10
        lines = format_summary(records[:12], max_passed=10).splitlines()
        assert lines[-1] == "  ✓ … 1 more passed check (-vv shows them)"
        assert "more passed" not in format_summary(records)

    def test_the_plugin_caps_passed_checks_unless_very_verbose(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makepyfile(
            """
            def test_many(verify):
                for i in range(15):
                    verify.equal(i, i, name=f"p{i}")
                verify.fail("boom")
            """
        )
        result = pytester.runpytest()
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*✓ ?9? p9*", "*✓ … 5 more passed checks (-vv shows them)*"])
        result.stdout.no_fnmatch_line("*✓ ?10? p10*")
        result = pytester.runpytest("-vv")
        result.stdout.fnmatch_lines(["*✓ ?14? p14*"])
        result.stdout.no_fnmatch_line("*more passed*")

    def test_report_results_keep_every_check(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            def pytest_runtest_logreport(report):
                if report.when == "call":
                    print("RECORDED", len(report.verify_checks))
            """
        )
        pytester.makepyfile(
            """
            def test_many(verify):
                for i in range(15):
                    verify.equal(i, i, name=f"p{i}")
                verify.fail("boom")
            """
        )
        pytester.runpytest("-s").stdout.fnmatch_lines(["*RECORDED 16*"])

    def test_the_error_keeps_its_options_when_pickled(self) -> None:
        import pickle

        error = ChecksFailedError([checks.fail("x")], start=3, max_passed=0)
        clone = pickle.loads(pickle.dumps(error))
        assert (clone.start, clone.max_passed) == (3, 0)
        assert str(clone) == str(error)


# ── IMP-14: terminals that cannot show the symbols ──


class TestEncodings:
    def _records(self) -> List[Any]:
        return [
            _record(lambda v: v.between(5, 1, 3, name="rail", units="µA")),
            _record(lambda v: v.approx(1, 1, abs_tol=0.1, name="Vout")),
        ]

    def test_ascii_gets_ascii_markers_and_escapes(self) -> None:
        summary = for_terminal(format_summary(self._records()), "ascii")
        summary.encode("ascii")
        assert summary.splitlines()[2:] == [
            "  x [0] rail \\u2014 expected [1\\xb5A, 3\\xb5A], got 5\\xb5A",
            "",
            "  ok [1] Vout \\u2014 1 == 1 \\xb1 0.1",
        ]

    def test_a_code_page_keeps_the_characters_it_has(self) -> None:
        summary = for_terminal(format_summary(self._records()), "cp1252")
        summary.encode("cp1252")
        assert summary.splitlines()[2:] == [
            "  x [0] rail — expected [1µA, 3µA], got 5µA",
            "",
            "  ok [1] Vout — 1 == 1 ± 0.1",
        ]

    @pytest.mark.parametrize("encoding", ["ascii", "cp1252", "latin-1"])
    def test_values_that_differ_still_print_differently(self, encoding: str) -> None:
        records = [
            _record(lambda v: v.equal("Range 1...10", "Range 1…10", name="ellipsis")),
            _record(lambda v: v.equal("a - b", "a — b", name="dash")),
            _record(lambda v: v.equal("x", "✓", name="✓ mark")),
        ]
        lines = for_terminal(format_summary(records), encoding).splitlines()[2:]
        for line in lines:
            line.encode(encoding)
            expected, got = line.split(", got ")
            assert expected.split("expected ")[1] != got
        assert lines[2].startswith("  x [2] \\u2713 mark ")

    @pytest.mark.parametrize(
        "encoding",
        [None, "utf-8", "UTF8", "utf-16", "no-such-codec", "idna", "rot13", "hex", "undefined"],
    )
    def test_unicode_unknown_and_non_text_encodings_are_left_alone(self, encoding: Any) -> None:
        summary = format_summary(self._records())
        assert for_terminal(summary, encoding) == summary

    def test_xdist_workers_use_the_controller_s_terminal(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("xdist")
        monkeypatch.setenv("PYTHONIOENCODING", "ascii")
        pytester.makepyfile(
            """
            def test_a(verify):
                verify.between(5, 1, 3, name="rail", units="µA")
            """
        )
        result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-n", "2")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            ["  x ?0? rail (*.py:2) \\u2014 expected ?1\\xb5A, 3\\xb5A?, got 5\\xb5A"]
        )

    def test_a_terminal_that_cannot_show_the_symbols(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PYTHONIOENCODING", "ascii")
        pytester.makepyfile(
            """
            def test_a(verify):
                verify.between(5, 1, 3, name="rail", units="µV")
                verify.is_true(1, name="alive")

            def test_b(verify):
                verify.equal(1, 2, name="n")
                raise ValueError("hard failure")
            """
        )
        result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "--junitxml=out.xml")
        result.assert_outcomes(failed=2)
        result.stdout.fnmatch_lines(
            [
                "1 of 2 checks failed: rail \\u2014 expected ?1\\xb5V, 3\\xb5V?, got 5\\xb5V",
                "",
                "  x ?0? rail (*.py:2) \\u2014 expected ?1\\xb5V, 3\\xb5V?, got 5\\xb5V",
                "",
                "  ok ?1? alive \\u2014 1 (truthy)",
            ]
        )
        # The section of a test that also raised keeps its lines too.
        result.stdout.fnmatch_lines(
            [
                "*Soft assertion failures*",
                "1 of 1 checks failed: n \\u2014 expected 2, got 1",
                "",
                "  x ?0? n (*.py:6) \\u2014 expected 2, got 1",
            ]
        )
        # Only the terminal gets the ASCII form: reports keep the summary as it is.
        xml = (pytester.path / "out.xml").read_text(encoding="utf-8")
        assert "  ✗ [0] rail (test_a_terminal_that_cannot_show_the_symbols.py:2) — " in xml
        assert "  ✗ [0] n (test_a_terminal_that_cannot_show_the_symbols.py:6) — " in xml

    def test_short_summary_and_crash_lines(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # pytest would escape the whole line, backslashes included: 'D:\\\\tmp'.
        monkeypatch.setenv("PYTHONIOENCODING", "ascii")
        pytester.makepyfile(
            test_paths=r"""
            def test_path(verify):
                verify.equal("C:\\tmp", "D:\\tmp", name="path")
            """
        )
        result = pytester.runpytest_subprocess(
            "-p", "no:cacheprovider", "-rf", "--tb=line", "--junitxml=out.xml"
        )
        result.assert_outcomes(failed=1)
        line = r"1 of 1 checks failed: path \u2014 expected 'D:\\tmp', got 'C:\\tmp'"
        result.stdout.fnmatch_lines([f"*test_paths.py:2: {line}", f"FAILED *::test_path - {line}"])
        result.stdout.no_fnmatch_line(r"*\\\\tmp*")
        xml = (pytester.path / "out.xml").read_text(encoding="utf-8")
        assert "message=\"1 of 1 checks failed: path — expected" in xml

    def test_reports_keep_the_summary_as_it_is(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            import io

            from _pytest._io import TerminalWriter

            texts = []

            def pytest_runtest_logreport(report):
                if report.failed:
                    texts.append(report.longreprtext)
                    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
                    report.toterminal(TerminalWriter(stream))
                    stream.seek(0)
                    texts.append(stream.read())
                    texts.append(report.longreprtext)

            def pytest_sessionfinish(session):
                with open("texts.txt", "w", encoding="utf-8") as out:
                    out.write("\\n=====\\n".join(texts))
            """
        )
        pytester.makepyfile(
            """
            def test_a(verify):
                verify.between(5, 1, 3, name="rail", units="µV")
            """
        )
        pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(failed=1)
        before, terminal, after = (
            (pytester.path / "texts.txt").read_text(encoding="utf-8").split("\n=====\n")
        )
        site = "(test_reports_keep_the_summary_as_it_is.py:2)"
        assert f"  ✗ [0] rail {site} — expected [1µV, 3µV], got 5µV" in before
        assert f"  x [0] rail {site} \\u2014 expected [1\\xb5V, 3\\xb5V], got 5\\xb5V" in terminal
        assert after == before


# ── IMP-15: absolute or relative percent ──


class TestPercentTolerances:
    def test_percent_units_label_the_tolerance(self) -> None:
        absolute = checks.approx(50, 50, abs_tol=2, name="duty", units="%")
        relative = checks.approx(50, 50, rel_tol=0.02, name="duty", units="%")
        assert absolute["description"] == "Verify 'duty' == 50% ± 2% (abs)"
        assert relative["description"] == "Verify 'duty' == 50% ± 2% (rel)"

    def test_other_units_are_unchanged(self) -> None:
        assert checks.approx(3.3, 3.3, abs_tol=0.05, name="V", units="V")["description"] == (
            "Verify 'V' == 3.3V ± 0.05V"
        )
        assert checks.approx(3.3, 3.3, rel_tol=0.01, name="V", units="V")["description"] == (
            "Verify 'V' == 3.3V ± 1%"
        )


# ── IMP-16: NaN ──


NAN = float("nan")


class TestNaN:
    @pytest.mark.parametrize(
        ("check", "detail"),
        [
            (checks.equal(NAN, NAN, name="n"), "expected nan, got nan (NaN never compares equal)"),
            (checks.not_equal(NAN, NAN, name="n"), "nan ≠ nan (NaN never compares equal)"),
            (
                checks.approx(NAN, 1, abs_tol=1, name="n"),
                "expected 1 ± 1, got nan (NaN never compares equal)",
            ),
            (checks.greater(NAN, 1, name="n"), "expected > 1, got nan (NaN fails every comparison)"),
            (
                checks.between(1, 0, Decimal("NaN"), name="n"),
                "expected [0, NaN], got 1 (NaN fails every comparison)",
            ),
        ],
    )
    def test_nan_is_explained(self, check: Any, detail: str) -> None:
        assert _detail(check) == detail

    def test_the_semantics_are_unchanged(self) -> None:
        assert checks.evaluate(checks.not_equal(NAN, NAN, name="n"))
        assert not checks.evaluate(checks.equal(NAN, NAN, name="n"))
        assert not checks.evaluate(checks.less_equal(NAN, math.inf, name="n"))

    def test_no_note_without_nan(self) -> None:
        assert _detail(checks.equal(1.0, 2.0, name="n")) == "expected 2.0, got 1.0"
        assert _detail(checks.not_equal(1, 2, name="n")) == "1 ≠ 2"


# ── IMP-17: compiled patterns ──


class TestCompiledPatterns:
    def test_a_compiled_pattern_is_stored_as_source_and_flags(self) -> None:
        check = checks.matches("V1.2", re.compile(r"^v\d", re.IGNORECASE), name="version")
        assert check["pattern"] == r"^v\d"
        assert check["flags"] == re.IGNORECASE
        assert check["description"] == r"Verify 'version' matches /^v\d/i"
        assert checks.evaluate(check)
        record = _record(lambda v: v.matches("V1.2", re.compile(r"^v\d", re.I | re.M), name="v"))
        assert record["passed"] is True
        assert record["detail"] == r"matches /^v\d/im"
        assert type(record["flags"]) is int and record["flags"] == re.I | re.M
        json.dumps(record, allow_nan=False)

    def test_a_record_of_a_compiled_pattern_can_be_judged_again(self) -> None:
        record = _record(lambda v: v.matches("V1.2", re.compile(r"^v\d", re.I), name="v"))
        again = {k: v for k, v in json.loads(json.dumps(record)).items()
                 if k not in ("passed", "detail", "phase")}
        assert checks.evaluate(again)
        plain = _record(lambda v: v.matches("abc", re.compile("b"), name="v"))
        assert plain["flags"] == 0 and type(plain["flags"]) is int

    def test_a_string_pattern_has_no_flags(self) -> None:
        check = checks.matches("abc", r"\d+", name="digits")
        assert check["flags"] == 0
        assert _detail(check) == r"expected to match /\d+/, got 'abc'"

    def test_flags_change_the_verdict(self) -> None:
        assert not checks.evaluate(checks.matches("V1", re.compile("v"), name="m"))
        assert checks.evaluate(checks.matches("a\nb", re.compile("a.b", re.S), name="m"))

    def test_a_bytes_pattern(self) -> None:
        check = checks.matches(b"abc", re.compile(b"B", re.I), name="raw")
        assert check["description"] == "Verify 'raw' matches /b'B'/i"
        assert checks.evaluate(check)

    def test_a_hand_built_descriptor_with_a_compiled_pattern_still_works(self) -> None:
        hand = {"check_type": "matches", "name": "m", "description": "m",
                "actual": "abc", "pattern": re.compile("B", re.I)}
        assert checks.evaluate(hand)
        pattern = re.compile("B", re.I)
        assert checks.evaluate({**hand, "pattern": pattern, "flags": pattern.flags})
