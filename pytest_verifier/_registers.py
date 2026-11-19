"""Register values: integers shown in hex, binary or octal, their bit fields, and which bits
of two of them differ.

A check gets a format from its ``radix=``/``width=`` (and, for ``bits``, ``fields=``)
arguments, or from the value itself: a class may define ``__verify_format__`` (see
:class:`VerifyFormat`). The arguments are checked like any other (a wrong one raises); the
value's answer is user code read once, when the check is built, and can never raise from a
check or fail it (:func:`_ask`).

Everything a format adds to a descriptor is plain JSON data (:meth:`Format.stamp`), so a
record carries it unchanged and a detail can be rendered again from the record
(:meth:`Format.of`). A check without a format gets :data:`PLAIN`, which adds nothing.
"""
from __future__ import annotations

import operator
import typing
from typing import Any, Dict, List, Literal, Mapping, NamedTuple, Optional, Tuple, Union

from ._descriptors import plain_text
from ._render import (
    VALUE_LIMIT,
    describe_error,
    escape,
    render_text,
    render_value,
    safe_repr,
    units_text,
)

#: How an integer is shown.
Radix = Literal["hex", "bin", "oct", "dec"]

RADIXES: Tuple[str, ...] = ("hex", "bin", "oct", "dec")

#: One field of a register: a bit index, or ``(msb, lsb)`` in either order (a tuple or a
#: two-item list). Annotate a table of fields kept in a constant with it:
#: ``CTRL: Dict[str, BitField] = {"EN": 0, "MODE": (3, 1)}``.
BitField = Union[int, Tuple[int, int], List[int]]

#: The widest value, width and bit index a format takes. A wider integer keeps its decimal
#: rendering.
MAX_BITS = 1024

#: What stands for the middle of a formatted value too long for a line.
_ELIDED = "..."

#: Most ranges of differing bits named in a detail, and kept in a record.
_RANGES_SHOWN = 8
_RANGES_KEPT = 64

#: Most differing fields named in a detail, and kept in a record.
_FIELDS_SHOWN = 6
_FIELDS_KEPT = 64

#: Longest field name shown in a detail.
_FIELD_NAME_LIMIT = 60


class ValueFormat(typing.TypedDict, total=False):
    """What ``__verify_format__`` returns: how the checks show the value. Every key is
    optional.

    ``radix`` is ``"hex"``, ``"bin"``, ``"oct"`` or ``"dec"`` (hex when only ``width`` or
    ``fields`` is given), ``width`` the number of bits, ``fields`` the bit fields by name (a
    bit index, or ``(msb, lsb)``), and ``value`` the integer to show and record for an object
    that is not an ``int`` (without it, ``operator.index()`` of an object that has
    ``__index__``).
    """

    radix: Radix
    width: int
    fields: Mapping[str, BitField]
    value: int


