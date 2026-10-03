"""The agent skill shipped in this package, and installing it where coding agents look.

The skill is the ``_skill`` folder of the package. ``pytest-verifier skill install`` (see
:mod:`pytest_verifier._cli`) copies it to ``<skills>/pytest-verifier/``, where ``<skills>`` is
``.claude/skills`` (Claude Code) or ``.agents/skills`` (agents that follow the open Agent Skills
layout, such as Codex), under the current folder or the home folder.

The folder belongs to pytest-verifier: installing again replaces a skill this command installed
(a ``SKILL.md`` named ``pytest-verifier`` with a ``metadata.version``), of any version, and leaves
anything else alone unless forced. Every target folder, and the folders it needs, is checked
before anything is written; each target is written to a temporary folder next to it, then
swapped in.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

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
    lines = text.lstrip("\ufeff").splitlines()  # a byte order mark, as some editors write
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


def compare_versions(first: str, second: str) -> Optional[int]:
    """-1, 0 or 1 as release *first* is older than, the same as or newer than *second*.

    Only the leading numbers count (``1.2.0rc1`` is ``1.2.0``, ``1.2`` is ``1.2.0``); ``None``
    when a version does not start with a number.
    """
    numbers = []
    for version in (first, second):
        match = re.match(r"\d+(?:\.\d+)*", version.strip())
        if match is None:
            return None
        numbers.append([int(part) for part in match.group().split(".")])
    size = max(len(numbers[0]), len(numbers[1]))
    a, b = (each + [0] * (size - len(each)) for each in numbers)
    return (a > b) - (a < b)


def installed_skill(folder: Path) -> Optional[Files]:
    """The files of the skill this command installed in *folder*, or ``None`` if it holds none.

    That skill has a ``SKILL.md`` named ``pytest-verifier`` with a ``metadata.version``: a skill
    someone wrote by hand under that name has no version, and is not replaced unless forced.
    """
    try:
        text = (folder / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields = frontmatter(text)
    if fields.get("name") != SKILL_NAME or not fields.get("metadata.version"):
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
    #: The folder that labels are relative to; ``None`` when they are full paths.
    root: Optional[Path] = None

    def name(self, path: Path) -> str:
        """How to name *path*, the target or a path near it, in messages."""
        if path == self.path:
            return self.label
        if self.root is not None:
            try:
                return path.relative_to(self.root).as_posix()
            except ValueError:
                pass
        return str(path)


def skills_folders(*, claude: bool, agents: bool, home: bool) -> List[Target]:
    """The ``pytest-verifier`` folders to install into, in the current or the home folder.

    With neither *claude* nor *agents*, both. Folders in the home folder are named by their
    full path in messages, folders in the current folder relative to it.

    Raises:
        RuntimeError: When *home* is set and the home folder cannot be found.
    """
    if not claude and not agents:
        claude = agents = True
    pairs = ((CLAUDE_SKILLS, claude), (AGENTS_SKILLS, agents))
    chosen = [parts for parts, wanted in pairs if wanted]
    if home:
        root = Path.home().absolute()
        return [Target(root.joinpath(*parts, SKILL_NAME), str(root.joinpath(*parts, SKILL_NAME)))
                for parts in chosen]
    root = Path.cwd()
    return [
        Target(root.joinpath(*parts, SKILL_NAME), "/".join((*parts, SKILL_NAME)), root)
        for parts in chosen
    ]


@dataclass
class Step:
    """What installing into one target will do."""

    target: Target
    #: ``install``, ``update``, ``replace`` (forced over something else), ``current`` (already
    #: the same files), ``same`` (the same folder as an earlier target), ``refuse`` (something
    #: else is there) or ``blocked`` (a file is where a folder it needs should be).
    action: str
    #: The folder written to: the target, or where its symlink points when it is a skill.
    folder: Path
    #: The version of the skill found there, when it is one.
    found_version: Optional[str] = None
    #: For ``same``, the target already handled; for ``refuse``, what is in the way; for
    #: ``blocked``, the file that is in the way.
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
        blocking = _blocking_parent(path)
        if blocking is not None:
            steps.append(Step(target, "blocked", path, note=target.name(blocking)))
            continue
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
        if folder.exists() or _is_link(folder):
            old = parent / f".{folder.name}-old-{secrets.token_hex(4)}"
            os.replace(folder, old)
            try:
                os.replace(staging, folder)
            except BaseException:
                os.replace(old, folder)
                raise
            _remove(old)
            if old.exists() or _is_link(old):
                raise LeftoverError(old)
        else:
            os.replace(staging, folder)
    except BaseException:
        _remove(staging)
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


class LeftoverError(OSError):
    """The new skill is in place, but the old folder, moved aside, could not be removed."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"could not remove the old skill, moved aside to {path}")
        self.path = path


def leftovers(folder: Path) -> List[Path]:
    """Folders that installs into *folder* left next to it (interrupted, or not removable)."""
    try:
        return sorted(
            path
            for path in folder.parent.iterdir()
            if re.fullmatch(rf"\.{re.escape(folder.name)}-(?:new|old)-[0-9a-f]{{8}}", path.name)
        )
    except OSError:
        return []


def remove_leftovers(folder: Path) -> List[Path]:
    """Remove :func:`leftovers` of *folder*; returns the ones that could not be removed."""
    for path in leftovers(folder):
        _remove(path)
    return leftovers(folder)


def _is_link(path: Path) -> bool:
    """A symlink, or a Windows directory junction (which ``is_symlink`` does not report)."""
    if path.is_symlink():
        return True
    try:
        tag = getattr(os.lstat(path), "st_reparse_tag", 0)
    except OSError:
        return False
    return bool(tag) and tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)


def _blocking_parent(path: Path) -> Optional[Path]:
    """The nearest existing parent of *path* when it is not a folder (so none can be made)."""
    for parent in path.parents:
        if parent.is_dir():
            return None
        if parent.exists() or parent.is_symlink():
            return parent
    return None


def _remove(path: Path) -> None:
    """Remove *path*, a folder (also with read-only files), a file or a link; never raises."""
    if path.is_dir() and not _is_link(path):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_retry_writable)
        else:
            shutil.rmtree(path, onerror=_retry_writable)
        return
    try:
        path.unlink()
    except OSError:
        try:
            path.rmdir()  # a Windows directory junction
        except OSError:
            pass


def _retry_writable(function: Callable[..., Any], name: str, exc_info: Any) -> None:
    """For rmtree: make a read-only file or folder writable and try again (Windows refuses to
    delete read-only files); give up quietly when that does not help."""
    try:
        os.chmod(name, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        function(name)
    except OSError:
        pass


def stale_skills(rootdir: Path) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """The project skill folders under *rootdir* whose skill this command installed for
    another version.

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
