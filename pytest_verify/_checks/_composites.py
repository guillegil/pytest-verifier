"""Composite check types: their verdict comes from child checks.

A child check can be a descriptor, or (for ``conditional`` and ``guard``) a zero-argument
callable that builds it. Only the selected callable is called, through the ``run`` function the
caller passes: the module-level API just calls it, and the fixture also collects the checks
recorded inside it, so they belong to the composite. A callable that was not selected leaves
``None`` in its slot.
"""
from __future__ import annotations

import functools
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .._descriptors import (
    _NO_CASE,
    CheckDescriptor,
    Child,
    GuardBranch,
    case_key,
    is_descriptor,
    require_child,
    require_name,
    select_case,
    unwrap,
)
from .._render import describe_error, safe_repr, safe_str, snapshot
from ._base import CompositeType, RunChild, call, child_detail, judge, register


def _is_lazy(child: object) -> bool:
    return callable(child) and not is_descriptor(child)


def _resolve(child: Any, where: str, run: RunChild) -> Tuple[Any, Optional[str]]:
    """Build a selected lazy child. Returns ``(child, error)``; a descriptor is returned as is.

    A callable that raises, or returns something that is not a check, leaves ``None`` and an
    error note, like an ``all_satisfy`` factory.
    """
    if not _is_lazy(child):
        return child, None
    try:
        built = run(child)
    except Exception as exc:
        return None, f"{where} raised {describe_error(exc)}"
    if not is_descriptor(built):
        return None, (
            f"{where} returned {type(built).__name__}, not a check (did you forget `return`?)"
        )
    return built, None


def _unbuilt(child: Any) -> Any:
    """A child slot after building: ``None`` for a lazy child that was not selected."""
    return None if _is_lazy(child) else child


class AllSatisfy(CompositeType):
    check_type = "all_satisfy"
    child_fields = ("child_checks",)

    @staticmethod
    def build(
        items: Iterable[Any],
        descriptor_factory: Callable[[Any], CheckDescriptor],
        *,
        name: str,
        run: RunChild = call,
    ) -> CheckDescriptor:
        """Build one child check per item by calling *descriptor_factory* right away.

        Problems with the data (``items`` is not iterable, the factory raises or returns
        something that is not a check) do not raise: the descriptor records them in ``error``
        and fails.
        """
        require_name(name, "all_satisfy")
        child_checks: List[CheckDescriptor] = []
        error: Optional[str] = None
        try:
            iterator = iter(items)
        except Exception as exc:
            iterator, error = iter(()), f"items are not iterable: {describe_error(exc)}"
        index = 0
        while error is None:
            try:
                item = next(iterator)
            except StopIteration:
                break
            except Exception as exc:
                error = f"iterating items raised {describe_error(exc)}"
                break
            try:
                child = run(functools.partial(descriptor_factory, item))
            except Exception as exc:
                error = f"descriptor_factory raised {describe_error(exc)} for item {index}"
                break
            if not is_descriptor(child):
                error = (
                    f"descriptor_factory returned {type(child).__name__} for item {index}, "
                    "not a check (did you forget `return`?)"
                )
                break
            child_checks.append(child)
            index += 1
        desc: CheckDescriptor = {
            "check_type": "all_satisfy",
            "name": name,
            "description": (
                f"Verify all items in '{name}' satisfy condition ({len(child_checks)} items)"
            ),
            "child_checks": child_checks,
        }
        if error is not None:
            desc["error"] = error
        return desc

    def children(self, d: Mapping[str, Any]) -> List[Any]:
        return list(d.get("child_checks") or [])

    def chosen(self, d: Mapping[str, Any]) -> List[Any]:
        return self.children(d)

    def combine(self, verdicts: List[bool]) -> bool:
        return all(verdicts)

    def map_children(self, d: Mapping[str, Any], fn: Callable[[Any], Any]) -> Dict[str, Any]:
        return {"child_checks": [fn(child) for child in d.get("child_checks") or []]}

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        children = d.get("child_checks") or []
        total = len(children)
        if passed:
            return f"all {total} items pass"
        failed = sum(1 for child in children if judge(child)[0] is not True)
        return f"expected all {total} to pass, got {failed} failed"


