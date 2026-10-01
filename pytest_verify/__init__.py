"""pytest-verify — soft assertions for pytest.

Usage as a **pytest fixture** (primary API)::

    def test_example(verify):
        verify.approx(3.28, 3.3, abs_tol=0.05, name="Vout", units="V")
        verify.greater(100, 50, name="Throughput", units="Mbps")

Usage as a **module** (secondary API)::

    from pytest_verify import verify

    desc = verify.approx(3.28, 3.3, abs_tol=0.05, name="Vout", units="V")
    assert verify.evaluate(desc)
"""
from __future__ import annotations

import pytest

from ._descriptors import CheckDescriptor, GuardBranch
from ._exceptions import ChecksFailedError
from ._stash import check_results_key
from ._verify import Verify

__all__ = [
    "CheckDescriptor",
    "ChecksFailedError",
    "GuardBranch",
    "Verify",
    "get_check_results",
    "verify",
]

#: Module-level ``Verify`` instance.  Returns unevaluated descriptors.
verify: Verify = Verify()


def get_check_results(item: pytest.Item) -> list[CheckDescriptor]:
    """Return verification check descriptors recorded for this test item.

    Args:
        item: The pytest test item whose check results to retrieve.

    Returns:
        A new list (copy) of every ``CheckDescriptor`` recorded for *item*
        during its execution.  Returns ``[]`` if no checks were recorded.
        Each descriptor carries ``passed`` and ``detail`` and holds JSON-safe
        snapshots of the checked values, so ``json.dumps`` works on it. Checks
        nested in a composite are inside their parent, not listed on their own.
        If the item was rerun, only the last attempt's checks are returned.
    """
    results = item.stash.get(check_results_key, None)
    return [] if results is None else list(results)
