"""Register-aware checks: ``radix=``/``width=``, ``verify.bits``, the ``__verify_format__``
protocol, and what they add to a record (``rendered``, ``bit_diff``, ...)."""
from __future__ import annotations

import enum
import json
import re
import typing
import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import pytest

import pytest_verifier
from pytest_verifier import (
    ChecksFailedError,
    LimitRow,
    ValueFormat,
    Verify,
    VerifyFormat,
    checks,
    load_limits,
)
from pytest_verifier._checks import REGISTRY, render_detail
from pytest_verifier._exceptions import format_summary
from pytest_verifier._registers import (
    MAX_BITS,
    bit_diff,
    bit_ranges,
    difference_clause,
    ranges_text,
    render_int,
)
from pytest_verifier._run import Run, recording_verify
from pytest_verifier._settle import settle
from pytest_verifier._summary import SessionSummary

#: The checks that take ``radix=`` and ``width=``, each as a passing call.
FORMATTED: Dict[str, Callable[..., Any]] = {
    "equal": lambda v, **extra: v.equal(0xF3, 0xF3, name="R", **extra),
    "not_equal": lambda v, **extra: v.not_equal(0xF0, 0xF3, name="R", **extra),
    "greater": lambda v, **extra: v.greater(0xF4, 0xF3, name="R", **extra),
    "greater_equal": lambda v, **extra: v.greater_equal(0xF3, 0xF3, name="R", **extra),
    "less": lambda v, **extra: v.less(0xF0, 0xF3, name="R", **extra),
    "less_equal": lambda v, **extra: v.less_equal(0xF3, 0xF3, name="R", **extra),
    "between": lambda v, **extra: v.between(0xF3, 0xF0, 0xFF, name="R", **extra),
    "bits": lambda v, **extra: v.bits(0xF3, 0xF3, name="R", **extra),
}

#: The keys a format may add to a descriptor.
FORMAT_KEYS = {
    "radix", "width", "fields", "mask", "rendered", "bit_diff", "format_error", "format_values",
}

FIELDS = {"EN": 0, "MODE": (3, 1)}


def _recording() -> Tuple[Run, Any]:
    run = Run()
    run.phase = "call"
    return run, recording_verify(run)


def _verify() -> Any:
    return _recording()[1]


def _again(record: Mapping[str, Any]) -> str:
    """The detail of *record* rendered again, from a JSON copy of it."""
    copy = json.loads(json.dumps(record, allow_nan=False))
    return render_detail(copy, copy["passed"])


class Word(int):
    """An ``int`` subclass that carries its format."""

    def __verify_format__(self) -> ValueFormat:
        return {"radix": "hex", "width": 16, "fields": FIELDS}


class Register:
    """A register object that is not an ``int``: it gives its value."""

    calls = 0

    def __init__(self, value: int) -> None:
        self.value = value

    def __verify_format__(self) -> Mapping[str, Any]:
        type(self).calls += 1
        return {"width": 16, "fields": FIELDS, "value": self.value}

    def __eq__(self, other: object) -> bool:
        return self.value == (other.value if isinstance(other, Register) else other)

    __hash__ = None  # type: ignore[assignment]


class Indexed:
    """An object with ``__index__`` and a format, but no ``value``."""

    def __init__(self, value: int) -> None:
        self._value = value

    def __index__(self) -> int:
        return self._value

    def __verify_format__(self) -> Mapping[str, Any]:
        return {"radix": "bin", "width": 8}

    def __eq__(self, other: object) -> bool:
        return self._value == other

    __hash__ = None  # type: ignore[assignment]


def _answering(answer: Any) -> Any:
    """An ``int`` subclass instance (value 5) whose ``__verify_format__`` returns *answer*,
    or raises it when it is an exception."""

    class Answering(int):
        def __verify_format__(self) -> Any:
            if isinstance(answer, BaseException):
                raise answer
            return answer

    return Answering(5)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRenderInt:
    @pytest.mark.parametrize(
        "value, radix, width, text",
        [
            (0xF3, "hex", 16, "0x00F3"),
            (0xF3, "hex", None, "0xF3"),
            (0xF3, "hex", 8, "0xF3"),
            (0xF3, "hex", 9, "0x0F3"),
            (0, "hex", 8, "0x00"),
            (1, "hex", 1, "0x1"),
            (0xABCDE, "hex", None, "0xA_BCDE"),
            (0xDEADBEEF, "hex", 32, "0xDEAD_BEEF"),
            (0xDEADBEEF, "hex", None, "0xDEAD_BEEF"),
            (0xBEEF, "hex", 32, "0x0000_BEEF"),
            (0x12345, "hex", 16, "0x1_2345"),  # wider than the width: every digit
            (0xF3, "bin", 8, "0b1111_0011"),
            (0xF, "bin", 8, "0b0000_1111"),
            (5, "bin", None, "0b101"),
            (5, "bin", 4, "0b0101"),
            (0b10101, "bin", None, "0b1_0101"),
            (0x1FF, "bin", 8, "0b1_1111_1111"),
            (0xF3, "oct", None, "0o363"),
            (0xF3, "oct", 16, "0o000363"),
            (0xF3, "dec", None, "243"),
            (0xF3, "dec", 16, "243"),
            (-15, "hex", 16, "-0x000F"),
            (-5, "bin", 4, "-0b0101"),
            (-8, "oct", None, "-0o10"),
            (-243, "dec", 16, "-243"),
            (2**64 - 1, "hex", 64, "0xFFFF_FFFF_FFFF_FFFF"),
            (2**64, "hex", 64, "0x1_0000_0000_0000_0000"),
            (2**127 + 1, "hex", 128, "0x8000_0000_0000_0000_0000_0000_0000_0001"),
        ],
    )
    def test_text(self, value: int, radix: str, width: Optional[int], text: str) -> None:
        assert render_int(value, radix, width) == text
        assert int(text, 0) == value  # a Python literal of the same number

    @pytest.mark.parametrize("radix", ["hex", "bin", "oct", "dec"])
    def test_a_value_above_the_limit_keeps_its_usual_rendering(self, radix: str) -> None:
        assert render_int(2**MAX_BITS - 1, radix) is not None
        assert render_int(2**MAX_BITS, radix) is None
        assert render_int(-(2**MAX_BITS), radix) is None

    def test_an_int_subclass_renders_as_its_number(self) -> None:
        class Loud(int):
            def __format__(self, spec: str) -> str:
                return "LOUD"

            __str__ = __repr__ = lambda self: "LOUD"  # type: ignore[assignment]

        check = checks.equal(Loud(0xF3), 0xF3, name="R", width=16)
        assert check["rendered"] == {"actual": "0x00F3", "expected": "0x00F3"}


class TestRadixAndWidth:
    @pytest.mark.parametrize("method", sorted(FORMATTED))
    def test_every_formatted_check_takes_them(self, method: str) -> None:
        check = FORMATTED[method](checks, radix="bin", width=8)
        assert check["radix"] == "bin" and check["width"] == 8
        assert check["rendered"]["actual"].startswith("0b1111_")
        assert "0b1111_" in check["description"]
        assert checks.evaluate(check)
        json.dumps(check)

    @pytest.mark.parametrize("method", sorted(FORMATTED))
    def test_width_alone_means_hex(self, method: str) -> None:
        check = FORMATTED[method](checks, width=16)
        assert check["radix"] == "hex" and check["width"] == 16
        assert check["rendered"]["actual"].startswith("0x00F")

    @pytest.mark.parametrize("method", sorted(set(FORMATTED) - {"bits"}))
    def test_radix_alone_uses_the_digits_the_value_needs(self, method: str) -> None:
        check = FORMATTED[method](checks, radix="hex")
        assert check["radix"] == "hex" and "width" not in check
        assert check["rendered"]["actual"] in ("0xF0", "0xF3", "0xF4")

    @pytest.mark.parametrize("method", sorted(set(FORMATTED) - {"bits"}))
    def test_a_plain_check_is_unchanged(self, method: str) -> None:
        plain = FORMATTED[method](checks)
        assert set(plain).isdisjoint(FORMAT_KEYS)
        record = settle(plain)[0]
        assert set(record).isdisjoint(FORMAT_KEYS)
        assert "0x" not in plain["description"] + record["detail"]

    def test_plain_text_is_byte_for_byte_as_before(self) -> None:
        verify = _verify()
        assert verify.equal(240, 243, name="R")["detail"] == "expected 243, got 240"
        assert verify.equal(243, 243, name="R", units="V")["detail"] == "243V == 243V"
        same = verify.not_equal(Decimal(1), 1, name="R")
        assert same["detail"] == "expected ≠ 1 (int), got 1 (Decimal)"
        assert verify.greater(1, Decimal(1), name="R")["detail"] == (
            "expected > 1 (Decimal), got 1 (int)"
        )
        assert verify.between(5, 1, 3, name="R", units="A")["detail"] == (
            "expected [1A, 3A], got 5A"
        )
        long = verify.equal(10**300, 10**300 + 1, name="R")["detail"]
        assert "first difference at digit" in long

    @pytest.mark.parametrize("method", sorted(FORMATTED))
    @pytest.mark.parametrize(
        "arguments, error, message",
        [
            ({"radix": "hexa"}, ValueError, r"radix must be 'hex', 'bin', 'oct' or 'dec', got 'he"),
            ({"radix": "HEX"}, ValueError, r"radix must be"),
            ({"radix": 16}, TypeError, r"radix must be 'hex', 'bin', 'oct' or 'dec', got int"),
            ({"radix": b"hex"}, TypeError, r"radix must be"),
            ({"width": 0}, ValueError, r"width must be a number of bits from 1 to 1024, got 0"),
            ({"width": -8}, ValueError, r"width must be a number of bits from 1 to 1024"),
            ({"width": 1025}, ValueError, r"width must be a number of bits from 1 to 1024"),
            ({"width": True}, TypeError, r"width must be a number of bits \(an int\), got bool"),
            ({"width": 16.0}, TypeError, r"width must be a number of bits \(an int\), got float"),
            ({"width": "16"}, TypeError, r"width must be a number of bits \(an int\), got str"),
        ],
    )
    def test_usage_errors_raise_at_the_call(
        self, method: str, arguments: Dict[str, Any], error: type, message: str
    ) -> None:
        run, verify = _recording()
        with pytest.raises(error, match=rf"{method}\(\) {message}"):
            FORMATTED[method](verify, **arguments)
        assert run.records == []

    def test_a_str_enum_radix_is_stored_as_text(self) -> None:
        class Base(str, enum.Enum):
            HEX = "hex"

        check = checks.equal(1, 1, name="R", radix=Base.HEX)  # type: ignore[arg-type]
        assert check["radix"] == "hex" and type(check["radix"]) is str

    def test_approx_and_the_other_checks_take_neither(self) -> None:
        for build in (
            lambda: checks.approx(1, 1, abs_tol=1, name="R", radix="hex"),  # type: ignore[call-arg]
            lambda: checks.length("ab", 2, name="R", width=8),  # type: ignore[call-arg]
            lambda: checks.is_true(1, name="R", radix="hex"),  # type: ignore[call-arg]
            lambda: checks.contains([1], 1, name="R", radix="hex"),  # type: ignore[call-arg]
        ):
            with pytest.raises(TypeError, match="unexpected keyword argument"):
                build()

    def test_descriptions(self) -> None:
        assert checks.equal(0, 0xF3, name="R", radix="hex", width=16)["description"] == (
            "Verify 'R' == 0x00F3"
        )
        assert checks.not_equal(0, 0xF3, name="R", radix="bin")["description"] == (
            "Verify 'R' != 0b1111_0011"
        )
        assert checks.greater(0, 8, name="R", radix="oct")["description"] == "Verify 'R' > 0o10"
        assert checks.less_equal(0, 8, name="R", radix="dec")["description"] == "Verify 'R' <= 8"
        assert checks.between(0, 1, 0x1F, name="R", width=8)["description"] == (
            "Verify 'R' ∈ [0x01, 0x1F]"
        )
        assert checks.between(0, 1, 3, inclusive=False, name="R", radix="bin", width=4)[
            "description"
        ] == "Verify 'R' ∈ (0b0001, 0b0011)"

    def test_details(self) -> None:
        verify = _verify()
        assert verify.equal(0xF3, 0xF3, name="R", width=16)["detail"] == "0x00F3 == 0x00F3"
        assert verify.equal(0xF0, 0xF3, name="R", width=16)["detail"] == (
            "expected 0x00F3, got 0x00F0; bits [1:0] differ"
        )
        assert verify.not_equal(0xF0, 0xF3, name="R", width=8)["detail"] == "0xF0 ≠ 0xF3"
        assert verify.not_equal(0xF3, 0xF3, name="R", width=8)["detail"] == (
            "expected ≠ 0xF3, got 0xF3"
        )
        assert verify.greater(0x20, 0x10, name="R", width=8)["detail"] == "0x20 > 0x10"
        assert verify.greater_equal(0x0F, 0x10, name="R", width=8)["detail"] == (
            "expected >= 0x10, got 0x0F"
        )
        assert verify.less(0x10, 0x10, name="R", radix="bin")["detail"] == (
            "expected < 0b1_0000, got 0b1_0000"
        )
        assert verify.less_equal(8, 9, name="R", radix="oct")["detail"] == "0o10 <= 0o11"
        assert verify.between(2, 1, 3, name="R", radix="bin", width=4)["detail"] == (
            "0b0010 ∈ [0b0001, 0b0011]"
        )
        assert verify.between(5, 1, 3, inclusive=False, name="R", width=4)["detail"] == (
            "expected (0x1, 0x3), got 0x5"
        )

    def test_units_follow_the_value_and_stay_out_of_rendered(self) -> None:
        record = _verify().equal(0xF0, 0xF3, name="R", width=8, units="V")
        assert record["description"] == "Verify 'R' == 0xF3V"
        assert record["detail"] == "expected 0xF3V, got 0xF0V; bits [1:0] differ"
        assert record["rendered"] == {"actual": "0xF0", "expected": "0xF3"}
        spaced = _verify().less(0x10, 0x20, name="R", width=8, units=" counts")
        assert spaced["detail"] == "0x10 counts < 0x20 counts"

    def test_an_expected_name_goes_before_the_value(self) -> None:
        record = _verify().equal(0xF0, 0xF3, name="R", width=16, expected_name="wdata")
        assert record["description"] == "Verify 'R' == wdata (0x00F3)"
        assert record["detail"] == "expected wdata (0x00F3), got 0x00F0; bits [1:0] differ"

    def test_decimal_radix_still_says_which_bits_differ(self) -> None:
        record = _verify().equal(240, 243, name="R", radix="dec")
        assert record["detail"] == "expected 243, got 240; bits [1:0] differ"
        assert record["rendered"] == {"actual": "240", "expected": "243"}
        assert record["radix"] == "dec"

    def test_a_format_never_changes_a_verdict(self) -> None:
        for method, build in FORMATTED.items():
            assert checks.evaluate(build(checks)) is checks.evaluate(build(checks, width=8)), method
        for actual, expected in [(1, 2), (1, 1), (1.0, 1), ("1", 1), (None, 1), (True, 1)]:
            plain = checks.evaluate(checks.equal(actual, expected, name="R"))
            assert checks.evaluate(checks.equal(actual, expected, name="R", radix="hex")) is plain


