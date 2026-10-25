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
import datetime
import difflib
import fnmatch
import re

#: The rule names the DSL knows.
KINDS = ("forbid-import", "forbid-pattern", "require-pattern")


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
    #: A temporary exception: violations of this rule are allowed up to and
    #: including this date (`allow-until: YYYY-MM-DD` at the end of the line).
    allow_until: "datetime.date | None" = None
    #: The ADR this rule came from: {"number", "title", "path"}; None for a
    #: rule in arch.yaml.
    adr: "dict | None" = None

    def describe(self) -> str:
        if self.kind == "forbid-import":
            return f"{self.from_pat} must not import {self.to_pat}"
        if self.kind == "forbid-pattern":
            return f"{self.glob} must not contain /{self.regex}/"
        return f"{self.glob} must contain /{self.regex}/"

    def where(self) -> str:
        """"arch.yaml:7" (or just the source when the line is unknown)."""
        return f"{self.source}:{self.line_no}" if self.line_no else self.source


# The text is stripped before it is matched, so no \s*$ is needed after the
# rest (a lazy group followed by \s*$ is quadratic on a long run of blanks).
_RULE_RE = re.compile(
    r"^(forbid-import|forbid-pattern|require-pattern)\s*:\s*(.+)$")


def parse_rule_line(line: str, source: str = "",
                    line_no: int = 0) -> "Rule | None":
    """Parse one DSL line. Returns None for blanks/comments/non-rules."""
    text = line.split("#", 1)[0].strip()
    if not text:
        return None
    m = _RULE_RE.match(text)
    if not m:
        return None
    allow_until = None
    if ALLOW_UNTIL in text:
        text, allow_until = split_allow_until(text, line.strip())
        m = _RULE_RE.match(text)
        if not m:
            return None
    kind, rest = m.group(1), m.group(2)
    rule = Rule(kind=kind, source=source, line_no=line_no, raw=line.strip(),
                allow_until=allow_until)
    if kind == "forbid-import":
        parts = rest.split("->")
        if len(parts) != 2:
            raise ValueError(f"bad forbid-import rule: {line.strip()!r} "
                             f"(want '<from> -> <to>')")
        rule.from_pat = parts[0].strip()
        rule.to_pat = parts[1].strip()
        if not rule.from_pat or not rule.to_pat:
            raise ValueError(f"bad forbid-import rule: {line.strip()!r}")
        for side in (rule.from_pat, rule.to_pat):
            if re.search(r"\s", side):
                # "handlers -> db because layering" would otherwise become a
                # rule whose target is the phrase "db because layering" and
                # never match anything.
                raise ValueError(
                    f"bad forbid-import rule: {line.strip()!r} ({side!r} "
                    "contains spaces; each side is one layer name or module "
                    "prefix - put explanations after a '#')")
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


#: The word that ends a rule line with a temporary exception:
#: `forbid-import: a -> b  allow-until: 2026-12-31`.
ALLOW_UNTIL = "allow-until"
_ALLOW_TAIL_RE = re.compile(r"[ \t]*[:=][ \t]*(\S*)[ \t]*$")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}$")


def split_allow_until(text: str, shown: str
                      ) -> "tuple[str, datetime.date | None]":
    """(rule text without the exception, its date) for a line that may end
    in `allow-until: YYYY-MM-DD` (`=` also works, handy in unquoted YAML).

    A line that mentions `allow-until` followed by a colon or equals sign
    but does not END with a valid date is an error: a typo here would
    otherwise turn the exception into a rule that never fires, or into a
    permanent one. A rule that merely contains the word (inside its regex,
    say) is left alone."""
    at = text.rfind(ALLOW_UNTIL)
    tail = _ALLOW_TAIL_RE.match(text, at + len(ALLOW_UNTIL))
    if tail is None:
        # "allow-until" without ":" or "=" after it: just part of the rule
        # unless the line is plainly an exception ("... allow-until 2026-..")
        if re.search(r"[ \t]allow-until[ \t]+\d", text):
            raise ValueError(
                f"bad exception in {shown!r}: write it as "
                f"'allow-until: YYYY-MM-DD' at the end of the rule")
        return text, None
    before = text[:at]
    if at and not before[-1].isspace():
        return text, None   # part of a longer word
    value = tail.group(1)
    if not _DATE_RE.match(value):
        raise ValueError(
            f"bad exception in {shown!r}: 'allow-until' needs a date like "
            f"2026-12-31 (year-month-day), got {value!r}")
    try:
        date = datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"bad exception in {shown!r}: {value!r} is not a real date "
            "(want YYYY-MM-DD)") from None
    return before.rstrip(), date


