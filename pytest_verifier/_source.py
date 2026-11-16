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

Results are cached per code object and instruction, so a check in a loop parses nothing after
its first run. Nothing here raises: without source (``exec``, the REPL) the name is
:data:`FALLBACK`.
"""
from __future__ import annotations

import ast
import itertools
import linecache
import sys
import weakref
from types import CodeType, FrameType
from typing import Any, Dict, List, Optional, Tuple

from ._location import ours

#: The name of a check whose value has no source text.
FALLBACK = "value"

#: Longest default name; a longer expression is cut and ends in ``...``.
NAME_LIMIT = 120

#: Cached names: ``(id(code), f_lasti) -> (weakref to the code, name)``. The weak reference
#: tells a code object from a newer one that reuses the id; ids avoid hashing code objects,
#: which walks their bytecode and constants.
_NAMES: Dict[Tuple[int, int], Tuple[Any, str]] = {}

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
    lines = linecache.getlines(code.co_filename, module_globals)
    if not lines:
        return FALLBACK
    if use_positions:
        found = _call_at(lines, _position(code, lasti))
        # The frame may be running another call that reaches the check method indirectly
        # (``map(verify.is_true, flags)``, a ``functools.partial`` factory): that call's
        # argument is not the check's value, so only a call of *method* names it; any
        # other call goes to the line lookup, as on Python 3.9/3.10.
        if found is not None and _called_name(found[0]) == method:
            call, snippet = found
            return _argument_text(call, snippet, parameter) or FALLBACK
    parsed = _parse(code.co_filename, lines)
    if parsed is None or lineno is None:
        return FALLBACK
    calls, source = parsed
    return _by_line(calls, source, lineno, method, parameter)


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
    calls: List[ast.Call], source: str, lineno: int, method: str, parameter: str
) -> str:
    """The value text of the call of *method* that spans *lineno*; :data:`FALLBACK` when there
    is none, or several with different texts."""
    texts = set()
    for node in calls:
        end = node.end_lineno if node.end_lineno is not None else node.lineno
        if node.lineno <= lineno <= end and _called_name(node) == method:
            texts.add(_argument_text(node, source, parameter))
    if len(texts) != 1:
        return FALLBACK
    return texts.pop() or FALLBACK


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
    text = ast.get_source_segment(source, node)
    if text is None or "\n" in text or not _is_text_of(text, node):
        text = ast.unparse(node)
    text = " ".join(text.split())
    if len(text) > NAME_LIMIT:
        text = text[: NAME_LIMIT - 3] + "..."
    return text


def _is_text_of(text: str, node: ast.expr) -> bool:
    """Whether *text* parses to *node*. Python 3.9 gives wrong offsets to expressions in a
    multi-line f-string, so a segment taken by them can be unrelated text."""
    try:
        parsed = ast.parse("(" + text + ")", mode="eval").body
    except (SyntaxError, ValueError):
        return False
    return ast.dump(parsed) == ast.dump(node)


def clear_caches() -> None:
    """Forget every cached name and parsed file."""
    _NAMES.clear()
    _TREES.clear()
