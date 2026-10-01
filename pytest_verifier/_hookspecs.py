"""Hooks pytest-verifier calls, so other plugins can read check results without importing it.

Implement them with ``@pytest.hookimpl(optionalhook=True)``: pytest then accepts the hook even
when pytest-verifier is not installed.
"""
from __future__ import annotations

from typing import List

import pytest

from ._descriptors import CheckDescriptor


@pytest.hookspec
def pytest_verify_results(
    item: pytest.Item, when: str, checks: List[CheckDescriptor], passed: bool
) -> None:
    """Called when a test phase ends with checks to judge.

    Checks made in setup and in the test body are judged when the test body ends
    (``when="call"``), checks made in teardown when teardown ends. Every check is passed once.

    Args:
        item: The test item.
        when: ``"setup"`` (only when setup fails or skips), ``"call"`` or ``"teardown"``.
        checks: The checks judged now, in the order they were made, as JSON-safe dicts.
        passed: Whether all of them passed.
    """
