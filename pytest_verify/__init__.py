"""Deprecated: pytest-verify is now pytest-verifier.

Import from ``pytest_verifier`` instead. The builder is now called ``checks``, so it cannot be
confused with the ``verify`` fixture::

    from pytest_verifier import checks

This package only forwards to ``pytest_verifier`` and will be removed in a future release.
"""
from __future__ import annotations

import os
import sys
import warnings
from types import FrameType
from typing import Optional

from pytest_verifier import (
    CheckDescriptor,
    ChecksFailedError,
    GuardBranch,
    Verify,
    get_check_results,
)
from pytest_verifier import checks as verify

__all__ = [
    "CheckDescriptor",
    "ChecksFailedError",
    "GuardBranch",
    "Verify",
    "get_check_results",
    "verify",
]

_RENAMED = (
    "pytest_verify was renamed to pytest_verifier, and its 'verify' builder to 'checks': use "
    "'from pytest_verifier import checks'. The pytest_verify package will be removed in a "
    "future release."
)


def _import_machinery(filename: str) -> bool:
    """Frames between the importing module and this one: importlib and pytest's import hook."""
    return (
        filename == __file__
        or filename.startswith("<frozen importlib")
        or filename.endswith(os.path.join("_pytest", "assertion", "rewrite.py"))
        or os.path.join("importlib", "_bootstrap") in filename
    )


def _warn_at_importer() -> None:
    """Warn at the line that imported this package, past the import machinery."""
    frame: Optional[FrameType] = sys._getframe(1)
    while frame is not None and _import_machinery(frame.f_code.co_filename):
        frame = frame.f_back
    if frame is None:
        warnings.warn(_RENAMED, DeprecationWarning, stacklevel=2)
        return
    module_globals = frame.f_globals
    warnings.warn_explicit(
        _RENAMED,
        DeprecationWarning,
        frame.f_code.co_filename,
        frame.f_lineno,
        module=module_globals.get("__name__"),
        registry=module_globals.setdefault("__warningregistry__", {}),
        module_globals=module_globals,
    )


_warn_at_importer()
