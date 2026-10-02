"""The ``pytest-verifier`` command.

::

    pytest-verifier skill install            # .claude/skills/ and .agents/skills/
    pytest-verifier skill install --claude   # only .claude/skills/
    pytest-verifier skill install --agents   # only .agents/skills/ (--generic works too)
    pytest-verifier skill install --global   # in your home folder instead

``python -m pytest_verifier`` runs the same command. Messages are ASCII, so they print on any
terminal.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

from . import _installer

_PROG = "pytest-verifier"

_SKILL_HELP = (
    "The agent skill teaches coding agents (Claude Code, Codex and others) to write tests with "
    "pytest-verifier: every verify.<method>, composites, verify.require and how to read failures."
)

_INSTALL_HELP = (
    "Copy the agent skill of this pytest-verifier version into .claude/skills/pytest-verifier "
    "(Claude Code) and .agents/skills/pytest-verifier (agents that read .agents/skills, such as "
    "Codex), under the current folder. Run it again after upgrading pytest-verifier to update "
    "the skill: a skill this command installed there is replaced. Anything else there is left "
    "alone unless --force is given."
)


def _parser() -> argparse.ArgumentParser:
    from . import __version__

    parser = argparse.ArgumentParser(
        prog=_PROG, description="Tools for pytest-verifier, the soft-assertion plugin for pytest."
    )
    parser.add_argument("--version", action="version", version=f"{_PROG} {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    skill = commands.add_parser(
        "skill", help="install the agent skill", description=_SKILL_HELP
    )
    actions = skill.add_subparsers(dest="action", metavar="ACTION")
    install = actions.add_parser(
        "install", help="install or update the agent skill", description=_INSTALL_HELP
    )
    install.add_argument(
        "--claude", action="store_true", help="only .claude/skills/ (Claude Code)"
    )
    install.add_argument(
        "--agents",
        "--generic",
        dest="agents",
        action="store_true",
        help="only .agents/skills/ (Codex and other agents that read it)",
    )
    install.add_argument(
        "--global",
        dest="home",
        action="store_true",
        help="install in your home folder (~/.claude/skills, ~/.agents/skills) instead of the "
        "current folder",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="replace a pytest-verifier folder there that this command did not install",
    )
    parser.set_defaults(usage=parser)
    skill.set_defaults(usage=skill)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the ``pytest-verifier`` command and return its exit status.

    Args:
        argv: The arguments, without the program name. Defaults to ``sys.argv[1:]``.

    Returns:
        0 when it succeeded, 1 when the install was refused or failed, 2 for a usage error.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command != "skill" or args.action != "install":
        args.usage.print_help(sys.stderr)
        return 2
    return _install(claude=args.claude, agents=args.agents, home=args.home, force=args.force)


def _install(*, claude: bool, agents: bool, home: bool, force: bool) -> int:
    files = _installer.shipped_files()
    version = _installer.skill_version(files) or "unknown"
    try:
        targets = _installer.skills_folders(claude=claude, agents=agents, home=home)
    except RuntimeError as exc:  # Path.home(): no home folder
        _error(f"could not find your home folder ({exc}); set HOME, or install without --global")
        return 1
    try:
        steps = _installer.plan(targets, files, force=force)
    except OSError as exc:
        _error(f"could not look for the skill folders: {exc}")
        return 1
    refused = [step for step in steps if step.action in ("refuse", "blocked")]
    if refused:
        for step in refused:
            if step.action == "blocked":
                _error(
                    f"cannot create {step.target.label}: {step.note} is a file, not a folder. "
                    "Move it away."
                )
            else:
                _error(
                    f"{step.target.label} is {step.note} that this command did not install. "
                    "Move it away, or run again with --force to replace it."
                )
        _error("nothing was installed.")
        return 1
    done: List[str] = []
    status = 0
    for step in steps:
        label = step.target.label
        if step.action == "same":
            print(f"{label} is the same folder as {step.note}")
            continue
        for stuck in _installer.remove_leftovers(step.target.path):
            _error(f"could not remove {step.target.name(stuck)}, left by an earlier install; "
                   "delete it")
            status = 1
        if step.action == "current":
            print(f"{label} is up to date (pytest-verifier {version})")
            continue
        try:
            _installer.write(step.folder, files)
        except _installer.LeftoverError as exc:
            print(_written(step, label, version))
            _error(f"could not remove the old skill, moved aside to "
                   f"{step.target.name(exc.path)}; delete it")
            status = 1
        except OSError as exc:
            _error(f"could not write {label}: {exc}")
            if done:
                _error(f"already written: {', '.join(done)}")
            return 1
        else:
            print(_written(step, label, version))
        done.append(label)
    return status


def _written(step: _installer.Step, label: str, version: str) -> str:
    if step.action == "install":
        return f"Installed the pytest-verifier {version} skill in {label}"
    if step.action == "replace":
        return f"Replaced {label} with the pytest-verifier {version} skill"
    found = step.found_version
    if found is not None and found != version:
        if _installer.compare_versions(found, version) == 1:
            return f"Downgraded {label} from {found} to {version}, the installed pytest-verifier"
        return f"Updated {label} from {found} to {version}"
    return f"Updated {label} to the pytest-verifier {version} skill (its files had changed)"


def _error(message: str) -> None:
    print(f"{_PROG}: error: {message}", file=sys.stderr)
