"""The agent skill shipped in this package, and installing it where coding agents look.

The skill is the ``_skill`` folder of the package. ``pytest-verifier skill install`` (see
:mod:`pytest_verifier._cli`) copies it to ``<skills>/pytest-verifier/``, where ``<skills>`` is
``.claude/skills`` (Claude Code) or ``.agents/skills`` (agents that follow the open Agent Skills
layout, such as Codex), under the current folder or the home folder.

The folder belongs to pytest-verifier: installing again replaces a pytest-verifier skill of any
version, and leaves anything else alone unless forced. Every target is checked before anything is
written, and each one is written to a temporary folder next to it, then swapped in.
"""
from __future__ import annotations

import os
import secrets
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: The skill's name: its folder name and the ``name`` in its frontmatter.
SKILL_NAME = "pytest-verifier"

#: Where agents look for skills, relative to a project folder or the home folder.
CLAUDE_SKILLS = (".claude", "skills")
AGENTS_SKILLS = (".agents", "skills")

#: The files of a skill folder, by their ``/``-separated path inside it.
Files = Dict[str, bytes]


def shipped_files() -> Files:
    """The files of the skill shipped in this package."""
    files: Files = {}

    def walk(folder: Any, prefix: str) -> None:
        for entry in folder.iterdir():
            if entry.name.startswith(".") or entry.name == "__pycache__":
                continue
            if entry.is_dir():
                walk(entry, f"{prefix}{entry.name}/")
            else:
                files[prefix + entry.name] = entry.read_bytes()

    walk(resources.files("pytest_verifier") / "_skill", "")
    return files


def frontmatter(text: str) -> Dict[str, str]:
    """The scalar fields of a SKILL.md frontmatter, nested ones as ``parent.key``.

    Only what the installer reads (``name``, ``metadata.version``): one level of nesting,
    plain or quoted scalars. Returns ``{}`` when there is no frontmatter.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: Dict[str, str] = {}
    parent = ""
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, colon, value = line.strip().partition(":")
        if not colon:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if line[:1] in (" ", "\t"):
            if parent:
                fields[f"{parent}.{key.strip()}"] = value
        else:
            parent = "" if value else key.strip()
            fields[key.strip()] = value
    return {}  # no closing line: not a frontmatter


def skill_version(files: Mapping[str, bytes]) -> Optional[str]:
    """The ``metadata.version`` of a skill, if it has one."""
    text = files.get("SKILL.md")
    if text is None:
        return None
    return frontmatter(text.decode("utf-8", "replace")).get("metadata.version") or None


def installed_skill(folder: Path) -> Optional[Files]:
    """The files of the pytest-verifier skill in *folder*, or ``None`` if it holds none."""
    try:
        text = (folder / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if frontmatter(text).get("name") != SKILL_NAME:
        return None
    files: Files = {}
    try:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                files[path.relative_to(folder).as_posix()] = path.read_bytes()
    except OSError:
        return None
    return files


@dataclass(frozen=True)
class Target:
    """A skill folder to install into, and how to name it in messages."""

    path: Path
    label: str


def skills_folders(*, claude: bool, agents: bool, home: bool) -> List[Target]:
    """The ``pytest-verifier`` folders to install into, in the current or the home folder.

    With neither *claude* nor *agents*, both.
    """
    if not claude and not agents:
        claude = agents = True
    root = Path.home() if home else Path.cwd()
    pairs = ((CLAUDE_SKILLS, claude), (AGENTS_SKILLS, agents))
    chosen = [parts for parts, wanted in pairs if wanted]
    prefix = "~/" if home else ""
    return [
        Target(root.joinpath(*parts, SKILL_NAME), prefix + "/".join((*parts, SKILL_NAME)))
        for parts in chosen
    ]


@dataclass
class Step:
    """What installing into one target will do."""

    target: Target
    #: ``install``, ``update``, ``replace`` (forced over something else), ``current`` (already
    #: the same files), ``same`` (the same folder as an earlier target) or ``refuse``.
    action: str
    #: The folder written to: the target, or where its symlink points when it is a skill.
    folder: Path
    #: The version of the skill found there, when it is one.
    found_version: Optional[str] = None
    #: For ``same``, the target already handled; for ``refuse``, what is in the way.
    note: str = ""


def plan(targets: Sequence[Target], files: Files, *, force: bool) -> List[Step]:
    """Decide what to do for each target, without writing anything."""
    steps: List[Step] = []
    seen: Dict[Path, Target] = {}
    for target in targets:
        path = target.path
        try:
            folder = path.resolve() if path.is_symlink() and path.is_dir() else path
            real = folder.resolve()
        except (OSError, RuntimeError):  # a symlink loop
            folder = real = path
        if real in seen:
            steps.append(Step(target, "same", folder, note=seen[real].label))
            continue
        seen[real] = target
        if not (path.exists() or path.is_symlink()):
            steps.append(Step(target, "install", path))
            continue
        installed = installed_skill(folder) if folder.is_dir() else None
        if installed is not None:
            action = "current" if installed == files else "update"
            steps.append(Step(target, action, folder, skill_version(installed)))
        elif force:
            steps.append(Step(target, "replace", path))  # a symlink is replaced, not followed
        else:
            what = "a file" if path.is_file() else "a folder" if path.is_dir() else "a link"
            steps.append(Step(target, "refuse", path, note=what))
    return steps


def write(folder: Path, files: Files) -> None:
    """Make *folder* hold exactly *files*, replacing what is there.

    The files go to a new folder next to it, which then takes its place, so an interrupted
    install leaves either the old folder or the new one (and possibly a hidden leftover).
    """
    parent = folder.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = _fresh(parent, f".{folder.name}-new-")
    try:
        for name, data in sorted(files.items()):
            path = staging.joinpath(*name.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if folder.exists() or folder.is_symlink():
            old = parent / f".{folder.name}-old-{secrets.token_hex(4)}"
            os.replace(folder, old)
            try:
                os.replace(staging, folder)
            except BaseException:
                os.replace(old, folder)
                raise
            _remove(old)
        else:
            os.replace(staging, folder)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _fresh(parent: Path, prefix: str) -> Path:
    """Create a new, empty folder in *parent* (with the usual permissions, unlike mkdtemp)."""
    while True:
        path = parent / f"{prefix}{secrets.token_hex(4)}"
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return path


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except OSError:
            pass


def stale_skills(rootdir: Path) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """The project skill folders under *rootdir* whose skill is not the one shipped here.

    Returns ``(label, installed version, shipped version)`` for each. Never raises.
    """
    try:
        shipped = shipped_files()
        version = skill_version(shipped)
        stale = []
        for parts in (CLAUDE_SKILLS, AGENTS_SKILLS):
            folder = rootdir.joinpath(*parts, SKILL_NAME)
            installed = installed_skill(folder)
            if installed is not None and skill_version(installed) != version:
                label = "/".join((*parts, SKILL_NAME))
                stale.append((label, skill_version(installed), version))
        return stale
    except Exception:
        return []
