"""Config + ADR loading: arch.yaml and the Constraints sections of the ADRs.

Loading is strict on purpose. A rule that is silently dropped leaves the
repo looking enforced when it is not, so anything that looks like a rule
but does not parse is an error with a file:line, and unknown keys (a typo
such as `path:` for `paths:`) are errors with a did-you-mean.
"""

from __future__ import annotations

import datetime
import dataclasses
import os
import posixpath
import re

from . import paths as paths_mod
from . import miniyaml
from . import rules as rules_mod

#: Where ADRs are looked for when --adr-dir is not given: the first of these
#: directories that exists is read (adr-tools writes doc/adr, MADR
#: docs/decisions). docs/adr is also where `init` and `new` create one.
ADR_DIR_CANDIDATES = ("docs/adr", "doc/adr", "docs/decisions",
                      "docs/architecture/decisions", "adr", "decisions")
DEFAULT_ADR_DIR = ADR_DIR_CANDIDATES[0]
ARCH_FILE = "arch.yaml"

#: Directories that are never entered below an ADR directory (hidden ones
#: are skipped too).
_ADR_SKIP_DIRS = frozenset({"node_modules", "__pycache__"})

CONSTRAINTS_RE = re.compile(
    r"^##\s+Constraints\s*$", re.IGNORECASE | re.MULTILINE)

_H2_RE = re.compile(r"^##\s")
# No lazy group followed by \s*$ here or in rules._RULE_RE: on a line with a
# long run of blanks that is tried at every position, which is quadratic.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

ARCH_KEYS = ("layers", "rules", "waivers")
LAYER_KEYS = ("name", "paths")
WAIVER_KEYS = ("rule", "paths", "owner", "expires", "reason")
#: A key with this prefix is a note, at the top level and in a layer.
NOTE_KEY_PREFIX = "x-"


class ConfigError(ValueError):
    """The config is wrong. `problems` has one "file:line: what" per issue."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


@dataclasses.dataclass
class Waiver:
    """A time-boxed exception to one rule (arch.yaml `waivers:`)."""
    rule: str                # the rule's description, as reports print it
    owner: str               # who answers for it
    expires: datetime.date   # the last day it applies
    reason: str
    paths: list              # globs; empty: every file
    line: int = 0


@dataclasses.dataclass
class Loaded:
    layers: list
    rules: list
    adrs: list
    warnings: list
    #: ADRs whose status retires them (superseded, deprecated, rejected):
    #: (display path, status, number of rules NOT enforced).
    inactive: list = dataclasses.field(default_factory=list)
    waivers: list = dataclasses.field(default_factory=list)


#: The first word of a status that retires an ADR: its constraints are not
#: enforced any more. adr-tools writes "Superseded by ..."; MADR's statuses
#: are proposed, rejected, accepted, deprecated and "superseded by ...".
INACTIVE_STATUSES = frozenset({"superseded", "deprecated", "rejected"})


def _status_word(status: str) -> str:
    """The first word of a status, lower case ("" if it has none)."""
    m = re.match(r"[^A-Za-z]*([A-Za-z]+)", status or "")
    return m.group(1).lower() if m else ""


def is_inactive_status(status: str) -> bool:
    """True when `status` (as an ADR spells it: "Superseded by 0005",
    "**Rejected**", "deprecated.") retires the ADR."""
    return _status_word(status) in INACTIVE_STATUSES


_LINK_TARGET_RE = re.compile(r"\]\(\s*<?([^)\s>]+)")
_NUMBER_RE = re.compile(r"\d+")


def _superseding(status: str, adrs: "list[dict]") -> "dict | None":
    """The ADR a "Superseded by ..." status names: a Markdown link to its
    file ("Superseded by [5](0005-x.md)") or its number ("by ADR-0005",
    "by 5"); None when it names none of `adrs`."""
    rest = re.sub(r"(?i)^[^A-Za-z]*superseded", "", status)
    m = _LINK_TARGET_RE.search(rest)
    if m:
        target = posixpath.basename(m.group(1).split("#", 1)[0]
                                    .replace("\\", "/"))
        for a in adrs:
            if os.path.basename(a["path"]) == target:
                return a
        return None
    m = _NUMBER_RE.search(rest)
    if m:
        for a in adrs:
            if a["number"] and int(a["number"]) == int(m.group(0)):
                return a
    return None


def _supersede_problem(adr: dict, adrs: "list[dict]", shown: str) -> str:
    """Why a superseded ADR may not drop its rules ("" when it may): its
    status must name an ADR that was read, and following "superseded by"
    from there must end at an ADR that is in force. Otherwise a status edit
    alone - pointing nowhere - would switch the rules off."""
    if _status_word(adr["status"]) != "superseded":
        return ""  # deprecated / rejected need no successor
    seen = {adr["path"]}
    current = adr
    while _status_word(current["status"]) == "superseded":
        nxt = _superseding(current["status"], adrs)
        if nxt is None:
            return (f"{shown}: status {adr['status']!r} must name the ADR "
                    "that replaces it, and that ADR must have been read "
                    "(e.g. 'Superseded by [5](0005-x.md)' or 'Superseded by "
                    "0005'; pass --adr-dir for ADRs elsewhere): until then "
                    f"its {len(adr['rules'])} rule(s) cannot be retired")
        if nxt["path"] in seen:
            return (f"{shown}: 'superseded by' goes round in a loop; its "
                    f"{len(adr['rules'])} rule(s) cannot be retired")
        seen.add(nxt["path"])
        current = nxt
    if is_inactive_status(current["status"]):
        other = os.path.basename(current["path"])
        return (f"{shown}: superseded by {other}, which is "
                f"{current['status']!r}, not in force: its "
                f"{len(adr['rules'])} rule(s) cannot be retired")
    return ""


def _lines(text: str) -> "list[str]":
    """Split on \\n, \\r\\n and \\r only, so line numbers match an editor's
    (str.splitlines() also splits on form feeds and U+2028)."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _without_html_comments(lines: "list[str]") -> "list[str]":
    """The lines with every <!-- ... --> blanked out (a commented-out rule
    is an example, not a rule); line numbers are preserved. An opener with
    no closer is not a comment. (str.find, not a regex: <!-- .*? --> looks
    for its closer again from every opener, which is quadratic.)"""
    text = "\n".join(lines)
    parts: "list[str]" = []
    pos = 0
    while True:
        start = text.find("<!--", pos)
        end = text.find("-->", start + 4) if start >= 0 else -1
        if end < 0:
            break      # no closer after this opener, so none after a later one
        parts.append(text[pos:start])
        parts.append("\n" * text.count("\n", start, end))
        pos = end + 3
    parts.append(text[pos:])
    return "".join(parts).split("\n")


