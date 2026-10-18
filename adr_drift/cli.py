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
import dataclasses
import errno
import json
import os
import re
import sys

from . import __version__
from . import config as config_mod
from . import diag as diag_mod
from . import engine as engine_mod
from . import githelp
from . import langs as langs_mod
from . import rules as rules_mod
from . import scanner as scanner_mod

VERSION = __version__

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
    print("Next: enable at least one rule in arch.yaml, then run "
          "`adr-drift check`.")
    print("(With no rules `check` exits 2, because nothing is enforced; "
          "pass --allow-empty to accept that while you adopt gradually.)")
    return 0


def _load_or_report(root: str, adr_dir: str):
    """config_mod.load_all(), or None after printing every problem found
    (one `error: file:line: what` per line on stderr)."""
    try:
        return config_mod.load_all(root, adr_dir)
    except config_mod.ConfigError as e:
        for problem in e.problems:
            print(f"error: {problem}", file=sys.stderr)
        n = len(e.problems)
        print(f"error: {n} problem{'' if n == 1 else 's'} in the "
              "configuration; nothing was checked", file=sys.stderr)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
    return None


def _no_rules_message() -> str:
    return (f"no rules found {sym('—')} run `adr-drift init` or add a "
            "## Constraints section to an ADR.")


def _nothing_to_enforce(args) -> int:
    """No rules at all. A green result would be a lie, so this is exit 2
    unless the caller opted in (--allow-empty) or asked for a report-only
    run (--fail-on none). JSON mode keeps stdout a valid array either way."""
    allowed = (getattr(args, "allow_empty", False)
               or getattr(args, "fail_on", "violations") == "none")
    message = _no_rules_message()
    if not allowed:
        print(message, file=sys.stderr)
        print("error: nothing is enforced, so a passing check would mean "
              "nothing; add a rule, or pass --allow-empty while you adopt "
              "adr-drift gradually.", file=sys.stderr)
        return 2
    if getattr(args, "format", "human") == "json":
        print("[]")
        print(message, file=sys.stderr)
    else:
        print(message)
    return 0


def _verdict(args, violations, diag) -> int:
    """The exit code: 1 for violations, 2 for warnings under --strict."""
    if getattr(args, "fail_on", "violations") == "none":
        return 0
    if violations:
        return 1
    if getattr(args, "strict", False) and diag.warnings:
        n = len(diag.warnings)
        print(f"error: --strict: {n} warning{'' if n == 1 else 's'} "
              "(above) treated as errors", file=sys.stderr)
        return 2
    return 0


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
    loaded = _load_or_report(root, args.adr_dir)
    if loaded is None:
        return 2
    diag = diag_mod.Diagnostics()
    for message in loaded.warnings:
        diag.warn(message)
    if not loaded.rules:
        return _nothing_to_enforce(args)
    entries = None
    if args.baseline:
        entries = _load_baseline(args.baseline, root)
        if entries is None:
            return 2
    violations = engine_mod.check(root, loaded.layers, loaded.rules,
                                  strict_py_only=args.strict_py_only,
                                  diag=diag)
    if entries is not None:
        violations = engine_mod.not_baselined(violations, entries)
    _print_violations(violations, args.format)
    return _verdict(args, violations, diag)


def _load_baseline(baseline: str, root: str) -> "list[dict] | None":
    """The entries of a baseline file (see `adr-drift baseline`).

    Returns None (after printing an error) when the file can't be used:
    failing closed beats silently dropping the grandfather list.
    """
    path = baseline if os.path.isabs(baseline) else os.path.join(root, baseline)
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError, RecursionError) as e:
        print(f"error: cannot read baseline {path}: {e}", file=sys.stderr)
        return None
    if not isinstance(data, list) or not all(
            isinstance(item, dict) and isinstance(item.get("file"), str)
            and isinstance(item.get("rule"), str) for item in data):
        print(f"error: baseline {path} is not a list of violations (each "
              "needs a 'file' and a 'rule'); rewrite it with "
              "`adr-drift baseline`", file=sys.stderr)
        return None
    return data


def cmd_baseline(args) -> int:
    root = args.root
    loaded = _load_or_report(root, args.adr_dir)
    if loaded is None:
        return 2
    diag = diag_mod.Diagnostics()
    for message in loaded.warnings:
        diag.warn(message)
    if not loaded.rules and not args.allow_empty:
        # An empty baseline grandfathers nothing: a mistake, not a result.
        print(_no_rules_message(), file=sys.stderr)
        print("error: nothing is enforced, so there is nothing to record; "
              "pass --allow-empty to write an empty baseline.",
              file=sys.stderr)
        return 2
    violations = engine_mod.check(root, loaded.layers, loaded.rules,
                                  strict_py_only=args.strict_py_only,
                                  diag=diag)
    out = (args.output if os.path.isabs(args.output)
           else os.path.join(root, args.output))
    with open(out, "w", encoding="utf-8") as f:
        json.dump([v.as_dict() for v in violations], f, indent=2)
        f.write("\n")
    rel = os.path.relpath(out, root)
    print(f"wrote {len(violations)} baselined violation(s) -> {rel}")
    print(f"run `adr-drift check --baseline {rel}` to grandfather them.")
    return 0


