"""Run the documented usage from ``typing_usage.py``, which CI also type-checks (M-16)."""
from __future__ import annotations

import pytest

from pytest_verify import ChecksFailedError, Verify
from pytest_verify import verify as mverify

from .typing_usage import error_api, fixture_api, module_api


def test_documented_module_usage_runs() -> None:
    module_api()


def test_documented_fixture_usage_runs(verify: Verify, request: pytest.FixtureRequest) -> None:
    fixture_api(verify, request)


def test_checks_failed_error_is_an_assertion_error() -> None:
    error = ChecksFailedError([mverify.fail("boom")])
    assert error_api(error) is error
