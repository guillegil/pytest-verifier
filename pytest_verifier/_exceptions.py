"""``ChecksFailedError`` and the ``N of M checks failed`` summary it carries."""
from __future__ import annotations

import codecs
import functools
import re
from typing import Any, Iterable, List, Mapping, Optional

import pytest

from ._checks import render_detail, summary_separator
from ._render import describe_error, escape, render_text, shorten

__all__ = ["ChecksFailedError", "format_summary", "render_detail"]

#: Longest detail a summary line shows; each value in a detail is bounded already.
_DETAIL_LIMIT = 2000

#: The summary's own markers, and their stand-ins where a terminal cannot show them.
_ASCII = {"✗": "x", "✓": "ok", "…": "..."}

#: The start of a summary line: ``  ✗ [0] …`` or ``  ✓ … 2 more passed checks``.
_MARKED = re.compile(r"  ([✗✓])( …)? ")


def _codec(encoding: Optional[str]) -> Optional[str]:
    """The codec to adapt text to, or ``None`` when every character prints in *encoding*, or
    it is unknown or not a text encoding."""
    if not encoding:
        return None
    try:
        codec = codecs.lookup(encoding).name
        if codec.startswith("utf"):
            return None
        "".join(_ASCII).encode(codec, "backslashreplace")  # a text encoding at all?
    except (LookupError, UnicodeError):
        return None
    return codec


def _encodable(text: str, codec: str) -> str:
    return text.encode(codec, "backslashreplace").decode(codec)


def for_terminal(text: str, encoding: Optional[str]) -> str:
    """*text*, a summary, made printable in a terminal's *encoding*. Never raises.

    The summary's markers become ASCII stand-ins (``x``, ``ok``) where the encoding cannot show
    them. Any other character it cannot show, in names, values and details alike, becomes an
    escape such as ``\\u2208``, so two different values never print the same. pytest would
    otherwise escape a whole block at once, line breaks included.
    """
    codec = _codec(encoding)
    if codec is None:
        return text
    try:
        return "\n".join(_terminal_line(line, codec) for line in text.split("\n"))
    except (LookupError, UnicodeError):
        return text


def _terminal_line(line: str, codec: str) -> str:
    match = _MARKED.match(line)
    if match is None:
        return _encodable(line, codec)
    head = ["", ""]
    for symbol in match.group(1), (match.group(2) or "").strip():
        if symbol:
            try:
                symbol.encode(codec)
            except UnicodeError:
                symbol = _ASCII[symbol]
            head.append(symbol)
    return " ".join(head) + " " + _encodable(line[match.end() :], codec)


def _line(marker: str, idx: int, result: Mapping[str, Any], passed: bool) -> str:
    name = render_text(result.get("name", ""))
    stored = result.get("detail")
    detail = stored if isinstance(stored, str) else render_detail(result, passed)
    detail = shorten(escape(detail), _DETAIL_LIMIT)
    return f"  {marker} [{idx}] {name}{summary_separator(result)}{detail}"


def format_summary(
    results: Iterable[Mapping[str, Any]],
    *,
    start: int = 0,
    max_passed: Optional[int] = None,
) -> str:
    """The ``N of M checks failed`` summary of spec §7. Never raises.

    Checks are numbered from *start*, their index among all the checks of the test. Every
    failed check is listed; at most *max_passed* passed checks are (all when ``None``), then a
    line says how many more passed.
    """
    results = list(results)
    failed = [(i, r) for i, r in enumerate(results, start) if r.get("passed") is not True]
    passed = [(i, r) for i, r in enumerate(results, start) if r.get("passed") is True]

    def line(marker: str, idx: int, result: Mapping[str, Any], is_passed: bool) -> str:
        try:
            return _line(marker, idx, result, is_passed)
        except Exception as exc:
            return f"  {marker} [{idx}] <check could not be rendered: {describe_error(exc)}>"

    lines: List[str] = [f"{len(failed)} of {len(results)} checks failed", ""]
    lines.extend(line("✗", idx, r, False) for idx, r in failed)
    if passed:
        shown = passed if max_passed is None else passed[: max(max_passed, 0)]
        lines.append("")
        lines.extend(line("✓", idx, r, True) for idx, r in shown)
        hidden = len(passed) - len(shown)
        if hidden:
            noun = "check" if hidden == 1 else "checks"
            lines.append(f"  ✓ … {hidden} more passed {noun} (-vv shows them)")
    return "\n".join(lines)


class ChecksFailedError(AssertionError, pytest.fail.Exception):  # type: ignore[misc,name-defined]
    """Raised when one or more soft checks of a test failed.

    The ``verify`` fixture raises it at the end of the phase in which the checks were recorded:
    after the test body for checks made in fixtures' setup and in the test, and after teardown
    for checks made while fixtures are torn down. It is an ``AssertionError``, so
    ``pytest.raises(AssertionError)`` and ``xfail(raises=AssertionError)`` catch it. Rerun
    filters that match by name need ``ChecksFailedError``. pytest prints only its message,
    without a traceback.

    The message follows spec §7: a ``N of M checks failed`` header, then the
    failed checks (``✗``) before the passed checks (``✓``), each prefixed with
    its ``[seq]`` index in evaluation order, its name, and a per-type
    ``expected … got …`` (failed) or compact (passed) detail clause.

    Args:
        results: The check descriptors to summarize.
        start: Index of the first of them among all the checks of the test, so that checks
            raised after teardown keep the numbers ``get_check_results`` gives them.
        max_passed: List at most this many passed checks (all when ``None``). The plugin
            lists 10 unless pytest runs with ``-vv``.

    Attributes:
        results: The check descriptors the summary was built from.
        start: Index of the first of them among all the checks of the test.
        max_passed: How many passed checks the summary lists at most.
    """

    def __init__(
        self,
        results: Iterable[Mapping[str, Any]],
        *,
        start: int = 0,
        max_passed: Optional[int] = None,
    ) -> None:
        self.results = list(results)
        self.start = start
        self.max_passed = max_passed
        message = format_summary(self.results, start=start, max_passed=max_passed)
        AssertionError.__init__(self, message)
        # pytest.fail.Exception attributes: show only the message, not a traceback.
        self.msg = message
        self.pytrace = False

    def __str__(self) -> str:
        return self.msg

    def __repr__(self) -> str:
        failed = sum(1 for r in self.results if r.get("passed") is not True)
        return f"{type(self).__name__}({failed} of {len(self.results)} checks failed)"

    def __reduce__(self) -> tuple[Any, ...]:
        rebuild = functools.partial(type(self), start=self.start, max_passed=self.max_passed)
        return (rebuild, (self.results,), self.__dict__)


ChecksFailedError.__module__ = "pytest_verifier"
