"""Exception-safe rendering of user values, and JSON-safe snapshots of them.

Checks receive arbitrary user objects. Their ``str()``, ``repr()`` and ``format()`` can raise
(a closed connection, a detached ORM row, an ``int`` above the int-to-str digit limit), and
most of them are not JSON-serializable. Everything that turns a user value into text or JSON
goes through this module, so a misbehaving value can never stop a test or crash a report.
"""
from __future__ import annotations

import collections
import enum
import functools
import math
import numbers
import re
import reprlib
import sys
from decimal import Decimal
from typing import Any, Dict, Optional

#: Most nodes a snapshot copies before it falls back to a bounded repr string.
_SNAPSHOT_NODE_LIMIT = 10_000

#: Longest string a snapshot copies; longer text is cut, ending in ``...``.
SNAPSHOT_TEXT_LIMIT = 10_000

#: A string in a snapshot costs one node more per this many characters.
_CHARACTERS_PER_NODE = 100

#: Ints this small are copied without checking them against the int-to-str digit limit.
_SMALL_INT = 10**18

#: Deepest nesting a snapshot copies before it falls back to a bounded repr string.
_SNAPSHOT_MAX_DEPTH = 50

#: Longest text :func:`bounded_format` returns.
_TEXT_LIMIT = 1000

#: Containers whose ``format()`` is their ``repr()``, rendered with the bounded repr instead.
_CONTAINERS = (list, tuple, dict, set, frozenset, collections.deque)

#: Longest rendering of one value in a description, a detail or a summary line.
VALUE_LIMIT = 240

#: Longest rendering of a unit label.
_UNITS_LIMIT = 40

_bounded = reprlib.Repr()
_bounded.maxlevel = 6
_bounded.maxlist = _bounded.maxtuple = _bounded.maxdict = 100
_bounded.maxset = _bounded.maxfrozenset = _bounded.maxdeque = _bounded.maxarray = 100
_bounded.maxstring = _bounded.maxother = 1000
_bounded.maxlong = 1000

#: The ``repr`` used to show a value to a person: about one line.
_display = reprlib.Repr()
_display.maxlevel = 4
_display.maxlist = _display.maxtuple = _display.maxdict = 20
_display.maxset = _display.maxfrozenset = _display.maxdeque = _display.maxarray = 20
_display.maxstring = _display.maxother = _display.maxlong = VALUE_LIMIT


def _escapes() -> Dict[int, str]:
    named = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    # Control characters and line separators; lone surrogates, which no stream can encode;
    # and U+FFFE/U+FFFF, which XML (a junit report) does not allow.
    codes = [*range(0x20), 0x7F, *range(0x80, 0xA0), 0x2028, 0x2029, *range(0xD800, 0xE000)]
    codes += [0xFFFE, 0xFFFF]
    return {
        code: named.get(chr(code), f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}")
        for code in codes
    }


_ESCAPES = _escapes()

#: A line break and the indentation around it, in a multi-line ``repr`` such as numpy's.
_LINE_BREAK = re.compile(r"[ \t]*(?:\r\n|[\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029])\s*")


def _int_digits_estimate(value: int) -> int:
    """Decimal digits of ``value``, computed without converting it to a string."""
    return int(abs(value).bit_length() * 0.30102999566398120) + 1


def _placeholder(value: object, operation: str, exc: BaseException) -> str:
    try:
        if isinstance(value, int) and isinstance(exc, ValueError):
            return f"<int with about {_int_digits_estimate(value)} digits>"
    except Exception:  # a proxy whose type check raises
        pass
    return f"<{type(value).__name__} object: {operation}() raised {type(exc).__name__}>"


def safe_str(value: object) -> str:
    """``str(value)``, or a placeholder if that raises."""
    try:
        return str(value)
    except Exception as exc:
        return _placeholder(value, "str", exc)


def safe_format(value: object) -> str:
    """``format(value)`` (what an f-string uses), or a placeholder if that raises."""
    try:
        return format(value)
    except Exception as exc:
        return _placeholder(value, "format", exc)


def safe_repr(value: object) -> str:
    """``repr(value)``, or a placeholder if that raises."""
    try:
        return repr(value)
    except Exception as exc:
        return _placeholder(value, "repr", exc)


def bounded_repr(value: object) -> str:
    """A ``repr`` that truncates large containers and strings, and never raises."""
    try:
        return _bounded.repr(value)
    except Exception:
        return safe_repr(value)


def bounded_format(value: object) -> str:
    """:func:`safe_format`, with large containers and long text shortened.

    Built-in containers get the bounded ``repr`` (what ``format`` would show, with the items
    past the first 100 elided), and any other text longer than 1000 characters is cut, so one
    huge value cannot blow up a summary or a recorded ``detail``.
    """
    if isinstance(value, _CONTAINERS):
        return bounded_repr(value)
    text = safe_format(value)
    return text if len(text) <= _TEXT_LIMIT else text[: _TEXT_LIMIT - 3] + "..."


def escape(text: str) -> str:
    """*text* on one line: line breaks and other control characters become escapes (``\\n``),
    so user text can never start a new line of a summary. So do lone surrogates and the
    noncharacters U+FFFE and U+FFFF, so the text can be written anywhere."""
    return text.translate(_ESCAPES)


def utf8_safe(text: str) -> str:
    """*text* with lone surrogates (``os.fsdecode`` makes them from undecodable file names) as
    ``\\udcxx`` escapes, so it can be encoded as UTF-8: written to a file or sent by
    pytest-xdist."""
    if text.isascii():
        return text
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "backslashreplace").decode("utf-8")
    return text


def one_line(text: str) -> str:
    """A ``repr`` on one line: each line break, with the indentation around it, becomes a space."""
    return escape(_LINE_BREAK.sub(" ", text))


