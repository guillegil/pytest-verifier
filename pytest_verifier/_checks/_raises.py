"""The ``raises`` check: whether a block of code raised the expected exception.

``verify.raises(...)`` returns a context manager (:class:`pytest_verifier.Raises`); when its
block ends, :meth:`RaisesCheck.build` turns what the block raised into a descriptor. The
verdict is decided then, from the live exception, and stored (``type_check``,
``match_check``), so that a record judges the same after a JSON round trip.
"""
from __future__ import annotations

import re
from typing import Any, List, Mapping, Optional, Tuple, Type, Union

from .._descriptors import CheckDescriptor, qualified_type_name, require_name, type_display
from .._render import describe_error, render_text, safe_repr
from ._base import CheckType, passes_through, register
from ._values import pattern_parts, regex_text, subject

#: What ``raises`` expects: an exception class, or a tuple of them.
ExpectedException = Union[Type[BaseException], Tuple[Type[BaseException], ...]]


def exception_classes(expected: object) -> Tuple[type, ...]:
    """The exception classes of *expected*, a class or a (nested) tuple of them."""
    if isinstance(expected, type) and issubclass(expected, BaseException):
        return (expected,)
    if isinstance(expected, tuple) and expected:
        return tuple(cls for member in expected for cls in exception_classes(member))
    raise TypeError(
        "raises() expected_exception must be an exception class or a tuple of them, "
        f"got {safe_repr(expected)}"
    )


def _pattern(match: object) -> Tuple[Optional[str], int]:
    """``(source, flags)`` of *match*, checked to be a valid regular expression."""
    if match is None:
        return None, 0
    if not isinstance(match, (str, re.Pattern)):
        raise TypeError(
            f"raises() match must be a string or a compiled pattern, got {type(match).__name__}"
        )
    source, flags = pattern_parts(match)
    if not isinstance(source, str):
        raise TypeError("raises() match must be a text pattern, not a bytes pattern")
    if not source:
        raise ValueError(
            "raises() match must not be empty: it would match any message. Leave it out, or "
            "use '^$' for an empty message"
        )
    try:
        re.compile(source, flags)
    except re.error as exc:
        raise ValueError(f"raises() match is not a valid regular expression: {exc}") from None
    return source, flags


#: Expected types that catch nearly anything: a typo in the block would raise one and pass.
_TOO_BROAD = (Exception, BaseException)


def exception_text(exc: BaseException) -> Optional[str]:
    """What ``match`` searches: ``str(exc)`` and its notes (PEP 678), one per line, as
    ``pytest.raises`` does. ``None`` when ``str()`` raises."""
    try:
        text = str(exc)
        notes = getattr(exc, "__notes__", None)
    except Exception:
        return None
    if isinstance(notes, list):
        text = "\n".join([text, *(note for note in notes if isinstance(note, str))])
    return text


def handles(exc: BaseException, expected: Tuple[type, ...]) -> bool:
    """Whether a ``raises`` block ends in a check when it raised *exc*. What ends the test or
    the session (``pytest.skip``, ``pytest.exit``, ``KeyboardInterrupt``, a required check that
    stopped the test) goes on unchanged unless the expected types name it."""
    return not passes_through(exc, expected)


def expected_instance(exc: Optional[BaseException], expected: Tuple[type, ...]) -> bool:
    """Whether *exc* is an instance of an expected class. Never raises."""
    try:
        return exc is not None and isinstance(exc, expected)
    except Exception:  # pragma: no cover - a misbehaving __instancecheck__
        return False


class RaisesCheck(CheckType):
    check_type = "raises"

    @staticmethod
    def build(
        raised: Optional[BaseException],
        expected_exception: ExpectedException,
        *,
        match: Optional[Union[str, "re.Pattern[str]"]] = None,
        name: str,
        raised_at: Optional[str] = None,
    ) -> CheckDescriptor:
        """The check of a ``raises`` block that raised *raised* (``None``: nothing), at
        *raised_at* (``"path:line"``)."""
        name = require_name(name, "raises")
        classes = exception_classes(expected_exception)
        source, flags = _pattern(match)
        broad = [cls.__name__ for cls in classes if cls in _TOO_BROAD]
        if broad and source is None:
            raise TypeError(
                f"raises() {broad[0]} is too broad without match=: a typo or a wrong argument "
                "in the block would raise it and pass. Name the exception class the code must "
                "raise, or give match="
            )
        display = " | ".join(type_display(cls) for cls in classes)
        matching = "" if source is None else f" matching {regex_text(source, flags)}"
        error: Optional[str] = None
        message = None if raised is None else exception_text(raised)
        try:
            type_check = raised is not None and isinstance(raised, classes)
        except Exception as exc:  # pragma: no cover - a misbehaving __instancecheck__
            type_check, error = False, describe_error(exc)
        match_check: Optional[bool] = None
        if type_check and source is not None:
            if message is None:
                match_check = False
                error = "match cannot be searched: str() of the exception raised"
            else:
                match_check = re.search(source, message, flags) is not None
        desc: CheckDescriptor = {
            "check_type": "raises",
            "name": name,
            "description": f"{subject(name)} raises {display}{matching}",
            "expected_type": display,
            "expected_types": [qualified_type_name(cls) for cls in classes],
            "match": source,
            "flags": flags,
            "raised_type": None if raised is None else type_display(type(raised)),
            "raised_message": message,
            "raised_at": raised_at,
            "type_check": type_check,
            "match_check": match_check,
        }
        if error is not None:
            desc["error"] = error
        return desc

    def compare(self, d: Mapping[str, Any]) -> Any:
        type_check = d.get("type_check")
        if not isinstance(type_check, bool):  # a hand-built descriptor: compare the names
            raised = d.get("raised_type")
            expected: List[str] = str(d.get("expected_type", "")).split(" | ")
            type_check = raised is not None and raised in expected
        if not type_check:
            return False
        if d.get("match") is None:
            return True
        match_check = d.get("match_check")
        if isinstance(match_check, bool):
            return match_check
        message = d.get("raised_message") or ""
        return re.search(d["match"], message, d.get("flags") or 0) is not None

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        raised = d.get("raised_type")
        message = d.get("raised_message")
        got = render_text(raised)
        if message:
            got += f": {render_text(message)}"
        if passed:
            return f"raised {got}"
        expected = render_text(d.get("expected_type"))
        if d.get("match") is not None:
            expected += f" matching {regex_text(d.get('match'), d.get('flags'))}"
        if raised is None:
            return f"expected {expected}, nothing was raised"
        where = d.get("raised_at")
        at = f" (raised at {render_text(where)})" if isinstance(where, str) and where else ""
        return f"expected {expected}, got {got}{at}{_escape_hint(d)}"


def _escape_hint(d: Mapping[str, Any]) -> str:
    """A hint for a pattern that is in the message as text but did not match it as a regular
    expression (parentheses, brackets, dots)."""
    pattern, message = d.get("match"), d.get("raised_message")
    if d.get("match_check") is False and isinstance(pattern, str) and isinstance(message, str):
        if pattern in message:
            return "; match is a regular expression: use re.escape() to match it as text"
    return ""


RAISES = register(RaisesCheck())
