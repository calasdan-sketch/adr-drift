"""Git helpers for `adr-drift drift`: what did the commits since a ref change?

Every call goes through one runner that keeps git's output machine-readable
(NUL-separated records, no C-quoting of non-ASCII names) and turns every
failure into GitError. The CLI prints its message and hint as `error:` /
`hint:` and exits 2 - never a traceback, never exit 1 (which means
"violations").
"""

from __future__ import annotations

import dataclasses
import re
import subprocess

#: Seconds one git call may take before it is abandoned.
TIMEOUT = 60

_REGULAR_MODES = (b"100644", b"100755")


class GitError(Exception):
    """git failed or cannot be used here; `hint` says what to do about it."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class GitNotInstalled(GitError):
    pass


def _run(root: str, *args: str, input: "bytes | None" = None) -> bytes:
    # core.quotepath=false: even outside -z output (blame, errors) a name
    # such as h\xe9llo.py is shown as written, not as "h\303\251llo.py".
    cmd = ["git", "-c", "core.quotepath=false", "-C", root, *args]
    try:
        p = subprocess.run(cmd, input=input, capture_output=True,
                           timeout=TIMEOUT)
    except FileNotFoundError:
        raise GitNotInstalled(
            "git is not installed or not on PATH",
            "drift reads the history with git (check does not need it)"
        ) from None
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} timed out after {TIMEOUT}s") from None
    except OSError as e:
        raise GitError(f"cannot run git: {e}") from None
    if p.returncode != 0:
        why = p.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {args[0]} failed" + (f": {why}" if why else ""))
    return p.stdout


def is_repo(root: str) -> bool:
    try:
        _run(root, "rev-parse", "--git-dir")
        return True
    except GitNotInstalled:
        raise
    except GitError:
        return False


def require_repo(root: str) -> None:
    if not is_repo(root):
        raise GitError(f"not a git repository: {root}",
                       "drift compares commits, so it needs a git checkout "
                       "(check works anywhere)")


def _is_shallow(root: str) -> bool:
    try:
        return _run(root, "rev-parse",
                    "--is-shallow-repository").strip() == b"true"
    except GitNotInstalled:
        raise
    except GitError:
        return False


def _missing_ref_hint(root: str, ref: str) -> str:
    if _is_shallow(root):
        return ("this is a shallow clone, so older history is missing: fetch "
                "it (with actions/checkout set `fetch-depth: 0`) or pass "
                "--since with a commit that is present")
    if re.fullmatch(r"HEAD(?:~[0-9]*|\^+)", ref):
        try:
            n = int(_run(root, "rev-list", "--count", "HEAD").strip())
        except (GitError, ValueError):
            n = 0
        if n:
            return (f"this repository has only {n} commit(s) of history; "
                    "pass --since with an earlier ref (a branch, a tag or a "
                    "commit)")
    return ("check the name with `git branch -a` and `git tag`; in CI make "
            "sure the base branch was fetched (actions/checkout: "
            "`fetch-depth: 0`)")


def resolve_commit(root: str, ref: str) -> str:
    """The full SHA of the commit `ref` names."""
    if not ref or ref.startswith("-"):
        raise GitError(
            f"invalid git ref {ref!r}",
            "a ref cannot start with '-'; pass a branch, tag or commit, "
            "e.g. --since origin/main")
    try:
        out = _run(root, "rev-parse", "--verify", "--quiet",
                   f"{ref}^{{commit}}")
    except GitNotInstalled:
        raise
    except GitError:
        raise GitError(f"unknown git ref {ref!r}",
                       _missing_ref_hint(root, ref)) from None
    return out.decode("ascii", "replace").strip()


def head_commit(root: str) -> str:
    try:
        return resolve_commit(root, "HEAD")
    except GitNotInstalled:
        raise
    except GitError:
        raise GitError("this repository has no commits yet",
                       "commit something, then run drift again") from None


@dataclasses.dataclass
class Change:
    status: str      # A added, D deleted, M modified, R renamed, T type change
    path: str        # the path at HEAD (the old path for a deletion)
    old_path: str    # the path at the base ("" when the file was added)
    old_blob: str    # blob id at the base ("" if absent or not a plain file)
    new_blob: str    # blob id at HEAD ("" if absent or not a plain file)


def _blob(mode: bytes, sha: bytes) -> str:
    # Symlinks (120000) and submodules (160000) have no source to scan.
    return sha.decode("ascii") if mode in _REGULAR_MODES else ""


def changes(root: str, base: str, head: str = "HEAD") -> "list[Change]":
    """Files that differ between commits `base` and `head`, renames paired."""
    out = _run(root, "diff", "--raw", "-z", "-M", "--no-abbrev",
               "--no-ext-diff", "--no-textconv", base, head)
    fields = out.split(b"\0")
    result: "list[Change]" = []
    i = 0
    while i < len(fields) and fields[i].startswith(b":"):
        meta = fields[i][1:].split(b" ")
        if len(meta) < 5:
            break
        old_mode, new_mode, old_sha, new_sha, status = meta[:5]
        kind = status[:1].decode("ascii", "replace")
        if kind in ("R", "C"):
            old_path, path = fields[i + 1], fields[i + 2]
            i += 3
        else:
            old_path = path = fields[i + 1]
            i += 2
        result.append(Change(
            status=kind,
            path=path.decode("utf-8", "replace"),
            old_path=("" if kind == "A" else old_path.decode("utf-8", "replace")),
            old_blob=_blob(old_mode, old_sha),
            new_blob=_blob(new_mode, new_sha)))
    return result


def read_blobs(root: str, blobs: "list[str]") -> "dict[str, bytes | None]":
    """Contents of the given blob ids (None for one that cannot be read)."""
    wanted = sorted(set(b for b in blobs if b))
    if not wanted:
        return {}
    out = _run(root, "cat-file", "--batch",
               input=("\n".join(wanted) + "\n").encode("ascii"))
    result: "dict[str, bytes | None]" = {}
    pos = 0
    for blob in wanted:
        end = out.find(b"\n", pos)
        if end < 0:
            result[blob] = None
            continue
        header = out[pos:end].split()
        pos = end + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            result[blob] = out[pos:pos + size]
            pos += size + 1  # the content is followed by a newline
        else:
            result[blob] = None  # "<id> missing"
    return result


def blame_commit_for_line(root: str, rel_path: str, lineno: int) -> str:
    """Short sha of the commit that introduced a given line (best effort)."""
    try:
        out = _run(root, "blame", "--porcelain", "-L", f"{lineno},{lineno}",
                   "HEAD", "--", rel_path).decode("utf-8", "replace")
    except GitError:
        return ""
    for line in out.splitlines():
        if len(line) >= 40 and all(c in "0123456789abcdef" for c in line[:40]):
            return line[:7]
    return ""