def _find_line(lines, pattern: str, start: int = 0) -> int:
    """1-based number of the first line at/after `start` matching the regex
    `pattern`; 0 when there is none."""
    rx = re.compile(pattern)
    for i in range(start, len(lines)):
        if rx.search(lines[i]):
            return i + 1
    return 0


def _where(path: str, line: int) -> str:
    return f"{path}:{line}" if line else path


def _rule_item_lines(lines) -> "list[int]":
    """Line numbers of the items of the top-level `rules:` list, best effort
    (miniyaml does not keep positions)."""
    start = _find_line(lines, r"^rules\s*:")
    if not start:
        return []
    if lines[start - 1].split(":", 1)[1].split(" #", 1)[0].strip():
        return [start]  # flow style, on the key's own line
    out: "list[int]" = []
    item_indent = None
    for i in range(start, len(lines)):
        raw = lines[i].rstrip()
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(stripped)
        if indent == 0 and not stripped.startswith("-"):
            break  # next top-level key
        if stripped.startswith("-") and (
                item_indent is None or indent == item_indent):
            item_indent = indent
            out.append(i + 1)
    return out


def load_arch_yaml(root: str, problems: "list[str] | None" = None,
                   waivers_out: "list | None" = None
                   ) -> "tuple[list[dict], list[rules_mod.Rule]]":
    """Returns (layers, rules). Missing arch.yaml -> ([], []). The waivers
    section is appended to `waivers_out` when it is given.

    Raises ConfigError listing every problem, unless `problems` is given:
    then problems are appended to it and whatever did parse is returned.
    """
    path = os.path.join(root, ARCH_FILE)
    if not os.path.isfile(path):
        return [], []
    own = problems is None
    problems = [] if own else problems
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        problems.append(f"{ARCH_FILE}: cannot read ({e})")
        text = ""
    lines = _lines(text)
    data = None
    if text:
        try:
            data = miniyaml.safe_load(text)
        except ValueError as e:
            problems.append(f"{ARCH_FILE}: {e}")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        problems.append(f"{ARCH_FILE}: expected a mapping with 'layers' and "
                        f"'rules' at the top level, got "
                        f"{type(data).__name__}")
        data = {}
    for key in data:
        if key in ARCH_KEYS or str(key).startswith(NOTE_KEY_PREFIX):
            continue
        line = _find_line(lines, "^" + re.escape(str(key)) + r"\s*:")
        problems.append(
            f"{_where(ARCH_FILE, line)}: unknown key {key!r}"
            f"{rules_mod.did_you_mean(key, ARCH_KEYS)} "
            f"(allowed: {', '.join(ARCH_KEYS)}; prefix a note key with "
            f"'{NOTE_KEY_PREFIX}' to keep it)")
    layers = _load_layers(data.get("layers"), lines, problems)
    out = _load_rules(data.get("rules"), lines, problems)
    waivers = _load_waivers(data.get("waivers"), lines, problems)
    if waivers_out is not None:
        waivers_out.extend(waivers)
    elif waivers:
        problems.append(f"{ARCH_FILE}: waivers are present but this caller "
                        "does not apply them")
    if own and problems:
        raise ConfigError(problems)
    return layers, out


