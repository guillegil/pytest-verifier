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
from types import CodeType, FrameType
from typing import AbstractSet, Optional, Tuple

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__)) + os.sep

#: How many frames past the caller to search for the test function.
_SEARCH_DEPTH = 100

#: ``(location, called_from)``; either can be ``None``.
Site = Tuple[Optional[str], Optional[str]]

NOWHERE: Site = (None, None)


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


def locate(rootdir: Optional[str], test_codes: AbstractSet[CodeType]) -> Site:
    """The site of the check being recorded now. Never raises."""
    try:
        frame: Optional[FrameType] = sys._getframe(1)
        while frame is not None and frame.f_code.co_filename.startswith(_PACKAGE_DIR):
            frame = frame.f_back
        if frame is None:
            return NOWHERE
        location = _where(frame, rootdir)
        if not test_codes or frame.f_code in test_codes:
            return location, None
        outer = frame.f_back
        for _ in range(_SEARCH_DEPTH):
            if outer is None:
                break
            if outer.f_code in test_codes:
                called_from = _where(outer, rootdir)
                return location, None if called_from == location else called_from
            outer = outer.f_back
        return location, None
    except Exception:  # pragma: no cover - a location must never break a check
        return NOWHERE


def split(site: object) -> Optional[Tuple[str, int]]:
    """``(path, line)`` of a stored ``"path:line"``, or ``None``."""
    if not isinstance(site, str):
        return None
    path, _, line = site.rpartition(":")
    if not path or not line.isdigit():
        return None
    return path, int(line)
