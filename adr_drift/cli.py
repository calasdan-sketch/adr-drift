"""ADR Drift: deterministic architecture tests for any language.

Commands:
  init      scaffold arch.yaml + a sample ADR from the repo's folder layout
  check     verify the repo against arch.yaml + ADR Constraints (CI-friendly)
  baseline  record current violations so check --baseline can grandfather them
  drift     report NEW violations introduced since a git ref
  coverage  which ADRs enforce a rule; the waivers in force
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
import posixpath
import re
import sys

from . import __version__
from . import config as config_mod
from . import diag as diag_mod
from . import engine as engine_mod
from . import codeowners as codeowners_mod
from . import githelp
from . import governance as governance_mod
from . import graphs as graphs_mod
from . import langs as langs_mod
from . import report as report_mod
from . import rulediff as rulediff_mod
from . import resolve as resolve_mod
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
#   forbid-cycles: <layers>            the layers matching <layers> (e.g. *) must
#                                      not depend on each other in a loop
#   layer-order: a > b > c             a layer imports only layers to its right
#   only-import: <layer> -> x, y       <layer> may import only layers x and y
#   public-api: <layer> -> <module>    outside <layer>, only <module> is imported
# Waivers (a time-boxed exception, with an owner) go in a `waivers:` list:
#   waivers:
#     - rule: "handlers must not import db"   # as `check` prints it
#       paths: ["src/handlers/legacy/**"]     # optional
#       owner: alice
#       expires: 2027-03-31
#       reason: "migration ticket 123"
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
    # - forbid-cycles: *
    # - layer-order: handlers > services > db
    # - public-api: db -> db.api

A Superseded, Deprecated or Rejected ADR's constraints are not enforced.

## Consequences

What follows from this decision.
"""


#: The ADR that records the decision to keep ADRs; adr-tools writes it too.
_SAMPLE_ADR = re.compile(r"(?:\d+[-_])?record-architecture-decisions\.md$",
                         re.IGNORECASE)

#: First line of the README.md that `index` writes. A README.md without it
#: (and that is not an index from before the marker existed) was written by
#: hand, and `index` leaves it alone unless it is told to --force.
INDEX_MARKER = ("<!-- generated by adr-drift index - edits here are "
                "overwritten -->")
# What 0.1.1 and earlier wrote before the table: no marker.
_OLD_INDEX_HEAD = ("# Architecture Decision Records\n\n"
                   "| # | Title | Status |\n|---|-------|--------|\n")


def is_generated_index(text: str) -> bool:
    """Did `adr-drift index` write this README.md? Yes if it starts with the
    marker, if it is the marker-less index of version 0.1.1 and earlier (the
    heading, the table and nothing else) or if there is nothing in it."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    if not text.strip() or text.startswith(INDEX_MARKER):
        return True
    return text.startswith(_OLD_INDEX_HEAD) and all(
        line.startswith("|")
        for line in text[len(_OLD_INDEX_HEAD):].split("\n") if line.strip())


def _shown(adr_rel: str, name: str) -> str:
    """`name` in the ADR directory, written with "/" like the paths the
    other messages show (on Windows os.path.join would mix the two)."""
    return posixpath.join(adr_rel.replace("\\", "/"), name)


def _readme_is_ours(path: str) -> bool:
    """True when there is no file at `path` or adr-drift wrote it; False when
    it holds anything else (or is a directory, or cannot be read): that is
    never overwritten without --force."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return is_generated_index(data.decode("utf-8", "replace"))


