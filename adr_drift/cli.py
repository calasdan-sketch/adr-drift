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
import contextlib
import errno
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

# Report glyphs and their ASCII stand-ins. A Windows console or pipe often
# runs cp1252, which cannot encode the check marks: printing them there
# raised UnicodeEncodeError and killed the run.
_ASCII_SYMBOLS = {"✓": "[ok]", "✗": "[x]", "—": "--"}


def sym(s: str) -> str:
    """`s` if the current stdout can encode it, else an ASCII stand-in."""
    enc = getattr(sys.stdout, "encoding", None)
    if not enc:
        return s  # e.g. io.StringIO: takes any str
    try:
        s.encode(enc)
    except (UnicodeEncodeError, LookupError):
        return _ASCII_SYMBOLS.get(
            s, s.encode("ascii", "replace").decode("ascii"))
    return s


def _harden_streams() -> None:
    """Never crash on a console that cannot encode a path or ADR title.

    Python's stderr already replaces unencodable characters; make stdout
    do the same instead of raising UnicodeEncodeError mid-report.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        # Both handlers raise UnicodeEncodeError on a character the code
        # page lacks: "strict" (the default) and "surrogateescape" (what a
        # piped stdout reports on Windows 11 / Python 3.11, with cp1252).
        if reconfigure is None or getattr(stream, "errors", None) not in (
                "strict", "surrogateescape"):
            continue
        try:
            reconfigure(errors="replace")
        except (OSError, ValueError):
            pass


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

Machine-checkable rules ADR Drift enforces in CI. Examples only — they
are commented out; delete the leading "#" to enforce one:

    # - forbid-import: handlers -> db.drivers
    # - forbid-pattern: src/**.py !~ \\beval\\(
    # - require-pattern: src/handlers/**.py =~ authenticate\\(

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
    with open(arch_path, "w", encoding="utf-8") as f:
        f.write(ARCH_TEMPLATE.format(
            layers=("layers:\n" + layer_yaml.rstrip("\n")) if layer_yaml
            else "layers: []"))
    adr_dir = os.path.join(root, args.adr_dir)
    os.makedirs(adr_dir, exist_ok=True)
    sample = os.path.join(adr_dir, "0001-record-architecture-decisions.md")
    if not os.path.exists(sample):
        with open(sample, "w", encoding="utf-8") as f:
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
        print(f"{sym('✓')} no architecture drift {sym('—')} all ADRs hold")
        return
    print(f"{sym('✗')} {len(violations)} architecture violation(s):\n")
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
        print(f"no rules found {sym('—')} run `adr-drift init` or add a "
              "## Constraints section to an ADR.")
        # Fresh `init` (arch.yaml present, rules still commented) is a
        # configured repo with nothing to enforce yet; no config at all is not.
        configured = os.path.isfile(os.path.join(root, config_mod.ARCH_FILE))
        return 0 if args.fail_on == "none" or configured else 2
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
        print(f"no rules found {sym('—')} nothing to drift from.")
        return 0

    changed = [p for p in githelp.changed_files(root, args.since)
               if os.path.splitext(p)[1].lower()
               in (scanner_mod.PY_EXTS | scanner_mod.GENERIC_EXTS)]
    if not changed:
        print(f"no source files changed since {args.since} "
              f"{sym('—')} no drift")
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
            print(f"{sym('✓')} no new drift since {args.since} "
                  f"({len(changed)} file(s) changed)")
        else:
            print(f"{sym('✗')} {len(new_violations)} NEW violation(s) "
                  f"since {args.since}:\n")
            for v in new_violations:
                commit = githelp.blame_commit_for_line(
                    root, v.file, v.line or 1)
                tag = f" [{commit}]" if commit else ""
                print(f"  {v.file}:{v.line or sym('—')}{tag}: {v.detail}")
                print(f"      rule: {v.rule.describe()} ({v.rule.source})")
        if fixed_violations:
            print(f"\n{sym('✓')} {len(fixed_violations)} fixed since "
                  f"{args.since}:\n")
            for v in fixed_violations:
                print(f"  {v.file}:{v.line or sym('—')}: {v.detail} (fixed)")
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
    with open(path, "w", encoding="utf-8") as f:
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
    with open(os.path.join(adr_dir, "README.md"), "w", encoding="utf-8") as f:
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


# Windows does not report a pipe nobody reads as BrokenPipeError: print() of
# a small output works there (it only fills the buffer) and the flush then
# fails with a plain OSError(EINVAL) - at interpreter exit, as "Exception
# ignored", with exit status 120 - or, under `python -u`, out of print().
# A module-level flag, so tests can switch the rule on (and off) anywhere.
_IS_WINDOWS = os.name == "nt"


def _is_closed_pipe(e: BaseException) -> bool:
    """True when `e` means nobody is reading this pipe any more: a
    BrokenPipeError or any OSError with errno EPIPE, and, on Windows only,
    plain EINVAL. Anywhere else EINVAL is a real error and is not
    swallowed."""
    if not isinstance(e, OSError):
        return False
    return (isinstance(e, BrokenPipeError) or e.errno == errno.EPIPE
            or (_IS_WINDOWS and e.errno == errno.EINVAL))


def _discard_output(stream) -> None:
    """Point `stream`'s file descriptor at the null device, so what is
    still buffered (and the interpreter's own flush at exit) goes nowhere
    instead of failing again. A stream with no descriptor needs nothing."""
    try:
        with open(os.devnull, "wb") as null:
            os.dup2(null.fileno(), stream.fileno())
    except (OSError, ValueError, AttributeError):
        pass


class _PipeSafe:
    """Stand-in for sys.stdout / sys.stderr while main() runs.

    The reader of a pipe can quit at any moment (`adr-drift check
    | head -1`). Left alone, Python raises BrokenPipeError out of print()
    or, when the output was still buffered, prints "Exception ignored" at
    exit and replaces the exit code with 120 (see _IS_WINDOWS for how
    Windows words that failure). Here the first failed write or flush
    points the stream at the null device and the command carries on into
    it: a closed pipe acts like `> /dev/null`, the output stops and the
    exit code is whatever the command computed.

    That is the choice against the usual `except BrokenPipeError:
    sys.exit(1)` recipe: for `run` the exit code IS the verdict (0 pass,
    1 a case failed, 2 an error), and a shortened pipe must never turn a
    regression into a pass, or into some other number.
    """

    def __init__(self, stream):
        self._stream = stream
        self._dead = False

    def _call(self, name, *args):
        if self._dead:
            return
        try:
            getattr(self._stream, name)(*args)
        except OSError as e:
            if not _is_closed_pipe(e):
                raise
            self._dead = True
            _discard_output(self._stream)

    def write(self, text):
        self._call("write", text)
        return len(text)

    def flush(self):
        self._call("flush")

    def __getattr__(self, name):  # encoding, isatty(), reconfigure() ...
        return getattr(self._stream, name)


@contextlib.contextmanager
def _closed_pipe_tolerant():
    """Run the body with stdout and stderr wrapped in _PipeSafe."""
    real = (sys.stdout, sys.stderr)  # either is None under pythonw
    safe = tuple(_PipeSafe(s) if s is not None else None for s in real)
    sys.stdout, sys.stderr = safe
    try:
        yield
    finally:
        try:
            # Flush now, with the command done, while a closed pipe is
            # still handled. A small output only ever sits in the buffer,
            # so nothing has failed yet; left to the interpreter's flush
            # at exit, the error would be "Exception ignored" and the exit
            # code 120 instead of the command's own.
            for s in safe:
                if s is not None:
                    s.flush()
        finally:
            sys.stdout, sys.stderr = real


def main(argv=None) -> int:
    _harden_streams()
    # `adr-drift check | head -1` must neither traceback nor change the exit
    # code (check's exit code is the verdict).
    with _closed_pipe_tolerant():
        return _main(argv)


def _main(argv) -> int:
    args = build_parser().parse_args(argv)
    return {"init": cmd_init, "check": cmd_check, "baseline": cmd_baseline,
            "drift": cmd_drift, "new": cmd_new,
            "index": cmd_index}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
