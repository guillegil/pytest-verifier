"""Limits tables: ``verify.limits`` checks measurements against a table of limits, and
``load_limits`` reads such a table from a CSV file.

A table maps each check's name to its row: the arguments of the check after the measured
value, and optionally which check it is (``"check"``). Without ``"check"``, the limits say:
``low`` and ``high`` make a ``between``, one of them a ``greater_equal``/``less_equal`` (or
``greater``/``less`` with ``inclusive=False``), ``expected`` with a tolerance an ``approx``,
and ``expected`` alone an ``equal``.

Every row is built, with its measurement, before any is recorded: a row that is wrong raises
and nothing is recorded. Then the checks are recorded together, so that a required table stops
the test only once every row is in the summary.
"""
from __future__ import annotations

import csv
import math
import os
import re
import typing
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple, Union

from ._checks import REGISTRY
from ._descriptors import CheckDescriptor, plain_text, unwrap
from ._render import safe_repr

if typing.TYPE_CHECKING:  # pragma: no cover
    from ._verify import Sink


class LimitRow(typing.TypedDict, total=False):
    """One row of a limits table: the arguments of a check, and which check it is.

    Every key is optional; ``check`` names the check method (``"approx"``, ``"between"``, ...)
    and is inferred from the limits when it is missing. See :meth:`Verify.limits`.
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

ON_MISSING = ("fail", "ignore")

#: The error of a row whose measurement is missing.
NOT_MEASURED = "not measured"


class Row:
    """One row, checked: its check method and the arguments after the measured value."""

    __slots__ = ("name", "method", "args", "kwargs")

    def __init__(
        self, name: str, method: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]
    ) -> None:
        self.name = name
        self.method = method
        self.args = args
        self.kwargs = kwargs

    def build(self, value: Any) -> CheckDescriptor:
        """The row's check of *value*; raises ``TypeError``/``ValueError`` naming the row."""
        check = REGISTRY[ROW_CHECKS[self.method][2]]
        try:
            descriptor: CheckDescriptor = check.build(  # type: ignore[attr-defined]
                value, *self.args, name=self.name, **self.kwargs
            )
        except (TypeError, ValueError) as exc:
            raise type(exc)(_row_error(self.name, str(exc))) from None
        return descriptor


def _row_error(name: object, message: str) -> str:
    return f"limits() row {safe_repr(name)}: {message}"


def _infer(name: str, given: Dict[str, Any]) -> str:
    """The check a row without ``"check"`` makes, from its limits. Takes ``inclusive`` out of
    a row with one bound, where it picks ``greater``/``less``."""
    if "low" in given and "high" in given:
        return "between"
    if "low" in given or "high" in given:
        inclusive = given.pop("inclusive", True)
        if not isinstance(inclusive, bool):
            raise TypeError(
                _row_error(name, f"inclusive must be a bool, got {safe_repr(inclusive)}")
            )
        if "low" in given:
            return "greater_equal" if inclusive else "greater"
        return "less_equal" if inclusive else "less"
    if "expected" in given:
        return "approx" if "abs_tol" in given or "rel_tol" in given else "equal"
    raise ValueError(
        _row_error(
            name,
            "says neither which check it is ('check') nor a limit (low, high or expected); got "
            f"{', '.join(sorted(given)) or 'nothing'}",
        )
    )


def parse_row(name: object, row: object) -> Row:
    """Check one row; raises ``TypeError``/``ValueError`` naming it. The row is not changed."""
    if not isinstance(name, str):
        raise TypeError(f"limits() row names must be str, got {safe_repr(name)}")
    if not name.strip():
        raise ValueError("limits() row names must not be empty")
    name = plain_text(name)
    if not isinstance(row, Mapping):
        raise TypeError(
            _row_error(name, f"must be a mapping of arguments, got {type(row).__name__}")
        )
    given: Dict[str, Any] = {}
    for key, value in row.items():
        if not isinstance(key, str):
            raise TypeError(
                _row_error(name, f"argument names must be str, got {safe_repr(key)}")
            )
        if value is not None:  # None, like an empty cell, means not given
            given[key] = value
    method = given.pop("check", None)
    if method is None:
        method = _infer(name, given)
    elif isinstance(method, str):
        method = _CHECK_ALIASES.get(method.strip(), method.strip())
    if not isinstance(method, str) or method not in ROW_CHECKS:
        raise ValueError(
            _row_error(
                name, f"unknown check {safe_repr(method)}; one of: {', '.join(ROW_CHECKS)}"
            )
        )
    alias = _THRESHOLD_ALIASES.get(method)
    if alias is not None and alias in given:
        if "threshold" in given:
            raise TypeError(_row_error(name, f"give threshold or {alias}, not both"))
        given["threshold"] = given.pop(alias)
    required, optional, _ = ROW_CHECKS[method]
    unknown = sorted(set(given) - set(required) - set(optional))
    if unknown:
        takes = [
            f"{argument} (or {alias})" if argument == "threshold" and alias else argument
            for argument in (*required, *optional)
        ]
        raise TypeError(
            _row_error(
                name,
                f"{method}() takes {', '.join(takes) or 'no limits'}; not: {', '.join(unknown)}",
            )
        )
    missing = [argument for argument in required if argument not in given]
    if missing:
        raise TypeError(_row_error(name, f"{method}() needs {', '.join(missing)}"))
    if method == "matches" and isinstance(given["pattern"], str):
        try:
            re.compile(given["pattern"])
        except re.error as exc:
            raise ValueError(
                _row_error(name, f"pattern is not a valid regular expression: {exc}")
            ) from None
    args = tuple(given[argument] for argument in required)
    kwargs = {argument: given[argument] for argument in optional if argument in given}
    return Row(name, method, args, kwargs)


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
    built: List[CheckDescriptor] = []
    for row in rows:
        if row.name in measured:
            built.append(row.build(measured[row.name][1]))
        elif on_missing == "fail":
            descriptor = row.build(None)
            descriptor["error"] = NOT_MEASURED
            built.append(descriptor)
    if not built:
        shown = ", ".join(repr(row.name) for row in rows[:5])
        raise ValueError(
            "limits(): no row of the table has a measurement, so nothing would be checked "
            f"(rows: {shown}{', ...' if len(rows) > 5 else ''})"
        )
    return {check["name"]: check for check in sink.batch(built)}