class TestOtherOperands:
    """A format applies only to the operands that are integers."""

    def test_bool(self) -> None:
        record = _verify().equal(True, 1, name="R", radix="hex", width=8)
        assert record["passed"] is True
        assert record["detail"] == "True == 0x01"
        assert record["rendered"] == {"expected": "0x01"}
        assert "bit_diff" not in record

    def test_float(self) -> None:
        record = _verify().equal(1.5, 1, name="R", radix="hex", width=8)
        assert record["detail"] == "expected 0x01, got 1.5"
        assert record["rendered"] == {"expected": "0x01"}
        above = _verify().greater(1.5, 1, name="R", radix="bin", units="V")
        assert above["detail"] == "1.5V > 0b1V"

    def test_str(self) -> None:
        record = _verify().equal("0xF3", 0xF3, name="R", width=8)
        assert record["passed"] is False
        assert record["detail"] == "expected 0xF3, got '0xF3'"

    def test_none(self) -> None:
        record = _verify().between(None, 1, 3, name="R", width=8)
        assert record["passed"] is False
        assert record["detail"].startswith("expected [0x01, 0x03], got None (TypeError: ")

    def test_no_integer_at_all(self) -> None:
        record = _verify().equal(1.5, "a", name="R", radix="hex")
        assert record["radix"] == "hex" and "rendered" not in record
        assert record["detail"] == "expected 'a', got 1.5"

    def test_an_int_enum_member_is_an_integer(self) -> None:
        class Gain(enum.IntEnum):
            LOW = 10

        record = _verify().equal(Gain.LOW, 11, name="R", width=8)
        assert record["rendered"] == {"actual": "0x0A", "expected": "0x0B"}
        assert record["detail"] == "expected 0x0B, got 0x0A; bit 0 differs"
        assert _again(record) == record["detail"]

    def test_an_object_with_index_and_no_protocol_is_not_an_integer(self) -> None:
        class Index:
            def __index__(self) -> int:
                return 5

            def __repr__(self) -> str:
                return "Index()"

        record = _verify().equal(Index(), 5, name="R", radix="hex", width=8)
        assert record["rendered"] == {"expected": "0x05"}
        assert record["detail"] == "expected 0x05, got Index()"


# ---------------------------------------------------------------------------
# Wide values
# ---------------------------------------------------------------------------


class TestWideValues:
    @pytest.mark.parametrize("width", [64, 128])
    def test_exact_in_the_record_and_in_json(self, width: int) -> None:
        actual = (0xDEADBEEFCAFEF00D << (width - 64)) | 1
        expected = actual ^ (1 << (width - 1)) ^ 1
        record = _verify().equal(actual, expected, name="W", width=width)
        copy = json.loads(json.dumps(record))
        assert copy["actual"] == actual and copy["expected"] == expected
        assert int(copy["rendered"]["actual"], 0) == actual
        assert int(copy["rendered"]["expected"], 0) == expected
        assert len(copy["rendered"]["actual"].replace("_", "")) == 2 + width // 4
        assert copy["bit_diff"]["bits"] == [[width - 1, width - 1], [0, 0]]
        assert copy["detail"].endswith(f"; bit {width - 1} and bit 0 differ")
        assert _again(record) == record["detail"]

    def test_64_bit_text(self) -> None:
        record = _verify().bits(0xFFFF_FFFF_FFFF_FFFF, 0xFFFF_FFFF_FFFF_FFFE, width=64, name="W")
        assert record["description"] == "Verify 'W' == 0xFFFF_FFFF_FFFF_FFFE"
        assert record["detail"] == (
            "expected 0xFFFF_FFFF_FFFF_FFFE, got 0xFFFF_FFFF_FFFF_FFFF; bit 0 differs"
        )
        assert record["actual"] == 18446744073709551615

    def test_a_value_wider_than_the_width_is_never_truncated(self) -> None:
        record = _verify().equal(0x12345, 0x2345, name="W", width=16)
        assert record["rendered"] == {"actual": "0x1_2345", "expected": "0x2345"}
        assert record["bit_diff"]["bits"] == [[16, 16]]

    def test_above_the_limit_the_usual_rendering_is_kept(self) -> None:
        huge = 1 << 2000
        record = _verify().equal(huge, huge, name="W", radix="hex")
        assert record["passed"] is True and "rendered" not in record
        assert "0x" not in record["description"]
        failed = _verify().bits(huge, 1, name="W")
        assert failed["passed"] is False and "bit_diff" not in failed
        assert failed["rendered"] == {"expected": "0x1"}
        json.dumps(failed, allow_nan=False)


# ---------------------------------------------------------------------------
# Bit differences
# ---------------------------------------------------------------------------


class TestBitDifference:
    @pytest.mark.parametrize(
        "bits, ranges, text",
        [
            (0b11, [[1, 0]], "bits [1:0] differ"),
            (0b10000, [[4, 4]], "bit 4 differs"),
            (0b10001, [[4, 4], [0, 0]], "bit 4 and bit 0 differ"),
            (0b11010011, [[7, 6], [4, 4], [1, 0]], "bits [7:6], bit 4 and bits [1:0] differ"),
            (0xFF00, [[15, 8]], "bits [15:8] differ"),
            (1 << 63, [[63, 63]], "bit 63 differs"),
        ],
    )
    def test_ranges_are_runs_msb_first(self, bits: int, ranges: List[List[int]], text: str) -> None:
        assert bit_ranges(bits) == ranges
        assert ranges_text(ranges) == text
        assert bit_diff(bits, 0) == {"bits": ranges, "text": text}

    def test_the_text_names_eight_ranges_then_counts(self) -> None:
        eight = bit_diff(0x5555, 0)
        assert eight is not None and eight["text"] == (
            "bit 14, bit 12, bit 10, bit 8, bit 6, bit 4, bit 2 and bit 0 differ"
        )
        nine = bit_diff(0x15555, 0)
        assert nine is not None and nine["text"] == (
            "bit 16, bit 14, bit 12, bit 10, bit 8, bit 6, bit 4, bit 2 and 1 more differ"
        )
        assert len(nine["bits"]) == 9  # the list itself is not cut there

    def test_the_list_keeps_64_ranges(self) -> None:
        alternating = int("10" * 100, 2)
        found = bit_diff(alternating, 0)
        assert found is not None
        assert len(found["bits"]) == 64 and found["more_bits"] == 36
        assert found["bits"][0] == [199, 199]
        assert found["text"].endswith(" and 92 more differ")

    def test_none_when_equal_or_negative_without_a_mask(self) -> None:
        assert bit_diff(5, 5) is None
        assert bit_diff(-5, 5) is None
        assert bit_diff(5, -5) is None
        assert bit_diff(-5, 5, 0xFF) == {
            "bits": [[7, 1]], "text": "bits [7:1] differ"
        }
        assert bit_diff(1 << 2000, 0) is None

    def test_only_bits_under_the_mask_count(self) -> None:
        assert bit_diff(0x12F0, 0xABF3, 0x00FF) == {"bits": [[1, 0]], "text": "bits [1:0] differ"}
        assert bit_diff(0x12F3, 0xABF3, 0x00FF) is None
        assert bit_diff(0xF0, 0xF3, 0) is None

    def test_fields(self) -> None:
        fields = {"EN": [0, 0], "MODE": [3, 1], "DIV": [15, 8], "RES": [7, 4]}
        found = bit_diff(0x12F0, 0x34F3, None, fields)
        assert found == {
            "bits": [[13, 13], [10, 9], [1, 0]],
            "text": "bit 13, bits [10:9] and bits [1:0] differ",
            "fields": [
                {"name": "EN", "msb": 0, "lsb": 0, "actual": "0b0", "expected": "0b1"},
                {"name": "MODE", "msb": 3, "lsb": 1, "actual": "0b000", "expected": "0b001"},
                {"name": "DIV", "msb": 15, "lsb": 8, "actual": "0x12", "expected": "0x34"},
            ],
        }
        assert difference_clause(found) == (
            "bit 13, bits [10:9] and bits [1:0] differ: EN [0] is 0b0, expected 0b1; "
            "MODE [3:1] is 0b000, expected 0b001; DIV [15:8] is 0x12, expected 0x34"
        )

    @pytest.mark.parametrize(
        "size, actual, expected",
        [
            (1, "0b1", "0b0"),
            (4, "0b0001", "0b0000"),
            (5, "0x01", "0x00"),
            (8, "0x01", "0x00"),
            (12, "0x001", "0x000"),
            (20, "0x0_0001", "0x0_0000"),
        ],
    )
    def test_a_field_is_binary_up_to_four_bits_then_hex(
        self, size: int, actual: str, expected: str
    ) -> None:
        found = bit_diff(1, 0, None, {"F": [size - 1, 0]})
        assert found is not None
        assert found["fields"] == [
            {"name": "F", "msb": size - 1, "lsb": 0, "actual": actual, "expected": expected}
        ]

    def test_bits_outside_every_field_are_still_listed(self) -> None:
        found = bit_diff(0x80, 0x00, None, {"EN": [0, 0]})
        assert found == {"bits": [[7, 7]], "text": "bit 7 differs"}
        assert difference_clause(found) == "bit 7 differs"

    def test_field_values_are_those_under_the_mask(self) -> None:
        found = bit_diff(0b1111, 0b0000, 0b0011, {"F": [3, 0]})
        assert found is not None and found["fields"] == [
            {"name": "F", "msb": 3, "lsb": 0, "actual": "0b0011", "expected": "0b0000"}
        ]

    def test_the_detail_names_six_fields_then_counts(self) -> None:
        fields = {f"F{n}": n for n in range(8)}
        record = _verify().bits(0xFF, 0x00, fields=fields, width=8, name="R")
        assert len(record["bit_diff"]["fields"]) == 8
        assert record["detail"] == (
            "expected 0x00, got 0xFF; bits [7:0] differ: "
            + "; ".join(f"F{n} [{n}] is 0b1, expected 0b0" for n in range(6))
            + "; and 2 more"
        )

    def test_overlapping_fields_are_each_named(self) -> None:
        record = _verify().bits(0b0110, 0b0000, fields={"LOW": (2, 0), "MID": (2, 1)}, name="R")
        assert [field["name"] for field in record["bit_diff"]["fields"]] == ["LOW", "MID"]
        assert record["detail"].endswith(
            "bits [2:1] differ: LOW [2:0] is 0b110, expected 0b000; MID [2:1] is 0b11, "
            "expected 0b00"
        )

    @pytest.mark.parametrize(
        "junk", [None, 5, "bits", [], {}, {"text": 5}, {"text": ""}, {"text": "x", "fields": 5},
                 {"text": "x", "fields": [5]}, {"text": "x", "fields": [{"name": "A"}]}]
    )
    def test_a_hand_built_difference_never_raises(self, junk: Any) -> None:
        assert difference_clause(junk) in ("", "x")

    def test_negative_operands_have_no_bit_list(self) -> None:
        record = _verify().equal(-15, 15, name="R", width=16)
        assert record["detail"] == "expected 0x000F, got -0x000F"
        assert "bit_diff" not in record


# ---------------------------------------------------------------------------
# verify.bits
# ---------------------------------------------------------------------------


