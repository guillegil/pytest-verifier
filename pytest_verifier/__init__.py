"""pytest-verifier: soft assertions for pytest.

Usage as a **pytest fixture** (primary API)::

    def test_example(verify):
        verify.approx(3.28, 3.3, abs_tol=0.05, name="Vout", units="V")
        verify.greater(100, 50, name="Throughput", units="Mbps")

Building checks without recording them (secondary API)::

    from pytest_verifier import checks

    desc = checks.approx(3.28, 3.3, abs_tol=0.05, name="Vout", units="V")
    assert checks.evaluate(desc)

PYTEST_DONT_REWRITE: the package has no asserts to rewrite, and a conftest may list it in
``pytest_plugins`` after a ``filterwarnings`` entry has imported it.
"""
from __future__ import annotations

import sys
import warnings
from typing import TYPE_CHECKING, Any

import pytest

from ._descriptors import CheckDescriptor, GuardBranch
from ._exceptions import ChecksFailedError
from ._limits import LimitRow, load_limits
from ._stash import check_results_key
from ._unused import UnusedCheckWarning
from ._verify import Raises, Require, Verify

#: The pytest plugin. The ``pytest_verifier`` entry point names this package, which loads it.
pytest_plugins = ["pytest_verifier.plugin"]

__all__ = [
    "CheckDescriptor",
    "ChecksFailedError",
    "GuardBranch",
    "LimitRow",
    "Raises",
    "Require",
    "UnusedCheckWarning",
    "Verify",
    "__version__",
    "checks",
    "get_check_results",
    "load_limits",
]


def _installed_version() -> str:
    try:
        from importlib import metadata

        return metadata.version("pytest-verifier")
    except Exception:  # running from a source tree that is not installed
        return "0+unknown"


#: The installed version of pytest-verifier, e.g. ``"0.8.0"``.
__version__: str = _installed_version()

#: Builds checks without recording them: every method returns an unevaluated descriptor.
#: In a test, use the ``verify`` fixture instead; it records the checks that decide the outcome.
checks: Verify = Verify()


if TYPE_CHECKING:
    #: Deprecated alias of :data:`checks`.
    verify: Verify = checks
else:

    def __getattr__(name: str) -> Any:
        if name == "verify":
            if sys._getframe(1).f_code.co_name == "_handle_fromlist":
                # ``from pytest_verifier import verify`` looks the name up twice; warn once.
                return checks
            warnings.warn(
                "pytest_verifier.verify is deprecated: the builder is now called 'checks' "
                "(from pytest_verifier import checks), so it cannot be confused with the "
                "'verify' fixture.",
                DeprecationWarning,
                stacklevel=2,
            )
            return checks
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
