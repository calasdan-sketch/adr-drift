"""Rules: the constraint DSL shared by arch.yaml and ADR files.

A rule is one line:

    forbid-import: <from> -> <to>        # layer/module <from> may not import <to>
    forbid-pattern: <glob> !~ <regex>    # files matching <glob> must not contain <regex>
    require-pattern: <glob> =~ <regex>   # files matching <glob> must contain <regex>

<from>/<to> match against layer names or module prefixes ("db.drivers",
"src/handlers"). "*" is a wildcard. Comments start with "#".
"""

from __future__ import annotations

import dataclasses
import fnmatch
import re


@dataclasses.dataclass
class Rule:
    kind: str            # forbid-import | forbid-pattern | require-pattern
    source: str          # "arch.yaml" or "docs/adr/0004-....md"
    line_no: int = 0
    raw: str = ""
    # forbid-import
    from_pat: str = ""
    to_pat: str = ""
    # pattern rules
    glob: str = ""
    regex: str = ""

    def describe(self) -> str:
        if self.kind == "forbid-import":
            return f"{self.from_pat} must not import {self.to_pat}"
        if self.kind == "forbid-pattern":
            return f"{self.glob} must not contain /{self.regex}/"
        return f"{self.glob} must contain /{self.regex}/"


_RULE_RE = re.compile(
    r"^(forbid-import|forbid-pattern|require-pattern)\s*:\s*(.+?)\s*$")


def parse_rule_line(line: str, source: str = "",
                    line_no: int = 0) -> "Rule | None":
    """Parse one DSL line. Returns None for blanks/comments/non-rules."""
    text = line.split("#", 1)[0].strip()
    if not text:
        return None
    m = _RULE_RE.match(text)
    if not m:
        return None
    kind, rest = m.group(1), m.group(2)
    rule = Rule(kind=kind, source=source, line_no=line_no, raw=line.strip())
    if kind == "forbid-import":
        parts = rest.split("->")
        if len(parts) != 2:
            raise ValueError(f"bad forbid-import rule: {line.strip()!r} "
                             f"(want '<from> -> <to>')")
        rule.from_pat = parts[0].strip()
        rule.to_pat = parts[1].strip()
        if not rule.from_pat or not rule.to_pat:
            raise ValueError(f"bad forbid-import rule: {line.strip()!r}")
    else:
        op = "!~" if kind == "forbid-pattern" else "=~"
        if op not in rest:
            raise ValueError(f"bad {kind} rule: {line.strip()!r} "
                             f"(want '<glob> {op} <regex>')")
        glob, regex = rest.split(op, 1)
        rule.glob = glob.strip()
        rule.regex = regex.strip()
        if not rule.glob or not rule.regex:
            raise ValueError(f"bad {kind} rule: {line.strip()!r}")
        try:
            re.compile(rule.regex)  # validate early
        except re.error as e:
            raise ValueError(f"bad {kind} regex in {line.strip()!r}: {e}")
    return rule


def _norm(pat: str) -> str:
    return pat.replace("/", ".").strip(".")


def match_pat(pat: str, value: str) -> bool:
    """Match a from/to pattern against a layer name or dotted module.

    "db.drivers" matches "db.drivers" and "db.drivers.pg" (prefix with
    boundary). "*" wildcards supported via fnmatch on both dotted and
    slash forms.
    """
    p, v = _norm(pat), _norm(value)
    if "*" in p:
        return (fnmatch.fnmatchcase(v, p)
                or fnmatch.fnmatchcase(value, pat)
                or fnmatch.fnmatchcase(v, p.rstrip("*").rstrip(".")))
    return v == p or v.startswith(p + ".")


def match_glob(glob: str, path: str) -> bool:
    path = path.replace("\\", "/")
    g = glob.replace("\\", "/")
    if fnmatch.fnmatchcase(path, g):
        return True
    # "**" should also match zero directories: "src/**.py" vs "src/a.py"
    if "**" in g:
        return fnmatch.fnmatchcase(path, g.replace("**/", ""))
    return False
