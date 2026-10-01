"""How pytest finds the plugin, and the deprecated ``pytest_verify`` names that still work.

The loading tests run pytest in a subprocess, so that entry points are discovered as in a real
session.
"""
from __future__ import annotations

import sys
import warnings

import pytest

import pytest_verifier

_FAILING_TEST = """
    def test_a(verify):
        verify.fail("boom")
"""


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


@pytest.fixture()
def no_autoload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")


@pytest.fixture(autouse=True)
def autoload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", raising=False)


def test_the_entry_point_loads_the_plugin(pytester: pytest.Pytester) -> None:
    pytester.makeconftest("""
        def pytest_configure(config):
            manager = config.pluginmanager
            print("HAS", manager.has_plugin("pytest_verifier"),
                  manager.has_plugin("pytest_verifier.plugin"))
    """)
    pytester.makepyfile(_FAILING_TEST)
    result = _run(pytester, "-s")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["HAS True True", "*1 of 1 checks failed*"])


def test_dash_p_loads_the_plugin_without_autoload(
    pytester: pytest.Pytester, no_autoload: None
) -> None:
    pytester.makepyfile(_FAILING_TEST)
    _run(pytester).assert_outcomes(errors=1)  # fixture 'verify' not found
    result = _run(pytester, "-p", "pytest_verifier")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*1 of 1 checks failed*"])


def test_dash_p_no_disables_the_plugin(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(_FAILING_TEST)
    result = _run(pytester, "-p", "no:pytest_verifier")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*fixture 'verify' not found*"])


@pytest.mark.parametrize("autoload_on", [True, False])
@pytest.mark.parametrize("module", ["pytest_verifier", "pytest_verifier.plugin"])
def test_a_conftest_can_list_the_plugin(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, module: str, autoload_on: bool
) -> None:
    """With autoload on as well: 0.5 crashed with "Plugin already registered under a different
    name" when a conftest listed the module the entry point had already loaded (IMP-29)."""
    if not autoload_on:
        monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    pytester.makeconftest(f"pytest_plugins = [{module!r}]\n")
    pytester.makepyfile(_FAILING_TEST)
    result = _run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*1 of 1 checks failed*"])


# ---------------------------------------------------------------------------
# Deprecated names
# ---------------------------------------------------------------------------


def test_the_builder_is_exported_as_checks() -> None:
    assert isinstance(pytest_verifier.checks, pytest_verifier.Verify)
    assert "checks" in pytest_verifier.__all__
    assert "verify" not in pytest_verifier.__all__


def test_pytest_verifier_verify_is_a_deprecated_alias() -> None:
    with pytest.warns(DeprecationWarning, match="builder is now called 'checks'") as record:
        alias = pytest_verifier.verify
    assert alias is pytest_verifier.checks
    assert record[0].filename == __file__


def test_unknown_attributes_still_raise() -> None:
    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        pytest_verifier.nope  # noqa: B018


def test_the_old_package_warns_where_it_is_imported() -> None:
    sys.modules.pop("pytest_verify", None)
    with pytest.warns(DeprecationWarning, match="renamed to pytest_verifier") as record:
        import pytest_verify
    assert record[0].filename == __file__
    assert pytest_verify.verify is pytest_verifier.checks
    for name in ("CheckDescriptor", "ChecksFailedError", "GuardBranch", "Verify"):
        assert getattr(pytest_verify, name) is getattr(pytest_verifier, name)
    assert pytest_verify.get_check_results is pytest_verifier.get_check_results


def test_the_old_import_keeps_working_in_a_test_suite(pytester: pytest.Pytester) -> None:
    """A 0.5 test module runs unchanged and is told, at its import line, what to change."""
    pytester.makepyfile("""
        from pytest_verify import verify as old_builder

        def test_a(verify):
            verify.record(old_builder.equal(1, 2, name="legacy"))
    """)
    result = _run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([
        "*1 of 1 checks failed*",
        "*test_the_old_import_keeps_working_in_a_test_suite.py:1: DeprecationWarning: "
        "pytest_verify was renamed to pytest_verifier*",
    ])


def test_the_old_plugin_module_in_a_conftest_keeps_working(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["pytest_verify._fixture"]\n')
    pytester.makepyfile(_FAILING_TEST)
    result = _run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*1 of 1 checks failed*", "*DeprecationWarning: pytest_verify was renamed*"]
    )


def test_a_leftover_pytest_verify_installation_is_reported(pytester: pytest.Pytester) -> None:
    """Installing pytest-verifier over pytest-verify 0.5 leaves the old ``verify`` entry point,
    which now loads the compatibility module; the session says how to clean up."""
    info = pytester.mkdir("pytest_verify-0.5.0.dist-info")
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: pytest-verify\nVersion: 0.5.0\n")
    (info / "entry_points.txt").write_text("[pytest11]\nverify = pytest_verify._fixture\n")
    pytester.makepyfile(_FAILING_TEST)
    result = _run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([
        "*1 of 1 checks failed*",
        "*PytestConfigWarning: pytest-verify 0.5 is still installed next to pytest-verifier*",
    ])


def test_the_old_plugin_module_loads_the_new_plugin() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from pytest_verify import _fixture
    assert _fixture.pytest_plugins == ["pytest_verifier.plugin"]