class _Selecting(CompositeType):
    """A composite that selects at most one child: ``conditional`` and ``guard``.

    Their detail starts with a ``[… → child]`` clause that follows the name directly.
    """

    separator = " "

    def selected(self, d: Mapping[str, Any]) -> Optional[Tuple[str, Any]]:
        """``(label, child)`` for the selected child, or ``None`` when nothing was selected."""
        raise NotImplementedError

    def chosen(self, d: Mapping[str, Any]) -> List[Any]:
        selected = self.selected(d)
        return [] if selected is None else [selected[1]]

    def combine(self, verdicts: List[bool]) -> bool:
        return bool(verdicts) and all(verdicts)


class Conditional(_Selecting):
    check_type = "conditional"
    child_fields = ("cases", "default")

    @staticmethod
    def build(
        switch_value: Any,
        *,
        cases: Mapping[Any, Child],
        default: Optional[Child] = None,
        name: str,
        run: RunChild = call,
    ) -> CheckDescriptor:
        require_name(name, "conditional")
        if not isinstance(cases, Mapping):
            raise TypeError(f"conditional() cases must be a mapping, got {type(cases).__name__}")
        normalized: Dict[str, Any] = {}
        originals: Dict[str, Any] = {}
        for key, check in cases.items():
            stored = case_key(key)
            if stored in normalized:
                raise ValueError(
                    f"conditional() case keys {safe_repr(originals[stored])} and "
                    f"{safe_repr(key)} are both stored as {stored!r}; use distinct keys"
                )
            normalized[stored] = require_child(check, f"conditional() case {safe_repr(key)}")
            originals[stored] = key
        if default is not None:
            require_child(default, "conditional() default")
        selected = select_case(switch_value, cases.keys())
        matched = None if selected is _NO_CASE else case_key(selected)
        error: Optional[str] = None
        if matched is not None:
            where = f"case {safe_repr(originals[matched])}"
            normalized[matched], error = _resolve(normalized[matched], where, run)
        elif default is not None:
            default, error = _resolve(default, "default", run)
        label = safe_str(switch_value)
        desc: CheckDescriptor = {
            "check_type": "conditional",
            "name": name,
            "description": f"Verify '{name}' [mode={label}]",
            "switch_value": snapshot(unwrap(switch_value)),
            "switch_label": label,
            "cases": {key: _unbuilt(child) for key, child in normalized.items()},
            "default": _unbuilt(default),
            "matched_case": matched,
        }
        if error is not None:
            desc["error"] = error
        return desc

    def selected(self, d: Mapping[str, Any]) -> Optional[Tuple[str, Any]]:
        """The stored ``matched_case`` is authoritative; it is recomputed only when the field
        is missing (a hand-built descriptor)."""
        cases = d.get("cases") or {}
        if "matched_case" in d:
            key = d.get("matched_case")
        else:
            found = select_case(d.get("switch_value"), cases.keys())
            key = None if found is _NO_CASE else found
        if key is not None and key in cases:
            return str(key), cases[key]
        default = d.get("default")
        return None if default is None else ("default", default)

    def children(self, d: Mapping[str, Any]) -> List[Any]:
        return [*(d.get("cases") or {}).values(), d.get("default")]

    def map_children(self, d: Mapping[str, Any], fn: Callable[[Any], Any]) -> Dict[str, Any]:
        cases = d.get("cases") or {}
        return {
            "cases": {str(key): fn(child) for key, child in cases.items()},
            "default": fn(d.get("default")),
        }

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        mode = f"mode={d.get('switch_label', safe_str(d.get('switch_value')))}"
        selected = self.selected(d)
        if selected is None:
            return f"[{mode} → no match]"
        child = selected[1]
        if child is None:
            return f"[{mode} → no check]"
        return f"[{mode} → {child.get('name', '')}] — {child_detail(child, passed)}"