def _load_layers(raw, lines, problems) -> "list[dict]":
    if raw is None:
        return []
    if not isinstance(raw, list):
        line = _find_line(lines, r"^layers\s*:")
        problems.append(
            f"{_where(ARCH_FILE, line)}: 'layers' must be a list of "
            f"{{name, paths}} entries, got {type(raw).__name__}")
        return []
    layers: "list[dict]" = []
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            problems.append(f"{ARCH_FILE}: layers[{i}] must be a mapping "
                            f"with 'name' and 'paths', got {item!r}")
            continue
        label = str(item.get("name") or f"layers[{i}]")
        for key in item:
            if key not in LAYER_KEYS and not str(key).startswith(
                    NOTE_KEY_PREFIX):
                line = _find_line(lines, r'^[ \t]*(?:-[ \t]*)?'
                                  + re.escape(str(key)) + r'\s*:')
                problems.append(
                    f"{_where(ARCH_FILE, line)}: layer {label!r}: unknown key "
                    f"{key!r}{rules_mod.did_you_mean(key, LAYER_KEYS)} "
                    f"(allowed: {', '.join(LAYER_KEYS)}; prefix a note key "
                    f"with '{NOTE_KEY_PREFIX}' to keep it)")
        name = item.get("name")
        if name is None or str(name).strip() == "":
            problems.append(f"{ARCH_FILE}: layers[{i}] has no 'name'")
            continue
        paths = item.get("paths")
        if isinstance(paths, str):
            paths = [paths]  # a single glob may be written without a list
        if (not isinstance(paths, list) or not paths
                or not all(isinstance(p, str) and p for p in paths)):
            problems.append(
                f"{ARCH_FILE}: layer {str(name)!r} needs 'paths': a list of "
                "globs such as [\"src/db/**\"]")
            continue
        layers.append({"name": str(name), "paths": list(paths)})
    return layers


def _item_lines(lines, key: str) -> "list[int]":
    """Line numbers of the items of the top-level list `key:` (best effort)."""
    start = _find_line(lines, r"^" + re.escape(key) + r"\s*:")
    if not start:
        return []
    out: "list[int]" = []
    item_indent = None
    for i in range(start, len(lines)):
        raw = lines[i].rstrip()
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(stripped)
        if indent == 0 and not stripped.startswith("-"):
            break
        if stripped.startswith("-") and (
                item_indent is None or indent == item_indent):
            item_indent = indent
            out.append(i + 1)
    return out


def _load_waivers(raw, lines, problems) -> "list[Waiver]":
    """arch.yaml `waivers:`. A waiver with no owner or no end date is the
    thing it exists to prevent, so each of `rule`, `owner`, `expires` and
    `reason` is required, and `expires` must be a real YYYY-MM-DD date."""
    if raw is None:
        return []
    key_line = _find_line(lines, r"^waivers\s*:")
    if not isinstance(raw, list):
        problems.append(f"{_where(ARCH_FILE, key_line)}: 'waivers' must be a "
                        f"list of mappings, got {type(raw).__name__}")
        return []
    item_lines = _item_lines(lines, "waivers")
    out: "list[Waiver]" = []
    for i, item in enumerate(raw):
        line = item_lines[i] if i < len(item_lines) else key_line
        where = _where(ARCH_FILE, line)
        if not isinstance(item, dict):
            problems.append(f"{where}: a waiver is a mapping with "
                            f"{', '.join(WAIVER_KEYS)}, got {item!r}")
            continue
        bad = False
        for key in item:
            if key not in WAIVER_KEYS and not str(key).startswith(
                    NOTE_KEY_PREFIX):
                problems.append(
                    f"{where}: waiver: unknown key {key!r}"
                    f"{rules_mod.did_you_mean(key, WAIVER_KEYS)} (allowed: "
                    f"{', '.join(WAIVER_KEYS)})")
                bad = True
        for key in ("rule", "owner", "expires", "reason"):
            value = item.get(key)
            if value is None or not str(value).strip():
                problems.append(f"{where}: waiver has no '{key}' (a waiver "
                                "needs a rule, an owner, an expiry date and "
                                "a reason)")
                bad = True
        expires = None
        if item.get("expires") is not None:
            try:
                expires = datetime.date.fromisoformat(
                    str(item["expires"]).strip())
            except ValueError:
                problems.append(f"{where}: waiver 'expires' must be a date "
                                f"like 2027-03-31, got {item['expires']!r}")
                bad = True
        paths = item.get("paths", [])
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not all(
                isinstance(p, str) and p for p in paths):
            problems.append(f"{where}: waiver 'paths' must be a list of "
                            "globs")
            bad = True
        if bad:
            continue
        out.append(Waiver(rule=" ".join(str(item["rule"]).split()),
                          owner=str(item["owner"]).strip(), expires=expires,
                          reason=str(item["reason"]).strip(),
                          paths=list(paths), line=line))
    return out


