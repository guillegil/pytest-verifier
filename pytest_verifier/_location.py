"""Where a recorded check was made.

The fixture stores the first caller outside this package as the check's ``location``
(``"path:line"``, relative to the rootdir). When that caller is not the test function itself,
for example a helper or a lazy child, it also stores ``called_from``: the line of the test
function that led to it, when the test function is on the stack.
"""
from __future__ import annotations

import functools
import os
import sys
from types import CodeType, FrameType, TracebackType
from typing import AbstractSet, Mapping, Optional, Tuple

_PACKAGE = __name__.partition(".")[0]

#: How many frames past the caller to search for the test function.
_SEARCH_DEPTH = 100

#: ``(location, called_from)``; either can be ``None``.
Site = Tuple[Optional[str], Optional[str]]

NOWHERE: Site = (None, None)


def ours(frame: FrameType) -> bool:
    """Whether *frame* runs code of this package.

    By module, not file name: pytest rewrites this package, and its cached code keeps the path
    it was compiled from, which differs from ``__file__`` once the venv is moved or reached
    through a symlink.
    """
    name = frame.f_globals.get("__name__")
    return isinstance(name, str) and (name == _PACKAGE or name.startswith(_PACKAGE + "."))


@functools.lru_cache(maxsize=256)
def display_path(filename: str, rootdir: Optional[str]) -> str:
    """*filename* relative to *rootdir* with ``/`` separators, or as it is when outside it."""
    if rootdir and os.path.isabs(filename):
        try:
            relative = os.path.relpath(filename, rootdir)
        except ValueError:  # another drive on Windows
            return filename
        if relative != os.pardir and not relative.startswith(os.pardir + os.sep):
            return relative.replace(os.sep, "/")
    return filename


def _where(frame: FrameType, rootdir: Optional[str]) -> str:
    return f"{display_path(frame.f_code.co_filename, rootdir)}:{frame.f_lineno}"


#: Directories that hold installed packages, also when a virtualenv is inside the rootdir.
_INSTALLED = frozenset({"site-packages", "dist-packages"})


def _is_library(site: str, rootdir: Optional[str]) -> bool:
    """Whether *site*, as :func:`_where` gives it, is in installed or outside code."""
    if rootdir and os.path.isabs(site):
        return True
    return not _INSTALLED.isdisjoint(site.replace("\\", "/").split("/"))


class FunctionCode:
    """The code of a test function: what :func:`locate` treats as the test itself.

    Code objects match by identity (hashing one walks its bytecode and constants), and the
    code objects are kept alive, so their ids are not reused while the run lasts. A function
    can also match by file and name, for a test wrapped by a decorator that hides it (no
    ``functools.wraps``).
    """

    __slots__ = ("codes", "ids", "names", "wrappers")

    def __init__(
        self,
        codes: AbstractSet[CodeType] = frozenset(),
        names: Optional[Mapping[str, AbstractSet[str]]] = None,
        wrappers: AbstractSet[CodeType] = frozenset(),
    ) -> None:
        self.codes = codes
        self.ids = frozenset(id(code) for code in codes)
        #: Function name -> the files it is defined in.
        self.names: Mapping[str, AbstractSet[str]] = names or {}
        #: The ids of the codes that run the test without being it, such as the wrapper of a
        #: decorator without ``functools.wraps`` or a test a plugin generates (pytest-bdd).
        self.wrappers = frozenset(id(code) for code in wrappers)

    def __bool__(self) -> bool:
        return bool(self.ids or self.names)

    def matches(self, code: CodeType) -> bool:
        if id(code) in self.ids:
            return True
        files = self.names.get(code.co_name)
        return files is not None and code.co_filename in files


def locate(rootdir: Optional[str], test: FunctionCode) -> Site:
    """The site of the check being recorded now. Never raises."""
    try:
        frame: Optional[FrameType] = sys._getframe(1)
        while frame is not None and ours(frame):
            frame = frame.f_back
        if frame is None:
            return NOWHERE
        location = _where(frame, rootdir)
        if not test or test.matches(frame.f_code):
            return location, None
        outer = frame.f_back
        for _ in range(_SEARCH_DEPTH):
            if outer is None:
                break
            if test.matches(outer.f_code):
                called_from = _where(outer, rootdir)
                if called_from == location or (
                    id(outer.f_code) in test.wrappers and _is_library(called_from, rootdir)
                ):
                    # The same line (a lambda), or library code that runs the test.
                    return location, None
                return location, called_from
            outer = outer.f_back
        return location, None
    except Exception:  # pragma: no cover - a location must never break a check
        return NOWHERE


def raised_at(traceback: Optional[TracebackType], rootdir: Optional[str]) -> Optional[str]:
    """``"path:line"`` where an exception was raised: the innermost entry of its *traceback*
    outside this package, relative to *rootdir*. Never raises."""
    try:
        found = None
        while traceback is not None:
            if not ours(traceback.tb_frame):
                found = traceback
            traceback = traceback.tb_next
        if found is None:
            return None
        filename = display_path(found.tb_frame.f_code.co_filename, rootdir)
        return f"{filename}:{found.tb_lineno}"
    except Exception:  # pragma: no cover - a location must never break a check
        return None


def split(site: object) -> Optional[Tuple[str, int]]:
    """``(path, line)`` of a stored ``"path:line"``, or ``None``."""
    if not isinstance(site, str):
        return None
    path, _, line = site.rpartition(":")
    if not path or not line.isdigit():
        return None
    return path, int(line)
