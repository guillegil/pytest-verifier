"""``pytest-verifier skill install``: where it installs the agent skill, how it updates it, and
what it refuses to touch. Also the pytest header line about an outdated project skill."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

from pytest_verifier import _cli, _installer

SHIPPED = _installer.shipped_files()
VERSION = _installer.skill_version(SHIPPED)
CLAUDE = Path(".claude", "skills", "pytest-verifier")
AGENTS = Path(".agents", "skills", "pytest-verifier")


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project folder as the current folder, and an empty home folder."""
    folder = tmp_path / "project"
    home = tmp_path / "home"
    folder.mkdir()
    home.mkdir()
    monkeypatch.chdir(folder)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return folder


def files_in(folder: Path) -> Dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def run(capsys: pytest.CaptureFixture[str], *args: str) -> "tuple[int, List[str], str]":
    status = _cli.main(list(args))
    out, err = capsys.readouterr()
    return status, out.splitlines(), err


def old_skill(folder: Path, version: str = "0.0.1", extra: bool = True) -> None:
    """A pytest-verifier skill of another version, with a file the new one does not have."""
    folder.mkdir(parents=True)
    text = SHIPPED["SKILL.md"].decode().replace(f'version: "{VERSION}"', f'version: "{version}"')
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    if extra:
        (folder / "old-notes.md").write_text("gone after the update", encoding="utf-8")


def test_the_skill_ships_with_the_package() -> None:
    assert "SKILL.md" in SHIPPED
    assert VERSION
    assert _installer.frontmatter(SHIPPED["SKILL.md"].decode())["name"] == "pytest-verifier"