def _load_rules(raw, lines, problems) -> "list[rules_mod.Rule]":
    if raw is None:
        return []
    key_line = _find_line(lines, r'^rules\s*:')
    if not isinstance(raw, list):
        problems.append(
            f"{_where(ARCH_FILE, key_line)}: 'rules' must be a list, one "
            f"rule per item, got {type(raw).__name__}")
        return []
    item_lines = _rule_item_lines(lines)
    out: "list[rules_mod.Rule]" = []
    for i, item in enumerate(raw):
        line = item_lines[i] if i < len(item_lines) else key_line
        where = _where(ARCH_FILE, line)
        if isinstance(item, dict):
            # `- forbid-import: a -> b` without quotes is a YAML mapping.
            if len(item) != 1:
                problems.append(
                    f"{where}: rule has extra keys {sorted(item)!r}; a rule "
                    "is one line like \"forbid-import: a -> b\"")
                continue
            ((kind, value),) = item.items()
            text = f"{kind}: {value}"
        elif isinstance(item, str):
            text = item
        else:
            problems.append(f"{where}: a rule must be a string like "
                            f"\"forbid-import: a -> b\", got {item!r}")
            continue
        try:
            rule = rules_mod.parse_rule_line(text, source=ARCH_FILE,
                                             line_no=line)
        except ValueError as e:
            problems.append(f"{where}: {e}")
            continue
        if rule is None:
            hint = rules_mod.rule_like_problem(text, listed=True)
            problems.append(
                f"{where}: not a rule: {text!r}"
                + (f" - {hint}" if hint else
                   " (rules are 'forbid-import: <from> -> <to>', "
                   "'forbid-pattern: <glob> !~ <regex>', "
                   "'require-pattern: <glob> =~ <regex>', "
                   "'forbid-cycles: <layers>', "
                   "'forbid-transitive: <from> -> <to>', "
                   "'layer-order: <a> > <b> > <c>', "
                   "'only-import: <layer> -> <a>, <b>' or "
                   "'public-api: <layer> -> <module>')"))
            continue
        out.append(rule)
    return out


def adr_files(root: str, adr_dir: str = DEFAULT_ADR_DIR,
              problems: "list[str] | None" = None) -> "list[str]":
    """The ADR files below one directory: every .md file in it and in its
    subdirectories (docs/adr/2024/...), except README.md (the index) and
    anything under a hidden directory. Sorted by the '/'-joined path below
    the directory, so the order is the same on every platform. A directory
    that cannot be read is appended to `problems` (it must not turn into
    "no ADRs" unnoticed)."""
    d = os.path.join(root, adr_dir)
    if not os.path.isdir(d):
        return []

    def unreadable(e: OSError) -> None:
        if problems is not None:
            problems.append(
                f"cannot read directory {e.filename} "
                f"({e.strerror or type(e).__name__}): the ADRs in it were "
                "NOT read")

    found: "list[str]" = []
    for dirpath, dirnames, filenames in os.walk(d, onerror=unreadable):
        dirnames[:] = [n for n in dirnames
                       if not n.startswith(".") and n not in _ADR_SKIP_DIRS]
        found.extend(os.path.join(dirpath, f) for f in filenames
                     if f.endswith(".md") and f != "README.md")
    return sorted(
        found, key=lambda p: paths_mod.rel_posix(p, d))


def _dir_list(adr_dir) -> "list[str]":
    """--adr-dir as a list: None (search), one name, or several."""
    if adr_dir is None:
        return []
    # `--adr-dir docs\adr`, as typed on Windows, means docs/adr everywhere.
    if isinstance(adr_dir, str):
        return [adr_dir.replace("\\", "/")]
    return [d.replace("\\", "/") for d in adr_dir]


def _same_dir(a: str, b: str) -> bool:
    def norm(d: str) -> str:
        return posixpath.normpath(d.replace("\\", "/"))
    return norm(a) == norm(b)


def primary_adr_dir(root: str, adr_dir=None) -> str:
    """The directory `new`, `index` and `init` write to (relative to root):
    the first --adr-dir, else the first candidate that exists, else
    docs/adr."""
    asked = _dir_list(adr_dir)
    if asked:
        return asked[0]
    for candidate in ADR_DIR_CANDIDATES:
        if os.path.isdir(os.path.join(root, candidate)):
            return candidate
    return DEFAULT_ADR_DIR


@dataclasses.dataclass
class AdrSearch:
    files: list        # the ADR files to read, in order
    warnings: list     # whatever could make "no violations" mean "nothing read"
    primary: str       # see primary_adr_dir()