_SOURCE_EXTS = scanner_mod.PY_EXTS | scanner_mod.GENERIC_EXTS


def _is_source(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _SOURCE_EXTS


def _git_failed(e: "githelp.GitError") -> int:
    print(f"error: {e}", file=sys.stderr)
    if e.hint:
        print(f"hint: {e.hint}", file=sys.stderr)
    return 2


def cmd_drift(args) -> int:
    root = args.root
    try:
        githelp.require_repo(root)
    except githelp.GitError as e:
        return _git_failed(e)
    loaded = _load_or_report(root, args.adr_dir)
    if loaded is None:
        return 2
    layers, all_rules = loaded.layers, loaded.rules
    diag = diag_mod.Diagnostics()
    for message in loaded.warnings:
        diag.warn(message)
    if not all_rules:
        return _nothing_to_enforce(args)

    # Compare commits: the base ref against HEAD. Both sides are read out of
    # git, so the working tree (uncommitted edits) never matters, and a
    # non-ASCII name is whatever git says it is.
    try:
        githelp.head_commit(root)
        base = githelp.resolve_commit(root, args.since)
        changed = [c for c in githelp.changes(root, base)
                   if _is_source(c.path) or _is_source(c.old_path)]
        blobs = githelp.read_blobs(
            root, [b for c in changed for b in (c.old_blob, c.new_blob)])
    except githelp.GitError as e:
        return _git_failed(e)
    if not changed:
        if args.format == "json":
            print("[]")
        else:
            print(f"no source files changed since {args.since} "
                  f"{sym('—')} no drift")
        return _verdict(args, [], diag)

    quiet = diag_mod.Diagnostics(echo=False)  # the base side: warn once
    go_module = langs_mod.go_module_path(root)

    def entry(path, data, sink):
        imports, _eng = scanner_mod.extract_imports(
            path, data, diag=sink, go_module=go_module)
        return (path, engine_mod._file_layer(layers, path), imports,
                scanner_mod.decode_source(path, data))

    now_entries, base_entries, renames = [], [], {}
    for c in changed:
        if c.status == "R":
            renames[c.old_path] = c.path
        now = blobs.get(c.new_blob) if c.new_blob and _is_source(c.path) else None
        if now is not None:
            now_entries.append(entry(c.path, now, diag))
        old = (blobs.get(c.old_blob)
               if c.old_blob and _is_source(c.old_path) else None)
        if old is not None:
            base_entries.append(entry(c.old_path, old, quiet))

    new_violations, fixed_violations = engine_mod.new_and_fixed(
        engine_mod.evaluate(base_entries, layers, all_rules),
        engine_mod.evaluate(now_entries, layers, all_rules), renames)
    fixed_violations = [dataclasses.replace(v, file=renames.get(v.file, v.file))
                        for v in fixed_violations]

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
    return _verdict(args, new_violations, diag)


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
    # Metadata only: the index must still build while a constraint is broken.
    adrs = [config_mod.parse_adr(p, parse_rules=False)
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
        description="Deterministic architecture tests: keep ADRs mechanically true.",
        epilog="exit codes: 0 ok, 1 violations found, 2 configuration or "
               "usage error (an unparsable rule, no rules at all, a git "
               "problem, --strict with warnings). Warnings and errors go "
               "to stderr; stdout carries only the report (with --format "
               "json always a JSON array).")
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
    c.add_argument("--allow-empty", action="store_true",
                   help="Exit 0 when there are no rules at all (default: "
                        "exit 2, because nothing would be enforced).")
    c.add_argument("--strict", action="store_true",
                   help="Treat warnings (a rule or layer that matches no "
                        "file, an unparsable file, ...) as errors: exit 2.")

    b = sub.add_parser("baseline",
                       help="Record current violations as a grandfather file.")
    b.add_argument("--output", default=".adr-baseline.json", metavar="FILE",
                   help="Where to write the baseline "
                        "(default: .adr-baseline.json under --root).")
    b.add_argument("--strict-py-only", action="store_true",
                   help="Skip the regex fallback for non-Python files.")
    b.add_argument("--allow-empty", action="store_true",
                   help="Write an empty baseline when there are no rules "
                        "(default: exit 2).")

    d = sub.add_parser("drift", help="New violations since a git ref.")
    d.add_argument("--since", default="HEAD~5",
                   help="Git ref to compare against (default: HEAD~5).")
    d.add_argument("--format", choices=["human", "json"], default="human")
    d.add_argument("--fail-on", choices=["violations", "none"],
                   default="violations")
    d.add_argument("--allow-empty", action="store_true",
                   help="Exit 0 when there are no rules at all (default: "
                        "exit 2, because nothing would be enforced).")
    d.add_argument("--strict", action="store_true",
                   help="Treat warnings as errors: exit 2.")

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
