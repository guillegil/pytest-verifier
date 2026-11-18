"""Default check names: the source text of the value a check is made on.

``verify.equal(dat.txfe, 1)`` without ``name=`` is named ``dat.txfe``: the text of the call's
value argument (its first positional argument, or the keyword that names it) at the user's call
site, the first frame outside this package.

On Python 3.11+ the call instruction's position (``co_positions`` at ``f_lasti``) says exactly
which call of the line runs, also for a call over several lines, and only the text of that
call is parsed. Without positions (Python 3.9/3.10, ``-X no_debug_ranges``) the file is parsed
(once while its source stays the same) and the call is the one of the method that spans the
line; when the line holds several such calls with different arguments, the name falls back.
A call that reaches the method indirectly (``map(verify.is_true, flags)``, a bound method or
``functools.partial`` as an ``all_satisfy`` factory) is never named by the outer call's
argument: both lookups need a call of the method itself, else the name falls back.

The same call gives the symbolic form of an operand name (:func:`spellings`): when
``actual_name=`` or ``expected_name=`` is written as an f-string, its literal text and the
source text of each interpolated expression, so ``f"block.reg{channel}.F"`` can also be shown
as ``block.reg<channel>.F``. The expressions are only read, never evaluated. Here the call must
be the one that provably runs: an indirect call never takes the f-string of a call of the
method on its line. Without positions that cannot always be told, so a line where another
call passes an operand name gives nothing (a ``functools.partial`` that bound the name
elsewhere, called on the line of a direct call, is the case left; the text must still fit).

Results are cached per code object and instruction, so a check in a loop parses nothing after
its first run. Nothing here raises: without source (``exec``, the REPL) the name is
:data:`FALLBACK` and no operand name has a symbolic form.
"""
from __future__ import annotations

import ast
import itertools
import linecache
import sys
import weakref
from types import CodeType, FrameType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, TypeVar, Union

from ._location import ours

#: The name of a check whose value has no source text.
FALLBACK = "value"

#: Longest default name; a longer expression is cut and ends in ``...``.
NAME_LIMIT = 120

#: Cached names: ``(id(code), f_lasti) -> (weakref to the code, name)``. The weak reference
#: tells a code object from a newer one that reuses the id; ids avoid hashing code objects,
#: which walks their bytecode and constants.
_NAMES: Dict[Tuple[int, int], Tuple[Any, str]] = {}

#: The parts of an f-string, in order: its literal text (``str``) and, each in a 1-tuple, the
#: source text of the expressions it interpolates.
Parts = Tuple[Union[str, Tuple[str]], ...]

#: The keyword arguments whose f-strings :func:`spellings` reads.
SPELLED = ("actual_name", "expected_name")

#: Cached f-strings of the operand names of a call, keyed like :data:`_NAMES`.
_SPELLINGS: Dict[Tuple[int, int], Tuple[Any, Mapping[str, Parts]]] = {}

_NOTHING: Mapping[str, Parts] = {}

_T = TypeVar("_T")

#: Files parsed to find a call by its line (no instruction positions): ``filename -> (the
#: linecache lines, source, every call in it)``, ``None`` for a source that does not parse. A
#: file is parsed again when linecache holds other lines for it.
_TREES: Dict[str, Tuple[List[str], str, Optional[List[ast.Call]]]] = {}

#: Bounds of the caches: cleared when full (a session rarely gets near them).
_NAMES_LIMIT = 50_000
_TREES_LIMIT = 512

#: Whether to use instruction positions (a test turns it off to exercise the 3.9 path).
use_positions = sys.version_info >= (3, 11)


def subject_text(method: str, parameter: str) -> str:
    """The source text of the value argument of the ``verify.<method>()`` call running now.

    *parameter* is the keyword name of that argument (``actual``, ``haystack``). Never raises:
    :data:`FALLBACK` when the source cannot be found.
    """
    try:
        frame: Optional[FrameType] = sys._getframe(1)
        while frame is not None and ours(frame):
            frame = frame.f_back
        if frame is None:
            return FALLBACK
        return _cached(frame, method, parameter)
    except Exception:  # a default name must never break a check
        return FALLBACK