def find_adrs(root: str, adr_dir=None) -> AdrSearch:
    """Locate the ADR files of a repository.

    Without --adr-dir the first of ADR_DIR_CANDIDATES that exists is read
    (any other candidate that holds ADR files is reported, not read in
    silence); with one or more --adr-dir exactly those are read. Directories
    are searched recursively. Whatever could make a green result mean
    "nothing was read" - a directory that is missing, one that is empty, ADR
    files next door - comes back as a warning.
    """
    asked = _dir_list(adr_dir)
    warnings: "list[str]" = []
    existing = [c for c in ADR_DIR_CANDIDATES
                if os.path.isdir(os.path.join(root, c))]
    if asked:
        use = []
        for d in asked:
            if os.path.isdir(os.path.join(root, d)):
                use.append(d)
                continue
            near = [c for c in existing if not _same_dir(c, d)]
            hint = (f" ({near[0]} exists: did you mean --adr-dir {near[0]}?)"
                    if near else "")
            warnings.append(f"ADR directory {d} not found, so no ADRs were "
                            f"read from it{hint}")
    else:
        use = existing[:1]
        for other in existing[1:]:
            n = len(adr_files(root, other))
            if n:
                warnings.append(
                    f"{other} also holds {n} ADR file{'' if n == 1 else 's'} "
                    f"that {'was' if n == 1 else 'were'} NOT read ({use[0]} "
                    f"is the one used); to read both, pass --adr-dir "
                    f"{use[0]} --adr-dir {other}")
    files: "list[str]" = []
    seen: "set[str]" = set()
    for d in use:
        for path in adr_files(root, d, warnings):
            key = os.path.normcase(os.path.realpath(path))
            if key not in seen:
                seen.add(key)
                files.append(path)
    if not files and (use or not asked):
        where = (f"in {', '.join(asked)}" if asked else
                 f"(looked in {', '.join(ADR_DIR_CANDIDATES)})")
        warnings.append(
            f"no ADR files found {where}: only the rules in "
            f"{ARCH_FILE} are checked" + ("" if asked else
                                          "; if your ADRs are elsewhere, "
                                          "pass --adr-dir DIR"))
    return AdrSearch(files, warnings, primary_adr_dir(root, adr_dir))


def next_adr_number(root: str, adr_dir: str) -> int:
    """The number after the highest one among the ADR files below the
    directory (1 when there are none)."""
    best = 0
    for path in adr_files(root, adr_dir):
        m = re.match(r"(\d+)[-_]", os.path.basename(path))
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


# -- title and status of an ADR ------------------------------------------------

_TITLE_RE = re.compile(r"#[ \t]+(.+)$")
_STATUS_HEADING_RE = re.compile(r"##[ \t]+status[ \t]*$", re.IGNORECASE)
_FRONT_STATUS_RE = re.compile(r"status[ \t]*:[ \t]*(.*)$", re.IGNORECASE)
# "Status: x", "* Status: x", "- Status: x", "**Status:** x", "**Status**: x"
_STATUS_LINE_RE = re.compile(
    r"[ \t]*(?:[-*+][ \t]+)?(?:\*\*|__)?status(?:\*\*|__)?[ \t]*:"
    r"(?:\*\*|__)?[ \t]*(.*)$", re.IGNORECASE)
_YAML_COMMENT_RE = re.compile(r"[ \t]#")
#: How far below the title a `Status:` line may be (and never past the first
#: `##` heading): below that it is prose.
_STATUS_ZONE = 40


def _front_matter(lines: "list[str]") -> "tuple[list[str], int]":
    """(the lines between the `---` fences at the top of the file, the index
    of the first line after them); ([], 0) when the file has none."""
    if lines and lines[0].rstrip() == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip() in ("---", "..."):
                return lines[1:i], i + 1
    return [], 0


def _yaml_scalar(value: str) -> str:
    """A front matter value without its quotes or its trailing comment."""
    value = value.strip()
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        if end > 0:
            return value[1:end].strip()
    return _YAML_COMMENT_RE.split(value, 1)[0].strip()


def _status_section(body: "list[str]") -> str:
    """The first line under the first `## Status` heading ("" if the section
    is empty: the next heading is not a status)."""
    for i, line in enumerate(body):
        if _STATUS_HEADING_RE.match(line):
            for j in range(i + 1, len(body)):
                text = body[j].strip()
                if text:
                    return "" if text.startswith("#") else text
            return ""
    return ""


