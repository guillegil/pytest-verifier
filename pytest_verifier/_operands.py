"""Operand names: how a test says the values of a check are spelled.

``actual_name=`` and ``expected_name=`` take text, and two forms of it are kept:

- the *concrete* text, with this run's values: ``tb.dev.map.block.reg3.F``;
- the *symbolic* text, with each interpolation as ``<expression>``:
  ``tb.dev.map.block.reg<channel>.F``, only when it is known and differs from the concrete one.

A template string (``t"...reg{channel}.F"``, Python 3.14+) carries both. For a ``str`` the
symbolic form is read from the source of the keyword argument when that is an f-string
(:func:`pytest_verifier._source.spellings`), and kept only when the f-string's literal parts
occur, in order, in the text received: the source found must be the one that made the text.
Nothing is evaluated a second time.
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Mapping, NamedTuple, Optional, Tuple, Union

from ._descriptors import CheckDescriptor, plain_text
from ._source import Parts, one_line, spellings

if sys.version_info >= (3, 14):
    from string.templatelib import Interpolation, Template

    #: What ``actual_name=`` and ``expected_name=`` accept.
    OperandName = Union[str, Template]

    def _template_texts(given: object) -> Optional[Tuple[str, Optional[str]]]:
        """``(concrete, symbolic)`` of a template string, ``None`` for anything else."""
        if not isinstance(given, Template):
            return None
        concrete, symbolic = [], []
        known = True
        for item in given:
            if isinstance(item, str):
                concrete.append(item)
                symbolic.append(item)
                continue
            concrete.append(_formatted(item))
            expression = one_line(item.expression) if isinstance(item.expression, str) else ""
            known = known and bool(expression)  # a hand-made Interpolation may have none
            symbolic.append(f"<{expression}>")
        return "".join(concrete), "".join(symbolic) if known else None

    def _formatted(item: Interpolation[Any]) -> str:
        """An interpolation as an f-string formats it: the conversion, then the format spec.
        An error (a wrong format spec, a value whose ``__format__`` raises) goes on to the
        test, as it does from the same f-string: it is a mistake in the call."""
        value = item.value
        if item.conversion == "r":
            value = repr(value)
        elif item.conversion == "s":
            value = str(value)
        elif item.conversion == "a":
            value = ascii(value)
        return format(value, item.format_spec)

else:  # pragma: no cover - exercised by the CI jobs before Python 3.14
    OperandName = str

    def _template_texts(given: object) -> Optional[Tuple[str, Optional[str]]]:
        """Template strings do not exist before Python 3.14."""
        return None


class Spelling(NamedTuple):
    """The operand names of one check, as its descriptor stores them (``None``: not given, or
    not known)."""

    actual: Optional[str] = None
    actual_symbolic: Optional[str] = None
    expected: Optional[str] = None
    expected_symbolic: Optional[str] = None

    def on(self, descriptor: CheckDescriptor) -> CheckDescriptor:
        """*descriptor* with ``actual_name`` and the symbolic forms, each only when there is
        one (``expected_name`` is stored by the check type, which also shows it)."""
        if self.actual is not None:
            descriptor["actual_name"] = self.actual
        if self.actual_symbolic is not None:
            descriptor["actual_name_symbolic"] = self.actual_symbolic
        if self.expected_symbolic is not None:
            descriptor["expected_name_symbolic"] = self.expected_symbolic
        return descriptor


#: The spelling of a check made without operand names.
UNSPELLED = Spelling()


def spell(method: str, actual_name: object, expected_name: object = None) -> Spelling:
    """The operand names given to ``verify.<method>()``, checked.

    ``None`` and text that is blank once stripped count as not given. The source of the call
    is read (once per call site) only when a name is a non-blank ``str``.

    Raises:
        TypeError: If a name is not a ``str``, a template string or ``None``.
        Exception: What formatting an interpolation of a template string raises, as the
            same f-string would.
    """
    if actual_name is None and expected_name is None:
        return UNSPELLED
    found: list[Mapping[str, Parts]] = []

    def written() -> Mapping[str, Parts]:
        if not found:
            found.append(spellings(method))
        return found[0]

    actual = _texts(actual_name, "actual_name", method, written)
    expected = _texts(expected_name, "expected_name", method, written)
    return Spelling(actual[0], actual[1], expected[0], expected[1])


def _texts(
    given: object, parameter: str, method: str, written: Callable[[], Mapping[str, Parts]]
) -> Tuple[Optional[str], Optional[str]]:
    """``(concrete, symbolic)`` of one operand name, each stripped; ``(None, None)`` when it
    was not given or is blank."""
    if given is None:
        return None, None
    symbolic: Optional[str]
    from_template = _template_texts(given)
    if from_template is not None:
        concrete, symbolic = from_template
        concrete = concrete.strip()
    elif isinstance(given, str):
        received = plain_text(given)
        concrete = received.strip()
        parts = written().get(parameter) if concrete else None
        symbolic = None if parts is None else symbolic_text(parts, received)
    else:
        raise TypeError(
            f"{method}() {parameter} must be a str, a template string or None, "
            f"got {type(given).__name__}"
        )
    if not concrete:
        return None, None
    if symbolic is not None:
        symbolic = symbolic.strip()
        if not symbolic or symbolic == concrete:
            symbolic = None
    return concrete, symbolic


def symbolic_text(parts: Parts, received: str) -> Optional[str]:
    """The f-string *parts* with each expression as ``<expression>``, or ``None`` when
    *received* cannot be what that f-string made: its literal parts must occur in it in order,
    the first at the start and the last at the end."""
    if not _fits(parts, received):
        return None
    return "".join(part if isinstance(part, str) else f"<{part[0]}>" for part in parts)


def _fits(parts: Parts, received: str) -> bool:
    if not parts:
        return received == ""
    position, end, last = 0, len(received), len(parts) - 1
    first: Any = parts[0]
    if isinstance(first, str):
        if not received.startswith(first):
            return False
        position = len(first)
        if last == 0:
            return position == end
    final: Any = parts[last]
    if last > 0 and isinstance(final, str):
        end -= len(final)
        if end < position or not received.endswith(final):
            return False
    for part in parts[1:last]:
        if isinstance(part, str):
            at = received.find(part, position, end)
            if at < 0:
                return False
            position = at + len(part)
    return True
