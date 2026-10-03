"""Limits tables: ``verify.limits`` checks measurements against a table of limits, and
``load_limits`` reads such a table from a CSV file.

A table maps each check's name to its row: the arguments of the check after the measured
value, and optionally which check it is (``"check"``). Without ``"check"``, the limits say:
``low`` and ``high`` make a ``between``, one of them a ``greater_equal``/``less_equal`` (or
``greater``/``less`` with ``inclusive=False``), ``expected`` with a tolerance an ``approx``,
and ``expected`` alone (not a float) an ``equal``.

Every row is checked when the table is read (``load_limits``) and again, built with its
measurement, before any check is recorded: a row that is wrong raises and nothing is recorded.
Then the checks are recorded together, so that a required table stops the test only once
every row is in the summary.
"""
from __future__ import annotations

import codecs
import csv
import difflib
import io
import math
import numbers
import os
import re
import typing
from collections.abc import Collection, Iterator
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple, Union

from ._checks import REGISTRY
from ._descriptors import CheckDescriptor, is_real, plain_text, unwrap
from ._render import render_text, safe_repr

if typing.TYPE_CHECKING:  # pragma: no cover
    from ._verify import Sink


class LimitRow(typing.TypedDict, total=False):
    """One row of a limits table: the arguments of a check, and which check it is.

    Every key is optional; ``check`` names the check method (``"approx"``, ``"between"``, ...)
    and is inferred from the limits when it is missing. ``source`` (any text, such as
    ``"limits.csv:12"``, which :func:`load_limits` sets) is kept on the record as
    ``limit_source``. See :meth:`Verify.limits`.
    """

    check: str
    expected: Any
    abs_tol: Optional[float]
    rel_tol: Optional[float]
    threshold: Any
    low: Any
    high: Any
    inclusive: bool
    units: Optional[str]
    needle: Any
    pattern: Union[str, "re.Pattern[str]"]
    expected_type: Any
    source: str


#: The checks a row can name: the arguments each takes after the measured value (required
#: ones, then optional ones) and its ``check_type``.
ROW_CHECKS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...], str]] = {
    "equal": (("expected",), ("units",), "equal"),
    "not_equal": (("expected",), ("units",), "not_equal"),
    "approx": (("expected",), ("abs_tol", "rel_tol", "units"), "approx"),
    "greater": (("threshold",), ("units",), "greater"),
    "greater_equal": (("threshold",), ("units",), "greater_equal"),
    "less": (("threshold",), ("units",), "less"),
    "less_equal": (("threshold",), ("units",), "less_equal"),
    "between": (("low", "high"), ("inclusive", "units"), "between"),
    "is_true": ((), (), "true"),
    "is_false": ((), (), "false"),
    "is_none": ((), (), "is_none"),
    "is_not_none": ((), (), "is_not_none"),
    "contains": (("needle",), (), "contains"),
    "not_contains": (("needle",), (), "not_contains"),
    "matches": (("pattern",), (), "matches"),
    "length": (("expected",), (), "length"),
    "is_instance": (("expected_type",), (), "is_instance"),
}

#: Other spellings of a row's ``check``: the ``check_type`` of a record.
_CHECK_ALIASES = {"true": "is_true", "false": "is_false"}

#: The limit that can stand for the ``threshold`` of an ordering check.
_THRESHOLD_ALIASES = {
    "greater": "low",
    "greater_equal": "low",
    "less": "high",
    "less_equal": "high",
}

#: Every argument some row takes.
ARGUMENTS = frozenset(
    argument
    for required, optional, _ in ROW_CHECKS.values()
    for argument in (*required, *optional, "low", "high")
)

#: Arguments that are always numbers.
_NUMBERS = frozenset({"low", "high", "threshold", "abs_tol", "rel_tol"})
#: Checks whose ``expected`` is a number.
_NUMBER_EXPECTED = frozenset({"approx", "length"})

ON_MISSING = ("fail", "ignore")

#: The error of a row whose measurement is missing.
NOT_MEASURED = "not measured"


class CellText(str):
    """The text of a CSV cell that holds the ``expected`` of ``equal``/``not_equal`` or the
    ``needle`` of ``contains``/``not_contains``: it becomes a number or text when it is
    checked, as the measurement is (see :func:`_typed_cell`)."""

    #: Whether the file reads ``3,3`` as a number (semicolon-separated files).
    comma = False


