"""What editors need to complete ``verify.`` in a test that does not annotate the fixture.

PyCharm does not read pytest11 entry points, and Pylance documents only the test file, its
imports and conftest.py files, so they need ``verify: Verify`` (PyCharm also accepts a conftest
that lists ``pytest_verifier.plugin``; README, "Editor completion and type checking"). jedi reads
the module the entry point names: ``pytest_verifier._entry`` shows it the fixture.

The checks run in a fresh interpreter in an empty folder, as an editor's jedi does: run from a
source tree, this process would also see the tree's ``pytest_verifier.egg-info`` and its entry
points.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import pytest_verifier


def _python(code: str, cwd: Path) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_the_entry_point_names_the_module_that_shows_the_fixture(tmp_path: Path) -> None:
    code = (
        "from importlib import metadata\n"
        "entries = metadata.distribution('pytest-verifier').entry_points\n"
        "print([(e.name, e.value) for e in entries if e.group == 'pytest11'])\n"
    )
    assert _python(code, tmp_path).splitlines() == [
        "[('pytest_verifier', 'pytest_verifier._entry')]"
    ]
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
    assert _python(code, tmp_path).splitlines() == ["['pytest_verifier.plugin'] False"]


@pytest.mark.parametrize("parameter", ["verify", "verify: Verify"])
def test_jedi_completes_the_fixture(tmp_path: Path, parameter: str) -> None:
    # jedi 0.18 reads entry points with pkg_resources, which recent setuptools no longer has.
    pytest.importorskip("jedi", minversion="0.19")
    path = tmp_path / "test_x.py"
    path.write_text(
        f"from pytest_verifier import Verify\n\n\ndef test_x({parameter}):\n    verify.",
        encoding="utf-8",
    )
    code = (
        "import jedi, pathlib\n"
        "path = pathlib.Path('test_x.py').resolve()\n"
        "script = jedi.Script(path.read_text(encoding='utf-8'), path=str(path),\n"
        "                     project=jedi.Project(str(path.parent)),\n"
        "                     environment=jedi.InterpreterEnvironment())\n"
        "print(' '.join(sorted(c.name for c in script.complete(5, len('    verify.')))))\n"
    )
    names = set(_python(code, tmp_path).split())
    assert {"approx", "between", "require", "record", "section"} <= names
