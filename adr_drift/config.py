"""Config + ADR loading: arch.yaml and the Constraints sections of the ADRs.

Loading is strict on purpose. A rule that is silently dropped leaves the
repo looking enforced when it is not, so anything that looks like a rule
but does not parse is an error with a file:line, and unknown keys (a typo
such as `path:` for `paths:`) are errors with a did-you-mean.
"""

from __future__ import annotations

import dataclasses
import os
import posixpath
import re

from . import miniyaml
from . import rules as rules_mod
from . import scanner as scanner_mod

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

ARCH_KEYS = ("layers", "rules")
LAYER_KEYS = ("name", "paths")
#: A key with this prefix is a note, at the top level and in a layer.
NOTE_KEY_PREFIX = "x-"


class ConfigError(ValueError):
    """The config is wrong. `problems` has one "file:line: what" per issue."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


@dataclasses.dataclass
class Loaded:
    layers: list
    rules: list
    adrs: list
    warnings: list
    notes: list = dataclasses.field(default_factory=list)  # for --verbose


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


def load_arch_yaml(root: str, problems: "list[str] | None" = None
                   ) -> "tuple[list[dict], list[rules_mod.Rule]]":
    """Returns (layers, rules). Missing arch.yaml -> ([], []).

    Raises ConfigError listing every problem, unless `problems` is given:
    then problems are appended to it and whatever did parse is returned.
    """
    path = os.path.join(root, ARCH_FILE)
    if not os.path.lexists(path):  # a link to /dev/zero is not "missing"
        return [], []
    own = problems is None
    problems = [] if own else problems
    try:
        data = scanner_mod.read_regular(path, scanner_mod.MAX_CONFIG_BYTES,
                                        root=root)
        text = data.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        problems.append(f"{ARCH_FILE}: cannot read "
                        f"({getattr(e, 'strerror', None) or e})")
        text = ""
    except MemoryError:
        problems.append(f"{ARCH_FILE}: cannot read (out of memory)")
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
                   "'forbid-pattern: <glob> !~ <regex>' or "
                   "'require-pattern: <glob> =~ <regex>')"))
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
        found, key=lambda p: os.path.relpath(p, d).replace(os.sep, "/"))


def _dir_list(adr_dir) -> "list[str]":
    """--adr-dir as a list: None (search), one name, or several."""
    if adr_dir is None:
        return []
    if isinstance(adr_dir, str):
        return [adr_dir]
    return list(adr_dir)


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


def adr_title_and_status(lines: "list[str]") -> "tuple[str, str]":
    """(title, status) of an ADR given its lines (HTML comments already
    blanked); "" for what is not there.

    The title is the first `# ` heading after any front matter (a `#` line
    inside the front matter is a YAML comment). The status is, in this order,
    the first line of a `## Status` section, the `status:` of the front matter
    (MADR) or a `Status:` line under the title."""
    front, start = _front_matter(lines)
    body = lines[start:]
    title = ""
    for line in body:
        m = _TITLE_RE.match(line)
        if m and m.group(1).strip():
            title = m.group(1).strip()
            break
    status = _status_section(body)
    if not status:
        for line in front:
            m = _FRONT_STATUS_RE.match(line)
            if m:
                status = _yaml_scalar(m.group(1))
                break
    return title, status or _status_line(body)


#: A status that retires the ADR: "Superseded by ADR-0007", "Deprecated".
#: Anchored at the start, so "Accepted (supersedes ADR-0002)" still counts.
_RETIRED_RE = re.compile(r"[\W_]*(superseded|deprecated)\b", re.IGNORECASE)
_TITLE_NUMBER_RE = re.compile(
    r"(?:adr[ -]*)?\d+(?:[ \t]*[:.)]|[ \t]+[-\u2013\u2014])[ \t]*",
    re.IGNORECASE)


def retired_status(status: str) -> str:
    """"superseded" or "deprecated" when the ADR's status says it no longer
    holds, else ""."""
    m = _RETIRED_RE.match(status or "")
    return m.group(1).lower() if m else ""


def _adr_number(path: str, lines: "list[str]") -> "int | None":
    m = re.match(r"(\d+)[-_]", os.path.basename(path))
    if m:
        return int(m.group(1))
    for line in lines:
        h = _TITLE_RE.match(line)
        if h:
            n = re.match(r"(?:adr[ -]*)?(\d+)\b", h.group(1), re.IGNORECASE)
            return int(n.group(1)) if n else None
    return None


def parse_adr(path: str, display: "str | None" = None,
              parse_rules: bool = True, root: "str | None" = None) -> dict:
    """Extract number, title, status, and constraint rules from an ADR.

    `display` is how the file is named in messages and rule sources (the
    repo-relative path); `parse_rules=False` reads only the metadata, so
    `index` keeps working while the constraints are being fixed.
    Raises ConfigError (a ValueError) listing every bad constraint line.
    """
    shown = display or path
    # Only a regular file of a sane size is read: an ADR directory can hold
    # a link to /dev/zero. RefusedFile is an OSError, MemoryError becomes one.
    try:
        data = scanner_mod.read_regular(path, scanner_mod.MAX_CONFIG_BYTES,
                                        root=root)
    except MemoryError:
        raise scanner_mod.RefusedFile("out of memory") from None
    text = data.decode("utf-8-sig", "replace")
    info: dict = {"path": path, "number": "", "title": "",
                  "status": "", "rules": [], "warnings": []}
    m = re.match(r"(\d+)[-_](.+)\.md$", os.path.basename(path))
    if m:
        info["number"] = m.group(1)
        info["title"] = m.group(2).replace("-", " ").replace("_", " ")
    lines = _without_html_comments(_lines(text))
    title, status = adr_title_and_status(lines)
    info["title"] = title or info["title"]
    info["status"] = status
    info["retired"] = retired_status(status)
    clean = _TITLE_NUMBER_RE.match(info["title"])
    info["adr"] = {"number": _adr_number(path, lines),
                   "title": (info["title"][clean.end():] if clean
                             else info["title"]) or info["title"],
                   "path": shown.replace(os.sep, "/")}
    if not parse_rules:
        return info
    if info["retired"]:
        # A superseded or deprecated decision no longer constrains anyone:
        # its Constraints are not read (so a stale line cannot break a run).
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
            rule.adr = info["adr"]
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
    layers, arch_rules = load_arch_yaml(root, problems)
    adrs: "list[dict]" = []
    search = find_adrs(root, adr_dir)
    warnings: "list[str]" = list(search.warnings)
    for p in search.files:
        shown = os.path.relpath(p, root).replace(os.sep, "/")
        try:
            adrs.append(parse_adr(p, display=shown, root=root))
        except ConfigError as e:
            problems.extend(e.problems)
        except OSError as e:
            problems.append(f"{shown}: cannot read "
                            f"({e.strerror or e})")
    if problems:
        raise ConfigError(problems)
    all_rules = list(arch_rules)
    notes: "list[str]" = []
    for a in adrs:
        all_rules.extend(a["rules"])
        warnings.extend(a["warnings"])
        if a["retired"]:
            shown = os.path.relpath(a["path"], root).replace(os.sep, "/")
            notes.append(
                f"{shown}: status '{a['status']}': this ADR is {a['retired']}"
                ", so its Constraints are not enforced")
    return Loaded(layers, all_rules, adrs, warnings, notes)


def load_all_rules(root: str, adr_dir=None
                   ) -> "tuple[list[dict], list[rules_mod.Rule], list[dict]]":
    loaded = load_all(root, adr_dir)
    return loaded.layers, loaded.rules, loaded.adrs
