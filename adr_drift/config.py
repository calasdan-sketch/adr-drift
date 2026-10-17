"""Config + ADR loading: arch.yaml and docs/adr/*.md Constraints sections."""

from __future__ import annotations

import os
import re

from . import miniyaml
from . import rules as rules_mod

DEFAULT_ADR_DIR = "docs/adr"
ARCH_FILE = "arch.yaml"

CONSTRAINTS_RE = re.compile(
    r"^##\s+Constraints\s*$", re.IGNORECASE | re.MULTILINE)


def load_arch_yaml(root: str) -> tuple[list[dict], list[rules_mod.Rule]]:
    """Returns (layers, rules). Missing arch.yaml -> ([], [])."""
    path = os.path.join(root, ARCH_FILE)
    if not os.path.isfile(path):
        return [], []
    with open(path, encoding="utf-8") as f:
        data = miniyaml.safe_load(f.read()) or {}
    layers = data.get("layers", []) or []
    out: list[rules_mod.Rule] = []
    for i, raw in enumerate(data.get("rules", []) or []):
        rule = rules_mod.parse_rule_line(str(raw), source=ARCH_FILE,
                                         line_no=i + 1)
        if rule:
            out.append(rule)
    return layers, out


def adr_files(root: str, adr_dir: str = DEFAULT_ADR_DIR) -> list[str]:
    d = os.path.join(root, adr_dir)
    if not os.path.isdir(d):
        return []
    return sorted(
        os.path.join(d, f) for f in os.listdir(d)
        if f.endswith(".md") and f != "README.md")


def parse_adr(path: str) -> dict:
    """Extract number, title, status, and constraint rules from an ADR."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    info: dict = {"path": path, "number": "", "title": "",
                  "status": "", "rules": []}
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
    m = CONSTRAINTS_RE.search(text)
    if m:
        section = text[m.end():]
        section = re.split(r"^##\s", section, maxsplit=1,
                           flags=re.MULTILINE)[0]
        for i, line in enumerate(section.splitlines(), 1):
            line = line.strip()
            if line.startswith(("-", "*")):
                line = line[1:].strip()
            try:
                rule = rules_mod.parse_rule_line(line, source=path,
                                                 line_no=i)
            except ValueError as e:
                raise ValueError(f"{path}: {e}")
            if rule:
                info["rules"].append(rule)
    return info


def load_all_rules(root: str, adr_dir: str = DEFAULT_ADR_DIR
                   ) -> tuple[list[dict], list[rules_mod.Rule], list[dict]]:
    layers, arch_rules = load_arch_yaml(root)
    adrs = [parse_adr(p) for p in adr_files(root, adr_dir)]
    all_rules = list(arch_rules)
    for a in adrs:
        for r in a["rules"]:
            # Report ADR sources repo-relative, not as absolute paths.
            r.source = os.path.relpath(r.source, root).replace(os.sep, "/")
        all_rules.extend(a["rules"])
    return layers, all_rules, adrs