def _status_line(body: "list[str]") -> str:
    """A `Status: x` line (also `* Status: x`, `**Status:** x`) between the
    title and the first `##` heading."""
    for line in body[:_STATUS_ZONE]:
        if _H2_RE.match(line):
            break
        m = _STATUS_LINE_RE.match(line)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _without_fences(lines: "list[str]") -> "list[str]":
    """The lines with every fenced code block (``` or ~~~, CommonMark style:
    closed by a fence of the same character at least as long) blanked out,
    line numbers preserved. A `## Status` shown as an example inside a code
    block is not the ADR's status. An unclosed fence runs to the end of the
    file, as Markdown renders it."""
    out: "list[str]" = []
    fence = ""
    for line in lines:
        m = _FENCE_RE.match(line)
        if not fence:
            if m:
                fence = m.group(1)
                out.append("")
            else:
                out.append(line)
            continue
        out.append("")
        if (m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence)
                and not line.strip()[len(m.group(1)):].strip()):
            fence = ""
    return out


def _front_status(front: "list[str]") -> str:
    for line in front:
        m = _FRONT_STATUS_RE.match(line)
        if m:
            return _yaml_scalar(m.group(1))
    return ""


def adr_status_sources(lines: "list[str]") -> "tuple[str, str]":
    """(the status the body gives - a `## Status` section or a `Status:`
    line -, the status the front matter gives); "" for what is not there.
    Fenced code blocks are not read."""
    lines = _without_fences(lines)
    front, start = _front_matter(lines)
    body = lines[start:]
    return (_status_section(body) or _status_line(body)), _front_status(front)


def adr_title_and_status(lines: "list[str]") -> "tuple[str, str]":
    """(title, status) of an ADR given its lines (HTML comments already
    blanked); "" for what is not there.

    The title is the first `# ` heading after any front matter (a `#` line
    inside the front matter is a YAML comment). The status is, in this order,
    the first line of a `## Status` section, the `status:` of the front matter
    (MADR) or a `Status:` line under the title."""
    lines = _without_fences(lines)
    front, start = _front_matter(lines)
    body = lines[start:]
    title = ""
    for line in body:
        m = _TITLE_RE.match(line)
        if m and m.group(1).strip():
            title = m.group(1).strip()
            break
    status = _status_section(body) or _front_status(front)
    return title, status or _status_line(body)


_OWNER_KEYS = ("owner", "owners", "deciders", "decision-makers",
               "decision_makers", "decider")
_OWNER_LINE_RE = re.compile(
    r"[ \t]*(?:[-*+][ \t]+)?(?:\*\*|__)?(owners?|deciders?|"
    r"decision[- _]makers?)(?:\*\*|__)?[ \t]*:(?:\*\*|__)?[ \t]*(.+)$",
    re.IGNORECASE)
_DECISION_HEADING_RE = re.compile(
    r"##[ \t]+(?:decision(?:[ \t]+outcome)?|resolution)[ \t]*$", re.IGNORECASE)
_BACKTICKED_RE = re.compile(r"`([^`\n]{1,200})`")
_MAX_OWNERS = 20
_MAX_NAME = 80
_MAX_REFS = 500


def _split_names(value: str) -> "list[str]":
    value = value.strip().strip("[]")
    names = [n.strip().strip("'\"`*_")[:_MAX_NAME]
             for n in re.split(r"[,;]|\band\b", value[:2000])]
    return [n for n in names if n][:_MAX_OWNERS]


def adr_owners(lines: "list[str]") -> "list[str]":
    """Who decided / owns an ADR: the front matter's owner(s) / deciders /
    decision-makers, else an `Owner:` / `Deciders:` line above the first
    `##` heading. [] when it names none."""
    lines = _without_fences(lines)
    front, start = _front_matter(lines)
    for i, line in enumerate(front):
        m = re.match(r"([A-Za-z_-]+)[ \t]*:[ \t]*(.*)$", line)
        if not m or m.group(1).lower() not in _OWNER_KEYS:
            continue
        value = _yaml_scalar(m.group(2))
        if not value:                      # a block list under the key
            items = []
            for nxt in front[i + 1:]:
                bullet = re.match(r"[ \t]*-[ \t]+(.+)$", nxt)
                if not bullet:
                    break
                items.append(_yaml_scalar(bullet.group(1)))
            return [x[:_MAX_NAME] for x in items if x][:_MAX_OWNERS]
        return _split_names(value)
    for line in lines[start:start + _STATUS_ZONE]:
        if _H2_RE.match(line):
            break
        m = _OWNER_LINE_RE.match(line)
        if m:
            return _split_names(m.group(2))
    return []


_SCOPE_KEYS = ("scope", "scopes")
_SCOPE_LINE_RE = re.compile(
    r"[ \t]*(?:[-*+][ \t]+)?(?:\*\*|__)?(scopes?)(?:\*\*|__)?[ \t]*:"
    r"(?:\*\*|__)?[ \t]*(.+)$", re.IGNORECASE)
_MAX_SCOPE = 50


class _ProseScope(ValueError):
    """A `Scope:` line that reads as a sentence, not as globs."""


