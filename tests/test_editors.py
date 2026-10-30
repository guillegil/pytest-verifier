"""What editors need to complete ``verify.`` in a test that does not annotate the fixture.

PyCharm and Pylance do not read pytest11 entry points, so they need ``verify: Verify``, or a
conftest that lists ``pytest_verifier.plugin`` (README, "Editor completion and type checking").
jedi reads the module the entry point names: ``pytest_verifier._entry`` shows it the fixture.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

import pytest_verifier


def test_the_entry_point_names_the_module_that_shows_the_fixture() -> None:
    (entry,) = [
        e for e in metadata.distribution("pytest-verifier").entry_points if e.group == "pytest11"
    ]
    assert (entry.name, entry.value) == ("pytest_verifier", "pytest_verifier._entry")
    source = Path(pytest_verifier.__file__).with_name("_entry.py").read_text(encoding="utf-8")
    imports = [
        [(alias.name, alias.asname) for alias in node.names]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and (node.level, node.module) == (1, "plugin")
    ]
    # jedi takes a fixture from an import only when the line imports that one name, without "as".
    assert imports == [[("verify", None)]]


def test_the_entry_module_loads_the_plugin_without_importing_it(tmp_path: Path) -> None:
    """pytest registers the plugin from ``pytest_plugins``; importing it first would make pytest
    warn that it cannot rewrite it."""
    code = (
        "import sys, pytest_verifier._entry as entry\n"
        "print(entry.pytest_plugins, 'pytest_verifier.plugin' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert result.stdout.split("\n")[0] == "['pytest_verifier.plugin'] False"


@pytest.mark.parametrize("parameter", ["verify", "verify: Verify"])
def test_jedi_completes_the_fixture(tmp_path: Path, parameter: str) -> None:
    jedi = pytest.importorskip("jedi")
    source = f"from pytest_verifier import Verify\n\n\ndef test_x({parameter}):\n    verify."
    path = tmp_path / "test_x.py"
    path.write_text(source, encoding="utf-8")
    # The package's folder, which an editable install does not put on sys.path.
    package_root = str(Path(pytest_verifier.__file__).parent.parent)
    project = jedi.Project(str(tmp_path), added_sys_path=[package_root])
    script = jedi.Script(
        source, path=str(path), project=project, environment=jedi.InterpreterEnvironment()
    )
    names = {completion.name for completion in script.complete(5, len("    verify."))}
    assert {"approx", "between", "require", "record", "section"} <= names