def _sample_adr(root: str, adr_rel: str) -> "tuple[str, bool]":
    """The ADR that records the decision to use ADRs -> (path, created).
    One that is there already (any number, any subdirectory) is not added a
    second time; a new one is numbered after the highest ADR."""
    for path in config_mod.adr_files(root, adr_rel):
        if _SAMPLE_ADR.match(os.path.basename(path)):
            return path, False
    n = config_mod.next_adr_number(root, adr_rel)
    path = os.path.join(root, adr_rel,
                        f"{n:04d}-record-architecture-decisions.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(ADR_TEMPLATE.format(num=n,
                                    title="Record architecture decisions"))
    return path, True


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
    adr_rel = config_mod.primary_adr_dir(root, args.adr_dir)
    os.makedirs(os.path.join(root, adr_rel), exist_ok=True)
    sample, created = _sample_adr(root, adr_rel)
    readme = _shown(adr_rel, "README.md")
    ours = _readme_is_ours(os.path.join(root, adr_rel, "README.md"))
    if ours:
        print(f"indexed {_write_index(root, adr_rel)} ADR(s) -> {readme}")
    print(f"wrote {config_mod.ARCH_FILE} "
          f"({len(layers)} layers: {', '.join(l['name'] for l in layers)})")
    print(f"sample ADR: {os.path.relpath(sample, root)}"
          + ("" if created else " (already there)"))
    if not ours:
        print(f"kept {readme}: adr-drift did not write it (`adr-drift index "
              "--force` replaces it with a generated index)")
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


def _announce_inactive(loaded) -> None:
    """Say on stderr which ADRs are retired and how many rules that drops:
    a rule that stops being enforced must never do so in silence."""
    for path, status, n in loaded.inactive:
        if n:
            print(f"note: {report_mod.retired_message(path, status, n)}",
                  file=sys.stderr)


def _empty_report(fmt: str, root: str = "", retired=()) -> None:
    """What stdout says for "nothing to report" in a machine format."""
    if fmt == "json":
        print("[]")
    elif fmt == "markdown":
        print(report_mod.markdown_report([], {}, lambda p: []).rstrip("\n"))
    elif fmt == "sarif":
        print(report_mod.sarif_text(
            [], VERSION, engine_mod.identity,
            githelp.repo_prefix(root) if root else "", retired))


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
    if getattr(args, "format", "human") in ("json", "sarif", "markdown"):
        _empty_report(args.format, getattr(args, "root", ""))
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


def _adrs_read(n: int) -> str:
    return f"{n} ADR{'' if n == 1 else 's'} read"


class ReportError(Exception):
    """The report could not be written (exit 2, never a traceback)."""


def _repo_top(root: str, prefix: str) -> str:
    """The top of the git repository, given --root and its prefix in it."""
    top = os.path.abspath(root)
    for part in [p for p in prefix.split("/") if p]:
        top = os.path.dirname(top)
    return top


def _markdown(violations, loaded, root: str, prefix: str, diag,
              retired) -> str:
    adrs = {}
    for a in (loaded.adrs if loaded is not None else []):
        shown = os.path.relpath(a["path"], root).replace(os.sep, "/")
        adrs[shown] = a
    owners = codeowners_mod.load(_repo_top(root, prefix))
    note = (f"{len(diag.warnings)} warning"
            f"{'' if len(diag.warnings) == 1 else 's'} - see the job log"
            if diag.warnings else "")
    join = (lambda p: (prefix.rstrip("/") + "/" + p) if prefix else p)
    return report_mod.markdown_report(
        violations, adrs, lambda p: owners.owners(join(p)), prefix,
        note=note, retired=retired)


def _print_machine(violations, fmt: str, diag, root: str = "",
                   retired=(), loaded=None) -> bool:
    """Print `violations` in a machine format -> False for "human".

    GitHub annotations and SARIF name files from the top of the git
    repository, so --root DIR inside it is put in front of every path.
    Everything is built before anything is printed: a report that cannot be
    built raises ReportError and stdout stays empty."""
    if fmt not in ("json", "sarif", "github", "markdown"):
        return False
    prefix = githelp.repo_prefix(root) if root and fmt != "json" else ""
    try:
        if fmt == "json":
            text = json.dumps([v.as_dict() for v in violations], indent=2)
        elif fmt == "sarif":
            text = report_mod.sarif_text(violations, VERSION,
                                         engine_mod.identity, prefix,
                                         retired)
        elif fmt == "markdown":
            text = _markdown(violations, loaded, root, prefix, diag,
                             retired).rstrip("\n")
        else:
            text = "\n".join(report_mod.github_annotations(
                violations, diag.warnings, prefix, retired))
    except (UnicodeError, ValueError, TypeError) as e:
        raise ReportError(f"cannot write the {fmt} report: {e}") from None
    if text:
        print(text)
    return True