def _scope_problem(glob: str) -> "str | None":
    g = glob.replace("\\", "/")
    if g.startswith("/") or re.match(r"[A-Za-z]:", g):
        return "must be relative to the repository root"
    if ".." in g.split("/"):
        return "must not contain '..'"
    if not g.strip("/*"):
        return "would match every file (leave the Scope out instead)"
    return None


def adr_scope(lines: "list[str]") -> "list[str]":
    """The files an ADR's rules are about, for a repository with several
    services: the front matter's `scope:` (a comma-separated list or a block
    list) or a `Scope:` line above the first `##` heading, as repo-relative
    globs. [] when the ADR names none (its rules apply everywhere).
    Raises ValueError for a glob that is absolute, climbs out with '..' or
    covers everything, so a scope can not be used to point outside."""
    lines = _without_fences(lines)
    front, start = _front_matter(lines)
    names: "list[str]" = []
    for i, line in enumerate(front):
        m = re.match(r"([A-Za-z_-]+)[ \t]*:[ \t]*(.*)$", line)
        if not m or m.group(1).lower() not in _SCOPE_KEYS:
            continue
        value = _yaml_scalar(m.group(2))
        if not value:
            for nxt in front[i + 1:]:
                bullet = re.match(r"[ \t]*-[ \t]+(.+)$", nxt)
                if not bullet:
                    break
                names.append(_yaml_scalar(bullet.group(1)))
        else:
            names = _split_globs(value)
        break
    else:
        for line in lines[start:start + _STATUS_ZONE]:
            if _H2_RE.match(line):
                break
            m = _SCOPE_LINE_RE.match(line)
            if m:
                names = _split_globs(m.group(2))
                break
    names = [n for n in names if n]
    if len(names) > _MAX_SCOPE:
        # Dropping the rest would shrink the scope without a word.
        raise ValueError(f"scope lists {len(names)} globs; at most "
                         f"{_MAX_SCOPE} are read - use fewer, wider globs")
    for n in names:
        if re.search(r"\s", n):
            raise _ProseScope(
                f"the Scope line {n[:60]!r} looks like a sentence, not "
                "globs, so it is ignored and the rules apply everywhere "
                "(write globs without spaces, like 'services/api/**')")
        why = _scope_problem(n)
        if why:
            raise ValueError(f"scope {n!r} {why}")
    return [n.replace("\\", "/") for n in names]


def _split_globs(value: str) -> "list[str]":
    value = value.strip().strip("[]")
    if len(value) > 2000:
        raise ValueError("scope line is longer than 2000 characters")
    return [n.strip().strip("'\"` ") for n in value.split(",")]


def adr_decision(lines: "list[str]", limit: int = 400) -> str:
    """The first words of the `## Decision` (or `Decision Outcome`) section,
    on one line; "" when there is none."""
    lines = _without_fences(lines)
    for i, line in enumerate(lines):
        if _DECISION_HEADING_RE.match(line):
            words: "list[str]" = []
            for nxt in lines[i + 1:]:
                if _H2_RE.match(nxt):
                    break
                text = re.sub(r"^[ \t]*(?:[-*+>]|\d+[.)])[ \t]+", "",
                              nxt.strip())
                if text:
                    words.append(text)
                if sum(len(w) + 1 for w in words) > limit:
                    break
            out = " ".join(words)
            return out if len(out) <= limit else out[:limit - 1].rstrip() + "\u2026"
    return ""


def adr_code_refs(lines: "list[str]") -> "list[tuple[str, int]]":
    """(path, line) of each `backticked/path` an ADR mentions that looks
    like a file or directory of the repository: it has a `/` and no
    spaces, wildcards or URL scheme."""
    out = []
    for n, line in enumerate(_without_fences(lines), 1):
        for m in _BACKTICKED_RE.finditer(line):
            ref = m.group(1).strip()
            if ("/" in ref and not re.search(r"[\s*?<>{}$|=:]", ref)
                    and not ref.startswith(("/", "~", "..", "http"))
                    and ".." not in ref.split("/")):
                out.append((ref.rstrip("/"), n))
                if len(out) >= _MAX_REFS:
                    return out
    return out


