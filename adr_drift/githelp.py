"""Git helpers for `adr-drift drift`: what did recent commits introduce?"""

from __future__ import annotations

import os
import subprocess


def _git(root: str, *args: str) -> str:
    p = subprocess.run(["git", "-C", root, *args],
                       capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def is_repo(root: str) -> bool:
    try:
        _git(root, "rev-parse", "--git-dir")
        return True
    except Exception:
        return False


def changed_files(root: str, since: str) -> list[str]:
    """Repo-relative paths changed between <since> and HEAD."""
    out = _git(root, "diff", "--name-only", f"{since}..HEAD")
    return [l.strip() for l in out.splitlines() if l.strip()]


def file_at_ref(root: str, ref: str, rel_path: str) -> str | None:
    """File content at a git ref, or None if the file didn't exist."""
    p = subprocess.run(["git", "-C", root, "show", f"{ref}:{rel_path}"],
                       capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        return None
    return p.stdout


def recent_commits(root: str, n: int) -> list[dict]:
    out = _git(root, "log", f"-{n}", "--format=%H%x01%h%x01%s")
    commits = []
    for line in out.splitlines():
        parts = line.split("\x01")
        if len(parts) == 3:
            commits.append({"sha": parts[0], "short": parts[1],
                            "subject": parts[2]})
    return commits


def blame_commit_for_line(root: str, rel_path: str, lineno: int) -> str:
    """Short sha of the commit that introduced a given line (best effort)."""
    try:
        out = _git(root, "blame", "--porcelain", "-L", f"{lineno},{lineno}",
                   "HEAD", "--", rel_path)
        for line in out.splitlines():
            if len(line) >= 40 and all(
                    c in "0123456789abcdef" for c in line[:40]):
                return line[:7]
    except Exception:
        pass
    return ""