class TestBits:
    def test_registered(self) -> None:
        assert REGISTRY["bits"].check_type == "bits"
        assert checks.bits(1, 1, name="R")["check_type"] == "bits"

    def test_hex_by_default(self) -> None:
        check = checks.bits(0xF3, 0xF3, name="CTRL")
        assert check == {
            "check_type": "bits",
            "name": "CTRL",
            "description": "Verify 'CTRL' == 0xF3",
            "actual": 0xF3,
            "expected": 0xF3,
            "radix": "hex",
            "rendered": {"actual": "0xF3", "expected": "0xF3"},
        }

    def test_the_whole_descriptor_with_a_mask_and_fields(self) -> None:
        check = checks.bits(0x12F0, 0xABF3, mask=0x00FF, fields=FIELDS, width=16, name="CTRL")
        assert check == {
            "check_type": "bits",
            "name": "CTRL",
            "description": "Verify 'CTRL' & 0x00FF == 0x00F3",
            "actual": 0x12F0,
            "expected": 0xABF3,
            "mask": 0x00FF,
            "radix": "hex",
            "width": 16,
            "fields": {"EN": [0, 0], "MODE": [3, 1]},
            "rendered": {"actual": "0x12F0", "expected": "0xABF3", "mask": "0x00FF"},
            "bit_diff": {
                "bits": [[1, 0]],
                "text": "bits [1:0] differ",
                "fields": [
                    {"name": "EN", "msb": 0, "lsb": 0, "actual": "0b0", "expected": "0b1"},
                    {"name": "MODE", "msb": 3, "lsb": 1, "actual": "0b000", "expected": "0b001"},
                ],
            },
        }

    def test_failed_detail(self) -> None:
        record = _verify().bits(0xF0, 0xF3, mask=0x00FF, fields={"MODE": (3, 1)}, width=16,
                                name="CTRL")
        assert record["passed"] is False
        assert record["detail"] == (
            "expected 0x00F3, got 0x00F0 (mask 0x00FF); bits [1:0] differ: "
            "MODE [3:1] is 0b000, expected 0b001"
        )
        assert _again(record) == record["detail"]

    def test_passed_details_stay_short(self) -> None:
        verify = _verify()
        assert verify.bits(0xF3, 0xF3, width=16, name="R")["detail"] == "0x00F3 == 0x00F3"
        masked = verify.bits(0x12F3, 0xABF3, mask=0x00FF, width=16, fields=FIELDS, name="R")
        assert masked["passed"] is True and "bit_diff" not in masked
        assert masked["detail"] == "0x12F3 & 0x00FF == 0x00F3"
        assert _again(masked) == masked["detail"]

    @pytest.mark.parametrize(
        "actual, expected, mask, passed",
        [
            (0xF3, 0xF3, None, True),
            (0xF0, 0xF3, None, False),
            (0x12F3, 0x00F3, 0x00FF, True),  # the bits that differ are masked out
            (0x12F3, 0x00F3, 0x10FF, False),
            (0xF0, 0xF3, 0, True),  # no bit counts
            (0xF0, 0xF3, 0b10, False),
            (-1, 0xFF, 0xFF, True),  # two's complement under a mask, as Python's & does
            (-1, 0xFE, 0xFF, False),
            (-5, -5, None, True),
            (-5, 5, None, False),
            (2**70, 2**70 + 1, 2**70, True),
        ],
    )
    def test_mask_semantics(
        self, actual: int, expected: int, mask: Optional[int], passed: bool
    ) -> None:
        check = checks.bits(actual, expected, mask=mask, name="R")
        assert checks.evaluate(check) is passed
        record = _verify().bits(actual, expected, mask=mask, name="R")
        assert record["passed"] is passed and "error" not in record
        assert ("bit_diff" in record) is (not passed and (mask is not None or actual >= 0))
        assert ("mask" in record) is (mask is not None)

    def test_the_description_shows_expected_under_the_mask(self) -> None:
        check = checks.bits(0, 0xABF3, mask=0x0F0F, width=16, name="R")
        assert check["description"] == "Verify 'R' & 0x0F0F == 0x0B03"
        assert check["rendered"]["expected"] == "0xABF3"  # the whole value given
        assert checks.bits(0, 0xF3, mask=0, name="R")["description"] == "Verify 'R' & 0x0 == 0x0"

    def test_radix(self) -> None:
        record = _verify().bits(0b1010, 0b1001, mask=0b1111, radix="bin", width=8, name="R")
        assert record["description"] == "Verify 'R' & 0b0000_1111 == 0b0000_1001"
        assert record["detail"] == (
            "expected 0b0000_1001, got 0b0000_1010 (mask 0b0000_1111); bits [1:0] differ"
        )
        decimal = _verify().bits(10, 9, radix="dec", name="R")
        assert decimal["detail"] == "expected 9, got 10; bits [1:0] differ"

    def test_negative_values_have_a_sign(self) -> None:
        record = _verify().bits(-5, 5, width=8, name="R")
        assert record["detail"] == "expected 0x05, got -0x05"
        assert _verify().bits(-5, -5, name="R")["detail"] == "-0x5 == -0x5"

    @pytest.mark.parametrize(
        "mask, error, message",
        [
            (-1, ValueError, r"bits\(\) mask must not be negative, got -1"),
            (True, TypeError, r"bits\(\) mask must be a non-negative int, got bool"),
            (1.0, TypeError, r"bits\(\) mask must be a non-negative int, got float"),
            ("0xFF", TypeError, r"bits\(\) mask must be a non-negative int, got str"),
        ],
    )
    def test_mask_usage_errors(self, mask: Any, error: type, message: str) -> None:
        run, verify = _recording()
        with pytest.raises(error, match=message):
            verify.bits(1, 1, mask=mask, name="R")
        assert run.records == []

    @pytest.mark.parametrize(
        "actual, expected, kind, label",
        [
            (1.0, 1, "float", "actual"),
            (True, 1, "bool", "actual"),
            ("0x1", 1, "str", "actual"),
            (None, 1, "NoneType", "actual"),
            (1, 1.0, "float", "expected"),
            (1, None, "NoneType", "expected"),
            (1, [1], "list", "expected"),
            (Decimal(1), 1, "Decimal", "actual"),
        ],
    )
    def test_operands_that_are_not_integers_fail_with_an_error(
        self, actual: Any, expected: Any, kind: str, label: str
    ) -> None:
        record = _verify().bits(actual, expected, width=8, name="R")
        assert record["passed"] is False
        assert record["error"].startswith(
            f"TypeError: bits compares integers, and {label} is a {kind}; "
        )
        assert record["detail"].endswith(f"({record['error']})")
        assert "bit_diff" not in record
        check = checks.bits(actual, expected, width=8, name="R")
        assert checks.evaluate(check) is False
        assert checks.evaluate_detailed(check)[0]["error"] == record["error"]
        json.dumps(record, allow_nan=False)

    def test_an_object_with_index_but_no_protocol_is_not_an_integer(self) -> None:
        class Index:
            def __index__(self) -> int:
                return 1

        record = _verify().bits(Index(), 1, name="R")
        assert record["passed"] is False and "is a Index" in record["error"]

    def test_an_int_enum_member_is_recorded_as_its_number(self) -> None:
        class Status(enum.IntFlag):
            READY = 1
            BUSY = 2

        record = _verify().bits(Status.READY | Status.BUSY, 1, width=8, name="R")
        assert record["actual"] == 3
        assert record["detail"] == "expected 0x01, got 0x03; bit 1 differs"

    def test_no_units(self) -> None:
        with pytest.raises(TypeError, match="unexpected keyword argument 'units'"):
            checks.bits(1, 1, name="R", units="V")  # type: ignore[call-arg]
        assert REGISTRY["bits"].units(checks.bits(1, 1, name="R")) is None
        assert REGISTRY["bits"].margin(checks.bits(1, 1, name="R")) is None

    def test_notes_and_names(self) -> None:
        record = _verify().bits(0xF0, 0xF3, name="CTRL", note=" after reset ",
                                expected_name="wdata")
        assert record["note"] == "after reset" and "auto_name" not in record
        assert record["description"] == "Verify 'CTRL' == wdata (0xF3)"
        assert record["detail"] == "expected wdata (0xF3), got 0xF0; bits [1:0] differ"
        summary = format_summary([record])
        assert "CTRL" in summary and "bits [1:0] differ (after reset)" in summary
        with pytest.raises(TypeError, match=r"bits\(\) note must be a str or None"):
            checks.bits(1, 1, name="R", note=3)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match=r"bits\(\) name must be a str"):
            checks.bits(1, 1, name=3)  # type: ignore[arg-type]

    def test_summary_and_error_message(self) -> None:
        record = _verify().bits(0xF0, 0xF3, mask=0xFF, width=16, name="CTRL")
        error = ChecksFailedError([record])
        assert "CTRL — expected 0x00F3, got 0x00F0 (mask 0x00FF); bits [1:0] differ" in str(error)

    @pytest.mark.parametrize(
        "junk",
        [
            {"mask": "ff"},
            {"mask": -1},
            {"radix": 5, "width": "wide", "fields": [1], "rendered": 3, "bit_diff": "x"},
            {"radix": "hexa"},
            {"fields": {"A": [1, 2, 3]}},
            {"rendered": {"actual": 5}},
            {"actual": "text"},
        ],
    )
    def test_a_hand_built_descriptor_never_raises(self, junk: Dict[str, Any]) -> None:
        built: Dict[str, Any] = {
            "check_type": "bits", "name": "R", "description": "d", "actual": 1, "expected": 1,
        }
        built.update(junk)
        record, _ = settle(built)
        assert isinstance(record["detail"], str)
        assert "<detail unavailable" not in record["detail"]
        json.dumps(record, allow_nan=False)
        assert isinstance(format_summary([record]), str)

    def test_a_hand_built_descriptor_is_judged(self) -> None:
        built = {"check_type": "bits", "name": "R", "description": "d", "actual": 0xF0,
                 "expected": 0xF3, "mask": 0xFF, "width": 16, "fields": {"EN": [0, 0]}}
        record, passed = settle(built)
        assert passed is False
        assert record["detail"] == (
            "expected 0x00F3, got 0x00F0 (mask 0x00FF); bits [1:0] differ: "
            "EN [0] is 0b0, expected 0b1"
        )
        bad_mask = dict(built, mask="ff")
        assert settle(bad_mask)[0]["error"].startswith("TypeError: the mask must be")


