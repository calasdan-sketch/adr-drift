"""Engine: evaluate rules against a scanned repo -> violations."""

from __future__ import annotations

import dataclasses
import os
import re

from . import rules as rules_mod
from . import scanner as scanner_mod


@dataclasses.dataclass
class Violation:
    rule: rules_mod.Rule
    file: str          # repo-relative path
    line: int          # 0 when the whole file is the subject
    detail: str

    def as_dict(self) -> dict:
        return {
            "rule": self.rule.describe(),
            "rule_source": (f"{self.rule.source}:{self.rule.line_no}"
                            if self.rule.line_no else self.rule.source),
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
        }


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _file_layer(layers: list[dict], rel_path: str) -> str:
    for layer in layers:
        for pat in layer.get("paths", []):
            if rules_mod.match_glob(pat, rel_path):
                return layer.get("name", "")
    return ""


def _layer_path_prefixes(layer: dict) -> list[str]:
    """Dotted module prefixes implied by a layer's path globs.

    Only the literal leading components count (up to the first
    wildcard): "src/db/**" -> ["src.db"], "handlers/**" -> ["handlers"].
    """
    prefixes: list[str] = []
    for pat in layer.get("paths", []):
        lit: list[str] = []
        for part in pat.replace("\\", "/").strip("/").split("/"):
            if any(ch in part for ch in "*?["):
                break
            lit.append(part)
        if lit:
            prefixes.append(".".join(lit))
    return prefixes


def _import_identities(mod: str, layers: list[dict]) -> list[str]:
    """Every name an imported module can be known by.

    Besides the literal module ("src.db.drivers") this includes the
    layer name(s) whose paths contain it ("db") and the module with the
    layer's top-level source dir stripped ("db.drivers"). Without this,
    src/-layout repos where code is imported as "src.db.x" silently
    dodge every to-side pattern that names the layer.
    """
    ids = [mod]
    for layer in layers:
        name = layer.get("name", "")
        for prefix in _layer_path_prefixes(layer):
            if not rules_mod.match_pat(prefix, mod):
                continue
            if name and name not in ids:
                ids.append(name)
            parts = prefix.split(".")
            if len(parts) >= 2 and mod.startswith(parts[0] + "."):
                stripped = mod[len(parts[0]) + 1:]
                if stripped and stripped not in ids:
                    ids.append(stripped)
    return ids


def check(root: str, layers: list[dict], all_rules: list[rules_mod.Rule],
          strict_py_only: bool = False) -> list[Violation]:
    entries: list[tuple[str, str, list[tuple[str, int]], str]] = []
    for path in scanner_mod.iter_source_files(root):
        rel = _rel(root, path)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        imports, _engine = scanner_mod.extract_imports(
            rel, content, strict_py_only=strict_py_only)
        entries.append((rel, _file_layer(layers, rel), imports, content))
    return evaluate(entries, layers, all_rules)


def evaluate(entries: list[tuple[str, str, list[tuple[str, int]], str]],
             layers: list[dict],
             all_rules: list[rules_mod.Rule]) -> list[Violation]:
    """Evaluate rules against pre-scanned entries.

    Each entry: (rel_path, layer, imports[(module, line)], content).
    Lets `drift` check historical git content through the same engine.
    """
    violations: list[Violation] = []
    for rule in all_rules:
        if rule.kind == "forbid-import":
            for rel, layer, imports, _content in entries:
                # The "from" side matches the file's layer name OR a module
                # prefix of the file path (so rules work without layers).
                from_hit = (layer and rules_mod.match_pat(rule.from_pat, layer))
                mod_prefix = rel.replace("/", ".").rsplit(".", 1)[0]
                if not from_hit:
                    from_hit = rules_mod.match_pat(rule.from_pat, mod_prefix)
                if not from_hit:
                    continue
                hit_lines: set[int] = set()
                for mod, lineno in imports:
                    # "from m import n" yields m and m.n: report a line once.
                    if lineno in hit_lines:
                        continue
                    if any(rules_mod.match_pat(rule.to_pat, ident)
                           for ident in _import_identities(mod, layers)):
                        hit_lines.add(lineno)
                        violations.append(Violation(
                            rule=rule, file=rel, line=lineno,
                            detail=f"imports {mod}"))
        else:
            rx = re.compile(rule.regex)
            for rel, _layer, _imports, content in entries:
                if not rules_mod.match_glob(rule.glob, rel):
                    continue
                if rule.kind == "forbid-pattern":
                    for i, line in enumerate(content.splitlines(), 1):
                        if rx.search(line):
                            violations.append(Violation(
                                rule=rule, file=rel, line=i,
                                detail=f"matches /{rule.regex}/"))
                else:  # require-pattern
                    if not rx.search(content):
                        violations.append(Violation(
                            rule=rule, file=rel, line=0,
                            detail=f"missing /{rule.regex}/"))
    violations.sort(key=lambda v: (v.file, v.line))
    return violations