def _print_violations(violations, fmt: str, adr_count: int,
                      diag=None, root: str = "", retired=(),
                      loaded=None) -> None:
    diag = diag if diag is not None else diag_mod.Diagnostics(echo=False)
    if _print_machine(violations, fmt, diag, root, retired,
                      loaded) and fmt != "github":
        return
    if not violations:
        # "all ADRs hold" over zero ADRs would be a lie: say how many were read
        verdict = ("all ADRs hold (%d read)" % adr_count if adr_count
                   else f"{_adrs_read(0)} (only the rules in "
                        f"{config_mod.ARCH_FILE} were checked)")
        print(f"{sym(chr(0x2713))} no architecture drift "
              f"{sym(chr(0x2014))} {verdict}")
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
            for step in v.evidence:
                print(f"      {step}")
            print(f"      rule: {v.rule.describe()} ({v.rule.source})")


def _waive(violations, loaded, diag, stale: bool = True):
    """Drop the violations arch.yaml waivers cover (until they expire), say
    so on stderr, and warn about waivers that expired, end soon or match
    nothing."""
    if not loaded.waivers:
        return violations
    try:
        day = governance_mod.today()
    except ValueError as e:
        raise ReportError(str(e)) from None
    kept, waived = governance_mod.apply_waivers(
        violations, loaded.waivers, day, diag.warn, stale)
    note = governance_mod.waiver_note(waived)
    if note:
        print(note, file=sys.stderr)
    return kept