class TestFields:
    def test_normalised_in_the_given_order(self) -> None:
        check = checks.bits(1, 1, fields={"MODE": (1, 3), "EN": 0, "DIV": [8, 15]}, name="R")  # type: ignore[dict-item]
        assert check["fields"] == {"MODE": [3, 1], "EN": [0, 0], "DIV": [15, 8]}
        assert list(check["fields"]) == ["MODE", "EN", "DIV"]
        json.dumps(check)

    def test_names_are_stripped_and_plain(self) -> None:
        class Name(str, enum.Enum):
            EN = "EN"

        check = checks.bits(1, 1, fields={Name.EN: 0, " MODE ": (3, 1)}, name="R")
        assert check["fields"] == {"EN": [0, 0], "MODE": [3, 1]}
        assert {type(name) for name in check["fields"]} == {str}

    def test_the_mapping_given_is_not_kept(self) -> None:
        fields = {"EN": 0}
        check = checks.bits(1, 1, fields=fields, name="R")
        fields["EN"] = 5
        assert check["fields"] == {"EN": [0, 0]}

    def test_empty_means_none(self) -> None:
        assert "fields" not in checks.bits(1, 1, fields={}, name="R")

    @pytest.mark.parametrize(
        "fields, width, error, message",
        [
            ([("EN", 0)], None, TypeError, r"fields must be a mapping of names"),
            ("EN", None, TypeError, r"fields must be a mapping of names"),
            ({0: 0}, None, TypeError, r"field names must be str, got 0"),
            ({"": 0}, None, ValueError, r"field names must not be empty"),
            ({"  ": 0}, None, ValueError, r"field names must not be empty"),
            ({"A": 0, " A": 1}, None, ValueError, r"names the field 'A' twice"),
            ({"A": -1}, None, ValueError, r"field 'A' has a negative bit index: -1"),
            ({"A": (3, -1)}, None, ValueError, r"field 'A' has a negative bit index: -1"),
            ({"A": True}, None, TypeError, r"field 'A' must be a bit index or \(msb, lsb\)"),
            ({"A": (True, 0)}, None, TypeError, r"field 'A' must be a bit index or \(msb, lsb\)"),
            ({"A": 1.0}, None, TypeError, r"field 'A' must be a bit index or \(msb, lsb\)"),
            ({"A": "3:1"}, None, TypeError, r"field 'A' must be a bit index or \(msb, lsb\)"),
            ({"A": (3, 2, 1)}, None, TypeError, r"field 'A' must be a bit index or \(msb, lsb\)"),
            ({"A": (3,)}, None, TypeError, r"field 'A' must be a bit index or \(msb, lsb\)"),
            ({"A": 16}, 16, ValueError, r"field 'A' names bit 16, which is outside the width, 16"),
            ({"A": (16, 0)}, 16, ValueError, r"field 'A' names bit 16, which is outside the wid"),
            ({"A": 1024}, None, ValueError, r"field 'A' names bit 1024, which is outside 1024"),
        ],
    )
    def test_usage_errors(
        self, fields: Any, width: Optional[int], error: type, message: str
    ) -> None:
        run, verify = _recording()
        with pytest.raises(error, match=rf"bits\(\) {message}"):
            verify.bits(1, 1, fields=fields, width=width, name="R")
        assert run.records == []

    def test_the_highest_bit_of_the_width_is_allowed(self) -> None:
        assert checks.bits(1, 1, fields={"A": 15}, width=16, name="R")["fields"] == {"A": [15, 15]}

    def test_only_bits_takes_fields(self) -> None:
        with pytest.raises(TypeError, match="unexpected keyword argument 'fields'"):
            checks.equal(1, 1, name="R", fields={"A": 0})  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# The protocol
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_an_int_subclass(self) -> None:
        record = _verify().equal(Word(0xF0), 0xF3, name="CTRL")
        assert record["description"] == "Verify 'CTRL' == 0x00F3"
        assert record["detail"] == (
            "expected 0x00F3, got 0x00F0; bits [1:0] differ: EN [0] is 0b0, expected 0b1; "
            "MODE [3:1] is 0b000, expected 0b001"
        )
        assert record["actual"] == 240 and type(record["actual"]) is int
        assert record["radix"] == "hex" and record["width"] == 16
        assert record["fields"] == {"EN": [0, 0], "MODE": [3, 1]}
        assert "format_values" not in record and "format_error" not in record

    def test_an_object_with_a_value(self) -> None:
        check = checks.equal(Register(0xF0), 0xF3, name="CTRL")
        assert isinstance(check["actual"], Register)  # still compared as the object
        assert check["format_values"] == {"actual": 0xF0}
        record = _verify().equal(Register(0xF0), 0xF3, name="CTRL")
        assert record["actual"] == 0xF0 and "format_values" not in record
        assert record["rendered"] == {"actual": "0x00F0", "expected": "0x00F3"}
        assert record["detail"].startswith("expected 0x00F3, got 0x00F0; bits [1:0] differ: EN")
        assert record["bit_diff"]["fields"][1] == {
            "name": "MODE", "msb": 3, "lsb": 1, "actual": "0b000", "expected": "0b001"
        }
        assert _again(record) == record["detail"]

    def test_an_object_with_index(self) -> None:
        record = _verify().equal(Indexed(5), 6, name="R")
        assert record["actual"] == 5
        assert record["detail"] == "expected 0b0000_0110, got 0b0000_0101; bits [1:0] differ"

    def test_the_comparison_is_still_the_objects(self) -> None:
        class Never(Register):
            def __eq__(self, other: object) -> bool:
                return False

            __hash__ = None  # type: ignore[assignment]

        record = _verify().equal(Never(0xF3), 0xF3, name="R")
        assert record["passed"] is False
        assert record["detail"] == (
            "expected 0x00F3, got 0x00F3; the integers are equal, but == is False"
        )
        assert "bit_diff" not in record

        class Always(Register):
            def __eq__(self, other: object) -> bool:
                return True

            __hash__ = None  # type: ignore[assignment]

        assert _verify().equal(Always(0xF0), 0xF3, name="R")["passed"] is True
        assert checks.evaluate(checks.equal(Always(0xF0), 0xF3, name="R")) is True

    def test_no_type_names_beside_equal_texts(self) -> None:
        verify = _verify()
        assert verify.not_equal(Word(5), 5, name="R")["detail"] == "expected ≠ 0x0005, got 0x0005"
        assert verify.greater(Word(5), 5, name="R")["detail"] == "expected > 0x0005, got 0x0005"
        assert verify.less(Word(5), 5, name="R")["detail"] == "expected < 0x0005, got 0x0005"
        assert verify.between(Word(5), 6, 7, name="R")["detail"] == (
            "expected [0x0006, 0x0007], got 0x0005"
        )
        assert verify.equal(Word(5), 5, name="R")["detail"] == "0x0005 == 0x0005"

    @pytest.mark.parametrize(
        "arguments, radix, width, text",
        [
            ({}, "hex", 16, "0x00F3"),
            ({"radix": "bin"}, "bin", 16, "0b0000_0000_1111_0011"),
            ({"width": 8}, "hex", 8, "0xF3"),
            ({"radix": "oct", "width": 9}, "oct", 9, "0o363"),
            ({"radix": "dec"}, "dec", 16, "243"),
        ],
    )
    def test_explicit_arguments_win_key_by_key(
        self, arguments: Dict[str, Any], radix: str, width: int, text: str
    ) -> None:
        check = checks.equal(Word(0xF3), 0xF3, name="R", **arguments)
        assert (check["radix"], check["width"]) == (radix, width)
        assert check["rendered"]["expected"] == text
        assert check["fields"] == {"EN": [0, 0], "MODE": [3, 1]}  # still the value's

    def test_explicit_fields_win_in_bits(self) -> None:
        check = checks.bits(Word(1), 0, fields={"GO": 0}, name="R")
        assert check["fields"] == {"GO": [0, 0]} and check["width"] == 16
        assert checks.bits(Word(1), 0, name="R")["fields"] == {"EN": [0, 0], "MODE": [3, 1]}

    def test_on_the_expected_operand_only(self) -> None:
        record = _verify().equal(0xF0, Register(0xF3), name="R")
        assert record["expected"] == 0xF3 and record["width"] == 16
        assert record["description"] == "Verify 'R' == 0x00F3"
        assert record["detail"].startswith("expected 0x00F3, got 0x00F0; bits [1:0] differ")

    @pytest.mark.parametrize(
        "build",
        [
            lambda v: v.greater(0x10, Word(0x20), name="R"),
            lambda v: v.less_equal(0x30, Word(0x20), name="R"),
            lambda v: v.between(0x30, Word(0x10), 0x20, name="R"),
            lambda v: v.between(0x30, 0x10, Word(0x20), name="R"),
            lambda v: v.not_equal(0x20, Word(0x20), name="R"),
            lambda v: v.bits(0x30, Word(0x20), name="R"),
        ],
    )
    def test_on_a_compared_operand_of_every_check(self, build: Any) -> None:
        record = build(_verify())
        assert record["passed"] is False
        assert record["radix"] == "hex" and record["width"] == 16
        assert all(text.startswith("0x00") for text in record["rendered"].values())

    def test_the_first_that_answers_gives_the_format(self) -> None:
        check = checks.equal(Indexed(5), Word(5), name="R")
        assert (check["radix"], check["width"]) == ("bin", 8) and "fields" not in check
        check = checks.equal(Word(5), Indexed(5), name="R")
        assert (check["radix"], check["width"]) == ("hex", 16)
        assert check["format_values"] == {"expected": 5}

    def test_read_once_per_value_when_built(self) -> None:
        Register.calls = 0
        check = checks.equal(Register(1), Register(2), name="R")
        assert Register.calls == 2
        record, passed = settle(check)
        render_detail(record, passed)
        checks.evaluate(check)
        assert Register.calls == 2
        assert record["actual"] == 1 and record["expected"] == 2

    def test_looked_up_on_the_type(self) -> None:
        class Plain:
            def __eq__(self, other: object) -> bool:
                return True

            __hash__ = None  # type: ignore[assignment]

        value = Plain()
        value.__verify_format__ = lambda: {"radix": "hex"}  # type: ignore[attr-defined]
        assert set(checks.equal(value, 1, name="R")).isdisjoint(FORMAT_KEYS)
        # A class is not a value of itself.
        assert set(checks.equal(Word, 1, name="R")).isdisjoint(FORMAT_KEYS)

    def test_none_means_no_format(self) -> None:
        check = checks.equal(_answering(None), 5, name="R")
        assert set(check).isdisjoint(FORMAT_KEYS)
        assert check["description"] == "Verify 'R' == 5"

    def test_an_empty_answer_means_no_format(self) -> None:
        check = checks.equal(_answering({}), 5, name="R")
        assert "radix" not in check and "rendered" not in check and "format_error" not in check

    def test_unknown_keys_are_ignored(self) -> None:
        check = checks.equal(_answering({"radix": "bin", "endian": "big"}), 5, name="R")
        assert check["radix"] == "bin" and "format_error" not in check

    def test_a_value_alone_is_recorded_without_a_format(self) -> None:
        class Counter:
            def __verify_format__(self) -> Mapping[str, Any]:
                return {"value": 7}

            def __eq__(self, other: object) -> bool:
                return other == 7

            __hash__ = None  # type: ignore[assignment]

            def __repr__(self) -> str:
                return "Counter()"

        record = _verify().equal(Counter(), 8, name="R")
        assert record["actual"] == 7 and "radix" not in record and "rendered" not in record
        assert record["detail"] == "expected 8, got Counter()"

    def test_fields_alone_mean_hex(self) -> None:
        check = checks.equal(_answering({"fields": {"GO": 0}}), 4, name="R")
        assert check["radix"] == "hex" and "width" not in check
        assert check["bit_diff"]["fields"] == [
            {"name": "GO", "msb": 0, "lsb": 0, "actual": "0b1", "expected": "0b0"}
        ]

    def test_a_raising_protocol_never_raises_or_fails_the_check(self) -> None:
        record = _verify().equal(_answering(RuntimeError("bus\nerror")), 5, name="R")
        assert record["passed"] is True and "error" not in record
        assert record["format_error"] == "Answering.__verify_format__: RuntimeError: bus\\nerror"
        assert record["detail"] == "5 == 5"
        assert "radix" not in record and "rendered" not in record
        failed = _verify().equal(_answering(ValueError("no")), 6, name="R")
        assert failed["passed"] is False and "error" not in failed
        assert failed["detail"] == "expected 6, got 5"

    @pytest.mark.parametrize(
        "junk, message",
        [
            (5, "TypeError: must return a mapping or None, got int"),
            ("hex", "TypeError: must return a mapping or None, got str"),
            ([("radix", "hex")], "TypeError: must return a mapping or None, got list"),
            ({"radix": "hexa"}, "ValueError: __verify_format__() radix must be"),
            ({"radix": 16}, "TypeError: __verify_format__() radix must be"),
            ({"width": "16"}, "TypeError: __verify_format__() width must be"),
            ({"width": 0}, "ValueError: __verify_format__() width must be"),
            ({"fields": {"A": -1}}, "ValueError: __verify_format__() field 'A' has a negative"),
            ({"fields": {"A": 16}, "width": 16}, "ValueError: __verify_format__() field 'A' names"),
            ({"fields": ["A"]}, "TypeError: __verify_format__() fields must be a mapping"),
            ({"value": "5"}, "TypeError: __verify_format__() value must be an int, got str"),
            ({"value": True}, "TypeError: __verify_format__() value must be an int, got bool"),
            ({"value": 1.5, "radix": "hex"}, "TypeError: __verify_format__() value must be an int"),
        ],
    )
    def test_junk_falls_back_and_says_why(self, junk: Any, message: str) -> None:
        record = _verify().equal(_answering(junk), 5, name="R")
        assert record["passed"] is True
        assert record["format_error"].startswith(f"Answering.__verify_format__: {message}")
        assert record["detail"] == "5 == 5"
        assert set(record).isdisjoint(FORMAT_KEYS - {"format_error"})
        json.dumps(record, allow_nan=False)

    def test_explicit_arguments_still_apply_beside_a_broken_protocol(self) -> None:
        record = _verify().equal(_answering(RuntimeError("x")), 6, name="R", radix="bin", width=4)
        assert record["format_error"].startswith("Answering.__verify_format__: RuntimeError")
        assert record["detail"] == "expected 0b0110, got 0b0101; bits [1:0] differ"

    def test_a_broken_protocol_on_one_operand_silences_the_other(self) -> None:
        check = checks.equal(Word(5), _answering(RuntimeError("x")), name="R")
        assert "format_error" in check and "radix" not in check

    def test_a_raising_index_is_a_format_error(self) -> None:
        class Broken:
            def __index__(self) -> int:
                raise OverflowError("gone")

            def __verify_format__(self) -> Mapping[str, Any]:
                return {"radix": "hex"}

            def __repr__(self) -> str:
                return "Broken()"

        record = _verify().equal(Broken(), 5, name="R")
        assert record["format_error"] == "Broken.__verify_format__: OverflowError: gone"
        assert record["detail"] == "expected 5, got Broken()"

    def test_bits_with_a_broken_protocol_fails_as_a_non_integer(self) -> None:
        class Broken:
            def __verify_format__(self) -> Mapping[str, Any]:
                raise RuntimeError("bus")

        record = _verify().bits(Broken(), 5, name="R")
        assert record["passed"] is False
        assert record["error"].startswith("TypeError: bits compares integers, and actual is a Br")
        assert record["format_error"] == "Broken.__verify_format__: RuntimeError: bus"

    def test_an_exception_that_is_not_an_error_goes_on(self) -> None:
        with pytest.raises(KeyboardInterrupt):
            checks.equal(_answering(KeyboardInterrupt()), 5, name="R")

    def test_bits_reads_the_value(self) -> None:
        record = _verify().bits(Register(0xF0), Register(0xF3), mask=0xFF, name="CTRL")
        assert record["actual"] == 0xF0 and record["expected"] == 0xF3
        assert record["detail"] == (
            "expected 0x00F3, got 0x00F0 (mask 0x00FF); bits [1:0] differ: "
            "EN [0] is 0b0, expected 0b1; MODE [3:1] is 0b000, expected 0b001"
        )
        check = checks.bits(Register(0xF0), 0xF0, name="CTRL")
        assert check["actual"] == 0xF0 and "format_values" not in check
        assert checks.evaluate(check)

    def test_a_narrower_width_leaves_out_the_formats_fields_outside_it(self) -> None:
        answer = {"value": 0xF0, "width": 16, "fields": {"EN": 0, "MODE": (1, 3), "HI": (15, 8)}}
        check = checks.bits(_answering(answer), 0xF3, name="R", width=4)
        assert check["width"] == 4
        assert check["fields"] == {"EN": [0, 0], "MODE": [3, 1]}
        assert all(msb < check["width"] for msb, _ in check["fields"].values())
        assert [field["name"] for field in check["bit_diff"]["fields"]] == ["EN", "MODE"]

    def test_a_narrower_width_may_leave_no_field(self) -> None:
        check = checks.equal(_answering({"fields": {"HIGH": 31}}), 5, name="R", width=8)
        assert "fields" not in check and check["width"] == 8

    def test_fields_beyond_the_formats_width_are_a_usage_error(self) -> None:
        register = _answering({"value": 0xF0, "width": 4})
        with pytest.raises(ValueError, match=r"field 'X' names bit 12.*the width, 4 bits"):
            checks.bits(register, 0xF3, name="R", fields={"X": 12})
        assert checks.bits(register, 0xF3, name="R", fields={"X": 3})["fields"] == {"X": [3, 3]}

    def test_the_protocol_and_the_typed_dict_are_exported(self) -> None:
        assert {"VerifyFormat", "ValueFormat"} <= set(pytest_verifier.__all__)
        assert VerifyFormat.__verify_format__.__doc__
        assert ValueFormat.__total__ is False
        assert set(ValueFormat.__annotations__) == {"radix", "width", "fields", "value"}
        word: VerifyFormat = Word(1)
        assert word.__verify_format__()["width"] == 16

    def test_other_checks_do_not_read_it(self) -> None:
        Register.calls = 0
        checks.approx(1.0, 1.0, abs_tol=1, name="R")
        checks.is_true(Register(1), name="R")
        checks.is_not_none(Register(1), name="R")
        checks.contains([Register(1)], 1, name="R")
        assert Register.calls == 0


