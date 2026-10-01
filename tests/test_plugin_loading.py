"""How pytest finds the plugin, the deprecated ``pytest_verifier.verify`` alias, and what
happens next to an old pytest-verify installation.

The loading tests run pytest in a subprocess, so that entry points are discovered as in a real
session.
"""
from __future__ import annotations

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


def test_from_import_of_the_alias_warns_once() -> None:
    with pytest.warns(DeprecationWarning) as record:
        from pytest_verifier import verify  # noqa: F401
    assert len(record) == 1


def _old_installation(pytester: pytest.Pytester) -> None:
    """What pytest-verify 0.5 installs: its entry point, its hook spec and its plugin module."""
    info = pytester.mkdir("pytest_verify-0.5.0.dist-info")
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: pytest-verify\nVersion: 0.5.0\n")
    (info / "entry_points.txt").write_text("[pytest11]\nverify = pytest_verify._fixture\n")
    package = pytester.mkpydir("pytest_verify")
    (package / "_hookspecs.py").write_text(
        "import pytest\n\n"
        "@pytest.hookspec\n"
        "def pytest_verify_results(item, when, checks, passed):\n"
        "    pass\n"
    )
    (package / "_fixture.py").write_text(
        "def pytest_addhooks(pluginmanager):\n"
        "    from pytest_verify import _hookspecs\n"
        "    pluginmanager.add_hookspecs(_hookspecs)\n"
    )


def test_an_old_installation_is_a_usage_error(pytester: pytest.Pytester) -> None:
    """Both plugins register the same hook; the session says what to uninstall."""
    _old_installation(pytester)
    pytester.makepyfile(_FAILING_TEST)
    result = _run(pytester)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*pytest-verify, the previous name of pytest-verifier, is still*"])


def test_an_old_installation_is_detected_whichever_plugin_loads_first(
    pytester: pytest.Pytester,
) -> None:
    from pytest_verifier import plugin

    _old_installation(pytester)
    manager = pytest.PytestPluginManager()
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(pytester.path))
        assert plugin._old_plugin_will_load(manager)
        manager.set_blocked("verify")
        assert not plugin._old_plugin_will_load(manager)


def test_an_old_installation_that_is_not_loaded_is_fine(pytester: pytest.Pytester) -> None:
    _old_installation(pytester)
    pytester.makepyfile(_FAILING_TEST)
    result = _run(pytester, "-p", "no:verify")
    result.assert_outcomes(failed=1)


def test_a_conftest_listing_the_package_after_a_filter_imported_it(
    pytester: pytest.Pytester, no_autoload: None
) -> None:
    """The documented filter imports the package before conftests load; listing it then must
    not warn that it cannot be rewritten."""
    pytester.makeini("""
        [pytest]
        filterwarnings =
            error
            ignore::pytest_verifier.UnusedCheckWarning
    """)
    pytester.makeconftest('pytest_plugins = ["pytest_verifier"]\n')
    pytester.makepyfile("def test_a(verify):\n    verify.equal(1, 1, name='one')\n")
    result = _run(pytester)
    result.assert_outcomes(passed=1)
    assert "PytestAssertRewriteWarning" not in result.stdout.str() + result.stderr.str()


def test_blocking_the_package_name_does_not_stop_the_plugin_module(
    pytester: pytest.Pytester, no_autoload: None
) -> None:
    pytester.makeconftest('pytest_plugins = ["pytest_verifier.plugin"]\n')
    pytester.makepyfile(_FAILING_TEST)
    result = _run(pytester, "-p", "no:pytest_verifier")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*'-p no:pytest_verifier' did not turn the plugin off*"])
