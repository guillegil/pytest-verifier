"""The session summary of ``--verify-summary``: the checks of every test, grouped by name.

It is built from the checks on the reports (``report.verify_checks``), so an xdist controller
sees the checks of every worker. Each label (the name, after the titles of its sections) gets
one line, the checks nested in composites included. With ``stats``, a line also shows the range
of the numeric values and the smallest margin (see :func:`~pytest_verifier._checks.margin`).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional

from ._checks import child_checks, margin
from ._exceptions import label
from ._render import escape, render_value, units_text

#: The values of ``--verify-summary``.
MODES = ("off", "failed", "all", "stats")


class _Group:
    """The checks of one label."""

    __slots__ = ("count", "failed", "first_failure", "low", "high", "margin", "units")

    def __init__(self) -> None:
        self.count = 0
        self.failed = 0
        #: The node ID of the first test in which one of them failed.
        self.first_failure: Optional[str] = None
        #: The smallest and largest numeric value checked, and the smallest margin.
        self.low: Optional[float] = None
        self.high: Optional[float] = None
        self.margin: Optional[float] = None
        self.units: Optional[str] = None

    def measure(self, check: Mapping[str, Any]) -> None:
        actual = check.get("actual")
        if isinstance(actual, (int, float)) and not isinstance(actual, bool):
            if math.isfinite(actual):
                self.low = actual if self.low is None else min(self.low, actual)
                self.high = actual if self.high is None else max(self.high, actual)
        found = margin(check)
        if found is not None:
            self.margin = found if self.margin is None else min(self.margin, found)
        units = check.get("units")
        if self.units is None and isinstance(units, str):
            self.units = units

    def stats(self) -> str:
        """``; 3.21V to 3.41V, margin -0.11V``, or ``""`` without numbers."""
        parts = []
        if self.low is not None and self.high is not None:
            low, high = render_value(self.low, self.units), render_value(self.high, self.units)
            parts.append(low if low == high else f"{low} to {high}")
        if self.margin is not None:
            # ``+ 0.0`` turns ``-0.0`` (a value at a ``less`` limit) into ``0``.
            parts.append(f"margin {self.margin + 0.0:.4g}{units_text(self.units)}")
        return "; " + ", ".join(parts) if parts else ""


class SessionSummary:
    """Collects the checks of a session and renders the ``--verify-summary`` lines."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self._groups: Dict[str, _Group] = {}

    def add(self, nodeid: str, checks: Iterable[Any]) -> None:
        """Count the judged *checks* of the test *nodeid*, and the checks nested in them."""
        for check in checks:
            self._add(nodeid, check)

    def _add(self, nodeid: str, check: Any) -> None:
        if not isinstance(check, Mapping):
            return
        passed = check.get("passed")
        if isinstance(passed, bool):  # children a composite did not select have no verdict
            group = self._groups.setdefault(label(check), _Group())
            group.count += 1
            if not passed:
                group.failed += 1
                if group.first_failure is None:
                    group.first_failure = escape(nodeid)
            if self.mode == "stats":
                group.measure(check)
        for child in child_checks(check):
            self._add(nodeid, child)

    def lines(self) -> List[str]:
        """One line per label, the ones with a failed check first; ``[]`` without checks."""
        if not self._groups:
            return []
        failed = [(name, group) for name, group in self._groups.items() if group.failed]
        if self.mode == "failed":
            if not failed:
                total = sum(group.count for group in self._groups.values())
                return [f"  ✓ all {total} checks passed"]
            shown = failed
        else:
            shown = failed + [(name, g) for name, g in self._groups.items() if not g.failed]
        lines = []
        for name, group in shown:
            if group.failed:
                line = (
                    f"  ✗ {name}: {group.failed} of {group.count} failed "
                    f"(first: {group.first_failure})"
                )
            else:
                line = f"  ✓ {name}: {group.count} passed"
            if self.mode == "stats":
                line += group.stats()
            lines.append(line)
        return lines