class CellNumber(float):
    """A CSV cell of a numeric limit (``low``, ``high``, ``threshold``, a tolerance, the
    ``expected`` of ``approx``) that is not a whole number: a ``float`` that keeps the cell's
    text, so that it is checked as a ``Decimal`` or ``Fraction`` from its digits against one
    (``3.2`` equals ``Decimal("3.2")``), and as a plain ``float`` against anything else."""

    #: The cell, and whether the file reads ``3,3`` as a number.
    text = ""
    comma = False

    def like(self, value: Any) -> Any:
        """The limit to check *value* against."""
        return _like(float(self), self.text, self.comma, value)


class Row:
    """One row, checked: its check method and the arguments after the measured value."""

    __slots__ = ("name", "method", "args", "kwargs", "source", "context")

    def __init__(
        self,
        name: str,
        method: str,
        args: Tuple[Any, ...],
        kwargs: Dict[str, Any],
        source: Optional[str],
        context: str,
    ) -> None:
        self.name = name
        self.method = method
        self.args = args
        self.kwargs = kwargs
        self.source = source
        self.context = context

    def build(self, value: Any) -> CheckDescriptor:
        """The row's check of *value*; raises ``TypeError``/``ValueError`` naming the row.

        A :class:`CellText` argument becomes what *value* is compared as; when it cannot be
        one, the check fails with an ``error`` saying why. A :class:`CellNumber` becomes a
        ``Decimal`` or ``Fraction`` against one, else a plain ``float``.
        """
        problem: Optional[str] = None
        args = list(self.args)
        for index, argument in enumerate(args):
            if isinstance(argument, CellText):
                args[index], problem = _typed_cell(argument, value, self.method)
            elif isinstance(argument, CellNumber):
                args[index] = argument.like(value)
        kwargs = {
            key: argument.like(value) if isinstance(argument, CellNumber) else argument
            for key, argument in self.kwargs.items()
        }
        check = REGISTRY[ROW_CHECKS[self.method][2]]
        try:
            descriptor: CheckDescriptor = check.build(  # type: ignore[attr-defined]
                value, *args, name=self.name, **kwargs
            )
        except (TypeError, ValueError) as exc:
            raise type(exc)(f"{self.context}: {exc}") from None
        if problem is not None:
            descriptor["error"] = problem
        if self.source is not None:
            descriptor["limit_source"] = self.source
        return descriptor


def _context(name: object) -> str:
    return f"limits() row {safe_repr(name)}"


def _finite(value: object) -> bool:
    """Whether *value* is a real number (not a ``bool``) that is neither NaN nor infinite."""
    if not is_real(value):
        return False
    if isinstance(value, numbers.Rational):  # int, Fraction: of any size, never a float
        return True
    if isinstance(value, Decimal):
        return value.is_finite()
    try:
        return math.isfinite(value)  # type: ignore[arg-type]
    except OverflowError:  # a real number too large for a float
        return True
    except (TypeError, ValueError):
        return False


def _not_a_number(argument: str, value: object) -> str:
    hint = ""
    if isinstance(value, str):
        hint = " (text: write the number, and the unit in 'units')"
    elif is_real(value):
        hint = " (leave the limit out for no limit)"
    return f"{argument} must be a finite number, got {safe_repr(value)}{hint}"


def _infer(given: Dict[str, Any], context: str) -> str:
    """The check a row without ``"check"`` makes, from its limits. Takes ``inclusive`` out of
    a row with one bound, where it picks ``greater``/``less``."""
    if "low" in given and "high" in given:
        return "between"
    if "low" in given or "high" in given:
        inclusive = given.pop("inclusive", True)
        if "low" in given:
            return "greater_equal" if inclusive else "greater"
        return "less_equal" if inclusive else "less"
    if "expected" in given:
        if "abs_tol" in given or "rel_tol" in given:
            return "approx"
        expected = given["expected"]
        if isinstance(expected, float) or (
            isinstance(expected, CellText)
            and isinstance(_number(expected, expected.comma), float)
        ):
            shown = safe_repr(str(expected) if isinstance(expected, str) else expected)
            raise ValueError(
                f"{context}: expected {shown} is a float and there is no tolerance: "
                "give abs_tol or rel_tol to compare a measured number (approx), or say "
                "check='equal' for an exact match, such as a version text"
            )
        return "equal"
    raise ValueError(
        f"{context} says neither which check it is ('check') nor a limit (low, high or "
        f"expected); got {', '.join(sorted(given)) or 'nothing'}"
    )


