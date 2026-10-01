"""``ChecksFailedError`` and the ``N of M checks failed`` summary it carries."""
from __future__ import annotations

import functools
from typing import Any, Iterable, Mapping

import pytest

from ._checks import render_detail, summary_separator
from ._render import describe_error, safe_str

__all__ = ["ChecksFailedError", "format_summary", "render_detail"]


def _line(marker: str, idx: int, result: Mapping[str, Any], passed: bool) -> str:
    name = safe_str(result.get("name", ""))
    stored = result.get("detail")
    detail = stored if isinstance(stored, str) else render_detail(result, passed)
    return f"  {marker} [{idx}] {name}{summary_separator(result)}{detail}"


def format_summary(results: Iterable[Mapping[str, Any]], *, start: int = 0) -> str:
    """The ``N of M checks failed`` summary of spec §7. Never raises.

    Checks are numbered from *start*, their index among all the checks of the test.
    """
    results = list(results)
    failed = [(i, r) for i, r in enumerate(results, start) if r.get("passed") is not True]
    passed = [(i, r) for i, r in enumerate(results, start) if r.get("passed") is True]

    def line(marker: str, idx: int, result: Mapping[str, Any], is_passed: bool) -> str:
        try:
            return _line(marker, idx, result, is_passed)
        except Exception as exc:
            return f"  {marker} [{idx}] <check could not be rendered: {describe_error(exc)}>"

    lines: list[str] = [f"{len(failed)} of {len(results)} checks failed", ""]
    lines.extend(line("✗", idx, r, False) for idx, r in failed)
    if passed:
        lines.append("")
        lines.extend(line("✓", idx, r, True) for idx, r in passed)
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

    Attributes:
        results: The check descriptors the summary was built from.
        start: Index of the first of them among all the checks of the test.
    """

    def __init__(self, results: Iterable[Mapping[str, Any]], *, start: int = 0) -> None:
        self.results = list(results)
        self.start = start
        message = format_summary(self.results, start=start)
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
        return (functools.partial(type(self), start=self.start), (self.results,), self.__dict__)


ChecksFailedError.__module__ = "pytest_verifier"