def cmd_check(args) -> int:
    root = args.root
    loaded = _load_or_report(root, args.adr_dir)
    if loaded is None:
        return 2
    diag = diag_mod.Diagnostics()
    for message in loaded.warnings:
        diag.warn(message)
    _announce_inactive(loaded)
    if not loaded.rules:
        return _nothing_to_enforce(args)
    entries = None
    if args.ratchet and not args.baseline:
        print("error: --ratchet needs --baseline FILE", file=sys.stderr)
        return 2
    if args.baseline:
        entries = _load_baseline(args.baseline, root)
        if entries is None:
            return 2
    violations = engine_mod.check(root, loaded.layers, loaded.rules,
                                  strict_py_only=args.strict_py_only,
                                  diag=diag, jobs=args.jobs)
    gone: list = []
    try:
        violations = _waive(violations, loaded, diag)
        if entries is not None:
            violations, gone = engine_mod.split_baseline(violations, entries)
        _print_violations(violations, args.format, len(loaded.adrs), diag,
                          root, loaded.inactive, loaded)
    except ReportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.ratchet and gone and args.fail_on != "none":
        n = len(gone)
        print(f"error: --ratchet: {n} grandfathered violation"
              f"{'' if n == 1 else 's'} in {args.baseline} "
              f"{'is' if n == 1 else 'are'} fixed. Run `adr-drift baseline` "
              "and commit it, so they cannot come back unnoticed.",
              file=sys.stderr)
        return 1
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
    _announce_inactive(loaded)
    if not loaded.rules and not args.allow_empty:
        # An empty baseline grandfathers nothing: a mistake, not a result.
        print(_no_rules_message(), file=sys.stderr)
        print("error: nothing is enforced, so there is nothing to record; "
              "pass --allow-empty to write an empty baseline.",
              file=sys.stderr)
        return 2
    violations = engine_mod.check(root, loaded.layers, loaded.rules,
                                  strict_py_only=args.strict_py_only,
                                  diag=diag, jobs=args.jobs)
    try:
        violations = _waive(violations, loaded, diag)
    except ReportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
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
    _announce_inactive(loaded)
    if not all_rules:
        return _nothing_to_enforce(args)
    # A cycle can run through files the range did not touch, and drift only
    # reads the changed ones: it cannot judge forbid-cycles. Say so, and if
    # nothing else is left, warn (a green drift would mean nothing).
    cycles = [r for r in all_rules if r.kind == "forbid-cycles"]
    all_rules = [r for r in all_rules if r.kind != "forbid-cycles"]
    if cycles:
        n = len(cycles)
        text = (f"{n} forbid-cycles rule{'' if n == 1 else 's'} "
                f"{'is' if n == 1 else 'are'} checked by `adr-drift check`, "
                "not by `drift` (a cycle can run through files that did not "
                "change)")
        if all_rules:
            print(f"note: {text}", file=sys.stderr)
        else:
            diag.warn(text + "; no other rule is left, so drift checks "
                      "nothing")

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
        if args.format in ("json", "sarif", "markdown"):
            _empty_report(args.format, root, loaded.inactive)
        elif args.format == "github":
            for line in report_mod.github_annotations(
                    [], diag.warnings, githelp.repo_prefix(root),
                    loaded.inactive):
                print(line)
            print(f"no source files changed since {args.since} -- no drift")
        else:
            print(f"no source files changed since {args.since} "
                  f"{sym('—')} no drift")
        return _verdict(args, [], diag)

    quiet = diag_mod.Diagnostics(echo=False)  # the base side: warn once
    go_module = langs_mod.go_module_path(root)
    resolver = resolve_mod.Resolver(root)

    limit = scanner_mod.max_file_bytes()

    def entry(path, data, sink):
        if limit and len(data) > limit:
            sink.warn(f"{path}: cannot read ({len(data)} bytes, over the "
                      f"{limit}-byte limit (ADR_DRIFT_MAX_FILE_BYTES)); "
                      "not checked")
            return None
        imports, _eng = scanner_mod.extract_imports(
            path, data, diag=sink, go_module=go_module, resolver=resolver)
        return (path, engine_mod._file_layer(layers, path), imports,
                scanner_mod.decode_source(path, data))

    now_entries, base_entries, renames = [], [], {}
    for c in changed:
        if c.status == "R":
            renames[c.old_path] = c.path
        now = blobs.get(c.new_blob) if c.new_blob and _is_source(c.path) else None
        got = entry(c.path, now, diag) if now is not None else None
        if got is not None:
            now_entries.append(got)
        old = (blobs.get(c.old_blob)
               if c.old_blob and _is_source(c.old_path) else None)
        got = entry(c.old_path, old, quiet) if old is not None else None
        if got is not None:
            base_entries.append(got)

    repo_paths = None
    if any(engine_mod._complex_globs(l) for l in layers):
        try:
            repo_paths = githelp.tree_paths(root)
        except githelp.GitError as e:
            return _git_failed(e)
    new_violations, fixed_violations = engine_mod.new_and_fixed(
        engine_mod.evaluate(base_entries, layers, all_rules, repo_paths),
        engine_mod.evaluate(now_entries, layers, all_rules, repo_paths),
        renames)
    fixed_violations = [dataclasses.replace(v, file=renames.get(v.file, v.file))
                        for v in fixed_violations]

    try:
        new_violations = _waive(new_violations, loaded, diag, stale=False)
        machine = _print_machine(new_violations, args.format, diag, root,
                                 loaded.inactive, loaded)
    except ReportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if machine:
        # Stable contract: the JSON list (and the SARIF results, and the
        # annotations) are the NEW violations, as before. Fixed violations
        # are reported in the human output.
        if args.format == "github":
            print(f"{len(new_violations)} new violation(s) since "
                  f"{args.since}, {len(fixed_violations)} fixed")
    else:
        if not new_violations:
            print(f"{sym('✓')} no new drift since {args.since} "
                  f"({len(changed)} file(s) changed; "
                  f"{_adrs_read(len(loaded.adrs))})")
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


