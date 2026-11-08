"""Path helpers shared by every module (standard library only)."""

from __future__ import annotations

import os


def rel_posix(path: str, root: str) -> str:
    """`path` below `root`, written with '/' on every platform.

    os.path.relpath raises ValueError on Windows when the two are on
    different drives (`--adr-dir D:\\adr` with the repository on C:); that
    is not an internal error, so the absolute path (with '/') is shown
    instead."""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        rel = os.path.abspath(path)
    return rel.replace(os.sep, "/")
