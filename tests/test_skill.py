"""The agent skill stays in step with the code (``pytest_verifier/_skill``).

These tests fail when a public method, an export or an option is missing from the skill, or when
the skill's version is not the package's: update the skill with every user-visible change.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import List, Set

import pytest

import pytest_verifier
from pytest_verifier import Require, Verify, _installer

SHIPPED = _installer.shipped_files()
SKILL = SHIPPED["SKILL.md"].decode("utf-8")
FIELDS = _installer.frontmatter(SKILL)
#: Every file of the skill, which is what an agent can read.
TEXT = "\n".join(data.decode("utf-8") for data in SHIPPED.values())


def _body(text: str) -> str:
    return text.split("\n---\n", 1)[1]


def _python_blocks() -> List[str]:
    blocks = []
    for name, data in SHIPPED.items():
        blocks += re.findall(r"^```python\n(.*?)^```", data.decode("utf-8"), flags=re.M | re.S)
    return blocks


def _public_methods(cls: type) -> Set[str]:
    return {name for name in vars(cls) if not name.startswith("_")}


class TestFrontmatter:
    def test_name_matches_the_folder_it_is_installed_in(self) -> None:
        assert FIELDS["name"] == _installer.SKILL_NAME == "pytest-verifier"

    def test_description_follows_the_agent_skills_rules(self) -> None:
        description = FIELDS["description"]
        assert 200 <= len(description) <= 1024
        assert "<" not in description and ">" not in description

    def test_only_known_fields(self) -> None:
        top = {key for key in FIELDS if "." not in key}
        assert top <= {"name", "description", "metadata", "license", "compatibility"}

    def test_version_is_the_package_version(self) -> None:
        version = pytest_verifier.__version__
        if version == "0+unknown":  # a source tree that is not installed
            pyproject = Path(__file__).parents[1] / "pyproject.toml"
            if not pyproject.exists():
                pytest.skip("no installed version and no pyproject.toml")
            found = re.search(r'^version = "(.+)"$', pyproject.read_text(), flags=re.M)
            assert found is not None
            version = found.group(1)
        assert FIELDS["metadata.version"] == version, (
            "update the skill for this release and set metadata.version in "
            "pytest_verifier/_skill/SKILL.md"
        )


class TestCoverage:
    """Everything a user can call or set is in the skill."""

    @pytest.mark.parametrize("method", sorted(_public_methods(Verify)))
    def test_every_verify_method(self, method: str) -> None:
        # As an attribute (verify.equal), a call (equal(...)) or code (`equal`).
        pattern = rf"\.{method}\b|\b{method}\(|`{method}`"
        assert re.search(pattern, TEXT), f"verify.{method} is not in the skill"

    def test_require_is_callable_with_a_check(self) -> None:
        assert "__call__" in vars(Require)
        assert re.search(r"verify\.require\(", TEXT)

    @pytest.mark.parametrize("name", sorted(set(pytest_verifier.__all__) - {"__version__"}))
    def test_every_export(self, name: str) -> None:
        assert re.search(rf"\b{name}\b", TEXT), f"pytest_verifier.{name} is not in the skill"

    def test_every_option_and_ini_setting(self, pytester: pytest.Pytester) -> None:
        result = pytester.runpytest("--help")
        help_text = "\n".join(result.outlines)
        options = set(re.findall(r"--verify-[a-z-]+", help_text))
        settings = set(re.findall(r"^\s+(verify_\w+)\s", help_text, flags=re.M))
        assert options and settings
        for name in sorted(options | settings):
            assert name in TEXT, f"{name} is not in the skill"

    def test_the_results_contract(self) -> None:
        for name in ("pytest_verify_results", "verify_checks", "optionalhook"):
            assert name in TEXT

    def test_the_install_command(self) -> None:
        assert "pytest-verifier skill install" in TEXT


class TestShape:
    def test_body_stays_short(self) -> None:
        assert len(_body(SKILL).splitlines()) < 500

    def test_python_examples_compile(self) -> None:
        blocks = _python_blocks()
        assert blocks
        for block in blocks:
            ast.parse(block)

    def test_examples_call_only_real_methods(self) -> None:
        known = _public_methods(Verify) | {"require"}
        for block in _python_blocks():
            for node in ast.walk(ast.parse(block)):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in ("verify", "checks")
                ):
                    assert node.attr in known, f"{node.value.id}.{node.attr} does not exist"

    def test_references_are_linked(self) -> None:
        for name in SHIPPED:
            if name != "SKILL.md":
                assert name in SKILL, f"SKILL.md does not point to {name}"

    def test_text_files_are_clean(self) -> None:
        for name, data in SHIPPED.items():
            text = data.decode("utf-8")
            assert "\r" not in text, name
            assert text.endswith("\n"), name
            assert not re.search(r"[ \t]+$", text, flags=re.M), name