def cmd_coverage(args) -> int:
    """Which ADRs enforce anything, and the waivers in force."""
    loaded = _load_or_report(args.root, args.adr_dir)
    if loaded is None:
        return 2
    try:
        day = governance_mod.today()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for message in loaded.warnings:
        print(f"warning: {message}", file=sys.stderr)
    cov = governance_mod.coverage(args.root, loaded, day)
    expired = [w for w in cov["waivers"] if w["state"] == "expired"]
    failed = (args.fail_under is not None and cov["percent"] < args.fail_under)
    if args.format == "json":
        print(json.dumps(cov, indent=2))
    else:
        print(f"{cov['enforced']} of {cov['in_force']} ADRs in force "
              f"enforce at least one rule ({cov['percent']}%)")
        for row in cov["adrs"]:
            mark = {"enforced": sym("✓"), "no-rule": sym("✗"),
                    "retired": "-"}[row["state"]]
            owners = f"  [{', '.join(row['owners'])}]" if row["owners"] else ""
            print(f"  {mark} {row['path']}  {row['status'] or sym('—')}  "
                  f"{row['rules']} rule{'' if row['rules'] == 1 else 's'}"
                  f"{owners}")
            if row["state"] == "no-rule":
                print("      nothing checks this decision: add a "
                      "## Constraints section")
            if row.get("hint"):
                print(f"      hint: {row['hint']}")
            for ref in row["missing_paths"]:
                print(f"      mentions {ref}, which does not exist")
        if cov["waivers"]:
            print(f"\nwaivers ({len(cov['waivers'])}):")
            for w in cov["waivers"]:
                left = (f"expired {-w['days_left']} day(s) ago"
                        if w["days_left"] < 0 else f"{w['days_left']} day(s) left")
                print(f"  {w['state']:8s} {w['expires']} ({left})  "
                      f"{w['owner']}: {w['rule']} - {w['reason']}")
    if failed:
        print(f"error: {cov['percent']}% is under --fail-under "
              f"{args.fail_under}", file=sys.stderr)
    if expired:
        print(f"error: {len(expired)} waiver"
              f"{'' if len(expired) == 1 else 's'} expired", file=sys.stderr)
    return 1 if (failed or expired) else 0


def _layers_or_report(args):
    """(loaded, entries, graph) for `graph` and `suggest`, or None."""
    loaded = _load_or_report(args.root, args.adr_dir)
    if loaded is None:
        return None
    if not loaded.layers:
        print("error: arch.yaml defines no layers; run `adr-drift init` "
              "first", file=sys.stderr)
        return None
    diag = diag_mod.Diagnostics()
    entries, _seen = engine_mod.scan_entries(
        args.root, loaded.layers, loaded.rules, False, diag, args.jobs)
    return loaded, entries, graphs_mod.layer_graph(entries, loaded.layers)


def cmd_graph(args) -> int:
    got = _layers_or_report(args)
    if got is None:
        return 2
    graph = got[2]
    if args.format == "json":
        print(json.dumps(graphs_mod.as_json(graph), indent=2))
    else:
        print(graphs_mod.mermaid(graph) if args.format == "mermaid"
              else graphs_mod.dot(graph), end="")
    return 0


def cmd_suggest(args) -> int:
    got = _layers_or_report(args)
    if got is None:
        return 2
    loaded, _entries, graph = got
    result = graphs_mod.suggest(graph, {r.kind for r in loaded.rules})
    if args.format == "json":
        print(json.dumps(result, indent=2))
        return 0
    if result["rules"]:
        print("# These hold in the code today. Review, then paste under "
              "`rules:` in arch.yaml\n# (or into an ADR's ## Constraints):")
        for r in result["rules"]:
            print(f'  - "{r}"')
    else:
        print("nothing to suggest")
    for n in result["notes"]:
        print(f"note: {report_mod.escape_terminal(n)}", file=sys.stderr)
    return 0


