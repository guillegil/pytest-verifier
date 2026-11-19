"""``bits``: two register values compared bit by bit, under an optional mask."""
from __future__ import annotations

from typing import Any, Mapping, Optional

from .._descriptors import CheckDescriptor, require_name
from .._registers import Format, is_integer, require_mask
from .._render import render_text
from ._base import CheckType, named, register
from ._values import subject

_OPERANDS = ("actual", "expected", "mask")


def _mask(d: Mapping[str, Any]) -> Optional[int]:
    """The mask of *d*, or ``None`` for every bit. Raises for anything else."""
    mask = d.get("mask")
    if mask is None:
        return None
    if not is_integer(mask) or mask < 0:
        raise TypeError(f"the mask must be a non-negative int, got {type(mask).__name__}")
    return int(mask)


class Bits(CheckType):
    """``(actual ^ expected) & mask == 0``. The operands are integers: a ``bool``, a float
    or text makes the check fail with an ``error``. Always shown in a radix (hex by
    default)."""

    check_type = "bits"
    format_keys = _OPERANDS
    format_diff = True
    format_default = "hex"

    @staticmethod
    def build(
        actual: Any,
        expected: Any,
        *,
        mask: Optional[int] = None,
        fields: Optional[Mapping[str, Any]] = None,
        width: Optional[int] = None,
        radix: Optional[str] = None,
        name: str,
        expected_name: Optional[str] = None,
    ) -> CheckDescriptor:
        name = require_name(name, "bits")
        mask = require_mask(mask, "bits")
        fmt = Format.read(
            "bits", {"actual": actual, "expected": expected}, radix, width, fields, default="hex"
        )
        integers = fmt.integers
        if mask is not None:
            text = fmt.render(mask)
            if text is not None:
                fmt.texts["mask"] = text
        shown = named(expected_name, _expected(fmt, expected, mask))
        under = "" if mask is None else f" & {fmt.shown('mask', mask)}"
        descriptor: CheckDescriptor = {
            "check_type": "bits",
            "name": name,
            "description": f"{subject(name)}{under} == {shown}",
            # The integers themselves: bits are compared, never the objects.
            "actual": integers.get("actual", actual),
            "expected": integers.get("expected", expected),
        }
        if mask is not None:
            descriptor["mask"] = mask
        if expected_name is not None:
            descriptor["expected_name"] = expected_name
        fmt.stamp(descriptor, values=False)
        difference = fmt.difference(mask)
        if difference is not None:
            descriptor["bit_diff"] = difference
        return descriptor

    def compare(self, d: Mapping[str, Any]) -> Any:
        actual, expected, mask = d["actual"], d["expected"], _mask(d)
        for label, operand in (("actual", actual), ("expected", expected)):
            if not is_integer(operand):
                raise TypeError(
                    f"bits compares integers, and {label} is a {type(operand).__name__}; "
                    + _advice(operand, d.get("format_error"))
                )
        differing = actual ^ expected
        return (differing if mask is None else differing & mask) == 0

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        fmt = Format.of(d, _OPERANDS, default="hex")
        try:
            mask = _mask(d)
        except TypeError:
            mask = None
        actual = fmt.shown("actual", d["actual"])
        expected = named(d.get("expected_name"), _expected(fmt, d["expected"], mask))
        if passed:
            under = "" if mask is None else f" & {fmt.shown('mask', mask)}"
            return f"{actual}{under} == {expected}"
        under = "" if mask is None else f" (mask {fmt.shown('mask', mask)})"
        clause = fmt.clause(d, mask)
        return f"expected {expected}, got {actual}{under}" + (f"; {clause}" if clause else "")

    def units(self, d: Mapping[str, Any]) -> Any:
        return None


def _expected(fmt: Format, expected: Any, mask: Optional[int]) -> str:
    """The expected value as the check states it: the bits under the mask."""
    number = fmt.integers.get("expected")
    if number is not None and mask is not None:
        text = fmt.shown_number(number & mask)
        if text is not None:
            return text
    return fmt.shown("expected", expected)


def _advice(operand: object, format_error: object) -> str:
    """What to do about an operand that is not an integer: its class has no
    ``__verify_format__``, or has one that gave no integer."""
    if getattr(type(operand), "__verify_format__", None) is None:
        return "convert it with int(), or give its class a __verify_format__ method"
    if isinstance(format_error, str) and format_error:
        return f"its __verify_format__ was not used ({render_text(format_error)})"
    return 'its __verify_format__ gave no "value", and it has no __index__'


BITS = register(Bits())