# ---------------------------------------------------------------------------
# Records, composites, sampling, require
# ---------------------------------------------------------------------------


class TestRecords:
    def test_keys_of_an_equal_record(self) -> None:
        record = _verify().equal(0xF0, 0xF3, name="R", radix="hex", width=16)
        assert {key: record[key] for key in sorted(FORMAT_KEYS & set(record))} == {
            "bit_diff": {"bits": [[1, 0]], "text": "bits [1:0] differ"},
            "radix": "hex",
            "rendered": {"actual": "0x00F0", "expected": "0x00F3"},
            "width": 16,
        }
        assert json.loads(json.dumps(record, allow_nan=False)) == record

    def test_rendered_keys_per_check(self) -> None:
        verify = _verify()
        assert set(verify.greater(1, 2, name="R", width=8)["rendered"]) == {"actual", "threshold"}
        assert set(verify.between(1, 2, 3, name="R", width=8)["rendered"]) == {
            "actual", "low", "high"
        }
        assert set(verify.bits(1, 2, mask=3, name="R")["rendered"]) == {
            "actual", "expected", "mask"
        }

    def test_bit_diff_only_on_equal_and_bits(self) -> None:
        verify = _verify()
        assert "bit_diff" not in verify.not_equal(1, 2, name="R", width=8)
        assert "bit_diff" not in verify.greater(1, 2, name="R", width=8)
        assert "bit_diff" not in verify.equal(2, 2, name="R", width=8)
        assert "bit_diff" in verify.equal(1, 2, name="R", width=8)

    def test_the_record_does_not_change_with_the_register(self) -> None:
        register = Register(0xF0)
        record = _verify().equal(register, 0xF3, name="R")
        register.value = 0xF3
        assert record["actual"] == 0xF0 and record["passed"] is False

    def test_a_hand_built_equal_with_a_radix(self) -> None:
        built = {"check_type": "equal", "name": "R", "description": "d", "actual": 240,
                 "expected": 243, "radix": "hex", "width": 16}
        record, passed = settle(built)
        assert passed is False
        assert record["detail"] == "expected 0x00F3, got 0x00F0; bits [1:0] differ"

    @pytest.mark.parametrize("method", sorted(set(FORMATTED) - {"bits"}))
    @pytest.mark.parametrize(
        "junk",
        [
            {"radix": 5},
            {"radix": "hexa", "width": 16.5},
            {"width": -1},
            {"fields": "EN"},
            {"radix": "hex", "rendered": "0x1", "bit_diff": 7, "format_values": 3},
            {"radix": "hex", "rendered": {"actual": 5}, "format_values": {"actual": "x"}},
            {"radix": "bin", "bit_diff": {"text": 5}},
        ],
    )
    def test_hand_built_junk_never_breaks_a_check(self, method: str, junk: Dict[str, Any]) -> None:
        built = dict(FORMATTED[method](checks))
        built.update(junk)
        record, passed = settle(built)
        assert passed is True and checks.evaluate(built) is True
        assert "<detail unavailable" not in record["detail"]
        json.dumps(record, allow_nan=False)
        assert isinstance(format_summary([record]), str)

    def test_the_stored_text_stands_for_an_operand_that_is_not_an_integer_any_more(self) -> None:
        record = _verify().equal(0xF0, 0xF3, name="R", width=16)
        edited = dict(record, actual="<Reg.CTRL: 240>")  # as a record keeps an int enum member
        assert render_detail(edited, False) == "expected 0x00F3, got 0x00F0; bits [1:0] differ"

    def test_the_integers_win_over_a_stored_text(self) -> None:
        record = _verify().equal(0xF0, 0xF3, name="R", width=16)
        edited = dict(record, rendered={"actual": "0xAAAA", "expected": "0xBBBB"})
        assert render_detail(edited, False) == "expected 0x00F3, got 0x00F0; bits [1:0] differ"


class TestComposites:
    def _children(self, record: Mapping[str, Any]) -> List[Any]:
        return list(record["child_checks"])

    def test_all_satisfy(self) -> None:
        verify = _verify()
        record = verify.all_satisfy(
            [0xF3, 0xF0],
            lambda word: verify.bits(word, 0xF3, mask=0xFF, fields=FIELDS, width=16, name="CTRL"),
            name="All words",
        )
        first, second = self._children(record)
        assert first["passed"] is True and first["detail"] == "0x00F3 & 0x00FF == 0x00F3"
        assert second["passed"] is False and second["rendered"]["actual"] == "0x00F0"
        assert second["bit_diff"]["text"] == "bits [1:0] differ"
        assert record["detail"] == (
            "expected all 2 to pass, got 1 failed: [1] expected 0x00F3, got 0x00F0 "
            "(mask 0x00FF); bits [1:0] differ: EN [0] is 0b0, expected 0b1; "
            "MODE [3:1] is 0b000, expected 0b001"
        )
        assert _again(record) == record["detail"]
        assert [_again(child) for child in (first, second)] == [first["detail"], second["detail"]]

    def test_conditional(self) -> None:
        verify = _verify()
        record = verify.conditional(
            "wide",
            cases={
                "wide": lambda: verify.bits(0xF0, 0xF3, width=32, name="W"),
                "narrow": verify.bits(1, 1, width=8, name="N"),
            },
            name="Mode",
        )
        assert record["passed"] is False
        assert record["cases"]["wide"]["detail"] == (
            "expected 0x0000_00F3, got 0x0000_00F0; bits [1:0] differ"
        )
        assert "passed" not in record["cases"]["narrow"]
        assert record["cases"]["narrow"]["rendered"] == {"actual": "0x01", "expected": "0x01"}
        assert "bits [1:0] differ" in record["detail"]
        assert _again(record) == record["detail"]

    def test_guard(self) -> None:
        verify = _verify()
        record = verify.guard(
            [(True, "on", verify.equal(Register(0xF0), 0xF3, name="CTRL"))],
            default=verify.bits(0, 0, name="off"),
            name="Power",
        )
        chosen = record["branches"][0]["check"]
        assert chosen["actual"] == 0xF0 and "format_values" not in chosen
        assert chosen["detail"].startswith("expected 0x00F3, got 0x00F0; bits [1:0] differ")
        assert "expected 0x00F3, got 0x00F0" in record["detail"]
        assert "format_values" not in json.dumps(record)
        assert _again(record) == record["detail"]

    def test_an_unselected_child_keeps_numbers_too(self) -> None:
        verify = _verify()
        record = verify.guard(
            [(False, "off", verify.equal(Register(1), 2, name="CTRL"))],
            default=verify.is_true(True, name="idle"),
            name="Power",
        )
        skipped = record["branches"][0]["check"]
        assert skipped["actual"] == 1 and "passed" not in skipped

    def test_built_then_evaluated(self) -> None:
        built = checks.all_satisfy(
            [1, 2], lambda word: checks.bits(word, 1, width=8, name="W"), name="All"
        )
        assert checks.evaluate(built) is False
        record, passed = settle(built)
        assert passed is False and "expected 0x01, got 0x02" in record["detail"]


class TestSampling:
    def test_eventually_with_a_register_value(self) -> None:
        verify = _verify()
        readings = iter([Register(0x00), Register(0xF0), Register(0xF3)])
        record = verify.eventually(
            lambda: verify.bits(next(readings), 0xF3, mask=0xFF, name="CTRL"),
            timeout=5,
            interval=0.001,
            name="Ready",
        )
        assert record["passed"] is True and record["tries"] == 3
        kept = record["child_checks"][-1]
        assert kept["detail"] == "0x00F3 & 0x00FF == 0x00F3"
        assert [entry[1] for entry in record["trace"]] == [0x00, 0xF0, 0xF3]
        json.dumps(record, allow_nan=False)

    def test_a_failed_eventually_shows_the_last_bits(self) -> None:
        verify = _verify()
        record = verify.eventually(
            lambda: verify.equal(Register(0xF0), 0xF3, name="CTRL"), timeout=0, name="Ready"
        )
        assert record["passed"] is False
        assert "expected 0x00F3, got 0x00F0; bits [1:0] differ" in record["detail"]
        assert record["value_changed"] is False
        assert record["trace"][0][1] == 0xF0

    def test_stable(self) -> None:
        verify = _verify()
        record = verify.stable(
            lambda: verify.less(Word(3), 4, name="LEVEL"), duration=0, name="Low"
        )
        assert record["passed"] is True
        assert record["child_checks"][0]["detail"] == "0x0003 < 0x0004"


class TestRequire:
    def test_a_required_bits_check_stops(self) -> None:
        run, verify = _recording()
        with pytest.raises(ChecksFailedError) as stopped:
            verify.require.bits(0xF0, 0xF3, mask=0xFF, width=16, name="CTRL")
        assert "expected 0x00F3, got 0x00F0 (mask 0x00FF); bits [1:0] differ" in str(stopped.value)
        assert [record["name"] for record in run.records] == ["CTRL"]

    def test_a_required_equal_with_a_radix_stops(self) -> None:
        _, verify = _recording()
        with pytest.raises(ChecksFailedError, match=r"expected 0xF3, got 0xF0; bits \[1:0\] diff"):
            verify.require.equal(0xF0, 0xF3, name="CTRL", radix="hex")
        assert verify.require.bits(1, 1, name="ok")["passed"] is True

    def test_require_of_a_built_check(self) -> None:
        _, verify = _recording()
        with pytest.raises(ChecksFailedError, match="bit 0 differs"):
            verify.require(checks.bits(1, 0, name="CTRL"))

    def test_checks_require_only_builds(self) -> None:
        with pytest.raises(RuntimeError, match="checks.require cannot stop a test"):
            checks.require.bits(1, 1, name="R")

    def test_fail_fast(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            """
            def test_register(verify):
                verify.bits(0xF0, 0xF3, mask=0xFF, width=16, name="CTRL")
                verify.fail("not reached")
            """
        )
        result = pytester.runpytest("--verify-fail-fast")
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(
            ["*1 of 1 checks failed, stopped at [[]0]: CTRL*bits [[]1:0] differ*"]
        )
        assert "not reached" not in result.stdout.str()


# ---------------------------------------------------------------------------
# checks.* and evaluate()
# ---------------------------------------------------------------------------


class TestBuilder:
    @pytest.mark.parametrize(
        "build, passed",
        [
            (lambda v: v.bits(0xF3, 0xF3, name="R"), True),
            (lambda v: v.bits(0xF0, 0xF3, mask=0xF0, fields=FIELDS, width=8, name="R"), True),
            (lambda v: v.bits(0xF0, 0xF3, fields=FIELDS, width=8, name="R"), False),
            (lambda v: v.bits(2**64, 2**64 + 1, width=65, name="R"), False),
            (lambda v: v.bits(1.5, 1, name="R"), False),
            (lambda v: v.equal(Register(0xF0), 0xF3, name="R"), False),
            (lambda v: v.equal(Word(0xF3), 0xF3, name="R"), True),
            (lambda v: v.between(Word(5), 1, 9, name="R"), True),
            (lambda v: v.greater(0x10, 0x20, radix="bin", name="R"), False),
        ],
    )
    def test_parity_with_the_fixture(self, build: Any, passed: bool) -> None:
        built = build(checks)
        assert "passed" not in built and "detail" not in built
        record = build(_verify())
        assert record["passed"] is passed
        assert checks.evaluate(built) is passed
        detailed = checks.evaluate_detailed(built)[0]
        assert detailed["passed"] is passed and detailed["details"] is built
        assert ("error" in detailed) is ("error" in record)
        copy = json.loads(json.dumps(record, allow_nan=False))
        assert copy == record
        assert checks.evaluate(copy) is passed
        assert checks.evaluate_detailed(copy)[0]["passed"] is passed
        settled, verdict = settle(built)
        assert verdict is passed
        assert settled == {key: value for key, value in record.items()
                           if key not in ("phase", "location", "called_from")}

    def test_a_built_bits_check_is_json_safe(self) -> None:
        built = checks.bits(Register(0xF0), 0xF3, mask=0xFF, fields=FIELDS, name="R")
        assert json.loads(json.dumps(built)) == built

    def test_record_of_a_built_check(self) -> None:
        run, verify = _recording()
        record = verify.record(checks.bits(0xF0, 0xF3, width=16, name="CTRL"))
        assert record["passed"] is False and run.records == [record]
        assert record["detail"] == "expected 0x00F3, got 0x00F0; bits [1:0] differ"


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


class Device:
    ctrl = 0xF0
    regs = {"status": 0x01}


