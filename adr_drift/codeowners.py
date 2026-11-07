"""CODEOWNERS: who answers for a file.

Follows GitHub's documented behaviour: the last matching pattern wins, a
pattern with a slash at the start or in the middle is anchored at the
repository root, one without matches at any depth, a trailing slash (or a
literal last segment) also covers everything below, `*` stays inside one
directory, `**` crosses directories, and a pattern with no owners makes a
file unowned. Sections ([Name]) are skipped as headings; `!`-negations are
not part of CODEOWNERS and are ignored.
"""

from __future__ import annotations

import os
import re

#: Where GitHub looks, in its order of precedence.
LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")

_MAX_BYTES = 1_000_000
_MAX_PATTERN = 256
_MAX_DEPTH = 256        # a path deeper than this is not looked up


def _seg_match(pat: str, text: str) -> bool:
    """`*` and `?` against one path segment, in O(len(pat) * len(text))
    whatever the pattern holds (a regex with many stars can take
    exponential time, and CODEOWNERS is written by the pull request)."""
    p = t = 0
    star = mark = -1
    while t < len(text):
        if p < len(pat) and pat[p] == "*":
            star, mark = p, t
            p += 1
        elif p < len(pat) and (pat[p] == "?" or pat[p] == text[t]):
            p += 1
            t += 1
        elif star >= 0:
            mark += 1
            t = mark
            p = star + 1
        else:
            return False
    return pat[p:].strip("*") == ""


class _Pattern:
    """One CODEOWNERS pattern as path segments; None stands for `**`."""

    def __init__(self, pattern: str) -> None:
        pat = pattern.replace("\\", "/")
        self.ok = bool(pat) and not pat.startswith("!") \
            and len(pat) <= _MAX_PATTERN
        body = pat.strip("/")
        if not body:
            self.ok = False
            return
        segs = body.split("/")
        anchored = pat.startswith("/") or len(segs) > 1
        out: "list[str | None]" = [None] if not anchored else []
        for seg in segs:
            if seg == "**":
                if not out or out[-1] is not None:
                    out.append(None)
            else:
                out.append(seg)
        # a literal last segment (or a trailing slash) covers what is below
        last = segs[-1]
        if (pat.endswith("/") or not any(c in last for c in "*?")) \
                and out[-1] is not None:
            out.append(None)
        self.segs = out

    def matches(self, path: "list[str]") -> bool:
        segs, n = self.segs, len(path)
        memo: "dict[tuple[int, int], bool]" = {}

        def go(i: int, j: int) -> bool:
            if i == len(segs):
                return j == n
            key = (i, j)
            if key in memo:
                return memo[key]
            if segs[i] is None:
                res = go(i + 1, j) or (j < n and go(i, j + 1))
            else:
                res = j < n and _seg_match(segs[i], path[j]) \
                    and go(i + 1, j + 1)
            memo[key] = res
            return res

        return go(0, 0)


class CodeOwners:
    def __init__(self, text: str = "") -> None:
        self.rules: "list[tuple[_Pattern, list[str]]]" = []
        for raw in text.replace("\r\n", "\n").split("\n"):
            line = re.sub(r"(?<!\\)#.*$", "", raw).strip()
            if not line or (line.startswith("[") and line.endswith("]")):
                continue
            parts = line.split()
            pat = _Pattern(parts[0])
            if pat.ok:
                self.rules.append((pat, parts[1:]))

    def owners(self, path: str) -> "list[str]":
        segs = [p for p in path.replace("\\", "/").split("/") if p]
        if len(segs) > _MAX_DEPTH:
            return []
        result: "list[str]" = []
        for pat, owners in self.rules:
            if pat.matches(segs):
                result = owners
        return result


def load(root: str) -> CodeOwners:
    """The CODEOWNERS file of the repository (no owners when there is none)."""
    for rel in LOCATIONS:
        path = os.path.join(root, *rel.split("/"))
        try:
            with open(path, "rb") as f:
                data = f.read(_MAX_BYTES)
        except OSError:
            continue
        return CodeOwners(data.decode("utf-8", "replace"))
    return CodeOwners()