def cmd_rules_diff(args) -> int:
    root = args.root
    try:
        githelp.require_repo(root)
        base = githelp.resolve_commit(root, args.since)
        head = githelp.resolve_commit(root, "HEAD")
        old = rulediff_mod.load_at(root, base, args.adr_dir)
        new = rulediff_mod.load_at(root, head, args.adr_dir)
    except githelp.GitError as e:
        return _git_failed(e)
    result = rulediff_mod.compare(old, new)
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        esc = report_mod.escape_terminal
        for key, title in (("weakened", "Weakened"),
                           ("strengthened", "Strengthened"),
                           ("notes", "Also")):
            if result[key]:
                print(f"{title}:")
                for item in result[key]:
                    print(f"  - {esc(item)}")
        if not any(result.values()):
            print(f"no rule changes since {esc(args.since)}")
    if args.fail_on_weakened and result["weakened"]:
        return 1
    return 0


def cmd_new(args) -> int:
    adr_rel = config_mod.primary_adr_dir(args.root, args.adr_dir)
    adr_dir = os.path.join(args.root, adr_rel)
    os.makedirs(adr_dir, exist_ok=True)
    n = config_mod.next_adr_number(args.root, adr_rel)
    slug = re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")
    path = os.path.join(adr_dir, f"{n:04d}-{slug}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(ADR_TEMPLATE.format(num=n, title=args.title))
    print(f"created {os.path.relpath(path, args.root)}")
    return 0


def _cell(text: str) -> str:
    """`text` for a cell of a Markdown table (a pipe would end the cell)."""
    return text.replace("|", "\\|")


def _write_index(root: str, adr_rel: str) -> int:
    """Write <adr_rel>/README.md, a table of the ADRs below that directory
    -> how many there are."""
    adr_dir = os.path.join(root, adr_rel)
    # Metadata only: the index must still build while a constraint is broken.
    adrs = [config_mod.parse_adr(p, parse_rules=False)
            for p in config_mod.adr_files(root, adr_rel)]
    lines = [INDEX_MARKER + "\n",
             "# Architecture Decision Records\n",
             "| # | Title | Status |",
             "|---|-------|--------|"]
    for a in adrs:
        rel = os.path.relpath(a["path"], adr_dir).replace(os.sep, "/")
        status = _cell(a["status"]) or "\u2014"
        lines.append(f"| {a['number']} | [{_cell(a['title'])}]({rel}) "
                     f"| {status} |")
    lines.append("")
    with open(os.path.join(adr_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return len(adrs)


def cmd_index(args) -> int:
    adr_rel = config_mod.primary_adr_dir(args.root, args.adr_dir)
    readme = _shown(adr_rel, "README.md")
    if not os.path.isdir(os.path.join(args.root, adr_rel)):
        print(f"error: ADR directory {adr_rel} not found, so there is "
              "nothing to index", file=sys.stderr)
        print("hint: `adr-drift new TITLE` or `adr-drift init` creates one; "
              "--adr-dir DIR names another", file=sys.stderr)
        return 2
    if not getattr(args, "force", False) and not _readme_is_ours(
            os.path.join(args.root, adr_rel, "README.md")):
        print(f"error: {readme} was not written by adr-drift, so it was "
              "left alone", file=sys.stderr)
        print("hint: `adr-drift index --force` replaces it with a generated "
              "index", file=sys.stderr)
        return 2
    try:
        count = _write_index(args.root, adr_rel)
    except OSError as e:
        what = os.path.basename(e.filename) if e.filename else "the index"
        print(f"error: cannot index {adr_rel}: {e.strerror or e} ({what})",
              file=sys.stderr)
        return 2
    print(f"indexed {count} ADR(s) -> {readme}")
    return 0


def _location_options(after_subcommand: bool) -> argparse.ArgumentParser:
    """--root and --adr-dir, as a parent parser.

    They are accepted on both sides of the subcommand: `adr-drift --root X
    check` and `adr-drift check --root X`. The GitHub Action runs
    `adr-drift check <args>`, so its `args: --adr-dir doc/adr` always arrives
    after the subcommand. A sub-parser's defaults overwrite what the main
    parser has already read, so on the subcommands the options have no
    default of their own (SUPPRESS): they only set a value when given.
    """
    skip = argparse.SUPPRESS
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--root", default=skip if after_subcommand else os.getcwd(),
                   help="Repo root (default: cwd).")
    # The two spellings use two names and parse_args() joins them, so
    # `--adr-dir A check --adr-dir B` reads both and drops neither.
    p.add_argument("--adr-dir", metavar="DIR", action="append",
                   type=_adr_dir_arg,
                   dest="adr_dir_after" if after_subcommand else "adr_dir",
                   default=skip if after_subcommand else None,
                   help="Where the ADRs are, searched recursively; repeat "
                        "to read several. Default: the first of "
                        + ", ".join(config_mod.ADR_DIR_CANDIDATES)
                        + " that exists.")
    return p


def _jobs_arg(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        n = -1
    if n < 0 or n > 256:
        raise argparse.ArgumentTypeError("needs a number from 0 to 256 "
                                         "(0: one per CPU)")
    return n


def _adr_dir_arg(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("needs a directory name")
    return value


FORMATS = ["human", "json", "github", "sarif", "markdown"]
FORMAT_HELP = ("human (default), json (a bare array of violations), github "
               "(GitHub Actions annotations on the offending lines, then the "
               "human report), sarif (SARIF 2.1.0 for code scanning) or "
               "markdown (a pull-request comment: the ADR behind each broken "
               "rule, who decided it, who owns the files).")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="adr-drift",
        parents=[_location_options(after_subcommand=False)],
        description="Deterministic architecture tests: keep ADRs mechanically true.",
        epilog="exit codes: 0 ok, 1 violations found, 2 configuration or "
               "usage error (an unparsable rule, no rules at all, a git "
               "problem, --strict with warnings). Warnings and errors go "
               "to stderr; stdout carries only the report (with --format "
               "json always a JSON array).")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)
    where = [_location_options(after_subcommand=True)]

    i = sub.add_parser("init", parents=where,
                       help="Scaffold arch.yaml + sample ADR.")
    i.add_argument("--force", action="store_true",
                   help="Overwrite an existing arch.yaml. A README.md that "
                        "adr-drift did not write is kept (see index "
                        "--force).")

    c = sub.add_parser("check", parents=where,
                       help="Verify the repo against all rules.")
    c.add_argument("--format", choices=FORMATS, default="human",
                   help=FORMAT_HELP)
    c.add_argument("--fail-on", choices=["violations", "none"],
                   default="violations")
    c.add_argument("--strict-py-only", action="store_true",
                   help="Skip the regex fallback for non-Python files.")
    c.add_argument("--jobs", type=_jobs_arg, default=0, metavar="N",
                   help="Worker processes for reading files (default: one "
                        "per CPU, up to 8; small repositories use one). "
                        "The result is the same for any N.")
    c.add_argument("--baseline", default=None, metavar="FILE",
                   help="Grandfather violations listed in FILE "
                        "(written by `adr-drift baseline`).")
    c.add_argument("--ratchet", action="store_true",
                   help="With --baseline: also fail (exit 1) when a "
                        "grandfathered violation has been fixed and is "
                        "still listed, so the baseline only ever shrinks.")
    c.add_argument("--allow-empty", action="store_true",
                   help="Exit 0 when there are no rules at all (default: "
                        "exit 2, because nothing would be enforced).")
    c.add_argument("--strict", action="store_true",
                   help="Treat warnings (a rule or layer that matches no "
                        "file, an unparsable file, ...) as errors: exit 2.")

    b = sub.add_parser("baseline", parents=where,
                       help="Record current violations as a grandfather file.")
    b.add_argument("--output", default=".adr-baseline.json", metavar="FILE",
                   help="Where to write the baseline "
                        "(default: .adr-baseline.json under --root).")
    b.add_argument("--strict-py-only", action="store_true",
                   help="Skip the regex fallback for non-Python files.")
    b.add_argument("--jobs", type=_jobs_arg, default=0, metavar="N",
                   help="Worker processes (see check --jobs).")
    b.add_argument("--allow-empty", action="store_true",
                   help="Write an empty baseline when there are no rules "
                        "(default: exit 2).")

    d = sub.add_parser("drift", parents=where,
                       help="New violations since a git ref.")
    d.add_argument("--since", default="HEAD~5",
                   help="Git ref to compare against (default: HEAD~5).")
    d.add_argument("--format", choices=FORMATS, default="human",
                   help=FORMAT_HELP)
    d.add_argument("--fail-on", choices=["violations", "none"],
                   default="violations")
    d.add_argument("--allow-empty", action="store_true",
                   help="Exit 0 when there are no rules at all (default: "
                        "exit 2, because nothing would be enforced).")
    d.add_argument("--strict", action="store_true",
                   help="Treat warnings as errors: exit 2.")

    v = sub.add_parser("coverage", parents=where,
                       help="Which ADRs enforce a rule; the waivers.")
    v.add_argument("--format", choices=["human", "json"], default="human")
    v.add_argument("--fail-under", type=int, metavar="PERCENT", default=None,
                   help="Exit 1 when fewer than PERCENT of the ADRs in force "
                        "enforce at least one rule.")

    r = sub.add_parser("rules-diff", parents=where,
                       help="How the rules changed since a git ref.")
    r.add_argument("--since", default="HEAD~1", metavar="REF",
                   help="Compare HEAD with this ref (default HEAD~1). "
                        "Only committed files are read.")
    r.add_argument("--format", choices=["human", "json"], default="human")
    r.add_argument("--fail-on-weakened", action="store_true",
                   help="Exit 1 when a rule was removed, an ADR retired or "
                        "a waiver added or extended.")

    g = sub.add_parser("graph", parents=where,
                       help="Draw how the layers import each other.")
    g.add_argument("--format", choices=["mermaid", "dot", "json"],
                   default="mermaid")
    g.add_argument("--jobs", type=_jobs_arg, default=0, metavar="N",
                   help="Read files in N processes.")
    u = sub.add_parser("suggest", parents=where,
                       help="Propose rules that already hold in the code.")
    u.add_argument("--format", choices=["human", "json"], default="human")
    u.add_argument("--jobs", type=_jobs_arg, default=0, metavar="N",
                   help="Read files in N processes.")

    n = sub.add_parser("new", parents=where,
                       help="Create the next-numbered ADR.")
    n.add_argument("title")

    x = sub.add_parser("index", parents=where,
                       help="Regenerate the ADR index README.")
    x.add_argument("--force", action="store_true",
                   help="Replace README.md even if adr-drift did not write "
                        "it (otherwise it is left alone: exit 2).")
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
        # Paths, module names and patterns come from the repository being
        # checked: never let one reach the terminal as a control sequence.
        self._call("write", report_mod.escape_terminal(text))
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


def parse_args(argv=None) -> argparse.Namespace:
    """The command line; `adr_dir` is None (look in the usual places) or the
    list of every --adr-dir given, before and after the subcommand."""
    args = build_parser().parse_args(argv)
    after = getattr(args, "adr_dir_after", None)
    if after:
        args.adr_dir = (args.adr_dir or []) + after
    return args


def _main(argv) -> int:
    args = parse_args(argv)
    try:
        return _dispatch(args)
    except engine_mod.RuleTooSlow as e:
        print(f"error: {report_mod.escape_terminal(str(e))}", file=sys.stderr)
        return 2


def _dispatch(args) -> int:
    return {"init": cmd_init, "check": cmd_check, "baseline": cmd_baseline,
            "drift": cmd_drift, "coverage": cmd_coverage, "new": cmd_new,
            "graph": cmd_graph, "suggest": cmd_suggest,
            "rules-diff": cmd_rules_diff,
            "index": cmd_index}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