def parse_row(name: object, row: object, context: Optional[str] = None) -> Row:
    """Check one row; raises ``TypeError``/``ValueError`` naming it (*context*, by default
    ``limits() row 'name'``). The row is not changed."""
    if not isinstance(name, str):
        raise TypeError(f"limits() row names must be str, got {safe_repr(name)}")
    if not name.strip():
        raise ValueError("limits() row names must not be empty")
    name = plain_text(name)
    context = context or _context(name)
    if not isinstance(row, Mapping):
        raise TypeError(f"{context} must be a mapping of arguments, got {type(row).__name__}")
    given: Dict[str, Any] = {}
    for key, value in row.items():
        if not isinstance(key, str):
            raise TypeError(f"{context}: argument names must be str, got {safe_repr(key)}")
        if value is not None:  # None, like an empty cell, means not given
            given[key] = value
    source = given.pop("source", None)
    if source is not None and not isinstance(source, str):
        raise TypeError(f"{context}: source must be text, got {safe_repr(source)}")
    if "inclusive" in given and not isinstance(given["inclusive"], bool):
        raise TypeError(
            f"{context}: inclusive must be True or False, got {safe_repr(given['inclusive'])}"
        )
    method = given.pop("check", None)
    if method is None:
        method = _infer(given, context)
    elif isinstance(method, str):
        method = _CHECK_ALIASES.get(method.strip(), method.strip())
    if not isinstance(method, str) or method not in ROW_CHECKS:
        raise ValueError(
            f"{context}: unknown check {safe_repr(method)}; one of: {', '.join(ROW_CHECKS)}"
        )
    _check_numbers(method, given, context)  # before low/high become the threshold
    alias = _THRESHOLD_ALIASES.get(method)
    if alias is not None and alias in given:
        if "threshold" in given:
            raise TypeError(f"{context}: give threshold or {alias}, not both")
        given["threshold"] = given.pop(alias)
    required, optional, _ = ROW_CHECKS[method]

    def spelt(arguments: typing.Iterable[str]) -> str:
        return ", ".join(
            f"{argument} (or {alias})" if argument == "threshold" and alias else argument
            for argument in arguments
        )

    unknown = sorted(set(given) - set(required) - set(optional))
    if unknown:
        raise TypeError(
            f"{context}: {method}() takes {spelt((*required, *optional)) or 'no limits'}; "
            f"not: {', '.join(unknown)}"
        )
    missing = [argument for argument in required if argument not in given]
    if missing:
        raise TypeError(f"{context}: {method}() needs {spelt(missing)}")
    if method == "matches":
        pattern = given["pattern"]
        if not isinstance(pattern, (str, re.Pattern)):
            raise TypeError(
                f"{context}: pattern must be text or a compiled pattern, got "
                f"{safe_repr(pattern)}"
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(
                f"{context}: pattern is not a valid regular expression: {exc}"
            ) from None
    args = tuple(given[argument] for argument in required)
    kwargs = {argument: given[argument] for argument in optional if argument in given}
    parsed = Row(name, method, args, kwargs, source, context)
    parsed.build(None)  # the check's own argument checks (tolerances, low > high)
    return parsed


def _check_numbers(method: str, given: Mapping[str, Any], context: str) -> None:
    for argument, value in given.items():
        if argument == "expected" and method == "length":
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise TypeError(
                    f"{context}: expected must be a whole number of items, "
                    f"got {safe_repr(value)}"
                )
        elif argument in _NUMBERS or (argument == "expected" and method in _NUMBER_EXPECTED):
            if not _finite(value):
                # The right type with a wrong value (NaN, infinity) is a ValueError.
                error = ValueError if is_real(value) else TypeError
                raise error(f"{context}: {_not_a_number(argument, value)}")
    rel_tol = given.get("rel_tol")
    if rel_tol is not None and not 0 <= rel_tol < 1:
        raise ValueError(
            f"{context}: rel_tol is a fraction of expected (0.02 is 2%), so it must be at "
            f"least 0 and below 1; got {safe_repr(rel_tol)}"
        )


def _typed_cell(cell: CellText, value: Any, method: str) -> Tuple[Any, Optional[str]]:
    """``(argument, problem)``: a CSV cell as the type it is compared as. Text against text
    (and when nothing was measured), a number against a number, ``True``/``False`` against a
    ``bool``; a ``needle`` as what the haystack holds. A cell that cannot be that type, or a
    measurement a CSV cell cannot stand for, leaves the text and a problem."""
    text = str(cell)
    measured = "the measurement is"
    if method in ("contains", "not_contains"):
        if isinstance(value, Iterator):
            return text, (
                f"a CSV needle is text or a number, and a {type(value).__name__} can be read "
                "only once: pass a list"
            )
        kind = _kind_of_items(value)
        if kind is None:
            return text, (
                f"a CSV needle is text or a number, and {type(value).__name__} holds "
                "something else, or a mix: check it with a row of a Python table"
            )
        if not (value is None or isinstance(value, str)):
            measured = f"the measurement ({type(value).__name__}) holds"
            value = next(iter(value), None)  # an item, for _like (one kind, so any item)
    else:
        kind = _kind(value)
        if kind is None:
            return text, (
                f"a CSV limit is text or a number, and the measurement is "
                f"{type(value).__name__}: check it with a row of a Python table"
            )
    if kind == "text":
        return text, None
    if kind == "bool":
        flag = _boolean(text)
        if flag is None:
            return text, f"the limit {text!r} is not true or false, and {measured} a bool"
        return flag, None
    number = _number(text, cell.comma)
    if number is None:
        return text, f"the limit {text!r} is text, and {measured} a number"
    return _like(number, text, cell.comma, value), None


def _like(number: Union[int, float], text: str, comma: bool, value: Any) -> Any:
    """A cell's *number* as the type of *value*, a measured number, when that type is exact:
    a ``Decimal`` or a ``Fraction`` from the cell's digits, so that ``1.10`` equals
    ``Decimal("1.10")``."""
    if not isinstance(value, (Decimal, Fraction)) or isinstance(number, int):
        return number
    digits = text.replace(",", ".") if comma and "." not in text else text
    try:
        return Decimal(digits) if isinstance(value, Decimal) else Fraction(digits)
    except (InvalidOperation, ValueError):  # pragma: no cover - _number accepted it
        return number


def _kind(value: Any) -> Optional[str]:
    if value is None or isinstance(value, str):
        return "text"
    if isinstance(value, bool):
        return "bool"
    if is_real(value):
        return "number"
    return None


def _kind_of_items(haystack: Any) -> Optional[str]:
    """What a ``needle`` cell is compared as, from the haystack: text in a ``str`` (and when
    nothing was measured), else the one kind of the items of a collection that can be read
    again (not an iterator, and not bytes)."""
    if haystack is None or isinstance(haystack, str):
        return "text"
    if (
        not isinstance(haystack, Collection)
        or isinstance(haystack, (Iterator, bytes, bytearray, memoryview))
    ):
        return None
    try:
        kinds = {_kind(item) for item in haystack}
    except Exception:  # a collection that cannot be read, such as a 0-d numpy array
        return None
    if not kinds:
        return "text"  # empty: no needle is in it, whatever its type
    if len(kinds) == 1 and None not in kinds:
        return kinds.pop()
    return None


def _key_text(key: object) -> str:
    """The row name a measurement key stands for: a ``str`` as is, an ``int`` as its decimal
    string, an enum member as its value."""
    value = unwrap(key)
    if isinstance(value, str):
        return plain_text(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(int(value))
    raise TypeError(
        "limits() measurement names must be str, int or enum members, as the table's row names "
        f"are text; got {safe_repr(key)}"
    )


def _by_name(measurements: Mapping[Any, Any]) -> Dict[str, Tuple[Any, Any]]:
    """``{row name: (key, value)}`` of the measurements."""
    found: Dict[str, Tuple[Any, Any]] = {}
    for key, value in measurements.items():
        text = _key_text(key)
        if text in found:
            raise ValueError(
                f"limits() measurements {safe_repr(found[text][0])} and {safe_repr(key)} both "
                f"stand for {text!r}"
            )
        found[text] = (key, value)
    return found


def _not_measured(name: str, unused: List[str]) -> str:
    """The error of a row with no measurement, naming an unused key that looks like it, in
    any case."""
    folded: Dict[str, str] = {}
    for key in unused:
        folded.setdefault(key.casefold(), key)
    close = difflib.get_close_matches(name.casefold(), list(folded), n=1, cutoff=0.6)
    hint = f"; is it '{render_text(folded[close[0]])}'?" if close else ""
    return f"{NOT_MEASURED}: the measurements have no '{render_text(name)}'{hint}"


def limit_checks(
    sink: Sink,
    measurements: Mapping[Any, Any],
    table: Mapping[str, Mapping[str, Any]],
    on_missing: str,
) -> Dict[str, CheckDescriptor]:
    """``verify.limits``: one check per row, in table order, recorded through *sink*."""
    if not isinstance(measurements, Mapping):
        raise TypeError(
            "limits() measurements must be a mapping of names to values, got "
            f"{type(measurements).__name__}"
        )
    if on_missing not in ON_MISSING:
        raise ValueError(
            f"limits() on_missing must be 'fail' or 'ignore', got {safe_repr(on_missing)}"
        )
    if not isinstance(table, Mapping):
        raise TypeError(
            "limits() table must be a mapping of check names to rows, such as the result of "
            f"load_limits(); got {type(table).__name__}"
        )
    rows = [parse_row(name, row) for name, row in table.items()]
    if not rows:
        raise ValueError("limits() table has no rows")
    measured = _by_name(measurements)
    names = {row.name for row in rows}
    unused = [text for text in measured if text not in names]
    built: List[CheckDescriptor] = []
    keys: List[str] = []  # the rows' names: a record's name may be escaped
    for row in rows:
        if row.name in measured:
            descriptor = row.build(measured[row.name][1])
        elif on_missing == "fail":
            descriptor = row.build(None)
            descriptor["error"] = _not_measured(row.name, unused)
        else:
            continue
        built.append(descriptor)
        keys.append(row.name)
    if not built:
        shown = ", ".join(repr(row.name) for row in rows[:5])
        raise ValueError(
            "limits(): no row of the table has a measurement, so nothing would be checked "
            f"(rows: {shown}{', ...' if len(rows) > 5 else ''}; measurements: "
            f"{', '.join(repr(text) for text in list(measured)[:5]) or 'none'})"
        )
    return dict(zip(keys, sink.batch(built)))


# ---------------------------------------------------------------------------
# load_limits
# ---------------------------------------------------------------------------

#: Arguments that are always text.
_TEXTS = frozenset({"units", "pattern"})
_TRUE = frozenset({"true", "yes", "1", "y"})
_FALSE = frozenset({"false", "no", "0", "n"})
_INTEGER = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|[0-9]+)")
_FLOAT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
#: A number with a dot between groups of three digits (``1.000,5``, ``1.000.000``).
_THOUSANDS = re.compile(r"[+-]?[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]*)?")
#: The columns a file may have besides the arguments and the selectors.
_COLUMNS = frozenset({"name", "check"})
_DELIMITERS = (",", ";", "\t")


def _number(text: str, comma: bool) -> Union[int, float, None]:
    """*text* as an ``int`` (decimal, or ``0x``/``0b``/``0o``) or a finite ``float``; ``None``
    when it is not one (also for ``nan``, ``inf`` and ``1_000``). With *comma*, a decimal comma
    is a decimal point."""
    if comma and "," in text and "." not in text:
        text = text.replace(",", ".")
    if _INTEGER.fullmatch(text):
        sign, digits = (text[0], text[1:]) if text[0] in "+-" else ("", text)
        base = 0 if digits[:2].lower() in ("0x", "0b", "0o") else 10
        return int(sign + digits, base)
    if _FLOAT.fullmatch(text):
        number = float(text)
        return number if math.isfinite(number) else None
    return None


def _boolean(text: str) -> Optional[bool]:
    word = text.strip().lower()
    if word in _TRUE:
        return True
    if word in _FALSE:
        return False
    return None


def _display(path: str) -> str:
    """*path* relative to the working directory when it is inside it, with ``/``."""
    absolute = os.path.abspath(path)
    try:
        relative = os.path.relpath(absolute)
    except ValueError:  # another drive on Windows
        return path
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return absolute
    return relative.replace(os.sep, "/")


def _comment(cells: List[str]) -> bool:
    """Whether a line is blank or a comment (its first cell starts with ``#``)."""
    first = next((cell.strip() for cell in cells if cell.strip()), "")
    return not first or (bool(cells) and cells[0].strip().startswith("#"))


def _header(text: str, delimiter: str) -> Tuple[List[str], int]:
    """The header cells with *delimiter*, and the number of lines up to it."""
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    for cells in reader:
        if not _comment(cells):
            return cells, reader.line_num
    return [], reader.line_num


def _column_map(columns: Optional[Mapping[str, Optional[str]]]) -> Dict[str, Optional[str]]:
    mapped: Dict[str, Optional[str]] = {}
    given: Dict[str, str] = {}
    for key, target in (columns or {}).items():
        if not isinstance(key, str) or not (target is None or isinstance(target, str)):
            raise TypeError(
                "load_limits() columns maps a file's column name to a row's argument name, or "
                f"to None to skip it; got {safe_repr(key)}: {safe_repr(target)}"
            )
        if target is not None and not target.strip():
            raise ValueError(
                f"load_limits() columns maps {key!r} to an empty name; skip a column with "
                f"None: {{{key!r}: None}}"
            )
        name = key.strip().lower()
        if name in mapped:
            raise ValueError(
                f"load_limits() columns names one column twice: {given[name]!r} and {key!r} "
                "(names match in any case)"
            )
        given[name] = key
        mapped[name] = target
    return mapped


def load_limits(
    path: Union[str, "os.PathLike[str]"],
    *,
    select: Optional[Mapping[str, object]] = None,
    columns: Optional[Mapping[str, Optional[str]]] = None,
    encoding: str = "utf-8-sig",
) -> Dict[str, LimitRow]:
    """Read a limits table for :meth:`Verify.limits` from a CSV file.

    The file starts with a header line (after a byte-order mark, if any, also with
    ``encoding="utf-8"``; a UTF-8 one read as ``cp1252`` is an error); blank lines and lines
    whose first cell starts with ``#`` are skipped. Columns are separated by commas,
    semicolons or tabs, whichever gives a ``name`` column; with semicolons, a decimal comma
    (``3,3``) is a number too. Column names match in any case, and cells are stripped; an
    empty cell is not given.

    Columns:

    - ``name`` (required): the check's name, which is also the measurement's.
    - ``check``: the check method; without it the limits say which (see :meth:`Verify.limits`).
    - ``low``, ``high``, ``threshold``, ``abs_tol``, ``rel_tol`` and the ``expected`` of an
      ``approx`` or ``length``: numbers (``3.3``, ``-5``, ``0x1F``; not ``nan`` or ``inf``).
      Against a ``Decimal`` or ``Fraction`` measurement, a cell is one too, from its digits,
      so that ``3.2`` is exactly ``Decimal("3.2")``.
    - ``expected`` of ``equal``/``not_equal``, and ``needle``: compared as the measurement is.
      Against a ``str`` the cell is text (``1.10`` stays ``"1.10"``), against a number it is
      a number (a ``Decimal`` or ``Fraction`` against one), against a ``bool`` true/false; a
      cell that cannot be that fails the check.
    - ``inclusive``: true/false (also yes/no, 1/0, any case). ``units``, ``pattern``: text.
    - Selector columns, named in *select*: ``select={"corner": "hot"}`` keeps the lines whose
      ``corner`` cell is ``hot`` (a cell may list values: ``hot|cold``) or empty. A line that
      names the value beats one that leaves it empty, so a default line can be overridden per
      corner.
    - Any other column is an error, so a misspelt limit (``hi``) is never skipped: rename it
      or skip it with *columns*.

    Every line is checked as :meth:`Verify.limits` checks a row, so a wrong file fails here,
    naming its line. Each row gets ``source``, ``"path:line"``, which records keep as
    ``limit_source``.

    Args:
        path: The CSV file. A relative path is relative to the working directory; use
            ``Path(__file__).with_name("limits.csv")`` for a file next to the test.
        select: The value of each selector column, compared as text (``str(value)``, enum
            members by value) with the cells, in the same case.
        columns: Renames columns to what rows call them, or skips them with ``None``:
            ``{"Min": "low", "Max": "high", "Notes": None}``. Names match in any case.
        encoding: The file's encoding. Excel's "CSV UTF-8" is the default; its plain "CSV"
            on Windows is ``"cp1252"``.

    Returns:
        The rows, by name, in file order.

    Raises:
        ValueError: If the file has no ``name`` column, a column that is neither a limit nor
            selected nor skipped, a line that is not a valid row (with its line), a *select*
            value no line has, or two lines for one name that fit equally.
    """
    shown = _display(os.fspath(path))
    wanted: Dict[str, str] = {}
    given: Dict[str, str] = {}
    for column, value in (select or {}).items():
        if not isinstance(column, str):
            raise TypeError(f"load_limits() select keys must be column names, got {column!r}")
        key = column.strip().lower()
        if key in wanted:
            raise ValueError(
                f"load_limits() select names one column twice: {given[key]!r} and {column!r} "
                "(names match in any case)"
            )
        given[key] = column
        wanted[key] = str(unwrap(value)).strip()
    mapped = _column_map(columns)
    try:
        with open(path, encoding=encoding, newline="") as stream:
            text = stream.read()
    except UnicodeDecodeError as exc:
        line = exc.object[: exc.start].count(b"\n") + 1
        raise ValueError(
            f"load_limits(): {shown}:{line} is not {encoding} text ({exc.reason}): save the "
            "file as 'CSV UTF-8', or pass its encoding, such as encoding=\"cp1252\""
        ) from None
    if text.startswith("\ufeff"):  # a byte-order mark, read with encoding="utf-8"
        text = text[1:]
    elif _starts_with_utf8_mark(text, encoding):
        raise ValueError(
            f"load_limits(): {shown} starts with a UTF-8 byte-order mark, so it is 'CSV UTF-8' "
            f"text, not {encoding}: leave out encoding="
        )
    delimiter, found = _choose_delimiter(text, mapped, shown)
    comma = delimiter == ";"
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    while reader.line_num < found.line:
        next(reader)
    names = found.names
    named = [name for name in names if name]
    repeated = sorted({name for name in named if named.count(name) > 1})
    if repeated:
        raise ValueError(f"load_limits(): {shown} has the column {', '.join(repeated)} twice")
    missing = sorted(set(wanted) - set(named))
    if missing:
        raise ValueError(
            f"load_limits(): select names {', '.join(missing)}, but {shown} has no such column "
            f"(its columns: {', '.join(named)})"
        )
    unknown = [name for name in named if name not in ARGUMENTS | _COLUMNS | set(wanted)]
    renamed = [
        index for index, name in enumerate(names) if name in unknown and found.renamed(index)
    ]
    if renamed:  # a misspelt columns= target
        header, target = found.header[renamed[0]], str(names[renamed[0]])
        close = difflib.get_close_matches(target, sorted(ARGUMENTS | _COLUMNS), n=1)
        raise ValueError(
            f"load_limits(): columns= maps the column {header!r} of {shown} to {target!r}, "
            f"which is not a limit{f' (did you mean {close[0]!r}?)' if close else ''}. Limit "
            f"columns: name, check, {', '.join(sorted(ARGUMENTS))}"
        )
    if unknown:
        raise ValueError(
            f"load_limits(): {shown} has columns that are not limits: {', '.join(unknown)}. "
            "Rename them to a limit or skip them with columns= (such as columns={"
            f"{unknown[0]!r}: None}}), or choose lines by them with select=. Limit columns: "
            f"name, check, {', '.join(sorted(ARGUMENTS))}"
        )
    values_seen: Dict[str, Set[str]] = {column: set() for column in wanted}
    #: name -> (how many selector cells matched, line number, row)
    chosen: Dict[str, Tuple[int, int, LimitRow]] = {}
    tied: Dict[str, List[int]] = {}
    for cells in reader:
        if _comment(cells):
            continue
        number = reader.line_num
        where = f"{shown}:{number}"
        for index, cell in enumerate(cells):
            if cell.strip() and (index >= len(names) or names[index] is None):
                if index < len(names) and found.skipped(index):
                    continue
                raise ValueError(
                    f"load_limits(): {where} has {cell.strip()!r} in column {index + 1}, which "
                    "has no name in the header"
                )
        values = {
            name: cell.strip() for name, cell in zip(names, cells) if name is not None
        }
        rank, selected = 0, True
        for column, value in wanted.items():
            options = [part.strip() for part in values.get(column, "").split("|")]
            options = [option for option in options if option]
            if options:
                values_seen[column].update(options)
                if value in options:
                    rank += 1
                else:
                    selected = False
        name = values.get("name", "")
        if not name:
            raise ValueError(f"load_limits(): {where} has no name")
        row = _row(values, where, comma)
        try:
            parse_row(name, row, f"load_limits(): {where}: row {name!r}")
        except TypeError as exc:
            raise ValueError(str(exc)) from None
        if not selected:
            continue
        if name in chosen:
            best, first, _ = chosen[name]
            if rank < best:
                continue
            if rank == best:
                tied.setdefault(name, [first]).append(number)
                continue
            tied.pop(name, None)
        chosen[name] = (rank, number, row)
    for column, value in wanted.items():
        if value not in values_seen[column]:
            raise ValueError(
                f"load_limits(): no line of {shown} has {column} = {value!r} (it has: "
                f"{', '.join(sorted(values_seen[column])) or 'nothing'}). A selected value "
                "must be named by a line, so that a misspelt one cannot leave only the "
                "default lines"
            )
    if tied:
        name, numbers = next(iter(tied.items()))
        raise ValueError(
            f"load_limits(): {shown} has {len(numbers)} lines for {name!r} that fit equally "
            f"(lines {', '.join(map(str, numbers))}); choose one with select="
        )
    return {name: row for name, (_, _, row) in chosen.items()}


def _starts_with_utf8_mark(text: str, encoding: str) -> bool:
    """Whether *text*, read with another *encoding* (such as cp1252), starts with the bytes
    of a UTF-8 byte-order mark (``ï»¿``)."""
    try:
        mark = codecs.BOM_UTF8.decode(encoding)
    except (UnicodeDecodeError, LookupError):  # utf-16: not a whole character
        return False
    return bool(mark) and mark != "\ufeff" and text.startswith(mark)


class _Found:
    """The header found with one delimiter: its cells, its column names, and its line."""

    def __init__(
        self,
        header: List[str],
        names: List[Optional[str]],
        line: int,
        ignored: Set[int],
        mapped: Set[int],
    ) -> None:
        self.header = header
        self.names = names
        self.line = line
        self.ignored = ignored
        self.mapped = mapped

    def skipped(self, index: int) -> bool:
        """Whether column *index* was skipped with ``columns=``."""
        return index in self.ignored

    def renamed(self, index: int) -> bool:
        """Whether column *index* was renamed with ``columns=``."""
        return index in self.mapped


def _choose_delimiter(
    text: str, mapped: Dict[str, Optional[str]], shown: str
) -> Tuple[str, _Found]:
    """The delimiter whose header has a ``name`` column (the one with most columns, if more
    than one does), and that header."""
    best: Optional[Tuple[str, _Found]] = None
    first: Optional[List[str]] = None
    for delimiter in _DELIMITERS:
        header, line = _header(text, delimiter)
        if first is None or len(header) > len(first):
            first = header
        names: List[Optional[str]] = []
        ignored: Set[int] = set()
        renamed: Set[int] = set()
        for index, cell in enumerate(header):
            key = cell.strip().lower()
            if key in mapped:
                target = mapped[key]
                (renamed if target is not None else ignored).add(index)
                names.append(None if target is None else target.strip().lower())
            else:
                names.append(key or None)
        if "name" in names and (best is None or len(names) > len(best[1].names)):
            cells = [cell.strip() for cell in header]
            best = (delimiter, _Found(cells, names, line, ignored, renamed))
    if best is None:
        if not first:
            raise ValueError(f"load_limits(): {shown} has no header line")
        raise ValueError(
            f"load_limits(): {shown} has no 'name' column (its header: "
            f"{', '.join(cell.strip() for cell in first)}); name the column of the check "
            "names 'name', or map it with columns={...: 'name'}"
        )
    return best


def _row(values: Dict[str, str], where: str, comma: bool) -> LimitRow:
    """The row of one CSV line: its non-empty limit cells, converted."""
    cell = values.get("check", "").lower()
    method = _CHECK_ALIASES.get(cell, cell) or None
    if method == "is_instance":
        raise ValueError(
            f"load_limits(): {where}: an is_instance row needs a class, which a CSV cell "
            "cannot hold; use a Python table for it"
        )
    approx = method == "approx" or (
        method is None and bool(values.get("abs_tol") or values.get("rel_tol"))
    )
    row: Dict[str, Any] = {}
    for column, text in values.items():
        if not text or column not in ARGUMENTS | {"check"}:
            continue  # empty, the name, a selector
        if column == "check":
            row[column] = method
        elif column in _TEXTS:
            row[column] = text
        elif column == "inclusive":
            flag = _boolean(text)
            if flag is None:
                raise ValueError(
                    f"load_limits(): {where}, column {column!r}: must be true or false, got "
                    f"{text!r}"
                )
            row[column] = flag
        elif column in _NUMBERS or (
            column == "expected" and (method in _NUMBER_EXPECTED or approx)
        ):
            number = _number(text, comma)
            if number is None:
                raise ValueError(
                    f"load_limits(): {where}, column {column!r}: must be a number, got "
                    f"{text!r}{_number_hint(text, comma)}"
                )
            if isinstance(number, float):  # keeps its digits, for a Decimal measurement
                number = CellNumber(number)
                number.text, number.comma = text, comma
            row[column] = number
        else:  # equal's expected, contains' needle: typed when checked
            typed = CellText(text)
            typed.comma = comma
            row[column] = typed
    row["source"] = where
    return row  # type: ignore[return-value]


def _number_hint(text: str, comma: bool) -> str:
    """Why a cell that is not a number may be one written another way."""
    if not comma:
        return " (a decimal comma needs ';' between columns)" if "," in text else ""
    if _THOUSANDS.fullmatch(text):
        return f" (remove the thousands separator: {text.replace('.', '')})"
    return ""