def spellings(method: str) -> Mapping[str, Parts]:
    """The operand names (:data:`SPELLED`) written as f-strings in the ``verify.<method>()``
    call running now: keyword to the parts of its f-string. A name given any other way (a
    plain literal, a variable, ``**kwargs``) is not in it. Never raises: empty when the source
    or the call cannot be found.
    """
    try:
        frame: Optional[FrameType] = sys._getframe(1)
        while frame is not None and ours(frame):
            frame = frame.f_back
        if frame is None:
            return _NOTHING
        code, lasti = frame.f_code, frame.f_lasti
        key = (id(code), lasti)
        hit = _SPELLINGS.get(key)
        if hit is not None and hit[0]() is code:
            return hit[1]
        found = _find_spellings(code, lasti, frame.f_lineno, frame.f_globals, method)
        if len(_SPELLINGS) >= _NAMES_LIMIT:
            _SPELLINGS.clear()
        _SPELLINGS[key] = (weakref.ref(code), found)
        return found
    except Exception:  # an operand name must never break a check
        return _NOTHING


def _cached(frame: FrameType, method: str, parameter: str) -> str:
    code, lasti = frame.f_code, frame.f_lasti
    key = (id(code), lasti)
    hit = _NAMES.get(key)
    if hit is not None and hit[0]() is code:
        return hit[1]
    name = _find(code, lasti, frame.f_lineno, frame.f_globals, method, parameter)
    if len(_NAMES) >= _NAMES_LIMIT:
        _NAMES.clear()
    _NAMES[key] = (weakref.ref(code), name)
    return name


def _find(
    code: CodeType,
    lasti: int,
    lineno: Optional[int],
    module_globals: Dict[str, Any],
    method: str,
    parameter: str,
) -> str:
    def text(call: ast.Call, source: str) -> str:
        return _argument_text(call, source, parameter)

    return _locate(code, lasti, lineno, module_globals, method, text) or FALLBACK


def _find_spellings(
    code: CodeType,
    lasti: int,
    lineno: Optional[int],
    module_globals: Dict[str, Any],
    method: str,
) -> Mapping[str, Parts]:
    found = _locate(code, lasti, lineno, module_globals, method, _f_strings, proven=True)
    return dict(found or ())


def _locate(
    code: CodeType,
    lasti: int,
    lineno: Optional[int],
    module_globals: Dict[str, Any],
    method: str,
    read: Callable[[ast.Call, str], _T],
    proven: bool = False,
) -> Optional[_T]:
    """What *read* finds in the call of *method* that the instruction at *lasti* makes:
    ``read(call, source)``, whose result must be hashable. ``None`` without source or call,
    or when the line holds several calls of *method* that read differently.

    With *proven*, only the call that is known to be the one running is read: an instruction
    that makes another call (an alias, a ``functools.partial``) gives ``None`` even when a
    call of *method* is on the same line, and without instruction positions so does a line
    with another call that passes an operand name (see :func:`_by_line`)."""
    lines = linecache.getlines(code.co_filename, module_globals)
    if not lines:
        return None
    if use_positions:
        found = _call_at(lines, _position(code, lasti))
        # The frame may be running another call that reaches the check method indirectly
        # (``map(verify.is_true, flags)``, a ``functools.partial`` factory): that call's
        # argument is not the check's value, so only a call of *method* names it; any
        # other call goes to the line lookup, as on Python 3.9/3.10.
        if found is not None and _called_name(found[0]) == method:
            call, snippet = found
            return read(call, snippet)
        if found is not None and proven:
            return None  # another call runs: a call of *method* on its line is not this one
    parsed = _parse(code.co_filename, lines)
    if parsed is None or lineno is None:
        return None
    calls, source = parsed
    return _by_line(calls, source, lineno, method, read, proven)


def _parse(filename: str, lines: List[str]) -> Optional[Tuple[List[ast.Call], str]]:
    """Every call in the source *lines* of *filename*, and the source; ``None`` for a source
    that does not parse (the file changed since it was imported)."""
    cached = _TREES.get(filename)
    if cached is None or cached[0] is not lines:
        source = "".join(lines)
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            calls: Optional[List[ast.Call]] = None
        else:
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        if len(_TREES) >= _TREES_LIMIT:
            _TREES.clear()
        cached = (lines, source, calls)
        _TREES[filename] = cached
    _, text, found = cached
    return None if found is None else (found, text)


