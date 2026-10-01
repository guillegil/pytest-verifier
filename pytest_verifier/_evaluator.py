"""``evaluate()`` and ``evaluate_detailed()``: judging descriptors without recording them.

The verdict of one descriptor comes from :func:`pytest_verifier._checks.judge`, which never
raises: whatever a comparison returns is coerced to a real ``bool``, and an exception (or a
result whose truth value is ambiguous, such as a numpy array) becomes a failed verdict with an
error note.
"""
from __future__ import annotations

import time
from typing import Any

from . import _unused
from ._checks import Verdict, judge, truth
from ._descriptors import CheckDescriptor, is_descriptor

__all__ = ["Verdict", "evaluate", "evaluate_detailed", "judge", "truth"]


def _require_descriptors(descriptors: tuple[Any, ...], function: str) -> None:
    for index, descriptor in enumerate(descriptors):
        if isinstance(descriptor, (list, tuple)):
            raise TypeError(
                f"{function}() takes descriptors as separate arguments; "
                f"use verify.{function}(*checks) to pass a list"
            )
        if not is_descriptor(descriptor):
            raise TypeError(
                f"{function}() argument {index} is not a check descriptor: "
                f"{type(descriptor).__name__}"
            )


def evaluate(*descriptors: CheckDescriptor) -> bool:
    """Evaluate one or more descriptors, returning ``True`` only if ALL pass.

    This is a pure function with no side effects — it does not mutate the
    descriptors or store results anywhere. Every descriptor is evaluated, and a check that
    cannot be evaluated (its comparison raises) counts as failed.
    """
    _require_descriptors(descriptors, "evaluate")
    _unused.used(*descriptors)
    verdicts = [judge(d)[0] for d in descriptors]
    return all(verdicts)


def evaluate_detailed(*descriptors: CheckDescriptor) -> list[dict[str, Any]]:
    """Evaluate descriptors and return detailed result dicts.

    Each result dict contains:
    - ``passed``: bool
    - ``details``: the original descriptor
    - ``seq``: 0-based sequence index
    - ``t``: timestamp of evaluation (seconds since epoch)
    - ``error``: why the check could not be evaluated (only present when it could not)
    """
    _require_descriptors(descriptors, "evaluate_detailed")
    _unused.used(*descriptors)
    results: list[dict[str, Any]] = []
    for seq, d in enumerate(descriptors):
        passed, error = judge(d)
        result: dict[str, Any] = {"passed": passed, "details": d, "seq": seq, "t": time.time()}
        if error is not None:
            result["error"] = error
        results.append(result)
    return results