class TestNames:
    def test_the_default_name_is_the_source_of_actual(self) -> None:
        dev = Device()
        check = checks.bits(dev.ctrl, 0xF3, mask=0xFF)
        assert check["name"] == "dev.ctrl" and check["auto_name"] is True
        assert check["description"] == "Verify 'dev.ctrl' & 0xFF == 0xF3"
        nested = checks.bits(dev.regs["status"], 1)
        assert nested["name"] == 'dev.regs["status"]'

    def test_the_default_name_with_a_radix(self) -> None:
        dev = Device()
        check = checks.equal(dev.ctrl, 0xF0, radix="hex", width=16)
        assert check["name"] == "dev.ctrl" and check["auto_name"] is True
        between = checks.between(
            dev.ctrl,
            0x00,
            0xFF,
            radix="hex",
        )
        assert between["name"] == "dev.ctrl"

    def test_the_default_name_through_the_fixture(self) -> None:
        dev = Device()
        record = _verify().bits(dev.ctrl, 0xF3)
        assert record["name"] == "dev.ctrl"
        assert record["location"].endswith("test_registers.py:%d" % (record_line(record)))

    def test_operand_names(self) -> None:
        channel, wdata = 3, 0xF3
        record = _verify().bits(
            0xF0, wdata, mask=0xFF, actual_name=f"dev.reg{channel}.F", expected_name="wdata"
        )
        assert record["name"] == "dev.reg3.F" and record["auto_name"] is True
        assert record["actual_name"] == "dev.reg3.F"
        assert record["actual_name_symbolic"] == "dev.reg<channel>.F"
        assert record["expected_name"] == "wdata"
        assert "expected_name_symbolic" not in record
        assert record["description"] == "Verify 'dev.reg3.F' & 0xFF == wdata (0xF3)"
        assert record["detail"] == (
            "expected wdata (0xF3), got 0xF0 (mask 0xFF); bits [1:0] differ"
        )

    def test_symbolic_expected_name(self) -> None:
        index = 2
        check = checks.bits(1, 1, name="R", expected_name=f"wdata[{index}]")
        assert check["expected_name"] == "wdata[2]"
        assert check["expected_name_symbolic"] == "wdata[<index>]"

    def test_operand_names_with_a_radix(self) -> None:
        channel = 3
        record = _verify().equal(
            0xF0, 0xF3, width=16, actual_name=f"reg{channel}", expected_name="wdata"
        )
        assert record["actual_name_symbolic"] == "reg<channel>"
        assert record["detail"] == "expected wdata (0x00F3), got 0x00F0; bits [1:0] differ"
        ordered = _verify().less(0xF3, 0xF0, radix="hex", actual_name="level",
                                 expected_name="depth")
        assert ordered["detail"] == "expected < depth (0xF0), got 0xF3"

    def test_a_name_wins_over_the_actual_name(self) -> None:
        check = checks.bits(1, 1, name="Control word", actual_name="dev.ctrl")
        assert check["name"] == "Control word" and "auto_name" not in check
        assert check["actual_name"] == "dev.ctrl"

    def test_between_and_bits_names_are_checked(self) -> None:
        with pytest.raises(TypeError, match=r"bits\(\) actual_name must be"):
            checks.bits(1, 1, actual_name=3)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="unexpected keyword argument 'expected_name'"):
            checks.between(1, 0, 2, radix="hex", expected_name="x")  # type: ignore[call-arg]


def record_line(record: Mapping[str, Any]) -> int:
    return int(str(record["location"]).rsplit(":", 1)[1])


# ---------------------------------------------------------------------------
# Limits tables
# ---------------------------------------------------------------------------


TABLE: Dict[str, LimitRow] = {
    "CTRL": {"check": "bits", "expected": 0xABF3, "mask": 0x00FF, "fields": FIELDS, "width": 16},
    "ID": {"expected": 0x1234, "radix": "hex", "width": 16},
    "LEVEL": {"low": 1, "high": 3, "radix": "bin", "width": 4},
    "COUNT": {"high": 0x10, "width": 8},
}


