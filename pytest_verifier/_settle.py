"""Turning a descriptor into a recorded result.

A check is judged exactly once, when it is recorded. The record keeps the verdict, the
rendered ``detail`` (built from the live values at that moment) and JSON-safe snapshots of the
values, so later mutation of the user's objects can neither change nor contradict what was
reported, and nothing keeps the user's objects alive.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ._checks import REGISTRY, CompositeType, judge, lookup, render_detail
from ._descriptors import CheckDescriptor, is_descriptor, plain_units
from ._render import VALUE_LIMIT, describe_error, safe_repr, snapshot, utf8_safe

#: Looks up a descriptor the fixture already recorded: ``(passed, record)`` or ``None``.
Known = Callable[[Any], Optional[Tuple[bool, CheckDescriptor]]]

_VERDICT_FIELDS = frozenset({"passed", "detail", "error"})

#: Fields that hold child checks, in any composite. A record rebuilds them instead of copying.
_CHILD_FIELDS = frozenset(
    field
    for check in REGISTRY.values()
    if isinstance(check, CompositeType)
    for field in check.child_fields
)


def _unknown(descriptor: Any) -> None:
    return None


def _evidence(descriptor: Mapping[str, Any]) -> Dict[str, Any]:
    """JSON-safe copies of every field except the verdict and the child checks. Units given
    as a ``str`` subclass (a hand-built descriptor's ``str`` enum member) are kept as their
    text, as the check methods store them."""
    check = lookup(descriptor)
    limits = {} if check is None else check.snapshot_limits
    return {
        _field(key): (
            snapshot(value, limits[key], VALUE_LIMIT)
            if key in limits
            else snapshot(plain_units(value) if key == "units" else value)
        )
        for key, value in descriptor.items()
        if key not in _VERDICT_FIELDS and key not in _CHILD_FIELDS
    }


def _field(key: Any) -> str:
    """A field name of a record: a hand-built descriptor's key that is not a str becomes its
    ``repr``, so the record stays JSON-safe."""
    return utf8_safe(key if isinstance(key, str) else safe_repr(key))


def _with_verdict(record: Dict[str, Any], passed: bool, error: Optional[str]) -> CheckDescriptor:
    record.pop("error", None)
    record["passed"] = passed
    if error is not None:
        record["error"] = error
    record["detail"] = render_detail(record, passed, error)
    return record  # type: ignore[return-value]


def settle(descriptor: Any, known: Known = _unknown) -> Tuple[CheckDescriptor, bool]:
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
        invalid: Dict[str, Any] = {"check_type": "invalid", "name": "", "description": ""}
        reason = f"not a check descriptor: {safe_repr(descriptor)}"
        return _with_verdict(invalid, False, reason), False
    check = lookup(descriptor)
    if isinstance(check, CompositeType):
        return _settle_composite(check, descriptor, known)
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
    check: CompositeType, descriptor: Mapping[str, Any], known: Known
) -> Tuple[CheckDescriptor, bool]:
    record = _evidence(descriptor)
    error = descriptor.get("error")
    try:
        chosen = {id(child) for child in check.chosen(descriptor)}
    except Exception as exc:  # a malformed hand-built descriptor
        chosen, error = set(), error or describe_error(exc)
    verdicts: List[bool] = []

    def place(child: Any) -> Any:
        if child is None:
            if id(child) in chosen:  # a selected slot with no check fails
                verdicts.append(False)
            return None
        if id(child) in chosen:
            child_record, child_passed = settle(child, known)
            verdicts.append(child_passed)
            return child_record
        return strip_verdicts(child, known)

    try:
        record.update(check.map_children(descriptor, place))
    except Exception as exc:  # a malformed hand-built descriptor
        error = error or describe_error(exc)
    passed = error is None and check.combine(verdicts) and not check.vetoed(descriptor)
    return _with_verdict(record, passed, error), passed


def strip_verdicts(descriptor: Any, known: Known = _unknown) -> Any:
    """A JSON-safe copy of a child that was not selected: no ``passed``, ``detail`` or ``error``
    anywhere in it, because it was never part of the verdict."""
    hit = known(descriptor)
    source = hit[1] if hit is not None else descriptor
    if not is_descriptor(source):
        return snapshot(source)
    record = _evidence(source)
    check = lookup(source)
    if isinstance(check, CompositeType):
        try:
            record.update(check.map_children(source, strip_verdicts))
        except Exception:  # a malformed hand-built descriptor: keep its evidence only
            pass
    return record