class TestInstall:
    def test_installs_into_both_folders_by_default(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status, out, err = run(capsys, "skill", "install")
        assert (status, err) == (0, "")
        assert out == [
            f"Installed the pytest-verifier {VERSION} skill in .claude/skills/pytest-verifier",
            f"Installed the pytest-verifier {VERSION} skill in .agents/skills/pytest-verifier",
        ]
        assert files_in(project / CLAUDE) == SHIPPED
        assert files_in(project / AGENTS) == SHIPPED

    @pytest.mark.parametrize(
        ("flags", "claude", "agents"),
        [
            (["--claude"], True, False),
            (["--agents"], False, True),
            (["--generic"], False, True),
            (["--claude", "--agents"], True, True),
        ],
    )
    def test_flags_choose_the_folders(
        self,
        project: Path,
        capsys: pytest.CaptureFixture[str],
        flags: List[str],
        claude: bool,
        agents: bool,
    ) -> None:
        status, out, _ = run(capsys, "skill", "install", *flags)
        assert status == 0
        assert (project / CLAUDE).is_dir() is claude
        assert (project / AGENTS).is_dir() is agents
        assert len(out) == claude + agents

    def test_global_installs_in_the_home_folder(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        home = project.parent / "home"
        status, out, _ = run(capsys, "skill", "install", "--global")
        assert status == 0
        assert out == [
            f"Installed the pytest-verifier {VERSION} skill in ~/.claude/skills/pytest-verifier",
            f"Installed the pytest-verifier {VERSION} skill in ~/.agents/skills/pytest-verifier",
        ]
        assert files_in(home / CLAUDE) == SHIPPED
        assert files_in(home / AGENTS) == SHIPPED
        assert not (project / ".claude").exists()
        assert not (project / ".agents").exists()

    def test_python_dash_m_runs_the_command(self, project: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pytest_verifier", "skill", "install", "--claude"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "Installed the pytest-verifier" in result.stdout
        assert files_in(project / CLAUDE) == SHIPPED

    def test_the_console_script_is_installed(self, project: Path) -> None:
        scripts = Path(sys.executable).parent
        script = scripts / ("pytest-verifier.exe" if os.name == "nt" else "pytest-verifier")
        if not script.exists():
            pytest.skip("pytest-verifier is not installed next to this Python")
        result = subprocess.run(
            [str(script), "skill", "install", "--agents"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert files_in(project / AGENTS) == SHIPPED

    def test_leaves_no_temporary_folders(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        old_skill(project / CLAUDE)
        run(capsys, "skill", "install")
        for skills in (project / ".claude" / "skills", project / ".agents" / "skills"):
            assert [path.name for path in skills.iterdir()] == ["pytest-verifier"]

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
    def test_folders_get_the_usual_permissions(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        old = os.umask(0o022)
        try:
            run(capsys, "skill", "install", "--claude")
        finally:
            os.umask(old)
        assert (project / CLAUDE).stat().st_mode & 0o777 == 0o755
        assert (project / CLAUDE / "SKILL.md").stat().st_mode & 0o777 == 0o644


class TestUpdate:
    def test_a_second_run_says_up_to_date(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run(capsys, "skill", "install")
        status, out, _ = run(capsys, "skill", "install")
        assert status == 0
        assert out == [
            f".claude/skills/pytest-verifier is up to date (pytest-verifier {VERSION})",
            f".agents/skills/pytest-verifier is up to date (pytest-verifier {VERSION})",
        ]

    def test_replaces_a_skill_of_another_version(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        old_skill(project / CLAUDE, "0.8.0")
        status, out, _ = run(capsys, "skill", "install", "--claude")
        assert status == 0
        assert out == [f"Updated .claude/skills/pytest-verifier from 0.8.0 to {VERSION}"]
        assert files_in(project / CLAUDE) == SHIPPED  # the old file is gone

    def test_restores_a_changed_skill_of_the_same_version(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run(capsys, "skill", "install", "--claude")
        with (project / CLAUDE / "SKILL.md").open("a", encoding="utf-8") as handle:
            handle.write("\nlocal note\n")
        status, out, _ = run(capsys, "skill", "install", "--claude")
        assert status == 0
        assert out == [
            f"Updated .claude/skills/pytest-verifier to the pytest-verifier {VERSION} skill "
            "(its files had changed)"
        ]
        assert files_in(project / CLAUDE) == SHIPPED

    def test_a_skill_without_a_version_is_still_ours(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (project / CLAUDE).mkdir(parents=True)
        (project / CLAUDE / "SKILL.md").write_text(
            "---\nname: pytest-verifier\ndescription: old\n---\nold\n", encoding="utf-8"
        )
        status, out, _ = run(capsys, "skill", "install", "--claude")
        assert status == 0
        assert "(its files had changed)" in out[0]
        assert files_in(project / CLAUDE) == SHIPPED


class TestRefuse:
    def test_leaves_another_folder_alone(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mine = project / CLAUDE
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("---\nname: my-skill\n---\n", encoding="utf-8")
        status, out, err = run(capsys, "skill", "install")
        assert (status, out) == (1, [])
        assert err.splitlines() == [
            "pytest-verifier: error: .claude/skills/pytest-verifier is a folder that is not a "
            "pytest-verifier skill. Move it away, or run again with --force to replace it.",
            "pytest-verifier: error: nothing was installed.",
        ]
        assert files_in(mine) == {"SKILL.md": b"---\nname: my-skill\n---\n"}
        assert not (project / ".agents").exists()  # every target is checked first

    def test_leaves_a_file_alone(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (project / CLAUDE).parent.mkdir(parents=True)
        (project / CLAUDE).write_text("mine", encoding="utf-8")
        status, _, err = run(capsys, "skill", "install", "--claude")
        assert status == 1
        assert "is a file that is not a pytest-verifier skill" in err
        assert (project / CLAUDE).read_text(encoding="utf-8") == "mine"

    def test_force_replaces_a_folder(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (project / CLAUDE).mkdir(parents=True)
        (project / CLAUDE / "notes.txt").write_text("mine", encoding="utf-8")
        status, out, _ = run(capsys, "skill", "install", "--claude", "--force")
        assert status == 0
        assert out == [f"Replaced .claude/skills/pytest-verifier with the pytest-verifier "
                       f"{VERSION} skill"]
        assert files_in(project / CLAUDE) == SHIPPED

    def test_force_replaces_a_file(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (project / CLAUDE).parent.mkdir(parents=True)
        (project / CLAUDE).write_text("mine", encoding="utf-8")
        status, _, _ = run(capsys, "skill", "install", "--claude", "--force")
        assert status == 0
        assert files_in(project / CLAUDE) == SHIPPED

    def test_reports_a_folder_it_cannot_create(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (project / ".agents").write_text("not a folder", encoding="utf-8")
        status, out, err = run(capsys, "skill", "install")
        assert status == 1
        assert out == [
            f"Installed the pytest-verifier {VERSION} skill in .claude/skills/pytest-verifier"
        ]
        lines = err.splitlines()
        assert lines[0].startswith(
            "pytest-verifier: error: could not write .agents/skills/pytest-verifier: "
        )
        assert lines[1] == (
            "pytest-verifier: error: already written: .claude/skills/pytest-verifier"
        )

    def test_a_failed_swap_keeps_the_old_skill(
        self, project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old_skill(project / CLAUDE, "0.8.0")
        before = files_in(project / CLAUDE)
        real_replace = os.replace
        calls: List[str] = []

        def replace(src: "os.PathLike[str]", dst: "os.PathLike[str]") -> None:
            calls.append(Path(src).name)
            if Path(src).name.startswith(".pytest-verifier-new-"):
                raise PermissionError("in use")
            real_replace(src, dst)

        monkeypatch.setattr(_installer.os, "replace", replace)
        status, _, err = run(capsys, "skill", "install", "--claude")
        assert status == 1
        assert "could not write .claude/skills/pytest-verifier: in use" in err
        assert files_in(project / CLAUDE) == before
        assert [path.name for path in (project / CLAUDE).parent.iterdir()] == ["pytest-verifier"]


needs_symlinks = pytest.mark.skipif(
    os.name == "nt", reason="creating symlinks needs extra rights on Windows"
)


@needs_symlinks
class TestSymlinks:
    def test_a_shared_skills_folder_is_written_once(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (project / ".claude" / "skills").mkdir(parents=True)
        (project / ".agents").mkdir()
        (project / ".agents" / "skills").symlink_to(project / ".claude" / "skills")
        status, out, _ = run(capsys, "skill", "install")
        assert status == 0
        assert out == [
            f"Installed the pytest-verifier {VERSION} skill in .claude/skills/pytest-verifier",
            ".agents/skills/pytest-verifier is the same folder as .claude/skills/pytest-verifier",
        ]
        assert files_in(project / AGENTS) == SHIPPED

    def test_a_linked_skill_is_updated_through_the_link(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        shared = project / "shared-skill"
        old_skill(shared, "0.8.0")
        (project / CLAUDE).parent.mkdir(parents=True)
        (project / CLAUDE).symlink_to(shared)
        status, out, _ = run(capsys, "skill", "install", "--claude")
        assert status == 0
        assert out == [f"Updated .claude/skills/pytest-verifier from 0.8.0 to {VERSION}"]
        assert (project / CLAUDE).is_symlink()
        assert files_in(shared) == SHIPPED

    def test_force_replaces_a_link_but_not_what_it_points_to(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        elsewhere = project / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.txt").write_text("keep", encoding="utf-8")
        (project / CLAUDE).parent.mkdir(parents=True)
        (project / CLAUDE).symlink_to(elsewhere)
        status, _, err = run(capsys, "skill", "install", "--claude")
        assert status == 1
        assert "is a folder that is not a pytest-verifier skill" in err
        status, _, _ = run(capsys, "skill", "install", "--claude", "--force")
        assert status == 0
        assert not (project / CLAUDE).is_symlink()
        assert files_in(project / CLAUDE) == SHIPPED
        assert files_in(elsewhere) == {"keep.txt": b"keep"}

    def test_a_broken_link_is_refused_then_forced(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (project / CLAUDE).parent.mkdir(parents=True)
        (project / CLAUDE).symlink_to(project / "missing")
        status, _, err = run(capsys, "skill", "install", "--claude")
        assert status == 1
        assert "is a link that is not a pytest-verifier skill" in err
        status, _, _ = run(capsys, "skill", "install", "--claude", "--force")
        assert status == 0
        assert files_in(project / CLAUDE) == SHIPPED


class TestUsage:
    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        import pytest_verifier

        with pytest.raises(SystemExit) as exit_info:
            _cli.main(["--version"])
        assert exit_info.value.code == 0
        assert capsys.readouterr().out.strip() == f"pytest-verifier {pytest_verifier.__version__}"

    @pytest.mark.parametrize("args", [[], ["skill"]])
    def test_without_an_action_prints_help(
        self, capsys: pytest.CaptureFixture[str], args: List[str]
    ) -> None:
        status, out, err = run(capsys, *args)
        assert (status, out) == (2, [])
        assert err.startswith(f"usage: pytest-verifier {' '.join(args)}".rstrip())

    @pytest.mark.parametrize("args", [["skill", "remove"], ["skill", "install", "--home"]])
    def test_unknown_arguments_are_usage_errors(
        self, capsys: pytest.CaptureFixture[str], args: List[str]
    ) -> None:
        with pytest.raises(SystemExit) as exit_info:
            _cli.main(args)
        assert exit_info.value.code == 2


class TestHeader:
    def test_names_an_outdated_project_skill(self, pytester: pytest.Pytester) -> None:
        old_skill(pytester.path / CLAUDE, "0.8.0", extra=False)
        pytester.makepyfile("def test_a(): pass")
        result = pytester.runpytest()
        result.stdout.fnmatch_lines([
            f"pytest-verifier {VERSION}: the agent skill in .claude/skills/pytest-verifier is for "
            "0.8.0; update it with: pytest-verifier skill install --claude",
        ])
        result.assert_outcomes(passed=1)

    def test_names_each_outdated_folder_with_its_flag(self, pytester: pytest.Pytester) -> None:
        old_skill(pytester.path / AGENTS, "0.8.0", extra=False)
        (pytester.path / CLAUDE).mkdir(parents=True)
        (pytester.path / CLAUDE / "SKILL.md").write_text(
            "---\nname: pytest-verifier\n---\n", encoding="utf-8"
        )
        pytester.makepyfile("def test_a(): pass")
        result = pytester.runpytest()
        result.stdout.fnmatch_lines([
            "*the agent skill in .claude/skills/pytest-verifier is for another version; "
            "update it with: pytest-verifier skill install --claude",
            "*the agent skill in .agents/skills/pytest-verifier is for 0.8.0; "
            "update it with: pytest-verifier skill install --agents",
        ])

    def test_says_nothing_about_a_current_or_foreign_skill(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(pytester.path)
        assert _cli.main(["skill", "install", "--claude"]) == 0
        (pytester.path / AGENTS).mkdir(parents=True)
        (pytester.path / AGENTS / "SKILL.md").write_text(
            "---\nname: other\nmetadata:\n  version: 0.0.1\n---\n", encoding="utf-8"
        )
        pytester.makepyfile("def test_a(): pass")
        result = pytester.runpytest()
        result.stdout.no_fnmatch_line("*agent skill*")
        result.assert_outcomes(passed=1)


class TestFrontmatter:
    def test_reads_scalars_and_one_level_of_nesting(self) -> None:
        text = (
            "---\n"
            "name: pytest-verifier\n"
            "description: >-\n"
            "  Folded: text with a colon\n"
            "metadata:\n"
            '  version: "1.2.3"\n'
            "  author: 'someone'\n"
            "# a comment\n"
            "---\n"
            "body: not frontmatter\n"
        )
        assert _installer.frontmatter(text) == {
            "name": "pytest-verifier",
            "description": ">-",
            "metadata": "",
            "metadata.version": "1.2.3",
            "metadata.author": "someone",
        }

    @pytest.mark.parametrize(
        "text", ["", "name: x\n", "---\nname: x\n", "# title\n---\nname: x\n---\n"]
    )
    def test_no_frontmatter(self, text: str) -> None:
        assert _installer.frontmatter(text) == {}