def _position(code: CodeType, lasti: int) -> Optional[Tuple[Any, ...]]:
    """``(line, end_line, column, end_column)`` of the instruction at *lasti*, or ``None``."""
    positions = getattr(code, "co_positions", None)
    if positions is None or lasti < 0:
        return None
    position = next(itertools.islice(positions(), lasti // 2, None), None)
    if position is None or None in position:
        return None
    return tuple(position)


def _call_at(
    lines: List[str], position: Optional[Tuple[Any, ...]]
) -> Optional[Tuple[ast.Call, str]]:
    """The call the instruction at *position* makes, parsed from its own text (columns are
    UTF-8 offsets), with that text; ``None`` when it is not a call. A method called on the
    line after its object starts at the method's name, which still parses as a call."""
    if position is None:
        return None
    line, end_line, column, end_column = position
    if not 1 <= line <= end_line <= len(lines):
        return None
    chunk = [text.encode("utf-8") for text in lines[line - 1 : end_line]]
    if len(chunk) == 1:
        span = chunk[0][column:end_column]
    else:
        span = chunk[0][column:] + b"".join(chunk[1:-1]) + chunk[-1][:end_column]
    snippet = "(" + span.decode("utf-8") + ")"
    try:
        body = ast.parse(snippet, mode="eval").body
    except (SyntaxError, ValueError):
        return None
    return (body, snippet) if isinstance(body, ast.Call) else None


def _by_line(
    calls: List[ast.Call],
    source: str,
    lineno: int,
    method: str,
    read: Callable[[ast.Call, str], _T],
    proven: bool = False,
) -> Optional[_T]:
    """What *read* finds in the call of *method* that spans *lineno*; ``None`` when there is
    no such call, or several that read differently. With *proven*, also when another call that
    spans the line passes an operand name (:data:`SPELLED`, or ``**kwargs``): it may be the
    call that reaches *method* (an alias, a ``functools.partial``), and the line cannot tell."""
    results = set()
    for node in calls:
        end = node.end_lineno if node.end_lineno is not None else node.lineno
        if not node.lineno <= lineno <= end:
            continue
        if _called_name(node) == method:
            results.add(read(node, source))
        elif proven and _may_spell(node):
            return None
    if len(results) != 1:
        return None
    return results.pop()


def _may_spell(call: ast.Call) -> bool:
    """Whether *call* passes an operand name: one of :data:`SPELLED`, or ``**kwargs``."""
    return any(keyword.arg is None or keyword.arg in SPELLED for keyword in call.keywords)


def _called_name(call: ast.Call) -> Optional[str]:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _argument_text(call: ast.Call, source: str, parameter: str) -> str:
    """The text of the value argument of *call*: its first positional argument, or the keyword
    *parameter*. ``""`` when it has neither, or only ``*args``/``**kwargs``."""
    node: Optional[ast.expr] = None
    if call.args:
        if not isinstance(call.args[0], ast.Starred):
            node = call.args[0]
    else:
        for keyword in call.keywords:
            if keyword.arg == parameter:
                node = keyword.value
    if node is None:
        return ""
    return _node_text(node, source)


def _node_text(node: ast.expr, source: str) -> str:
    """The source text of *node* on one line, cut to :data:`NAME_LIMIT`; unparsed when the
    text at its offsets is not its own (see :func:`_is_text_of`) or spans lines."""
    text = ast.get_source_segment(source, node)
    if text is None or "\n" in text or not _is_text_of(text, node):
        text = ast.unparse(node)
    return one_line(text)


def one_line(text: str) -> str:
    """Source text with every run of whitespace as one space, cut to :data:`NAME_LIMIT`."""
    text = " ".join(text.split())
    if len(text) > NAME_LIMIT:
        text = text[: NAME_LIMIT - 3] + "..."
    return text


def _f_strings(call: ast.Call, source: str) -> Tuple[Tuple[str, Parts], ...]:
    """The :data:`SPELLED` keywords of *call* whose value is an f-string (also one joined to
    literals by implicit concatenation), each with the parts of that f-string."""
    return tuple(
        (keyword.arg, _parts(keyword.value, source))
        for keyword in call.keywords
        if keyword.arg in SPELLED and isinstance(keyword.value, ast.JoinedStr)
    )


def _parts(node: ast.JoinedStr, source: str) -> Parts:
    """The literal text and the interpolated expressions of an f-string. A format spec and a
    conversion are left out; ``{x=}`` is the literal ``x=`` and the expression ``x``."""
    parts: List[Union[str, Tuple[str]]] = []
    for piece in node.values:
        if isinstance(piece, ast.FormattedValue):
            parts.append((_node_text(piece.value, source),))
        elif isinstance(piece, ast.Constant) and isinstance(piece.value, str):
            parts.append(piece.value)
    return tuple(parts)


def _is_text_of(text: str, node: ast.expr) -> bool:
    """Whether *text* parses to *node*. Python 3.9 gives wrong offsets to expressions in a
    multi-line f-string, so a segment taken by them can be unrelated text."""
    try:
        parsed = ast.parse("(" + text + ")", mode="eval").body
    except (SyntaxError, ValueError):
        return False
    return ast.dump(parsed) == ast.dump(node)


def clear_caches() -> None:
    """Forget every cached name, operand name and parsed file."""
    _NAMES.clear()
    _SPELLINGS.clear()
    _TREES.clear()