class VerifyFormat(typing.Protocol):
    """A value that says how checks show it: a register of a register model.

    ``equal``, ``not_equal``, the ordering checks, ``between`` and ``bits`` call
    ``type(value).__verify_format__(value)`` once, when the check is built, on the checked
    value and then on the value it is compared with. The first answer gives the format, and
    the check's own ``radix=``, ``width=`` and ``fields=`` arguments win over it. The method
    may return ``None`` for no format. If it raises or returns something else, the check is
    made without it and its record says why in ``format_error``.
    """

    def __verify_format__(self) -> Mapping[str, Any]:
        """The format of this value: the keys of :class:`ValueFormat`."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Arguments (usage errors raise)
# ---------------------------------------------------------------------------


def is_integer(value: object) -> bool:
    """Whether *value* is an ``int`` that is not a ``bool``. Never raises."""
    try:
        return isinstance(value, int) and not isinstance(value, bool)
    except Exception:  # a proxy whose type check raises
        return False


def plain_int(value: int) -> int:
    """*value* as a plain ``int`` (an ``int`` subclass may override ``__int__``)."""
    return value if type(value) is int else int.__index__(value)


def require_radix(radix: object, check: str) -> Optional[str]:
    """*radix* checked: one of :data:`RADIXES`, or ``None``."""
    if radix is None:
        return None
    if not isinstance(radix, str):
        raise TypeError(
            f"{check}() radix must be 'hex', 'bin', 'oct' or 'dec', got {type(radix).__name__}"
        )
    text = plain_text(radix)
    if text not in RADIXES:
        raise ValueError(
            f"{check}() radix must be 'hex', 'bin', 'oct' or 'dec', got {safe_repr(text)}"
        )
    return text


def require_width(width: object, check: str) -> Optional[int]:
    """*width* checked: a number of bits from 1 to :data:`MAX_BITS`, or ``None``."""
    if width is None:
        return None
    if not is_integer(width):
        raise TypeError(
            f"{check}() width must be a number of bits (an int), got {type(width).__name__}"
        )
    number = plain_int(width)  # type: ignore[arg-type]
    if not 1 <= number <= MAX_BITS:
        raise ValueError(
            f"{check}() width must be a number of bits from 1 to {MAX_BITS}, got {number}"
        )
    return number


def require_mask(mask: object, check: str) -> Optional[int]:
    """*mask* checked: a non-negative ``int``, or ``None`` for every bit."""
    if mask is None:
        return None
    if not is_integer(mask):
        raise TypeError(
            f"{check}() mask must be a non-negative int, got {type(mask).__name__}"
        )
    number = plain_int(mask)  # type: ignore[arg-type]
    if number < 0:
        raise ValueError(f"{check}() mask must not be negative, got {number}")
    return number


def _bit(index: object, check: str, name: str, width: Optional[int]) -> int:
    if not is_integer(index):
        raise TypeError(
            f"{check}() field {name!r} must be a bit index or (msb, lsb), "
            f"got {safe_repr(index)}"
        )
    number = plain_int(index)  # type: ignore[arg-type]
    if number < 0:
        raise ValueError(f"{check}() field {name!r} has a negative bit index: {number}")
    limit = MAX_BITS if width is None else width
    if number >= limit:
        where = f"the width, {width} bits" if width is not None else f"{MAX_BITS} bits"
        raise ValueError(
            f"{check}() field {name!r} names bit {number}, which is outside {where}"
        )
    return number


def require_fields(
    fields: object, check: str, width: Optional[int] = None
) -> Optional[Dict[str, List[int]]]:
    """*fields* checked and normalised to ``{name: [msb, lsb]}``, in the given order; ``None``
    when there are none. A bit must be below *width* when that is known."""
    if fields is None:
        return None
    if not isinstance(fields, Mapping):
        raise TypeError(
            f"{check}() fields must be a mapping of names to a bit index or (msb, lsb), "
            f"got {type(fields).__name__}"
        )
    found: Dict[str, List[int]] = {}
    for key, bits in fields.items():
        if not isinstance(key, str):
            raise TypeError(f"{check}() field names must be str, got {safe_repr(key)}")
        name = plain_text(key).strip()
        if not name:
            raise ValueError(f"{check}() field names must not be empty")
        if name in found:
            raise ValueError(f"{check}() names the field {name!r} twice")
        if isinstance(bits, (tuple, list)):
            if len(bits) != 2:
                raise TypeError(
                    f"{check}() field {name!r} must be a bit index or (msb, lsb), "
                    f"got {safe_repr(bits)}"
                )
            first, second = _bit(bits[0], check, name, width), _bit(bits[1], check, name, width)
            found[name] = [max(first, second), min(first, second)]
        else:
            index = _bit(bits, check, name, width)
            found[name] = [index, index]
    return found or None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _grouped(digits: str) -> str:
    """*digits* in groups of four from the right: ``DEAD_BEEF``. Four or fewer stay as is."""
    if len(digits) <= 4:
        return digits
    head = len(digits) % 4
    groups = [digits[:head]] if head else []
    groups += [digits[index : index + 4] for index in range(head, len(digits), 4)]
    return "_".join(groups)


def render_int(value: int, radix: str, width: Optional[int] = None) -> Optional[str]:
    """*value* in *radix* as a Python literal: ``0x00F3``, ``0b1111_0011``, ``0o363``, ``243``.

    Hex and binary are padded to *width* bits and grouped in fours when longer than four
    digits; octal is padded only. A negative value has a sign (never two's complement), and a
    value wider than *width* shows every digit. ``None`` for a value above :data:`MAX_BITS`
    bits, which keeps its usual rendering.
    """
    if value.bit_length() > MAX_BITS:
        return None
    if radix == "dec":
        return str(value)
    sign, magnitude = ("-", -value) if value < 0 else ("", value)
    if radix == "bin":
        return f"{sign}0b{_grouped(format(magnitude, 'b').zfill(width or 0))}"
    if radix == "oct":
        return f"{sign}0o{format(magnitude, 'o').zfill(-(-(width or 0) // 3))}"
    return f"{sign}0x{_grouped(format(magnitude, 'X').zfill(-(-(width or 0) // 4)))}"


def display(text: str) -> str:
    """A formatted value as a description or a detail shows it: whole up to
    :data:`~pytest_verifier._render.VALUE_LIMIT` characters, else its first and last digits
    around ``...`` (the low bits of a register stay in sight). The ``rendered`` of a record
    keeps the whole text."""
    if len(text) <= VALUE_LIMIT:
        return text
    tail = (VALUE_LIMIT - len(_ELIDED)) // 2
    return text[: VALUE_LIMIT - len(_ELIDED) - tail] + _ELIDED + text[-tail:]


def field_text(value: int, bits: int) -> str:
    """The value of a field of *bits* bits: binary up to four bits (``0b001``), else hex."""
    text = render_int(value, "bin" if bits <= 4 else "hex", bits)
    return str(value) if text is None else text


def span_text(msb: int, lsb: int) -> str:
    """The bits of a field: ``[3:1]``, or ``[0]`` for one bit."""
    return f"[{msb}]" if msb == lsb else f"[{msb}:{lsb}]"


# ---------------------------------------------------------------------------
# Bit differences
# ---------------------------------------------------------------------------


def bit_ranges(bits: int) -> List[List[int]]:
    """The runs of set bits of a non-negative *bits* as ``[msb, lsb]``, highest first."""
    ranges: List[List[int]] = []
    position = 0
    while bits:
        if bits & 1:
            low = position
            while bits & 1:
                bits >>= 1
                position += 1
            ranges.append([position - 1, low])
        else:
            zeros = (bits & -bits).bit_length() - 1
            bits >>= zeros
            position += zeros
    ranges.reverse()
    return ranges


def ranges_text(ranges: List[List[int]]) -> str:
    """``bits [7:6], bit 4 and bits [1:0] differ``: the first ranges, then ``and N more``."""
    shown = [
        f"bit {msb}" if msb == lsb else f"bits [{msb}:{lsb}]"
        for msb, lsb in ranges[:_RANGES_SHOWN]
    ]
    hidden = len(ranges) - len(shown)
    if hidden:
        return f"{', '.join(shown)} and {hidden} more differ"
    if len(shown) == 1:
        return f"{shown[0]} {'differs' if ranges[0][0] == ranges[0][1] else 'differ'}"
    return f"{', '.join(shown[:-1])} and {shown[-1]} differ"


def bit_diff(
    actual: int,
    expected: int,
    mask: Optional[int] = None,
    fields: Optional[Mapping[str, List[int]]] = None,
) -> Optional[Dict[str, Any]]:
    """Which bits of two integers differ, under *mask* when given: the ``bit_diff`` of a
    record, or ``None`` when none differs.

    Also ``None`` without a mask when one of them is negative (it has no highest bit), and
    for integers above :data:`MAX_BITS` bits.
    """
    if mask is None and (actual < 0 or expected < 0):
        return None
    differing = actual ^ expected if mask is None else (actual ^ expected) & mask
    if differing <= 0 or differing.bit_length() > MAX_BITS:
        return None
    ranges = bit_ranges(differing)
    found: Dict[str, Any] = {"bits": ranges[:_RANGES_KEPT], "text": ranges_text(ranges)}
    if len(ranges) > _RANGES_KEPT:
        found["more_bits"] = len(ranges) - _RANGES_KEPT
    if fields:
        if mask is not None:
            actual, expected = actual & mask, expected & mask
        changed = []
        for name, (msb, lsb) in fields.items():
            size = msb - lsb + 1
            ones = (1 << size) - 1
            if (differing >> lsb) & ones:
                changed.append(
                    {
                        "name": name,
                        "msb": msb,
                        "lsb": lsb,
                        "actual": field_text((actual >> lsb) & ones, size),
                        "expected": field_text((expected >> lsb) & ones, size),
                    }
                )
        if changed:
            found["fields"] = changed[:_FIELDS_KEPT]
            if len(changed) > _FIELDS_KEPT:
                found["more_fields"] = len(changed) - _FIELDS_KEPT
    return found


def difference_clause(diff: object) -> str:
    """A ``bit_diff`` as the clause of a detail: ``bits [1:0] differ: MODE [3:1] is 0b000,
    expected 0b001``. ``""`` for anything that is not one. Never raises."""
    try:
        if not isinstance(diff, Mapping):
            return ""
        text = diff.get("text")
        if not isinstance(text, str) or not text:
            return ""
        text = render_text(text)  # built here it is short; a hand-built one may not be
        parts = []
        fields = diff.get("fields")
        for field in fields if isinstance(fields, (list, tuple)) else ():
            name = render_text(field["name"], _FIELD_NAME_LIMIT)
            span = span_text(int(field["msb"]), int(field["lsb"]))
            parts.append(
                f"{name} {span} is {display(render_text(field['actual'], MAX_BITS))}, "
                f"expected {display(render_text(field['expected'], MAX_BITS))}"
            )
        hidden = len(parts) - _FIELDS_SHOWN
        more = diff.get("more_fields")
        if is_integer(more):
            hidden += more  # type: ignore[operator]
        parts = parts[:_FIELDS_SHOWN]
        if hidden > 0:
            parts.append(f"and {hidden} more")
        return f"{text}: {'; '.join(parts)}" if parts else text
    except Exception:  # a hand-built bit_diff
        return ""


# ---------------------------------------------------------------------------
# The value protocol
# ---------------------------------------------------------------------------


class _Answer(NamedTuple):
    """What a value's ``__verify_format__`` said, checked."""

    radix: Optional[str]
    width: Optional[int]
    fields: Optional[Dict[str, List[int]]]
    #: The integer the value stands for, when it has one.
    value: Optional[int]


def _has_protocol(value: object) -> bool:
    try:
        return getattr(type(value), "__verify_format__", None) is not None
    except Exception:  # a metaclass whose attribute lookup raises
        return False


def _ask(value: object) -> Tuple[Optional[_Answer], Optional[str]]:
    """``(answer, error)`` of the ``__verify_format__`` of *value*: no answer without the
    method or when it returns ``None``, and an error when it raises or returns junk. Never
    raises (an exception that is not an ``Exception``, such as ``KeyboardInterrupt``, goes
    on)."""
    if not _has_protocol(value):
        return None, None
    name = "__verify_format__"
    try:
        answer = type(value).__verify_format__(value)  # type: ignore[attr-defined]
        if answer is None:
            return None, None
        if not isinstance(answer, Mapping):
            raise TypeError(f"must return a mapping or None, got {type(answer).__name__}")
        radix = require_radix(answer.get("radix"), name)
        width = require_width(answer.get("width"), name)
        fields = require_fields(answer.get("fields"), name, width)
        given = answer.get("value")
        number: Optional[int] = None
        if given is not None:
            if not is_integer(given):
                raise TypeError(f"{name}() value must be an int, got {type(given).__name__}")
            number = plain_int(given)
        elif is_integer(value):
            number = plain_int(value)  # type: ignore[arg-type]
        elif hasattr(type(value), "__index__"):
            number = plain_int(operator.index(value))  # type: ignore[arg-type]
        return _Answer(radix, width, fields, number), None
    except Exception as exc:
        try:
            kind = type(value).__name__
        except Exception:  # pragma: no cover - a metaclass without a name
            kind = "value"
        return None, render_text(f"{kind}.{name}: {describe_error(exc)}")


# ---------------------------------------------------------------------------
# The format of one check
# ---------------------------------------------------------------------------


class Format:
    """The format of a check's operands: the radix in force (``None``: none), the width,
    the fields, the integer of each operand that has one, and the text of each as shown."""

    __slots__ = ("radix", "width", "fields", "integers", "texts", "foreign", "error")

    def __init__(
        self,
        radix: Optional[str] = None,
        width: Optional[int] = None,
        fields: Optional[Dict[str, List[int]]] = None,
    ) -> None:
        self.radix = radix
        self.width = width
        self.fields = fields
        #: The integer of each operand that is one, by its descriptor field.
        self.integers: Dict[str, int] = {}
        #: The text of each operand the format applies to, without units.
        self.texts: Dict[str, str] = {}
        #: The integers a record stores in place of the operand: a register object's.
        self.foreign: Dict[str, int] = {}
        #: Why a value's ``__verify_format__`` was not used.
        self.error: Optional[str] = None

    @classmethod
    def read(
        cls,
        check: str,
        operands: Mapping[str, Any],
        radix: object = None,
        width: object = None,
        fields: object = None,
        *,
        default: Optional[str] = None,
    ) -> Format:
        """The format of a check being built: its arguments (checked here: a wrong one
        raises), else what the first of *operands* with a ``__verify_format__`` says.
        *default* is the radix of a check that always has one (``bits``).

        Returns :data:`PLAIN` for a check without arguments, protocol or *default*.
        """
        if (
            radix is None
            and width is None
            and fields is None
            and default is None
            and not any(_has_protocol(operand) for operand in operands.values())
        ):
            return PLAIN
        chosen_radix = require_radix(radix, check)
        chosen_width = require_width(width, check)
        chosen_fields = require_fields(fields, check, chosen_width)
        explicit = chosen_fields is not None
        answers: Dict[str, _Answer] = {}
        error: Optional[str] = None
        for key, operand in operands.items():
            answer, problem = _ask(operand)
            if problem is not None:
                error = error or problem
            elif answer is not None:
                answers[key] = answer
        if error is not None:
            answers = {}  # a protocol that misbehaves is not used at all
        for answer in answers.values():  # the first that answers gives the format
            chosen_radix = chosen_radix or answer.radix
            chosen_width = chosen_width if chosen_width is not None else answer.width
            chosen_fields = chosen_fields or answer.fields
            break
        if chosen_width is not None and chosen_fields:
            if explicit:  # fields= against the width the value gave: a usage error too
                require_fields(fields, check, chosen_width)
            else:  # the value's fields under a narrower width=: only the ones inside it
                chosen_fields = _inside(chosen_fields, chosen_width)
        if chosen_radix is None and (chosen_width is not None or chosen_fields):
            chosen_radix = "hex"
        found = cls(chosen_radix or default, chosen_width, chosen_fields)
        found.error = error
        for key, operand in operands.items():
            answer = answers.get(key)
            if answer is not None and answer.value is not None:
                found.integers[key] = answer.value
                if not is_integer(operand) or plain_int(operand) != answer.value:
                    found.foreign[key] = answer.value  # what a snapshot would not keep
            elif is_integer(operand):
                try:
                    found.integers[key] = plain_int(operand)
                except Exception:  # pragma: no cover - an int subclass from another world
                    pass
        found._render()
        return found

    @classmethod
    def of(
        cls, d: Mapping[str, Any], keys: Tuple[str, ...], default: Optional[str] = None
    ) -> Format:
        """The format of a built or recorded check *d*, for its operand fields *keys*. Reads
        only plain data, ignores anything malformed (a hand-built descriptor) and never
        raises."""
        try:
            radix, width, fields = d.get("radix"), d.get("width"), d.get("fields")
            if radix is None and width is None and fields is None and default is None:
                return PLAIN
            return cls._of(d, keys, radix, width, fields, default)
        except Exception:
            return PLAIN

    @classmethod
    def _of(
        cls,
        d: Mapping[str, Any],
        keys: Tuple[str, ...],
        radix: object,
        width: object,
        fields: object,
        default: Optional[str],
    ) -> Format:
        chosen_radix = radix if isinstance(radix, str) and radix in RADIXES else None
        chosen_width = width if is_integer(width) and 1 <= width <= MAX_BITS else None  # type: ignore[operator]
        try:
            chosen_fields = require_fields(fields, "fields")
        except (TypeError, ValueError):
            chosen_fields = None
        if chosen_width is not None and chosen_fields:
            chosen_fields = _inside(chosen_fields, chosen_width)  # type: ignore[arg-type]
        if chosen_radix is None and (chosen_width is not None or chosen_fields):
            chosen_radix = "hex"
        chosen_radix = chosen_radix or default
        if chosen_radix is None:
            return PLAIN
        found = cls(chosen_radix, chosen_width, chosen_fields)  # type: ignore[arg-type]
        values = d.get("format_values")
        rendered = d.get("rendered")
        for key in keys:
            number: Any = values.get(key) if isinstance(values, Mapping) else None
            if not is_integer(number):
                number = d.get(key)
            if is_integer(number):
                found.integers[key] = plain_int(number)
        found._render()
        if isinstance(rendered, Mapping):
            # The text the check showed when it was built, for an operand that is not an
            # integer any more (a record keeps an int enum member as its repr). An operand
            # that is one is rendered from it: a copy of a check with another value must
            # not show the old one.
            for key in keys:
                text = rendered.get(key)
                if isinstance(text, str) and key not in found.integers:
                    found.texts[key] = escape(text)
        return found

    def _render(self) -> None:
        if self.radix is None:
            return
        for key, number in self.integers.items():
            text = render_int(number, self.radix, self.width)
            if text is not None:
                self.texts[key] = text

    def render(self, number: int) -> Optional[str]:
        """*number* in this format, or ``None`` when it has none or the number is too wide."""
        return None if self.radix is None else render_int(number, self.radix, self.width)

    def shown(self, key: str, value: object, units: Optional[str] = None) -> str:
        """The operand *key* as the check shows it: in the format, with its units, when it
        is an integer; else as any other value."""
        text = self.texts.get(key)
        return render_value(value, units) if text is None else display(text) + units_text(units)

    def shown_number(self, number: int) -> Optional[str]:
        """*number* in this format as a description or a detail shows it (see
        :func:`display`), or ``None`` as :meth:`render`."""
        text = self.render(number)
        return None if text is None else display(text)

    def difference(self, mask: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """The ``bit_diff`` of ``actual`` and ``expected``, when both are integers, a format
        is in force and some bit differs."""
        actual, expected = self.integers.get("actual"), self.integers.get("expected")
        if self.radix is None or actual is None or expected is None:
            return None
        return bit_diff(actual, expected, mask, self.fields)

    def clause(self, d: Mapping[str, Any], mask: Optional[int] = None) -> str:
        """The bit difference of *d* as a detail shows it: computed from its integers when
        it has both (so a copy of a check with another value shows its own), else the one
        stored in it. ``""`` without one."""
        if "actual" in self.integers and "expected" in self.integers:
            try:
                return difference_clause(self.difference(mask))
            except Exception:  # pragma: no cover - integers of a hand-built descriptor
                return ""
        return difference_clause(d.get("bit_diff"))

    def stamp(self, descriptor: Any, *, values: bool = True) -> None:
        """Add what the format says to *descriptor*, a check being built: ``radix``,
        ``width``, ``fields``, ``rendered``, ``format_error`` and, with *values*, the
        ``format_values`` a record stores as the operands."""
        if self.radix is not None:
            descriptor["radix"] = self.radix
            if self.width is not None:
                descriptor["width"] = self.width
            if self.fields:
                descriptor["fields"] = {name: list(bits) for name, bits in self.fields.items()}
            if self.texts:
                descriptor["rendered"] = dict(self.texts)
        if values and self.foreign:
            descriptor["format_values"] = dict(self.foreign)
        if self.error is not None:
            descriptor["format_error"] = self.error


#: The format of a check without one: nothing is shown differently and nothing is stored.
PLAIN = Format()


def _inside(fields: Dict[str, List[int]], width: int) -> Optional[Dict[str, List[int]]]:
    """The *fields* whose bits are all below *width*; ``None`` when none is."""
    return {name: bits for name, bits in fields.items() if bits[0] < width} or None


def recorded(
    d: Mapping[str, Any],
    keys: Tuple[str, ...] = (),
    *,
    passed: Optional[bool] = None,
    diff: bool = False,
    default: Optional[str] = None,
) -> Mapping[str, Any]:
    """The fields of *d* as a record copies them. Never raises.

    The integer of a register object (``format_values``) takes the object's place, so that
    records hold numbers. For a check with a format (*keys*: its operand fields, *default*:
    the radix of a type that always has one), ``rendered`` is the text of the integers the
    check has now, and with *diff* so is ``bit_diff``, which a check that *passed* does not
    get: nothing it compared differs.
    """
    fields = d
    if "format_values" in d:
        values = d["format_values"]
        fields = {key: value for key, value in d.items() if key != "format_values"}
        if isinstance(values, Mapping):
            for key, number in values.items():
                if key in fields and is_integer(number):
                    fields[key] = number
    if not keys:
        return fields
    try:
        return _refreshed(fields, keys, passed, diff, default)
    except Exception:  # a hand-built descriptor: its fields as they are
        return fields


def _refreshed(
    fields: Mapping[str, Any],
    keys: Tuple[str, ...],
    passed: Optional[bool],
    diff: bool,
    default: Optional[str],
) -> Mapping[str, Any]:
    fmt = Format.of(fields, keys, default)
    if fmt.radix is None:
        return fields
    out = dict(fields)
    stored = out.get("rendered")
    texts = dict(stored) if isinstance(stored, Mapping) else {}
    for key in keys:
        if key in fmt.integers:
            texts.pop(key, None)
            text = fmt.render(fmt.integers[key])
            if text is not None:
                texts[key] = text
    if texts:
        out["rendered"] = texts
    else:
        out.pop("rendered", None)
    if diff:
        if passed:
            out.pop("bit_diff", None)
        elif "actual" in fmt.integers and "expected" in fmt.integers:
            mask = out.get("mask") if "mask" in keys else None
            usable = mask is None or (is_integer(mask) and mask >= 0)
            difference = fmt.difference(mask) if usable else None
            if difference is None:
                out.pop("bit_diff", None)
            else:
                out["bit_diff"] = difference
    return out


def render_number(value: object, radix: object, width: object, units: Optional[str]) -> str:
    """A number of a record whose check had the format *radix*/*width*, with its units."""
    if is_integer(value) and isinstance(radix, str) and radix in RADIXES:
        bits = width if is_integer(width) and 1 <= width <= MAX_BITS else None  # type: ignore[operator]
        text = render_int(plain_int(value), radix, bits)  # type: ignore[arg-type]
        if text is not None:
            return text + units_text(units)
    return render_value(value, units)
