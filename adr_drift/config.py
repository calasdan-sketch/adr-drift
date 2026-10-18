"""Config + ADR loading: arch.yaml and docs/adr/*.md Constraints sections.

Loading is strict on purpose. A rule that is silently dropped leaves the
repo looking enforced when it is not, so anything that looks like a rule
but does not parse is an error with a file:line, and unknown keys (a typo
such as `path:` for `paths:`) are errors with a did-you-mean.
"""

from __future__ import annotations

import dataclasses
import os
import re

from . import miniyaml
from . import rules as rules_mod

DEFAULT_ADR_DIR = "docs/adr"
ARCH_FILE = "arch.yaml"

CONSTRAINTS_RE = re.compile(
    r"^##\s+Constraints\s*$", re.IGNORECASE | re.MULTILINE)

_H2_RE = re.compile(r"^##\s")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

ARCH_KEYS = ("layers", "rules")
LAYER_KEYS = ("name", "paths")


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


def _lines(text: str) -> "list[str]":
    """Split on \\n, \\r\\n and \\r only, so line numbers match an editor's
    (str.splitlines() also splits on form feeds and U+2028)."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _without_html_comments(lines: "list[str]") -> "list[str]":
    """The lines with every <!-- ... --> blanked out (a commented-out rule
    is an example, not a rule); line numbers are preserved."""
    text = _HTML_COMMENT_RE.sub(
        lambda m: "\n" * m.group(0).count("\n"), "\n".join(lines))
    return text.split("\n")


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
        if key in ARCH_KEYS or str(key).startswith("x-"):
            continue
        line = _find_line(lines, "^" + re.escape(str(key)) + r"\s*:")
        problems.append(
            f"{_where(ARCH_FILE, line)}: unknown key {key!r}"
            f"{rules_mod.did_you_mean(key, ARCH_KEYS)} "
            f"(allowed: {', '.join(ARCH_KEYS)}; prefix a note key with "
            "'x-' to keep it)")
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
            if key not in LAYER_KEYS:
                line = _find_line(lines, r'^\s*-?\s*' + re.escape(str(key))
                                  + r'\s*:')
                problems.append(
                    f"{_where(ARCH_FILE, line)}: layer {label!r}: unknown key "
                    f"{key!r}{rules_mod.did_you_mean(key, LAYER_KEYS)} "
                    f"(allowed: {', '.join(LAYER_KEYS)})")
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


def adr_files(root: str, adr_dir: str = DEFAULT_ADR_DIR) -> "list[str]":
    d = os.path.join(root, adr_dir)
    if not os.path.isdir(d):
        return []
    return sorted(
        os.path.join(d, f) for f in os.listdir(d)
        if f.endswith(".md") and f != "README.md")


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
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        info["title"] = m.group(1).strip()
    m = re.search(r"^##\s+Status\s*$\n(.+?)(?=^##\s|\Z)",
                  text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    if m:
        info["status"] = m.group(1).strip().splitlines()[0].strip()
    if not parse_rules:
        return info
    problems: "list[str]" = []
    in_section = False
    for number, raw in enumerate(_without_html_comments(_lines(text)), 1):
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


def load_all(root: str, adr_dir: str = DEFAULT_ADR_DIR) -> Loaded:
    """Everything in one pass; ConfigError lists the problems of ALL files."""
    problems: "list[str]" = []
    layers, arch_rules = load_arch_yaml(root, problems)
    adrs: "list[dict]" = []
    warnings: "list[str]" = []
    for p in adr_files(root, adr_dir):
        shown = os.path.relpath(p, root).replace(os.sep, "/")
        try:
            adrs.append(parse_adr(p, display=shown))
        except ConfigError as e:
            problems.extend(e.problems)
        except OSError as e:
            problems.append(f"{shown}: cannot read ({e})")
    if problems:
        raise ConfigError(problems)
    all_rules = list(arch_rules)
    for a in adrs:
        all_rules.extend(a["rules"])
        warnings.extend(a["warnings"])
    return Loaded(layers, all_rules, adrs, warnings)


def load_all_rules(root: str, adr_dir: str = DEFAULT_ADR_DIR
                   ) -> "tuple[list[dict], list[rules_mod.Rule], list[dict]]":
    loaded = load_all(root, adr_dir)
    return loaded.layers, loaded.rules, loaded.adrs
