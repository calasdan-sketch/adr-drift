"""ADR Drift: deterministic architecture tests for any language.

Commands:
  init      scaffold arch.yaml + a sample ADR from the repo's folder layout
  check     verify the repo against arch.yaml + ADR Constraints (CI-friendly)
  baseline  record current violations so check --baseline can grandfather them
  drift     report NEW violations introduced since a git ref
  new       create the next-numbered ADR from a template
  index     regenerate the one-sentence-per-ADR index (docs/adr/README.md)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from . import config as config_mod
from . import engine as engine_mod
from . import githelp
from . import rules as rules_mod
from . import scanner as scanner_mod

VERSION = "0.1.0"

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".tox", "vendor", "docs"}


def _infer_layers(root: str) -> list[dict]:
    base = os.path.join(root, "src") if os.path.isdir(
        os.path.join(root, "src")) else root
    names = sorted(d for d in os.listdir(base)
                   if os.path.isdir(os.path.join(base, d))
                   and d not in SKIP_DIRS and not d.startswith(".")
                   and d != "tests")
    prefix = "src/" if base != root else ""
    return [{"name": n, "paths": [f"{prefix}{n}/**"]} for n in names]


ARCH_TEMPLATE = """# ADR Drift architecture config.
# Layers are matched top-down; the first matching layer wins.
# Rules use the constraint DSL (see README):
#   forbid-import: <from> -> <to>      layer/module <from> may not import <to>
#   forbid-pattern: <glob> !~ <regex>  files matching <glob> must not contain <regex>
#   require-pattern: <glob> =~ <regex> files matching <glob> must contain <regex>
layers:
{layers}
rules:
  # Tighten me: uncomment and adapt.
  # - "forbid-import: handlers -> db.drivers"
"""

ADR_TEMPLATE = """# {num}: {title}

## Status

Proposed

## Context

Why this decision is being made.

## Decision

What we decided.

## Constraints

Machine-checkable rules ADR Drift enforces in CI. Examples:

- forbid-import: handlers -> db.drivers
- forbid-pattern: src/**.py !~ \\beval\\(
- require-pattern: src/handlers/**.py =~ authenticate\\(

## Consequences

What follows from this decision.
"""


def cmd_init(args) -> int:
    root = args.root
    arch_path = os.path.join(root, config_mod.ARCH_FILE)
    if os.path.exists(arch_path) and not args.force:
        print(f"{config_mod.ARCH_FILE} already exists (use --force to overwrite).")
        return 2
    layers = _infer_layers(root)
    layer_yaml = "".join(
        f"  - name: {l['name']}\n    paths: {[p for p in l['paths']]}".replace(
            "'", '"') + "\n" for l in layers)
    with open(arch_path, "w") as f:
        f.write(ARCH_TEMPLATE.format(layers=layer_yaml or "  []\n"))
    adr_dir = os.path.join(root, args.adr_dir)
    os.makedirs(adr_dir, exist_ok=True)
    sample = os.path.join(adr_dir, "0001-record-architecture-decisions.md")
    if not os.path.exists(sample):
        with open(sample, "w") as f:
            f.write(ADR_TEMPLATE.format(num=1,
                                        title="Record architecture decisions"))
    cmd_index(args)
    print(f"wrote {config_mod.ARCH_FILE} "
          f"({len(layers)} layers: {', '.join(l['name'] for l in layers)})")
    print(f"sample ADR: {os.path.relpath(sample, root)}")
    print("Next: tighten the rules in arch.yaml, then run `adr-drift check`.")
    return 0


def _load(root, adr_dir):
    return config_mod.load_all_rules(root, adr_dir)


def _print_violations(violations, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps([v.as_dict() for v in violations], indent=2))
        return
    if not violations:
        print("✓ no architecture drift — all ADRs hold")
        return
    print(f"✗ {len(violations)} architecture violation(s):\n")
    by_file: dict[str, list] = {}
    for v in violations:
        by_file.setdefault(v.file, []).append(v)
    for path in sorted(by_file):
        print(f"  {path}")
        for v in by_file[path]:
            where = f":{v.line}" if v.line else ""
            print(f"    {path}{where}: {v.detail}")
            print(f"      rule: {v.rule.describe()} ({v.rule.source})")


def cmd_check(args) -> int:
    root = args.root
    try:
        layers, all_rules, adrs = _load(root, args.adr_dir)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not all_rules:
        print("no rules found — run `adr-drift init` or add a ## Constraints "
              "section to an ADR.")
        return 0 if args.fail_on == "none" else 2
    violations = engine_mod.check(root, layers, all_rules,
                                  strict_py_only=args.strict_py_only)
    if args.baseline:
        keys = _load_baseline(args.baseline, root)
        if keys is None:
            return 2
        violations = [v for v in violations
                      if (v.file, v.line, v.rule.describe()) not in keys]
    _print_violations(violations, args.format)
    if violations and args.fail_on != "none":
        return 1
    return 0


def _load_baseline(baseline: str, root: str) -> set | None:
    """Violation identity triples from a baseline file.

    Returns None (after printing an error) when the file can't be read:
    failing closed beats silently dropping the grandfather list.
    """
    path = baseline if os.path.isabs(baseline) else os.path.join(root, baseline)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"error: cannot read baseline {path}: {e}", file=sys.stderr)
        return None
    keys = set()
    for item in data:
        keys.add((item.get("file"), item.get("line"), item.get("rule")))
    return keys


def cmd_baseline(args) -> int:
    root = args.root
    try:
        layers, all_rules, _adrs = _load(root, args.adr_dir)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    violations = engine_mod.check(root, layers, all_rules,
                                  strict_py_only=args.strict_py_only)
    out = (args.output if os.path.isabs(args.output)
           else os.path.join(root, args.output))
    with open(out, "w", encoding="utf-8") as f:
        json.dump([v.as_dict() for v in violations], f, indent=2)
        f.write("\n")
    rel = os.path.relpath(out, root)
    print(f"wrote {len(violations)} baselined violation(s) -> {rel}")
    print(f"run `adr-drift check --baseline {rel}` to grandfather them.")
    return 0


def cmd_drift(args) -> int:
    root = args.root
    if not githelp.is_repo(root):
        print("error: not a git repo", file=sys.stderr)
        return 2
    try:
        layers, all_rules, _adrs = _load(root, args.adr_dir)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not all_rules:
        print("no rules found — nothing to drift from.")
        return 0

    changed = [p for p in githelp.changed_files(root, args.since)
               if os.path.splitext(p)[1].lower()
               in (scanner_mod.PY_EXTS | scanner_mod.GENERIC_EXTS)]
    if not changed:
        print(f"no source files changed since {args.since} — no drift")
        return 0

    def entries_for(get_content) -> list:
        entries = []
        for rel in changed:
            content = get_content(rel)
            if content is None:
                continue
            imports, _eng = scanner_mod.extract_imports(rel, content)
            layer = engine_mod._file_layer(layers, rel)
            entries.append((rel, layer, imports, content))
        return entries

    now_entries = entries_for(
        lambda rel: open(os.path.join(root, rel), encoding="utf-8",
                         errors="replace").read()
        if os.path.isfile(os.path.join(root, rel)) else None)
    base_entries = entries_for(
        lambda rel: githelp.file_at_ref(root, args.since, rel))

    now_v = {(v.file, v.line, v.rule.describe())
             for v in engine_mod.evaluate(now_entries, layers, all_rules)}
    base_v = {(v.file, v.line, v.rule.describe())
              for v in engine_mod.evaluate(base_entries, layers, all_rules)}
    new_keys = now_v - base_v
    fixed_keys = base_v - now_v
    new_violations = [v for v in engine_mod.evaluate(
        now_entries, layers, all_rules)
        if (v.file, v.line, v.rule.describe()) in new_keys]
    fixed_violations = [v for v in engine_mod.evaluate(
        base_entries, layers, all_rules)
        if (v.file, v.line, v.rule.describe()) in fixed_keys]

    if args.format == "json":
        # Stable contract: the JSON list is the NEW violations, as before.
        # Fixed violations are reported in the human output.
        print(json.dumps([v.as_dict() for v in new_violations], indent=2))
    else:
        if not new_violations:
            print(f"✓ no new drift since {args.since} "
                  f"({len(changed)} file(s) changed)")
        else:
            print(f"✗ {len(new_violations)} NEW violation(s) "
                  f"since {args.since}:\n")
            for v in new_violations:
                commit = githelp.blame_commit_for_line(
                    root, v.file, v.line or 1)
                tag = f" [{commit}]" if commit else ""
                print(f"  {v.file}:{v.line or '—'}{tag}: {v.detail}")
                print(f"      rule: {v.rule.describe()} ({v.rule.source})")
        if fixed_violations:
            print(f"\n✓ {len(fixed_violations)} fixed since "
                  f"{args.since}:\n")
            for v in fixed_violations:
                print(f"  {v.file}:{v.line or '—'}: {v.detail} (fixed)")
                print(f"      rule: {v.rule.describe()} ({v.rule.source})")
    if new_violations and args.fail_on != "none":
        return 1
    return 0


def cmd_new(args) -> int:
    adr_dir = os.path.join(args.root, args.adr_dir)
    os.makedirs(adr_dir, exist_ok=True)
    nums = []
    for f in os.listdir(adr_dir):
        m = re.match(r"(\d+)[-_]", f)
        if m:
            nums.append(int(m.group(1)))
    n = max(nums, default=0) + 1
    slug = re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")
    path = os.path.join(adr_dir, f"{n:04d}-{slug}.md")
    with open(path, "w") as f:
        f.write(ADR_TEMPLATE.format(num=n, title=args.title))
    print(f"created {os.path.relpath(path, args.root)}")
    return 0


def cmd_index(args) -> int:
    adr_dir = os.path.join(args.root, args.adr_dir)
    adrs = [config_mod.parse_adr(p)
            for p in config_mod.adr_files(args.root, args.adr_dir)]
    lines = ["# Architecture Decision Records\n",
             "| # | Title | Status |",
             "|---|-------|--------|"]
    for a in adrs:
        rel = os.path.relpath(a["path"], adr_dir).replace(os.sep, "/")
        lines.append(f"| {a['number']} | [{a['title']}]({rel}) "
                     f"| {a['status'] or '—'} |")
    lines.append("")
    with open(os.path.join(adr_dir, "README.md"), "w") as f:
        f.write("\n".join(lines))
    print(f"indexed {len(adrs)} ADR(s) -> "
          f"{os.path.join(args.adr_dir, 'README.md')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="adr-drift",
        description="Deterministic architecture tests: keep ADRs mechanically true.")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    p.add_argument("--root", default=os.getcwd(),
                   help="Repo root (default: cwd).")
    p.add_argument("--adr-dir", default=config_mod.DEFAULT_ADR_DIR,
                   help="ADR directory (default: docs/adr).")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="Scaffold arch.yaml + sample ADR.")
    i.add_argument("--force", action="store_true")

    c = sub.add_parser("check", help="Verify the repo against all rules.")
    c.add_argument("--format", choices=["human", "json"], default="human")
    c.add_argument("--fail-on", choices=["violations", "none"],
                   default="violations")
    c.add_argument("--strict-py-only", action="store_true",
                   help="Skip the regex fallback for non-Python files.")
    c.add_argument("--baseline", default=None, metavar="FILE",
                   help="Grandfather violations listed in FILE "
                        "(written by `adr-drift baseline`).")

    b = sub.add_parser("baseline",
                       help="Record current violations as a grandfather file.")
    b.add_argument("--output", default=".adr-baseline.json", metavar="FILE",
                   help="Where to write the baseline "
                        "(default: .adr-baseline.json under --root).")
    b.add_argument("--strict-py-only", action="store_true",
                   help="Skip the regex fallback for non-Python files.")

    d = sub.add_parser("drift", help="New violations since a git ref.")
    d.add_argument("--since", default="HEAD~5",
                   help="Git ref to compare against (default: HEAD~5).")
    d.add_argument("--format", choices=["human", "json"], default="human")
    d.add_argument("--fail-on", choices=["violations", "none"],
                   default="violations")

    n = sub.add_parser("new", help="Create the next-numbered ADR.")
    n.add_argument("title")

    sub.add_parser("index", help="Regenerate the ADR index README.")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return {"init": cmd_init, "check": cmd_check, "baseline": cmd_baseline,
            "drift": cmd_drift, "new": cmd_new,
            "index": cmd_index}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
