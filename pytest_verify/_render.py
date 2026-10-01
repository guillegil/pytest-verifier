"""Exception-safe rendering of user values, and JSON-safe snapshots of them.

Checks receive arbitrary user objects. Their ``str()``, ``repr()`` and ``format()`` can raise
(a closed connection, a detached ORM row, an ``int`` above the int-to-str digit limit), and
most of them are not JSON-serializable. Everything that turns a user value into text or JSON
goes through this module, so a misbehaving value can never stop a test or crash a report.
"""
from __future__ import annotations

import enum
import math
import reprlib
import sys
from typing import Any

#: Most nodes a snapshot copies before it falls back to a bounded repr string.
_SNAPSHOT_NODE_LIMIT = 10_000

#: Deepest nesting a snapshot copies before it falls back to a bounded repr string.
_SNAPSHOT_MAX_DEPTH = 50

_bounded = reprlib.Repr()
_bounded.maxlevel = 6
_bounded.maxlist = _bounded.maxtuple = _bounded.maxdict = 100
_bounded.maxset = _bounded.maxfrozenset = _bounded.maxdeque = _bounded.maxarray = 100
_bounded.maxstring = _bounded.maxother = 1000
_bounded.maxlong = 1000


def _int_digits_estimate(value: int) -> int:
    """Decimal digits of ``value``, computed without converting it to a string."""
    return int(abs(value).bit_length() * 0.30102999566398120) + 1


def _placeholder(value: object, operation: str, exc: BaseException) -> str:
    if isinstance(value, int) and isinstance(exc, ValueError):
        return f"<int with about {_int_digits_estimate(value)} digits>"
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


def describe_error(exc: BaseException) -> str:
    """Render an exception as ``"TypeError: message"`` (or just the type without a message)."""
    message = safe_str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


class _TooBig(Exception):
    """A value exceeded the snapshot node or depth budget."""


def snapshot(value: Any) -> Any:
    """Return a JSON-safe copy of ``value``.

    JSON-native data (``None``, ``bool``, ``int``, finite ``float``, ``str``, lists, tuples and
    dicts with string keys) is copied as such, so it no longer references the caller's object.
    Non-finite floats become ``"nan"``, ``"inf"`` or ``"-inf"``. Anything else, including enum
    members, sets, bytes, ``Decimal`` and custom objects, becomes its (bounded) ``repr``. The
    result always passes ``json.dumps(..., allow_nan=False)``.
    """
    budget = [_SNAPSHOT_NODE_LIMIT]
    try:
        return _snapshot(value, budget, 0)
    except Exception:  # over budget, or a subclass whose __str__/__float__/__iter__ raises
        return bounded_repr(value)


def _snapshot(value: Any, budget: list[int], depth: int) -> Any:
    budget[0] -= 1
    if budget[0] < 0 or depth > _SNAPSHOT_MAX_DEPTH:
        raise _TooBig
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, enum.Enum):
        return safe_repr(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return _snapshot_int(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_snapshot(item, budget, depth + 1) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {str(key): _snapshot(item, budget, depth + 1) for key, item in value.items()}
    return bounded_repr(value)


def _snapshot_int(value: int) -> Any:
    limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
    if limit and _int_digits_estimate(value) >= limit:
        return f"<int with about {_int_digits_estimate(value)} digits>"
    return int(value)
