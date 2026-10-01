"""Turning a descriptor into a recorded result.

A check is judged exactly once, when it is recorded. The record keeps the verdict, the
rendered ``detail`` (built from the live values at that moment) and JSON-safe snapshots of the
values, so later mutation of the user's objects can neither change nor contradict what was
reported, and nothing keeps the user's objects alive.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Tuple

from ._descriptors import COMPOSITE_TYPES, CheckDescriptor, is_descriptor
from ._evaluator import judge, selected_child
from ._exceptions import render_detail
from ._render import describe_error, safe_repr, snapshot

#: Looks up a descriptor the fixture already recorded: ``(passed, record)`` or ``None``.
Known = Callable[[Any], Optional[Tuple[bool, CheckDescriptor]]]

_VERDICT_FIELDS = frozenset({"passed", "detail", "error"})
_CHILD_FIELDS = frozenset({"child_checks", "branches", "cases", "default"})


def _unknown(descriptor: Any) -> None:
    return None


def _evidence(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe copies of every field except the verdict and the child checks."""
    return {
        key: snapshot(value)
        for key, value in descriptor.items()
        if key not in _VERDICT_FIELDS and key not in _CHILD_FIELDS
    }


def _with_verdict(record: dict[str, Any], passed: bool, error: str | None) -> CheckDescriptor:
    record.pop("error", None)
    record["passed"] = passed
    if error is not None:
        record["error"] = error
    record["detail"] = render_detail(record, passed, error)
    return record  # type: ignore[return-value]


def settle(descriptor: Any, known: Known = _unknown) -> tuple[CheckDescriptor, bool]:
    """Judge *descriptor* (and the children it selects) and return ``(record, passed)``.

    Descriptors that *known* recognises keep their recorded verdict. Every evaluated child of a
    composite carries its own ``passed``; children a guard/conditional did not select carry no
    verdict at all.
    """
    hit = known(descriptor)
    if hit is not None:
        passed, recorded = hit
        out = dict(recorded)
        out["passed"] = passed
        return out, passed  # type: ignore[return-value]
    if not is_descriptor(descriptor):
        invalid: dict[str, Any] = {"check_type": "invalid", "name": "", "description": ""}
        return _with_verdict(invalid, False, f"not a check descriptor: {safe_repr(descriptor)}"), False
    if descriptor.get("check_type") in COMPOSITE_TYPES:
        return _settle_composite(descriptor, known)
    passed, error = judge(descriptor)
    # Render from the live values now; the record keeps only the snapshots.
    detail = render_detail(descriptor, passed, error)
    record = _evidence(descriptor)
    record["passed"] = passed
    if error is not None:
        record["error"] = error
    record["detail"] = detail
    return record, passed  # type: ignore[return-value]


def _settle_composite(
    descriptor: Mapping[str, Any], known: Known
) -> tuple[CheckDescriptor, bool]:
    record = _evidence(descriptor)
    check_type = descriptor["check_type"]
    error = descriptor.get("error")
    passed = False

    if check_type == "all_satisfy":
        children, verdicts = [], []
        for child in descriptor.get("child_checks") or []:
            child_record, child_passed = settle(child, known)
            children.append(child_record)
            verdicts.append(child_passed)
        record["child_checks"] = children
        passed = all(verdicts)
    else:
        try:
            selected = selected_child(descriptor)
        except Exception as exc:  # a malformed hand-built descriptor
            selected, error = None, error or describe_error(exc)
        chosen = None if selected is None else selected[1]

        def place(child: Any) -> Any:
            nonlocal passed
            if child is None:
                return None
            if chosen is not None and child is chosen:
                child_record, passed = settle(child, known)
                return child_record
            return strip_verdicts(child, known)

        if check_type == "guard":
            record["branches"] = [
                {
                    "condition": snapshot(branch.get("condition")),
                    "label": snapshot(branch.get("label")),
                    "check": place(branch.get("check")),
                }
                for branch in descriptor.get("branches") or []
            ]
        else:
            record["cases"] = {
                str(key): place(child) for key, child in (descriptor.get("cases") or {}).items()
            }
        record["default"] = place(descriptor.get("default"))

    if error is not None:
        passed = False
    return _with_verdict(record, passed, error), passed


def strip_verdicts(descriptor: Any, known: Known = _unknown) -> Any:
    """A JSON-safe copy of a child that was not selected: no ``passed``, ``detail`` or ``error``
    anywhere in it, because it was never part of the verdict."""
    hit = known(descriptor)
    source = hit[1] if hit is not None else descriptor
    if not is_descriptor(source):
        return snapshot(source)
    record = _evidence(source)
    check_type = source.get("check_type")
    if check_type == "all_satisfy":
        record["child_checks"] = [strip_verdicts(c) for c in source.get("child_checks") or []]
    elif check_type == "guard":
        record["branches"] = [
            {
                "condition": snapshot(branch.get("condition")),
                "label": snapshot(branch.get("label")),
                "check": strip_verdicts(branch.get("check")),
            }
            for branch in source.get("branches") or []
        ]
    elif check_type == "conditional":
        record["cases"] = {
            str(key): strip_verdicts(child) for key, child in (source.get("cases") or {}).items()
        }
    if check_type in ("guard", "conditional"):
        default = source.get("default")
        record["default"] = None if default is None else strip_verdicts(default)
    return record