def _unpack_branch(branch: object, index: int) -> Tuple[object, Any, Any]:
    condition: object
    label: Any
    check: Any
    try:
        condition, label, check = branch  # type: ignore[misc]
    except (TypeError, ValueError):
        raise TypeError(
            f"guard() branch {index} must be a (condition, label, check) tuple, "
            f"got {safe_repr(branch)}"
        ) from None
    if is_descriptor(condition):
        raise TypeError(
            f"guard() branch {index} condition is a check descriptor, which is always truthy; "
            "use its verdict instead, e.g. check['passed']"
        )
    return condition, label, require_child(check, f"guard() branch {index} check")


class Guard(_Selecting):
    check_type = "guard"
    child_fields = ("branches", "default")

    @staticmethod
    def build(
        branches: Sequence[Tuple[object, str, Child]],
        *,
        default: Optional[Child] = None,
        name: str,
        run: RunChild = call,
    ) -> CheckDescriptor:
        """Build a guard descriptor: an ordered if/elif/else chain.

        Each branch is a ``(condition, label, check)`` tuple. The first branch whose condition
        is truthy is selected; if none is, ``default`` is. A callable condition is called, in
        order, until one branch matches.
        """
        require_name(name, "guard")
        if default is not None:
            require_child(default, "guard() default")
        unpacked = [_unpack_branch(branch, index) for index, branch in enumerate(branches)]
        normalized: List[GuardBranch] = []
        matched_index: Optional[int] = None
        error: Optional[str] = None
        for index, (condition, label, check) in enumerate(unpacked):
            deciding = matched_index is None and error is None
            truth: Optional[bool]
            if _is_lazy(condition) and not deciding:
                truth = None  # not called: an earlier branch already decided
            else:
                try:
                    lazy = _is_lazy(condition)
                    truth = bool(condition() if lazy else condition)  # type: ignore[operator]
                except Exception as exc:
                    truth = False
                    if deciding:
                        error = (
                            f"condition of branch {index} ({safe_str(label)}) raised "
                            f"{describe_error(exc)}"
                        )
                if truth and deciding and error is None:
                    matched_index = index
            normalized.append({"condition": truth, "label": label, "check": check})
        if error is None and matched_index is not None:
            branch = normalized[matched_index]
            where = f"branch {matched_index} ({safe_str(branch['label'])})"
            branch["check"], error = _resolve(branch["check"], where, run)
        elif error is None and default is not None:
            default, error = _resolve(default, "default", run)
        for branch in normalized:
            branch["check"] = _unbuilt(branch["check"])
        desc: CheckDescriptor = {
            "check_type": "guard",
            "name": name,
            "description": f"Verify '{name}' [guarded]",
            "branches": normalized,
            "default": _unbuilt(default),
            "matched_index": matched_index,
        }
        if error is not None:
            desc["error"] = error
        return desc

    def selected(self, d: Mapping[str, Any]) -> Optional[Tuple[str, Any]]:
        """The stored ``matched_index`` is authoritative; it is recomputed only when the field
        is missing (a hand-built descriptor)."""
        branches = d.get("branches") or []
        if "matched_index" in d:
            matched = d.get("matched_index")
        else:
            matched = next((i for i, b in enumerate(branches) if b.get("condition")), None)
        if matched is not None:
            branch = branches[matched]
            return str(branch.get("label", "")), branch.get("check")
        default = d.get("default")
        return None if default is None else ("default", default)

    def children(self, d: Mapping[str, Any]) -> List[Any]:
        return [*(branch.get("check") for branch in d.get("branches") or []), d.get("default")]

    def map_children(self, d: Mapping[str, Any], fn: Callable[[Any], Any]) -> Dict[str, Any]:
        return {
            "branches": [
                {
                    "condition": snapshot(branch.get("condition")),
                    "label": snapshot(branch.get("label")),
                    "check": fn(branch.get("check")),
                }
                for branch in d.get("branches") or []
            ],
            "default": fn(d.get("default")),
        }

    def detail(self, d: Mapping[str, Any], passed: bool) -> str:
        selected = self.selected(d)
        if selected is None:
            return "[→ no match]"
        label, child = selected
        if child is None:
            return f"[→ {label}]"
        return f"[→ {label}] — {child_detail(child, passed)}"


ALL_SATISFY = register(AllSatisfy())
CONDITIONAL = register(Conditional())
GUARD = register(Guard())