# ---------------------------------------------------------------------------
# load_limits
# ---------------------------------------------------------------------------

#: Arguments that are always numbers.
_NUMBERS = frozenset({"low", "high", "threshold", "abs_tol", "rel_tol"})
#: Checks whose ``expected`` is a number.
_NUMBER_EXPECTED = frozenset({"approx", "length"})
#: Arguments that are always text.
_TEXTS = frozenset({"units", "pattern"})
#: What a ``type`` cell makes of ``expected`` and ``needle``.
_TYPES = ("str", "int", "float", "bool")
_TRUE = frozenset({"true", "yes", "1", "y"})
_FALSE = frozenset({"false", "no", "0", "n"})
_INTEGER = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|[0-9]+)")
_FLOAT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _number(text: str, comma: bool) -> Union[int, float, None]:
    """*text* as an ``int`` (decimal, or ``0x``/``0b``/``0o``) or a finite ``float``; ``None``
    when it is not one. With *comma*, a decimal comma is a decimal point."""
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
    word = text.lower()
    if word in _TRUE:
        return True
    if word in _FALSE:
        return False
    return None


class _Line:
    """Converts the cells of one CSV line by the argument they hold."""

    def __init__(self, where: str, comma: bool) -> None:
        self.where = where
        self.comma = comma

    def error(self, column: str, wanted: str, text: str) -> ValueError:
        return ValueError(
            f"load_limits(): {self.where}, column {column!r}: {wanted}, got {text!r}"
        )

    def number(self, column: str, text: str) -> Union[int, float]:
        number = _number(text, self.comma)
        if number is None:
            raise self.error(column, "must be a number", text)
        return number

    def typed(self, column: str, text: str, kind: str) -> Any:
        """*text* as *kind*, the line's ``type``: ``str``, ``int``, ``float`` or ``bool``."""
        if kind == "str":
            return text
        if kind == "bool":
            value = _boolean(text)
            if value is None:
                raise self.error(column, "must be true or false (type bool)", text)
            return value
        number = _number(text, self.comma)
        if kind == "int" and isinstance(number, int):
            return number
        if kind == "float" and number is not None:
            return float(number)
        raise self.error(column, f"must be a number of type {kind}", text)


def _delimiter(header: str) -> str:
    """Comma, semicolon or tab: whichever the header line has most of."""
    counts = {delimiter: header.count(delimiter) for delimiter in (",", ";", "\t")}
    return max(counts, key=lambda each: counts[each]) if any(counts.values()) else ","