def shorten(text: str, limit: int = VALUE_LIMIT) -> str:
    """*text* cut to *limit* characters, ending in ``...`` when it was cut."""
    return text if len(text) <= limit else text[: limit - 3] + "..."


def is_number(value: object) -> bool:
    """A number shown with ``str()`` and its units: not a ``bool`` and not an enum member.
    Never raises."""
    try:
        return isinstance(value, (numbers.Number, Decimal)) and not isinstance(
            value, (bool, enum.Enum)
        )
    except Exception:  # a proxy whose type check raises
        return False


@functools.lru_cache(maxsize=64)
def _units(units: str) -> str:
    return shorten(escape(units), _UNITS_LIMIT)


def units_text(units: object) -> str:
    """A unit label as it follows a number: on one line and short. ``""`` for ``None``."""
    if units is None:
        return ""
    if type(units) is str:
        return _units(units)
    return shorten(escape(safe_str(units)), _UNITS_LIMIT)


def render_value(value: object, units: Optional[str] = None) -> str:
    """How a checked value appears in descriptions, details and summaries. Never raises.

    Numbers read naturally, with their units: ``3.3V``. Enum members show as ``Mode.ACTIVE``,
    and numeric ones add their value: ``Gain.LOW (10dB)``. Everything else gets a bounded
    ``repr`` without units, so strings are quoted: ``'3.3'``. The result is one line of at most
    about :data:`VALUE_LIMIT` characters (plus the units).
    """
    kind = type(value)
    if kind is float:  # the common cases first: their text is short and has no escapes
        return format(value) + units_text(units)
    if kind is int:
        return shorten(safe_format(value)) + units_text(units)
    if is_number(value):
        return shorten(escape(safe_format(value))) + units_text(units)
    try:
        member = isinstance(value, enum.Enum)
    except Exception:  # a proxy whose type check raises
        member = False
    if member:
        try:
            return _render_member(value, units)
        except Exception:  # an enum whose name or value raises
            pass
    return _display_repr(value)


def _display_repr(value: object) -> str:
    try:
        text = _display.repr(value)
    except Exception:
        text = safe_repr(value)
    return shorten(one_line(text))


def _render_member(member: object, units: Optional[str]) -> str:
    """``Class.NAME``, with the value of a numeric member such as an ``IntEnum``:
    ``Gain.LOW (10dB)``."""
    name = getattr(member, "name", None)
    if isinstance(name, str):
        text = shorten(escape(f"{type(member).__name__}.{name}"))
    else:  # a combination of flags without a name, before Python 3.11
        text = _display_repr(member)
    if isinstance(member, numbers.Number):
        text += f" ({render_value(getattr(member, 'value', None), units)})"
    return text


def render_text(value: object, limit: int = VALUE_LIMIT) -> str:
    """User text such as a name, a label or a message, on one line and at most *limit* long."""
    return shorten(escape(safe_str(value)), limit)


def describe_error(exc: BaseException) -> str:
    """Render an exception as ``"TypeError: message"`` (or just the type without a message)."""
    message = safe_str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


class _TooBig(Exception):
    """A value exceeded the snapshot node or depth budget."""


def snapshot(value: Any, limit: int = _SNAPSHOT_NODE_LIMIT, text_limit: int = 0) -> Any:
    """Return a JSON-safe copy of ``value``.

    JSON-native data (``None``, ``bool``, ``int``, finite ``float``, ``str``, lists, tuples and
    dicts with string keys) is copied as such, so it no longer references the caller's object.
    Non-finite floats become ``"nan"``, ``"inf"`` or ``"-inf"``. Anything else, including enum
    members, sets, bytes, ``Decimal`` and custom objects, becomes its (bounded) ``repr``. The
    result always passes ``json.dumps(..., allow_nan=False)``. A value with more than *limit*
    nodes becomes its bounded ``repr`` too; long text counts as several nodes. Strings are cut
    to :data:`SNAPSHOT_TEXT_LIMIT` characters, or to *text_limit* when given (a preview rather
    than a copy).
    """
    budget = [limit]
    try:
        return _snapshot(value, budget, 0, text_limit)
    except Exception:  # over budget, or a subclass whose __str__/__float__/__iter__ raises
        text = utf8_safe(bounded_repr(value))
        return shorten(text, text_limit) if text_limit else text


def _snapshot(value: Any, budget: list[int], depth: int, text_limit: int) -> Any:
    budget[0] -= 1
    if budget[0] < 0 or depth > _SNAPSHOT_MAX_DEPTH:
        raise _TooBig
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, enum.Enum):
        return safe_repr(value)
    if isinstance(value, str):
        return _snapshot_text(str(value), budget, text_limit)
    if isinstance(value, int):
        return _snapshot_int(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_snapshot(item, budget, depth + 1, text_limit) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {
            _snapshot_text(str(key), budget, text_limit): (
                _snapshot(item, budget, depth + 1, text_limit)
            )
            for key, item in value.items()
        }
    text = utf8_safe(bounded_repr(value))
    return shorten(text, text_limit) if text_limit else text


def _snapshot_text(text: str, budget: list[int], text_limit: int) -> str:
    """*text* cut to the snapshot's limit, charged to its node *budget* by length."""
    text = shorten(text, text_limit or SNAPSHOT_TEXT_LIMIT)
    budget[0] -= len(text) // _CHARACTERS_PER_NODE
    if budget[0] < 0:
        raise _TooBig
    return utf8_safe(text)


def _snapshot_int(value: int) -> Any:
    if -_SMALL_INT < value < _SMALL_INT:  # the common case: far below any digit limit
        return int(value)
    limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
    if limit and _int_digits_estimate(value) >= limit:
        return f"<int with about {_int_digits_estimate(value)} digits>"
    return int(value)