def parse_adr(path: str, display: "str | None" = None,
              parse_rules: bool = True) -> dict:
    """Extract number, title, status, and constraint rules from an ADR.

    `display` is how the file is named in messages and rule sources (the
    repo-relative path); `parse_rules=False` reads only the metadata, so
    `index` keeps working while the constraints are being fixed.
    Raises ConfigError (a ValueError) listing every bad constraint line.
    """
    shown = display or path
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    info: dict = {"path": path, "number": "", "title": "",
                  "status": "", "rules": [], "warnings": []}
    m = re.match(r"(\d+)[-_](.+)\.md$", os.path.basename(path))
    if m:
        info["number"] = m.group(1)
        info["title"] = m.group(2).replace("-", " ").replace("_", " ")
    lines = _without_html_comments(_lines(text))
    title, status = adr_title_and_status(lines)
    in_body, in_front = adr_status_sources(lines)
    if in_body and in_front and _status_word(in_body) != _status_word(
            in_front):
        retire = [is_inactive_status(x) for x in (in_body, in_front)]
        if retire[0] != retire[1]:
            # One says retired, one says enforced: enforce (fail closed).
            status = in_body if not retire[0] else in_front
        info["warnings"].append(
            f"{shown}: the front matter says status {in_front!r} but the "
            f"document says {in_body!r}; using {status!r}"
            + (" (the one that keeps the rules enforced)"
               if retire[0] != retire[1] else ""))
    info["title"] = title or info["title"]
    info["status"] = status
    info["owners"] = adr_owners(lines)
    scope: "list[str]" = []
    try:
        scope = adr_scope(lines)
    except _ProseScope as e:
        info["warnings"].append(f"{shown}: {e}")
    except ValueError as e:
        if parse_rules:
            raise ConfigError([f"{shown}: {e}"])
    info["scope"] = scope
    info["decision"] = adr_decision(lines)
    info["code_refs"] = adr_code_refs(lines)
    if not parse_rules:
        return info
    problems: "list[str]" = []
    in_section = False
    for number, raw in enumerate(lines, 1):
        if CONSTRAINTS_RE.match(raw):
            in_section = True
            continue
        h = _HEADING_RE.match(raw)
        if (h and 2 <= len(h.group(1)) <= 4
                and re.match(r"(?i)\W*constraints?\b", h.group(2))):
            info["warnings"].append(
                f"{_where(shown, number)}: heading {raw.strip()!r} is not "
                "'## Constraints', so rules below it are NOT read")
        if in_section and _H2_RE.match(raw):
            in_section = False
        if not in_section:
            continue
        line, listed = rules_mod.clean_rule_text(raw)
        try:
            rule = rules_mod.parse_rule_line(line, source=shown,
                                             line_no=number)
        except ValueError as e:
            problems.append(f"{_where(shown, number)}: {e}")
            continue
        if rule is not None:
            rule.scope = list(scope)
            info["rules"].append(rule)
            continue
        hint = rules_mod.rule_like_problem(line, listed)
        if hint:
            problems.append(
                f"{_where(shown, number)}: {raw.strip()!r} is not a valid "
                f"rule: {hint}")
    if problems:
        raise ConfigError(problems)
    return info


def load_all(root: str, adr_dir=None) -> Loaded:
    """Everything in one pass; ConfigError lists the problems of ALL files.

    `adr_dir` is None (look in the usual places), one directory or a list of
    them; see find_adrs()."""
    problems: "list[str]" = []
    waivers: "list[Waiver]" = []
    layers, arch_rules = load_arch_yaml(root, problems, waivers)
    adrs: "list[dict]" = []
    search = find_adrs(root, adr_dir)
    warnings: "list[str]" = list(search.warnings)
    for p in search.files:
        shown = paths_mod.rel_posix(p, root)
        try:
            adrs.append(parse_adr(p, display=shown))
        except ConfigError as e:
            problems.extend(e.problems)
        except OSError as e:
            problems.append(f"{shown}: cannot read ({e})")
    if problems:
        raise ConfigError(problems)
    all_rules = list(arch_rules)
    inactive: "list[tuple[str, str, int]]" = []
    for a in adrs:
        shown = paths_mod.rel_posix(a["path"], root)
        if is_inactive_status(a["status"]) and a["rules"]:
            problem = _supersede_problem(a, adrs, shown)
            if problem:
                problems.append(problem)
    if problems:
        raise ConfigError(problems)
    for a in adrs:
        warnings.extend(a["warnings"])
        if is_inactive_status(a["status"]):
            # A superseded or rejected decision must not keep failing CI;
            # its rules were still parsed, so a broken one is still an error.
            inactive.append((paths_mod.rel_posix(a["path"], root), a["status"], len(a["rules"])))
            continue
        all_rules.extend(a["rules"])
    known = {r.describe() for r in all_rules}
    for w in waivers:
        if w.rule not in known:
            near = rules_mod.did_you_mean(w.rule, sorted(known))
            problems.append(
                f"{_where(ARCH_FILE, w.line)}: waiver names the rule "
                f"{w.rule!r}, which no enforced rule says{near}; write it "
                "exactly as `adr-drift check` prints it")
    if problems:
        raise ConfigError(problems)
    return Loaded(layers, all_rules, adrs, warnings, inactive, waivers)


def load_all_rules(root: str, adr_dir=None
                   ) -> "tuple[list[dict], list[rules_mod.Rule], list[dict]]":
    loaded = load_all(root, adr_dir)
    return loaded.layers, loaded.rules, loaded.adrs