def load_limits(
    path: Union[str, "os.PathLike[str]"],
    *,
    select: Optional[Mapping[str, object]] = None,
    ignore: typing.Collection[str] = (),
) -> Dict[str, LimitRow]:
    """Read a limits table for :meth:`Verify.limits` from a CSV file.

    The file is UTF-8 (a BOM is fine) and starts with a header line. Columns are separated by
    commas, semicolons or tabs, whichever the header has most of; with semicolons, a decimal
    comma (``3,3``) is a number too. Cells are stripped, and an empty cell is not given.

    Columns:

    - ``name`` (required): the check's name, which is also the measurement's.
    - ``check``: the check method; without it the limits say which (see :meth:`Verify.limits`).
    - ``low``, ``high``, ``threshold``, ``abs_tol``, ``rel_tol``: numbers, always (``int``,
      ``float``, ``0x1F``); the ``expected`` of an ``approx`` or ``length`` too.
    - ``expected`` of ``equal``/``not_equal`` and ``needle``: text, unless the line's ``type``
      column says ``int``, ``float`` or ``bool``.
    - ``inclusive``: true/false (also yes/no, 1/0, any case). ``units``, ``pattern``: text.
    - Selector columns, named in *select*: ``select={"corner": "hot"}`` keeps the lines whose
      ``corner`` is ``hot`` or empty; a line that names ``hot`` beats one that leaves it empty,
      so a default line can be overridden per corner.
    - Columns in *ignore* (notes, references) are skipped. Any other column is an error, so a
      misspelt limit (``hi``) cannot be skipped by mistake.

    Args:
        path: The CSV file.
        select: Values of selector columns, compared as text with the stripped cells.
        ignore: Columns to skip.

    Returns:
        The rows, by name, in file order.

    Raises:
        ValueError: If the file has no ``name`` column, a column that is neither a limit nor
            selected nor ignored, a cell that is not what its column needs (with its line), a
            *select* value no line has, or two lines for one name that fit equally.
    """
    source = os.fspath(path)
    wanted: Dict[str, str] = {}
    for column, value in (select or {}).items():
        if not isinstance(column, str):
            raise TypeError(f"load_limits() select keys must be column names, got {column!r}")
        wanted[column] = str(unwrap(value)).strip()
    ignored = {ignore} if isinstance(ignore, str) else set(ignore)
    with open(path, encoding="utf-8-sig", newline="") as stream:
        text = stream.read()
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].strip():
        raise ValueError(f"load_limits(): {source} has no header line")
    reader = csv.reader(lines, delimiter=_delimiter(lines[0]))
    comma = _delimiter(lines[0]) == ";"
    header = [column.strip() for column in next(reader)]
    if "name" not in header:
        raise ValueError(f"load_limits(): {source} has no 'name' column; its columns: {header}")
    repeated = sorted({column for column in header if header.count(column) > 1})
    if repeated:
        raise ValueError(f"load_limits(): {source} repeats the column {', '.join(repeated)}")
    missing = sorted(set(wanted) - set(header))
    if missing:
        raise ValueError(
            f"load_limits(): select names {', '.join(missing)}, but {source} has no such column"
        )
    unknown = sorted(
        set(header) - ARGUMENTS - {"name", "check", "type", ""} - set(wanted) - ignored
    )
    if unknown:
        raise ValueError(
            f"load_limits(): {source} has columns that are not limits: {', '.join(unknown)}. "
            "Choose lines by them with select=, or skip them with ignore="
        )
    values_seen: Dict[str, Set[str]] = {column: set() for column in wanted}
    #: name -> (how many selector cells matched, line number, row)
    chosen: Dict[str, Tuple[int, int, LimitRow]] = {}
    tied: Dict[str, List[int]] = {}
    for cells in reader:
        if not any(cell.strip() for cell in cells):
            continue  # a blank line
        number = reader.line_num
        where = f"{source}:{number}"
        if any(cell.strip() for cell in cells[len(header):]):
            raise ValueError(f"load_limits(): {where} has more cells than the header")
        values = {column: cell.strip() for column, cell in zip(header, cells)}
        rank, selected = 0, True
        for column, value in wanted.items():
            cell = values.get(column, "")
            if cell:
                values_seen[column].add(cell)
                if cell == value:
                    rank += 1
                else:
                    selected = False
        if not selected:
            continue
        name = values.get("name", "")
        if not name:
            raise ValueError(f"load_limits(): {where} has no name")
        row = _row(values, _Line(where, comma))
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
                f"load_limits(): no line of {source} has {column} = {value!r} (it has: "
                f"{', '.join(sorted(values_seen[column])) or 'nothing'})"
            )
    if tied:
        name, numbers = next(iter(tied.items()))
        raise ValueError(
            f"load_limits(): {source} has {len(numbers)} lines for {name!r} that fit equally "
            f"(lines {', '.join(map(str, numbers))}); choose one with select="
        )
    return {name: row for name, (_, _, row) in chosen.items()}


def _row(values: Dict[str, str], line: _Line) -> LimitRow:
    """The row of one CSV line: its non-empty limit cells, converted."""
    method = _CHECK_ALIASES.get(values.get("check", ""), values.get("check", "")) or None
    if method == "is_instance":
        raise ValueError(
            f"load_limits(): {line.where}: an is_instance row needs a class, which a CSV cell "
            "cannot hold; use a Python table for it"
        )
    kind = values.get("type", "")
    if kind and kind not in _TYPES:
        raise line.error("type", f"must be one of {', '.join(_TYPES)}", kind)
    approx = method is None and bool(values.get("abs_tol") or values.get("rel_tol"))
    row: Dict[str, Any] = {}
    for column, text in values.items():
        if not text or column not in ARGUMENTS | {"check"}:
            continue  # empty, the name, the type, a selector or an ignored column
        if column == "check" or column in _TEXTS:
            row[column] = text
        elif column == "inclusive":
            value = _boolean(text)
            if value is None:
                raise line.error(column, "must be true or false", text)
            row[column] = value
        elif column in _NUMBERS or (
            column == "expected" and (method in _NUMBER_EXPECTED or approx)
        ):
            row[column] = line.number(column, text)
        else:  # equal's expected, contains' needle
            row[column] = line.typed(column, text, kind or "str")
    return row  # type: ignore[return-value]