class TestLimits:
    def test_rows(self) -> None:
        made = _verify().limits({"CTRL": 0x12F0, "ID": 0x1234, "LEVEL": 5, "COUNT": 0x20}, TABLE)
        assert [check["check_type"] for check in made.values()] == [
            "bits", "equal", "between", "less_equal"
        ]
        assert made["CTRL"]["detail"] == (
            "expected 0x00F3, got 0x12F0 (mask 0x00FF); bits [1:0] differ: "
            "EN [0] is 0b0, expected 0b1; MODE [3:1] is 0b000, expected 0b001"
        )
        assert made["ID"]["detail"] == "0x1234 == 0x1234"
        assert made["LEVEL"]["detail"] == "expected [0b0001, 0b0011], got 0b0101"
        assert made["COUNT"]["detail"] == "expected <= 0x10, got 0x20"
        assert made["CTRL"]["fields"] == {"EN": [0, 0], "MODE": [3, 1]}

    def test_the_table_is_not_changed(self) -> None:
        before = json.dumps(TABLE)
        _verify().limits({"CTRL": 1}, TABLE, on_missing="ignore")
        assert json.dumps(TABLE) == before

    def test_a_register_object_as_the_measurement(self) -> None:
        made = _verify().limits({"CTRL": Register(0xF0)}, {"CTRL": {"check": "bits",
                                                                    "expected": 0xF3}})
        assert made["CTRL"]["actual"] == 0xF0 and made["CTRL"]["width"] == 16

    def test_not_measured(self) -> None:
        made = _verify().limits({"ID": 0x1234}, {"ID": TABLE["ID"], "CTRL": TABLE["CTRL"]})
        missing = made["CTRL"]
        assert missing["passed"] is False and missing["error"].startswith("not measured")
        assert missing["detail"].startswith("expected 0x00F3, got None (mask 0x00FF) (not measur")

    def test_bits_is_never_inferred(self) -> None:
        made = _verify().limits({"X": 1}, {"X": {"expected": 1, "width": 8}})
        assert made["X"]["check_type"] == "equal"

    @pytest.mark.parametrize(
        "row, error, message",
        [
            ({"check": "bits"}, TypeError, r"bits\(\) needs expected"),
            ({"check": "bits", "expected": 1.5}, TypeError, r"expected must be an int \(bits"),
            ({"check": "bits", "expected": True}, TypeError, r"expected must be an int \(bits"),
            ({"check": "bits", "expected": "0x1"}, TypeError, r"expected must be an int \(bits"),
            ({"check": "bits", "expected": 1, "mask": -1}, ValueError, r"bits\(\) mask must not"),
            ({"check": "bits", "expected": 1, "fields": {"A": -1}}, ValueError, r"negative bit"),
            ({"check": "bits", "expected": 1, "units": "V"}, TypeError,
             r"bits\(\) takes expected, mask, fields, width, radix; not: units"),
            ({"expected": 1, "radix": "hexa"}, ValueError, r"equal\(\) radix must be"),
            ({"expected": 1, "width": 0}, ValueError, r"equal\(\) width must be"),
            ({"low": 1, "radix": 16}, TypeError, r"greater_equal\(\) radix must be"),
            ({"expected": 1, "mask": 1}, TypeError, r"equal\(\) takes .*; not: mask"),
            ({"expected": 1.0, "abs_tol": 1, "radix": "hex"}, TypeError,
             r"approx\(\) takes .*; not: radix"),
            ({"check": "length", "expected": 1, "width": 8}, TypeError, r"not: width"),
        ],
    )
    def test_wrong_rows_raise_and_record_nothing(
        self, row: Dict[str, Any], error: type, message: str
    ) -> None:
        run, verify = _recording()
        with pytest.raises(error, match=rf"limits\(\) row 'X': .*{message}"):
            verify.limits({"X": 1}, {"X": row})
        assert run.records == []

    def test_csv(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "limits.csv").write_text(
            "name,check,expected,mask,fields,width,radix,low,high\n"
            "CTRL,bits,0xABF3,0x00FF,EN=0 MODE=3:1,16,,,\n"
            "ID,,0x1234,,,16,hex,,\n"
            "LEVEL,,,,,4,bin,1,3\n"
            "COUNT,,,,,8,,,0x10\n",
            encoding="utf-8",
        )
        table = load_limits("limits.csv")
        assert table["CTRL"] == {
            "check": "bits", "expected": 0xABF3, "mask": 0x00FF,
            "fields": {"EN": 0, "MODE": (3, 1)}, "width": 16, "source": "limits.csv:2",
        }
        assert table["ID"]["radix"] == "hex" and table["ID"]["width"] == 16
        made = _verify().limits({"CTRL": 0x12F0, "ID": 0x1234, "LEVEL": 5, "COUNT": 0x20}, table)
        python = _verify().limits({"CTRL": 0x12F0, "ID": 0x1234, "LEVEL": 5, "COUNT": 0x20}, TABLE)
        assert {name: check["detail"] for name, check in made.items()} == {
            name: check["detail"] for name, check in python.items()
        }
        assert made["CTRL"]["limit_source"] == "limits.csv:2"
        assert made["CTRL"]["bit_diff"] == python["CTRL"]["bit_diff"]

    def test_csv_with_semicolons(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "limits.csv").write_text(
            "name;check;expected;mask;fields;radix\nCTRL;bits;243;255;EN=0  MODE=1:3;bin\n",
            encoding="utf-8",
        )
        made = _verify().limits({"CTRL": 0xF3}, load_limits("limits.csv"))
        assert made["CTRL"]["fields"] == {"EN": [0, 0], "MODE": [3, 1]}
        assert made["CTRL"]["detail"] == "0b1111_0011 & 0b1111_1111 == 0b1111_0011"

    @pytest.mark.parametrize(
        "header, line, message",
        [
            ("name,check,expected,width", "X,bits,1,wide",
             r"limits.csv:2, column 'width': must be a whole number, got 'wide'"),
            ("name,check,expected,width", "X,bits,1,8.5",
             r"column 'width': must be a whole number, got '8.5'"),
            ("name,check,expected,mask", "X,bits,1,ff", r"column 'mask': must be a whole number"),
            ("name,check,expected,mask", "X,bits,1,-1", r"bits\(\) mask must not be negative"),
            ("name,check,expected,fields", "X,bits,1,EN",
             r"column 'fields': 'EN' is not a field; write name=bit or name=msb:lsb"),
            ("name,check,expected,fields", "X,bits,1,EN=0 EN=1",
             r"column 'fields': the field 'EN' is there twice"),
            ("name,check,expected,fields", "X,bits,1,EN=a", r"'EN=a' is not a field"),
            ("name,check,expected", "X,bits,1.5", r"expected must be an int \(bits"),
            ("name,check,expected", "X,bits,abc", r"column 'expected': must be a number"),
            ("name,expected,radix", "X,1,hexa", r"equal\(\) radix must be"),
            ("name,expected,width", "X,1,0", r"equal\(\) width must be"),
            ("name,check,expected,width,fields", "X,bits,1,4,EN=4",
             r"field 'EN' names bit 4, which is outside the width, 4 bits"),
        ],
    )
    def test_csv_errors_name_the_line(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, header: str, line: str, message: str
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "limits.csv").write_text(f"{header}\n{line}\n", encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_limits("limits.csv")

    def test_limit_row_hints(self) -> None:
        hints = typing.get_type_hints(LimitRow)
        assert hints["radix"] is str and hints["width"] is int and hints["mask"] is int
        assert typing.get_origin(hints["fields"]) is not None


# ---------------------------------------------------------------------------
# Session outputs
# ---------------------------------------------------------------------------


_TESTS = """
    import pytest

    @pytest.mark.parametrize("word", [0xF3, 0xF0])
    def test_ctrl(verify, word):
        verify.bits(word, 0xF3, mask=0x00FF, fields={"EN": 0, "MODE": (3, 1)}, width=16,
                    name="CTRL")
        verify.equal(word, word, name="ID", radix="hex", width=16)

    def test_wide(verify):
        verify.equal(0xFFFF_FFFF_FFFF_FFFF, 0xFFFF_FFFF_FFFF_FFFE, name="WIDE", width=64)
        verify.less(3, 4, name="Plain")
"""

_CTRL_DETAIL = (
    "expected 0x00F3, got 0x00F0 (mask 0x00FF); bits [1:0] differ: EN [0] is 0b0, "
    "expected 0b1; MODE [3:1] is 0b000, expected 0b001"
)


class TestSummary:
    def _lines(self, *records: Mapping[str, Any]) -> List[str]:
        summary = SessionSummary("stats")
        summary.add("t.py::test", records)
        return summary.lines()

    def test_the_range_is_in_the_groups_format(self) -> None:
        verify = _verify()
        lines = self._lines(
            verify.bits(0xF0, 0xF3, width=16, name="CTRL"),
            verify.bits(0xF3, 0xF3, width=16, name="CTRL"),
        )
        assert lines == ["  ✗ CTRL: 1 of 2 failed (first: t.py::test); 0x00F0 to 0x00F3"]

    def test_one_value(self) -> None:
        verify = _verify()
        lines = self._lines(verify.equal(0xF3, 0xF3, name="ID", radix="bin", units="V"))
        assert lines == ["  ✓ ID: 1 passed; 0b1111_0011V"]

    def test_with_a_margin(self) -> None:
        verify = _verify()
        lines = self._lines(
            verify.less(0x10, 0x20, name="LEVEL", width=8),
            verify.less(0x18, 0x20, name="LEVEL", width=8),
        )
        assert lines == ["  ✓ LEVEL: 2 passed; 0x10 to 0x18, margin 0x8"]

    def test_mixed_formats_leave_the_range_out(self) -> None:
        verify = _verify()
        mixed_width = self._lines(
            verify.equal(1, 1, name="R", width=8), verify.equal(2, 2, name="R", width=16)
        )
        assert mixed_width == ["  ✓ R: 2 passed"]
        with_plain = self._lines(
            verify.equal(1, 1, name="R", width=8), verify.equal(2, 2, name="R")
        )
        assert with_plain == ["  ✓ R: 2 passed"]
        mixed_radix = self._lines(
            verify.less(1, 5, name="R", radix="hex"), verify.less(2, 5, name="R", radix="bin")
        )
        assert mixed_radix == ["  ✓ R: 2 passed; margin 3"]

    def test_plain_groups_are_unchanged(self) -> None:
        verify = _verify()
        lines = self._lines(
            verify.less(240, 250, name="N", units="V"), verify.less(243, 250, name="N", units="V")
        )
        assert lines == ["  ✓ N: 2 passed; 240V to 243V, margin 7V"]

    def test_a_register_object_is_measured_by_its_number(self) -> None:
        verify = _verify()
        lines = self._lines(verify.equal(Register(0xF0), 0xF0, name="R"))
        assert lines == ["  ✓ R: 1 passed; 0x00F0"]

    def test_nested_checks(self) -> None:
        verify = _verify()
        record = verify.all_satisfy(
            [1, 2], lambda word: verify.bits(word, word, width=8, name="W"), name="All"
        )
        assert self._lines(record) == ["  ✓ All: 1 passed", "  ✓ W: 2 passed; 0x01 to 0x02"]

    def test_a_64_bit_range_is_exact(self) -> None:
        verify = _verify()
        lines = self._lines(
            verify.bits(2**64 - 1, 2**64 - 1, width=64, name="W"),
            verify.bits(2**64 - 2, 2**64 - 2, width=64, name="W"),
        )
        assert lines == [
            "  ✓ W: 2 passed; 0xFFFF_FFFF_FFFF_FFFE to 0xFFFF_FFFF_FFFF_FFFF"
        ]

    def test_plain_summary_lines(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(_TESTS)
        result = pytester.runpytest("--verify-summary=all")
        result.stdout.fnmatch_lines(
            [
                "  ✗ CTRL: 1 of 2 failed (first: test_plain_summary_lines.py::test_ctrl[[]240[]])",
                "  ✗ WIDE: 1 of 1 failed (first: test_plain_summary_lines.py::test_wide)",
                "  ✓ ID: 2 passed",
                "  ✓ Plain: 1 passed",
            ]
        )

    def test_stats_lines(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(_TESTS)
        result = pytester.runpytest("--verify-summary=stats")
        result.stdout.fnmatch_lines(
            [
                "  ✗ CTRL: 1 of 2 failed (first: *test_ctrl[[]240[]]); 0x00F0 to 0x00F3",
                "  ✗ WIDE: 1 of 1 failed (first: *::test_wide); 0xFFFF_FFFF_FFFF_FFFF",
                "  ✓ ID: 2 passed; 0x00F0 to 0x00F3",
                "  ✓ Plain: 1 passed; 3, margin 1",
            ]
        )


class TestSessionOutputs:
    def test_the_failure_summary(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(_TESTS)
        result = pytester.runpytest()
        result.assert_outcomes(passed=1, failed=2)
        result.stdout.fnmatch_lines(
            [
                "*1 of 2 checks failed: CTRL — expected 0x00F3, got 0x00F0 (mask 0x00FF); "
                "bits [[]1:0[]] differ: EN [[]0[]] is 0b0, expected 0b1; MODE [[]3:1[]] is 0b000, "
                "expected 0b001",
                "*✗ [[]0[]] CTRL (test_the_failure_summary.py:5) — expected 0x00F3, got 0x00F0*",
                "*✓ [[]1[]] ID — 0x00F0 == 0x00F0",
                "*✗ [[]0[]] WIDE (test_the_failure_summary.py:10) — expected "
                "0xFFFF_FFFF_FFFF_FFFE, got 0xFFFF_FFFF_FFFF_FFFF; bit 0 differs",
            ]
        )

    def test_verify_json(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(_TESTS)
        pytester.runpytest("--verify-json=checks.jsonl")
        text = (pytester.path / "checks.jsonl").read_text(encoding="utf-8")
        lines = [json.loads(line) for line in text.splitlines()]
        failed = [line["check"] for line in lines if line["check"]["name"] == "CTRL"][1]
        assert failed["check_type"] == "bits" and failed["passed"] is False
        assert failed["detail"] == _CTRL_DETAIL
        assert failed["mask"] == 255 and failed["radix"] == "hex" and failed["width"] == 16
        assert failed["fields"] == {"EN": [0, 0], "MODE": [3, 1]}
        assert failed["rendered"] == {"actual": "0x00F0", "expected": "0x00F3", "mask": "0x00FF"}
        assert failed["bit_diff"]["bits"] == [[1, 0]]
        assert [field["name"] for field in failed["bit_diff"]["fields"]] == ["EN", "MODE"]
        wide = next(line["check"] for line in lines if line["check"]["name"] == "WIDE")
        assert '"actual": 18446744073709551615' in text  # every digit is in the file
        assert wide["actual"] == 2**64 - 1 and wide["expected"] == 2**64 - 2
        assert wide["rendered"]["actual"] == "0xFFFF_FFFF_FFFF_FFFF"
        plain = next(line["check"] for line in lines if line["check"]["name"] == "Plain")
        assert set(plain).isdisjoint(FORMAT_KEYS)

    def test_junit_properties(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(_TESTS)
        pytester.runpytest(
            "--junitxml=out.xml", "-o", "junit_family=xunit1", "-o", "verify_junit_properties=all"
        )
        root = ET.parse(str(pytester.path / "out.xml")).getroot()
        properties = {
            (case.get("name"), prop.get("name")): prop.get("value")
            for case in root.iter("testcase")
            for prop in case.iter("property")
        }
        assert properties[("test_ctrl[240]", "verify[0] CTRL")] == f"failed: {_CTRL_DETAIL}"
        assert properties[("test_ctrl[243]", "verify[0] CTRL")] == (
            "passed: 0x00F3 & 0x00FF == 0x00F3"
        )
        assert properties[("test_ctrl[240]", "verify[1] ID")] == "passed: 0x00F0 == 0x00F0"
        assert properties[("test_wide", "verify[0] WIDE")] == (
            "failed: expected 0xFFFF_FFFF_FFFF_FFFE, got 0xFFFF_FFFF_FFFF_FFFF; bit 0 differs"
        )

    def test_reports_and_the_results_hook_carry_the_keys(self, pytester: pytest.Pytester) -> None:
        pytester.makeconftest(
            """
            import json

            from pytest_verifier import get_check_results

            SEEN = []

            def pytest_verify_results(item, when, checks, passed):
                SEEN.extend(checks)

            def pytest_runtest_logreport(report):
                if report.when == "call":
                    checks = report.verify_checks
                    assert checks == json.loads(json.dumps(checks))
                    assert checks[0]["rendered"]["actual"] == "0x00F0"
                    assert checks[0]["bit_diff"]["text"] == "bits [1:0] differ"
                    assert checks[1]["actual"] == 0xF0 and "format_values" not in checks[1]

            def pytest_runtest_teardown(item):
                assert [check["check_type"] for check in get_check_results(item)] == [
                    "bits", "equal"
                ]
                assert [check["radix"] for check in SEEN] == ["hex", "hex"]
            """
        )
        pytester.makepyfile(
            """
            class Register:
                def __verify_format__(self):
                    return {"width": 16, "value": 0xF0}

                def __eq__(self, other):
                    return other == 0xF0

            def test_register(verify):
                verify.bits(0xF0, 0xF3, width=16, name="CTRL")
                verify.equal(Register(), 0xF0)
            """
        )
        result = pytester.runpytest("-p", "no:cacheprovider")
        result.assert_outcomes(failed=1)
        assert "INTERNALERROR" not in result.stdout.str()
        result.stdout.fnmatch_lines(["*✓ [[]1[]] Register() — 0x00F0 == 0x00F0"])

    def test_xdist_safe_records(self) -> None:
        # What pytest-xdist sends is the JSON form of the records: nothing may be lost in it.
        record = _verify().bits(2**100, 2**100 + 1, fields={"LSB": 0}, width=128, name="W")
        assert json.loads(json.dumps(record)) == record


# ---------------------------------------------------------------------------
# Typing
# ---------------------------------------------------------------------------


class TestHints:
    """Public annotations must resolve with ``get_type_hints`` on every supported Python."""

    RADIX = Optional[typing.Literal["hex", "bin", "oct", "dec"]]

    @pytest.mark.parametrize("method", sorted(FORMATTED))
    def test_radix_and_width(self, method: str) -> None:
        hints = typing.get_type_hints(getattr(Verify, method))
        assert hints["radix"] == self.RADIX
        assert hints["width"] == Optional[int]
        assert hints["return"] is pytest_verifier.CheckDescriptor

    def test_bits(self) -> None:
        hints = typing.get_type_hints(Verify.bits)
        assert hints["mask"] == Optional[int]
        assert hints["fields"] == Optional[
            typing.Mapping[str, typing.Union[int, typing.Tuple[int, int], typing.List[int]]]
        ]
        assert hints["name"] == Optional[str] and hints["note"] == Optional[str]
        assert "expected_name" in hints and "actual_name" in hints

    def test_the_signature_of_bits(self) -> None:
        import inspect

        parameters = inspect.signature(Verify.bits).parameters
        assert list(parameters) == [
            "self", "actual", "expected", "mask", "fields", "width", "radix", "name", "note",
            "actual_name", "expected_name",
        ]
        keyword_only = [name for name, p in parameters.items() if p.kind is p.KEYWORD_ONLY]
        assert keyword_only == list(parameters)[3:]
        assert all(parameters[name].default is None for name in keyword_only)
        assert Verify.bits.__doc__ and "Args:" in Verify.bits.__doc__

    @pytest.mark.parametrize("method", sorted(set(FORMATTED) - {"bits"}))
    def test_radix_and_width_are_keyword_only_and_documented(self, method: str) -> None:
        import inspect

        function = getattr(Verify, method)
        parameters = inspect.signature(function).parameters
        for name in ("radix", "width"):
            assert parameters[name].kind is parameters[name].KEYWORD_ONLY
            assert parameters[name].default is None
            assert re.search(rf"\n\s+{name}: ", function.__doc__)
        with pytest.raises(TypeError):
            FORMATTED[method](checks, "hex")

    def test_the_exported_types(self) -> None:
        hints = typing.get_type_hints(ValueFormat)
        assert hints["width"] is int and hints["value"] is int
        assert hints["radix"] == typing.Literal["hex", "bin", "oct", "dec"]
        returned = typing.get_type_hints(VerifyFormat.__verify_format__)["return"]
        assert typing.get_origin(returned) is not None
        descriptor = typing.get_type_hints(pytest_verifier.CheckDescriptor)
        assert {"radix", "width", "fields", "mask", "rendered", "bit_diff", "format_error"} <= set(
            descriptor
        )


# ---------------------------------------------------------------------------
# Bounds, copies of checks, passed records
# ---------------------------------------------------------------------------


class Loose:
    """A register whose ``==`` ignores the low nibble."""

    def __init__(self, value: int) -> None:
        self.value = value

    def __verify_format__(self) -> Mapping[str, Any]:
        return {"width": 8, "value": self.value}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, int) and self.value >> 4 == other >> 4

    __hash__ = None  # type: ignore[assignment]


class TestBoundedText:
    """A formatted value is one line of about the usual length; ``rendered`` is exact."""

    @pytest.mark.parametrize(
        "build",
        [
            lambda: checks.equal(1, 2, radix="bin", width=1024, name="x"),
            lambda: checks.equal(1, 2, width=1024, name="x"),
            lambda: checks.not_equal(1, 1, radix="bin", width=1024, name="x"),
            lambda: checks.less(2, 1, radix="bin", width=1024, name="x"),
            lambda: checks.between(1, 2, 3, radix="bin", width=1024, name="x"),
            lambda: checks.bits(1, 2, mask=3, radix="bin", width=1024, name="x"),
        ],
    )
    def test_every_value_shown_is_bounded(self, build: Callable[[], Any]) -> None:
        check = build()
        record, passed = settle(check)
        assert passed is False
        operands = len(check["rendered"])
        for text in (check["description"], record["detail"]):
            assert "\n" not in text
            assert len(text) <= operands * 260 + 120, len(text)

    def test_the_middle_is_elided_and_the_low_bits_stay(self) -> None:
        record, _ = settle(checks.equal(1, 2, radix="bin", width=1024, name="x"))
        assert record["detail"].startswith("expected 0b0000_0000_")
        assert record["detail"].count("_00...000_0000_") == 2
        assert "_0000_0010, got 0b0000_" in record["detail"]
        assert len(record["detail"]) == len("expected , got ; bits [1:0] differ") + 2 * 240
        assert record["detail"].endswith("_0001; bits [1:0] differ")

    def test_rendered_keeps_every_digit(self) -> None:
        record, _ = settle(checks.equal(1, 2, radix="bin", width=1024, name="x"))
        text = record["rendered"]["actual"]
        assert "..." not in text and int(text, 0) == 1 and len(text) == 2 + 1024 + 255

    def test_a_value_that_fits_is_shown_whole(self) -> None:
        value = 0xDEAD_BEEF_0123_4567_89AB_CDEF_DEAD_BEEF
        record, _ = settle(checks.equal(value, 0, width=128, name="x"))
        assert "got 0xDEAD_BEEF_0123_4567_89AB_CDEF_DEAD_BEEF;" in record["detail"]

    def test_plain_checks_keep_their_bound(self) -> None:
        record, _ = settle(checks.equal(0, 2**1023, name="x"))
        assert len(record["detail"]) == 256 and "radix" not in record

    @pytest.mark.parametrize("key", ["rendered", "bit_diff"])
    def test_a_hand_built_text_is_bounded_and_on_one_line(self, key: str) -> None:
        junk = {
            "rendered": {"actual": "A\nB" + "z" * 5000},
            "bit_diff": {"bits": [[1, 0]], "text": "B\n" + "b" * 5000},
        }[key]
        built = {
            "check_type": "equal", "name": "hand", "description": "x", "radix": "hex",
            "actual": "not a number", "expected": 2, key: junk,
        }
        record, passed = settle(built)
        assert passed is False
        assert len(record["detail"]) < 300 and "\n" not in record["detail"]
        if key == "rendered":
            assert record["detail"].startswith("expected 0x2, got A\\nBzzz")
            assert "..." in record["detail"]

    def test_a_stored_text_never_replaces_an_integer(self) -> None:
        built = {
            "check_type": "equal", "name": "hand", "description": "x", "radix": "hex",
            "actual": 1, "expected": 2, "rendered": {"actual": "A\nB" + "z" * 5000},
        }
        record = _verify().record(built)
        assert record["detail"] == "expected 0x2, got 0x1; bits [1:0] differ"
        assert record["rendered"] == {"actual": "0x1", "expected": "0x2"}

    def test_a_long_field_value_is_bounded(self) -> None:
        check = checks.bits(0, (1 << 1024) - 1, fields={"ALL": (1023, 0)}, name="x")
        record, _ = settle(check)
        assert len(record["detail"]) < 1300
        assert len(check["bit_diff"]["fields"][0]["expected"]) > 300  # the record is exact


class TestCopiesOfChecks:
    """A copy of a check with another value is judged and shown on that value."""

    def test_equal(self) -> None:
        template = checks.equal(0xF0, 0xF3, width=8, name="x")
        copy = dict(template, actual=0xF3)
        record, passed = settle(copy)
        assert passed is True and checks.evaluate(copy) is True  # type: ignore[arg-type]
        assert record["detail"] == "0xF3 == 0xF3"
        assert record["rendered"] == {"actual": "0xF3", "expected": "0xF3"}
        assert "bit_diff" not in record

    def test_equal_that_now_fails(self) -> None:
        copy = dict(checks.equal(0xF3, 0xF3, width=8, name="x"), actual=0xF0)
        record, passed = settle(copy)
        assert passed is False
        assert record["detail"] == "expected 0xF3, got 0xF0; bits [1:0] differ"
        assert record["bit_diff"]["bits"] == [[1, 0]]

    def test_bits(self) -> None:
        template = checks.bits(0xF0, 0xF3, width=8, mask=0x0F, fields={"LOW": (1, 0)}, name="x")
        record, passed = settle(dict(template, actual=0xF2))
        assert passed is False
        assert record["detail"] == (
            "expected 0x03, got 0xF2 (mask 0x0F); bit 0 differs: LOW [1:0] is 0b10, expected 0b11"
        )
        assert record["rendered"]["actual"] == "0xF2"
        assert record["bit_diff"]["bits"] == [[0, 0]]
        record, passed = settle(dict(template, actual=0xA3))
        assert passed is True and record["detail"] == "0xA3 & 0x0F == 0x03"
        assert "bit_diff" not in record

    def test_an_ordering_check(self) -> None:
        record, passed = settle(dict(checks.less(0x30, 0x20, width=8, name="x"), actual=0x10))
        assert passed is True and record["detail"] == "0x10 < 0x20"
        assert record["rendered"] == {"actual": "0x10", "threshold": "0x20"}

    def test_the_fixture_records_the_copy(self) -> None:
        verify = _verify()
        record = verify.record(dict(checks.equal(0xF0, 0xF3, width=8, name="x"), actual=0xF3))
        assert record["passed"] is True and record["detail"] == "0xF3 == 0xF3"
        assert _again(record) == record["detail"]

    def test_a_record_of_an_unchanged_check_is_as_built(self) -> None:
        check = checks.bits(0x12F0, 0xABF3, mask=0xFF, width=16, fields=FIELDS, name="x")
        record, _ = settle(check)
        for key in ("radix", "width", "fields", "mask", "rendered", "bit_diff"):
            assert record[key] == check[key]  # type: ignore[literal-required]


class TestPassedRecords:
    """``bit_diff`` says which bits made a check fail: a passed record has none."""

    def test_a_register_with_a_loose_equality(self) -> None:
        check = checks.equal(Loose(0xF1), 0xF3, name="loose")
        assert check["bit_diff"]["text"] == "bit 1 differs"  # not judged yet
        record, passed = settle(check)
        assert passed is True and record["detail"] == "0xF1 == 0xF3"
        assert "bit_diff" not in record
        assert record["rendered"] == {"actual": "0xF1", "expected": "0xF3"}

    def test_an_int_subclass_that_gives_another_value(self) -> None:
        record = _verify().equal(_answering({"width": 8, "value": 99}), 5, name="intsub")
        assert record["passed"] is True and record["actual"] == 99
        assert record["detail"] == "0x63 == 0x05" and "bit_diff" not in record

    def test_a_failed_record_keeps_it(self) -> None:
        record = _verify().equal(Loose(0xE1), 0xF3, name="loose")
        assert record["passed"] is False and record["bit_diff"]["bits"] == [[4, 4], [1, 1]]

    def test_a_child_that_was_not_selected_keeps_it(self) -> None:
        verify = _verify()
        record = verify.conditional(
            1, cases={1: checks.is_true(1, name="a"), 2: checks.equal(1, 2, width=4, name="b")},
            name="mode",
        )
        other = record["cases"]["2"]
        assert "passed" not in other and other["bit_diff"]["text"] == "bits [1:0] differ"


class TestOperandsOutsideTheFormat:
    """An operand a format does not apply to that reads like the checked value: the plain
    detail, which names the types."""

    def test_not_equal(self) -> None:
        plain, _ = settle(checks.not_equal(5, Decimal(5), name="R"))
        record, _ = settle(checks.not_equal(5, Decimal(5), radix="dec", name="R"))
        assert record["detail"] == plain["detail"] == "expected ≠ 5 (Decimal), got 5 (int)"

    def test_an_ordering_check(self) -> None:
        plain, _ = settle(checks.greater(5, Decimal(5), name="R"))
        record, _ = settle(checks.greater(5, Decimal(5), radix="dec", name="R"))
        assert record["detail"] == plain["detail"] == "expected > 5 (Decimal), got 5 (int)"

    def test_between(self) -> None:
        plain, _ = settle(checks.between(5, Decimal(5), 9, inclusive=False, name="R"))
        record, _ = settle(
            checks.between(5, Decimal(5), 9, inclusive=False, radix="dec", name="R")
        )
        assert record["detail"] == plain["detail"]
        assert "(Decimal)" in record["detail"]

    def test_texts_that_differ_stay_in_the_format(self) -> None:
        record, _ = settle(checks.not_equal(5, Decimal(5), radix="hex", width=8, name="R"))
        assert record["detail"] == "expected ≠ 5, got 0x05"

    def test_two_integers_never_get_types(self) -> None:
        record, _ = settle(checks.not_equal(Word(5), 5, name="R"))
        assert record["detail"] == "expected ≠ 0x0005, got 0x0005"


class TestBitsAdvice:
    def _error(self, value: Any) -> str:
        record, passed = settle(checks.bits(value, 1, name="R"))
        assert passed is False
        return str(record["error"])

    def test_a_class_without_the_method(self) -> None:
        assert self._error(1.5) == (
            "TypeError: bits compares integers, and actual is a float; convert it with int(), "
            "or give its class a __verify_format__ method"
        )

    @pytest.mark.parametrize("answer", [None, {}, {"width": 8}])
    def test_a_method_that_gave_no_value(self, answer: Any) -> None:
        class Reg:
            def __verify_format__(self) -> Any:
                return answer

        assert self._error(Reg()) == (
            "TypeError: bits compares integers, and actual is a Reg; its __verify_format__ "
            'gave no "value", and it has no __index__'
        )

    def test_a_method_that_raised(self) -> None:
        class Reg:
            def __verify_format__(self) -> Any:
                raise RuntimeError("bus")

        assert self._error(Reg()) == (
            "TypeError: bits compares integers, and actual is a Reg; its __verify_format__ "
            "was not used (Reg.__verify_format__: RuntimeError: bus)"
        )


class TestManyFields:
    def test_the_record_keeps_64_fields(self) -> None:
        check = checks.bits(0, (1 << 70) - 1, fields={f"F{i}": i for i in range(70)}, name="x")
        diff = check["bit_diff"]
        assert len(diff["fields"]) == 64 and diff["more_fields"] == 6
        assert [field["name"] for field in diff["fields"][:2]] == ["F0", "F1"]
        record, _ = settle(check)
        assert record["bit_diff"] == diff
        assert record["detail"].endswith("F5 [5] is 0b0, expected 0b1; and 64 more")
        assert record["detail"].count(" is 0b0, expected 0b1") == 6


class TestSummaryFormats:
    def _lines(self, *records: Any) -> List[str]:
        summary = SessionSummary("stats")
        summary.add("t.py::test", records)
        return summary.lines()

    def test_a_whole_margin_is_in_the_groups_radix(self) -> None:
        verify = _verify()
        assert self._lines(verify.less(0x1F0, 0x100, name="LT", width=16)) == [
            "  ✗ LT: 1 of 1 failed (first: t.py::test); 0x01F0, margin -0xF0"
        ]
        assert self._lines(verify.less(5, 8, name="N", radix="bin", width=4)) == [
            "  ✓ N: 1 passed; 0b0101, margin 0b11"
        ]
        assert self._lines(verify.greater(0xF3, 0xE7, name="GT", width=8, units="V")) == [
            "  ✓ GT: 1 passed; 0xF3V, margin 0xCV"
        ]

    def test_a_margin_in_dec_or_without_a_format_is_unchanged(self) -> None:
        verify = _verify()
        assert self._lines(verify.less(5, 8, name="N", radix="dec")) == [
            "  ✓ N: 1 passed; 5, margin 3"
        ]
        assert self._lines(verify.less(5, 8, name="N")) == ["  ✓ N: 1 passed; 5, margin 3"]

    def test_a_margin_that_is_not_whole_stays_decimal(self) -> None:
        verify = _verify()
        assert self._lines(verify.less(5, 8.5, name="N", width=8)) == [
            "  ✓ N: 1 passed; 0x05, margin 3.5"
        ]

    def test_a_value_outside_the_format_leaves_the_range_out(self) -> None:
        verify = _verify()
        lines = self._lines(
            verify.equal(0xF0, 0xF0, name="flt", radix="hex"),
            verify.equal(1.5, 1.5, name="flt", radix="hex"),
        )
        assert lines == ["  ✓ flt: 2 passed"]

    def test_floats_with_a_radix_read_as_floats(self) -> None:
        verify = _verify()
        lines = self._lines(
            verify.equal(0.5, 0.5, name="flt", radix="hex"),
            verify.equal(1.5, 1.5, name="flt", radix="hex"),
        )
        assert lines == ["  ✓ flt: 2 passed; 0.5 to 1.5"]


class TestGroupedLiteralsInFiles:
    def test_load_limits_reads_what_the_plugin_prints(self, tmp_path: Any) -> None:
        path = tmp_path / "limits.csv"
        path.write_text(
            "name,check,expected,mask,width\n"
            "X,bits,0x1_0000,0xFFFF_0000,32\n"
            "Y,bits,0b1111_0011,,\n"
            "Z,equal,-0xDEAD_BEEF,,\n",
            encoding="utf-8",
        )
        table = load_limits(path)
        assert table["X"]["expected"] == 0x1_0000 and table["X"]["mask"] == 0xFFFF_0000
        assert table["Y"]["expected"] == 0b1111_0011
        made = _verify().limits({"Z": -0xDEAD_BEEF}, {"Z": table["Z"]})
        assert made["Z"]["passed"] is True and made["Z"]["expected"] == -0xDEAD_BEEF

    @pytest.mark.parametrize("cell", ["0x_", "0x1__0", "0x1_", "1_000", "_1"])
    def test_other_underscores_are_still_refused(self, tmp_path: Any, cell: str) -> None:
        path = tmp_path / "limits.csv"
        path.write_text(f"name,check,expected\nX,bits,{cell}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="column 'expected'"):
            load_limits(path)


class TestFieldsHint:
    def test_bit_field_is_exported(self) -> None:
        assert "BitField" in pytest_verifier.__all__
        assert pytest_verifier.BitField == typing.Union[
            int, typing.Tuple[int, int], typing.List[int]
        ]

    def test_a_table_kept_in_a_constant(self) -> None:
        table: Dict[str, pytest_verifier.BitField] = {"EN": 0, "MODE": (3, 1), "DIV": [15, 8]}
        check = checks.bits(1, 2, fields=table, name="x")
        assert check["fields"] == {"EN": [0, 0], "MODE": [3, 1], "DIV": [15, 8]}


class TestOneRead:
    """The README's register model: one bus read per check, so the value shown is the value
    compared."""

    def test_the_reading_carries_the_format(self) -> None:
        class Reading(int):
            register: Any

            def __verify_format__(self) -> ValueFormat:
                return {
                    "radix": "hex", "width": self.register.width, "fields": self.register.fields
                }

        class Bus:
            reads = 0

            def read(self, address: int) -> int:
                self.reads += 1
                return 0xF0 if self.reads == 1 else 0xF3  # a value that changes when read

        class Model:
            def __init__(self, bus: Bus, address: int, fields: Dict[str, Any]) -> None:
                self.bus, self.address, self.width, self.fields = bus, address, 16, fields

            def read(self) -> Reading:
                reading = Reading(self.bus.read(self.address))
                reading.register = self
                return reading

        bus = Bus()
        ctrl = Model(bus, 0x10, {"EN": 0, "MODE": (3, 1), "DIV": (15, 8)})
        record = _verify().equal(ctrl.read(), 0x00F3)
        assert bus.reads == 1
        assert record["passed"] is False and record["name"] == "ctrl.read()"
        assert record["detail"] == (
            "expected 0x00F3, got 0x00F0; bits [1:0] differ: "
            "EN [0] is 0b0, expected 0b1; MODE [3:1] is 0b000, expected 0b001"
        )


class TestGuardConditionThatRaises:
    """What the skill says of a guard whose callable condition raises."""

    def _boom(self) -> bool:
        raise ValueError("cond")

    def test_a_lazy_default_is_not_called(self) -> None:
        verify, called = _verify(), []
        record = verify.guard(
            [(self._boom, "boost", lambda: verify.is_true(1, name="x"))],
            default=lambda: called.append(1) or verify.is_true(1, name="dflt"),
            name="g",
        )
        assert record["passed"] is False and not called
        assert record["detail"] == (
            "[→ no branch chosen] (condition of branch 0 (boost) raised ValueError: cond)"
        )

    def test_an_eager_default_is_shown_before_the_error(self) -> None:
        verify = _verify()
        record = verify.guard(
            [(self._boom, "boost", lambda: verify.is_true(1, name="x"))],
            default=verify.is_true(1, name="dflt"),
            name="g",
        )
        assert record["passed"] is False
        assert record["detail"] == (
            "[→ default] — 1 (truthy) (condition of branch 0 (boost) raised ValueError: cond)"
        )
