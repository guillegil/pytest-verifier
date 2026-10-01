"""Run the documented usage from ``typing_usage.py``, which CI also type-checks (M-16)."""
from __future__ import annotations

import pytest

from pytest_verifier import ChecksFailedError, Verify
from pytest_verifier import checks as mverify

from .typing_usage import deprecated_alias, error_api, fixture_api, module_api


def test_documented_module_usage_runs() -> None:
    module_api()


def test_documented_fixture_usage_runs(verify: Verify, request: pytest.FixtureRequest) -> None:
    fixture_api(verify, request)


def test_checks_failed_error_is_an_assertion_error() -> None:
    error = ChecksFailedError([mverify.fail("boom")])
    assert error_api(error) is error


def test_the_deprecated_alias_runs_and_warns() -> None:
    with pytest.warns(DeprecationWarning, match="builder is now called 'checks'"):
        check = deprecated_alias()
    assert mverify.evaluate(check)