# -- Markdown decoration and "does this look like a rule?" -------------------

_BULLET_RE = re.compile(r"^(?:[-*+]|\d{1,4}[.)])\s+")
_TASK_RE = re.compile(r"^\[[ xX]\]\s+")
_EMPHASIS_RE = re.compile(r"^(\*\*|__)(.*?)\1")
_HEAD_WORD_RE = re.compile(r"^([A-Za-z][A-Za-z_ -]{2,40}?)\s*:")
_KEYWORD_INSIDE_RE = re.compile(
    r"(?<![\w-])(?:forbid|require)[-_ ]?(?:import|pattern)s?\s*:", re.I)
_BARE_ARROW_RE = re.compile(r"^[\w.*/-]+\s*->\s*[\w.*/-]+$")
_NAME_NO_COLON_RE = re.compile(
    r"^(?:forbid-import|forbid-pattern|require-pattern)\s")


def clean_rule_text(line: str) -> "tuple[str, bool]":
    """Strip the Markdown around a rule line -> (text, was_a_list_item).

    People write rules as `- rule`, `* rule`, `1. rule`, `- [ ] rule`,
    `> rule`, **rule** or `` `rule` `` (optionally followed by a sentence
    of explanation); all of them are the same rule.
    """
    s = line.strip()
    while s.startswith(">"):
        s = s[1:].lstrip()
    listed = False
    m = _BULLET_RE.match(s)
    if m:
        listed = True
        s = s[m.end():].lstrip()
        t = _TASK_RE.match(s)
        if t:
            s = s[t.end():].lstrip()
    if s.startswith("`"):
        n = len(s) - len(s.lstrip("`"))
        end = s.find("`" * n, n)
        if end > n:
            s = s[n:end].strip()
    else:
        s = _EMPHASIS_RE.sub(r"\2", s, count=1).strip()
    return s, listed


def rule_like_problem(text: str, listed: bool = False) -> "str | None":
    """Why `text` looks like a rule that did NOT parse, or None for prose.

    Called for a line that parse_rule_line() returned None for. A rule that
    is silently ignored is worse than an error: the repo looks enforced and
    is not.
    """
    t = text.split("#", 1)[0].strip()
    if not t:
        return None
    head = _HEAD_WORD_RE.match(t)
    if head:
        word = head.group(1)
        norm = re.sub(r"[\s_]+", "-", word.strip().lower())
        if word == norm and norm in KINDS:
            return f"rule {norm!r} has nothing after the colon"
        if norm in KINDS:
            return (f"unknown rule name {word!r}: write it exactly as "
                    f"{norm!r} (lower case, hyphen)")
        # A typo is about as long as the name it misses; "See forbid-import"
        # (a sentence that mentions the keyword) is not a typo of it.
        close = [k for k in difflib.get_close_matches(norm, KINDS, n=3,
                                                      cutoff=0.75)
                 if abs(len(k) - len(norm)) <= 2]
        if close:
            return f"unknown rule name {word!r} (did you mean {close[0]!r}?)"
    if _NAME_NO_COLON_RE.match(t):
        return (f"the rule name must be followed by a colon, like "
                f"'{t.split(None, 1)[0]}: ...'")
    m = _KEYWORD_INSIDE_RE.search(t)
    if m:
        operators = ("->", "!~", "=~")
        # A list item, or a line that carries a rule's payload after the
        # name, is a rule attempt; a sentence that merely names the keyword
        # is prose.
        if listed or m.start() == 0 or any(
                op in t[m.end():] for op in operators):
            return ("a rule must start with its name, like "
                    "'forbid-import: <from> -> <to>' "
                    f"(found {m.group(0).strip()!r} inside the line)")
    if _BARE_ARROW_RE.match(t):
        return f"looks like a rule without its name; did you mean 'forbid-import: {t}'?"
    return None


def did_you_mean(word: str, choices: "tuple[str, ...] | list[str]") -> str:
    """" (did you mean 'paths'?)" for a near miss, else an empty string."""
    close = difflib.get_close_matches(str(word).lower(), list(choices),
                                      n=1, cutoff=0.6)
    return f" (did you mean {close[0]!r}?)" if close else ""


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
