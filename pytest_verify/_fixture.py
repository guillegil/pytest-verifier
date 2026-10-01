"""Deprecated: the plugin module is now ``pytest_verifier.plugin``.

Kept so that a conftest with ``pytest_plugins = ["pytest_verify._fixture"]`` still loads the
plugin, and so does the ``verify`` entry point of a pytest-verify 0.5 installation that is still
there.
"""
from __future__ import annotations

import sys

import pytest

pytest_plugins = ["pytest_verifier.plugin"]

_STALE = (
    "pytest-verify 0.5 is still installed next to pytest-verifier, and loads the plugin under "
    "its old name 'verify', so '-p no:pytest_verifier' cannot turn it off. Run "
    "'pip uninstall pytest-verify', then reinstall pytest-verifier."
)


def pytest_configure(config: pytest.Config) -> None:
    if config.pluginmanager.get_plugin("verify") is sys.modules[__name__]:
        config.issue_config_time_warning(pytest.PytestConfigWarning(_STALE), stacklevel=2)
