# ADR Drift

Deterministic architecture tests for any language. ADRs rot the moment they
merge — this keeps them mechanically true. Define layers and constraints once,
then `adr-drift check` fails CI when code drifts from the decisions.

## The problem

> "The moment an ADR gets merged, it decays into a passive historical record
> unless something mechanical forces it to stay alive."

Developers — and now AI coding agents — merge boundary violations because
nothing checks decisions against code. ArchUnit is Java-only, deptrac is
PHP-only. ADR Drift is language-agnostic, repo-local, and CI-friendly.

## Install

```bash
pip install adr-drift   # provides `adr-drift`
```

From source: `pip install git+https://github.com/calasdan-sketch/adr-drift`

Or run without installing: `python3 -m adr_drift` from the repo root.

## Quick start

```bash
cd your-repo
python3 -m adr_drift init      # scaffold arch.yaml + sample ADR from folder layout
# enable a rule in arch.yaml and/or add ## Constraints to your ADRs
python3 -m adr_drift check     # verify; exit 1 on violations (CI-ready)
# repo already has legacy violations? grandfather them, then enforce:
python3 -m adr_drift baseline                    # record current violations
python3 -m adr_drift check --baseline .adr-baseline.json
```

## Commands

| Command | What it does |
|---|---|
| `init` | Infer layers from folder layout, write `arch.yaml` + sample ADR + index (a `README.md` of yours is kept) |
| `check` | Verify the repo against `arch.yaml` + every ADR's `## Constraints` (`--baseline FILE` grandfathers recorded violations; `--strict` fails on warnings; `--allow-empty` accepts a repo with no rules; `--format human\|json\|sarif`; `--verbose` explains what is not enforced) |
| `baseline` | Write current violations to `.adr-baseline.json` (default) for `check --baseline` |
| `drift --since HEAD~5` | Report violations *introduced* between a git ref and `HEAD`, plus violations *fixed* in that range (pre-existing ones ignored) |
| `new "title"` | Create the next-numbered ADR from a template |
| `index` | Regenerate the ADR index (`docs/adr/README.md`); a `README.md` adr-drift did not write is left alone unless you pass `--force` |

Every command takes `--root DIR` (the repository; default: the working
directory) and `--adr-dir DIR` (where the ADRs are, see below), before or
after the command name: `adr-drift --root app check` and
`adr-drift check --root app` are the same.

## The constraint DSL

Rules live in `arch.yaml` and in any ADR's `## Constraints` section:

```
forbid-import: handlers -> db.drivers     # layering: handlers may not import drivers
forbid-pattern: src/**.py !~ \beval\(     # no eval() anywhere in src/
require-pattern: src/handlers/**.py =~ authenticate\(   # handlers must authenticate
```

`from`/`to` match layer names or module prefixes (`db.drivers` matches
`db.drivers.pg`); `*` wildcards supported. Comments start with `#`.
Python relative imports resolve against the file's package
(`from ..db.drivers import pg` in `src/handlers/` is `src.db.drivers.pg`),
and `from app.db import drivers` counts as importing `app.db.drivers`.
JS/TS relative specifiers resolve against the importing file with the
extension dropped (`"../db/config.js"` matches a `config.ts` module).
To-side patterns resolve against layer paths too, whichever way the code
spells the import: a `db` layer mapped to `src/myapp/db/**` is reached by
`import myapp.db.conn` (`src/` is the import root), `import src.myapp.db.conn`
and, in a flat layout, `import db.conn` - a rule naming the layer (`db`) or
any of those spellings matches all of them. Only the layer's leading
directories are stripped, never its own, so `conn` alone matches nothing.

### Temporary exceptions: `allow-until`

A migration takes time. End a rule with `allow-until: YYYY-MM-DD` and its
violations are allowed up to and including that day:

```
forbid-import: handlers -> db.drivers  allow-until: 2026-12-31   # migrating, see ticket 123
```

From the next day the violations fail again, with a message that says the
exception ended and when. A run prints one `note:` line on stderr whenever an
exception lets violations through (`--verbose` lists them), and a warning
for an exception that has ended: either its violations now fail, or there are
none left and the `allow-until` can be deleted (`--strict` makes that
warning an error). A date that is not a real `YYYY-MM-DD` is an error, not a
rule that never ends. In `arch.yaml` quote the line (`"forbid-import: a -> b
allow-until: 2026-12-31"`). `--today YYYY-MM-DD` pretends it is another day
(handy for testing a deadline). A `forbid-pattern` whose regex itself ends in
`allow-until:` must be written another way (for example `allow-until[:]`).

### Superseded and deprecated ADRs

An ADR whose status says `Superseded` (for example `Superseded by ADR-0007`)
or `Deprecated` stops enforcing its constraints, and its Constraints section
is not even parsed. `check --verbose` lists such ADRs. `Accepted (supersedes
ADR-0002)` is not retired: only a status that *starts* with one of the two
words counts.

### Where the ADRs are

Every `*.md` file in the ADR directory and in its subdirectories is read
(`docs/adr/2024/0007-use-kafka.md` counts), except `README.md` files and
anything under a hidden directory or `node_modules`. Without `--adr-dir` the
directory is the first of `docs/adr`, `doc/adr` (adr-tools),
`docs/decisions` (MADR), `docs/architecture/decisions`, `adr` and
`decisions` that exists. With `--adr-dir DIR` exactly that directory is read;
repeat the option to read several:
`--adr-dir docs/adr --adr-dir services/api/adr`. `new`, `index` and `init`
write into the first `--adr-dir`, else into the directory found, else
`docs/adr`.

Reading no ADRs must never pass for "all ADRs hold". These are warnings
(`--strict` makes them exit 2): no ADR file found at all, an `--adr-dir` that
does not exist, a directory that cannot be read, and another candidate
directory whose ADRs were *not* read because an earlier one was used (pass
every directory you want with `--adr-dir`). The all-clear line says how many
ADRs were read - `all ADRs hold (4 read)`, or `0 ADRs read (only the rules in
arch.yaml were checked)` - and `drift` ends its line with the same count. With
`--format json` stdout stays the bare array of violations; the warnings are
on stderr.

### The index, the sample ADR and the statuses

`index` writes a table of the ADRs (number, title, status) to `README.md`
in the ADR directory, under a first line that says adr-drift generated it.
A `README.md` without that line - one you wrote - is never overwritten:
`index` says so and exits 2, and `index --force` replaces it. (The index
written by 0.1.1 and earlier has no such line; it is still recognised as
long as it is only the table.) `index` needs the ADR directory to exist
(exit 2 otherwise).

`init` writes the index the same way but keeps a `README.md` of yours
(it says so; `--force` on `init` only overwrites `arch.yaml`). It numbers
the sample ADR, `record-architecture-decisions`, after the highest ADR in
the directory and adds none when the directory has one already (adr-tools
writes the same one).

The title is the first `# ` heading (after any front matter). The status is
the first line of a `## Status` section; else the `status:` of the front
matter (MADR); else a `Status:` line between the title and the first `##`
heading (`* Status: accepted`, `**Status:** accepted`). No status shows
as a dash.

### Rules are strict: nothing is dropped silently

A rule that is quietly ignored leaves a repo looking enforced when it is not,
so a rule that does not parse is an error (exit 2, `file:line` on stderr),
and every problem in every file is listed in one run:

- In `arch.yaml` a rule is one list item; quoting it is optional
  (`- forbid-import: a -> b` works).
- In an ADR, `## Constraints` is read line by line. A rule may be plain, a
  list item (`-`, `*`, `1.`, `1)`), a task box (`- [ ]`), in `` `backticks` ``
  or `**bold**`, optionally followed by a sentence of explanation. Ordinary
  prose is ignored, and so is anything after a `#`.
- A line that *looks* like a rule but is not one is an error: a misspelt
  name (`forbid_import:`, `forbid-patern:`), a name with nothing after the
  colon, a bare `a -> b` item, a rule name buried in the middle of a line.
- Each side of a `forbid-import` is one layer name or module prefix; spaces
  in it (`handlers -> db because layering`) are an error - explain after a `#`.
- Unknown keys in `arch.yaml` (`path:` for `paths:`) are errors with a
  did-you-mean. Prefix a key with `x-` to keep a note there, at the top
  level or in a layer (`x-owner: platform team`).
- A heading such as `### Constraints` is not `## Constraints`, so the rules
  below it would not be read: that is a warning.
- A repo with no rules at all fails (exit 2) unless you pass `--allow-empty`:
  a green check that enforces nothing is worse than a red one.

## How it checks

- **Python** is parsed with the real `ast` — no regex false positives from
  comments or strings. Files are read as bytes, so a UTF-8 BOM and a
  `# coding:` cookie work as they do for Python itself. A file Python cannot
  parse (syntax error, NUL byte, nested too deeply, out of memory) is
  reported as `unparsable` on stderr - a warning, or exit 2 under `--strict`:
  its imports cannot be checked, but pattern rules still run on its text.
- **JS/TS/Go/Rust/Java/Ruby/PHP** use a heuristic extractor (a small
  tokenizer, not a parser): comments and string literals are ignored and
  multi-line statements are read whole, in time linear in the size of the
  file whatever it holds. A Rust or PHP file whose `use` lists are nested
  more than 16 deep, or are so tangled that they would need far more work
  than that, is reported as `unparsable` like a Python file that Python
  cannot parse. See *Languages: exact support* below;
  `--strict-py-only` disables the extractor.
- `drift` compares commits: the violations at `HEAD` against those at the
  base ref, both read out of git (`git diff -z`, `git cat-file`) - it never
  touches your working tree, uncommitted edits are ignored, and file names
  with non-ASCII characters work. Renamed files are followed. `git blame`
  tags each new violation with its introducing commit. When `--root` (or the
  working directory) is a folder inside a larger repository - a package in a
  monorepo - `drift` looks only at the files under it and names them from it,
  exactly as `check` does; changes next door are neither counted nor checked,
  and a file moved across the boundary counts as added or deleted.
- A git problem (unknown ref, a shallow clone without the history, fewer
  commits than the default `--since HEAD~5`, git missing) is an `error:`
  with a `hint:` and exit 2 - never a traceback and never exit 1. In GitHub
  Actions, `drift` needs `actions/checkout` with `fetch-depth: 0`.

### What counts as "the same violation"

`check --baseline` and `drift` identify a violation by its **file, rule and
what was found** (the imported module for `forbid-import`, the rule's pattern
for the pattern rules) - not by its line number. Adding a docstring above a
grandfathered import, or renaming the file, does not turn it into a new
violation. When a file has several identical violations they are counted: a
second `eval(` in a file that had one is reported as one new violation (which
of the two is the new one is a best guess). A baseline written by 0.1.0 keeps
working; a hand-made entry without `detail` still matches by exact file, line
and rule.

## Languages: exact support

`forbid-import` needs to know a file's imports. Python is exact (`ast`:
`import x`, `import x.y as z`, `from x import y`, relative imports resolved
against the file's package; `importlib` and `__import__` calls are not
seen). The rest is a heuristic extractor - read what it covers:

| Language | Files | Recognised | Not recognised |
|---|---|---|---|
| JavaScript / TypeScript | `.js .jsx .ts .tsx .mjs .cjs` | `import x from "m"`, `import { a, b } from "m"` (also over several lines), `import * as ns from "m"`, `import type ... from "m"`, `import "m"`, `export ... from "m"`, `export * from "m"`, `import("m")`, `require("m")`, `import x = require("m")`; relative specifiers resolve against the file | path aliases (tsconfig `paths`, bundler aliases), non-literal `import(x)` / `require(x)`, `.vue` / `.svelte` files |
| Go | `.go` | `import "m"`, aliased / `.` / `_` imports, grouped `import ( ... )`, raw-string paths; the module path in the root `go.mod` is removed from the repo's own packages (`github.com/acme/app/internal/db` is `internal/db`) | a `go.mod` below the root (multi-module repos), build tags |
| Java | `.java` | `import a.b.C;`, `import static a.b.C.m;`, wildcards (`a.b.*` is `a.b`) | uses of a class in the same package (there is no import), fully qualified names in code |
| Rust | `.rs` | `use a::b;`, nested trees `use a::{b::{c, d}, e as f, self}`, globs, `pub use`, `pub(crate) use`, `extern crate`, `mod x;`; `crate::`, `self::` and `super::` resolve against the file's module | macro-generated `use`, `#[path = "..."]` modules, paths used in code without a `use` |
| PHP | `.php` | `use A\B;`, `use A\{B, C as D}`, `use function` / `use const`, `require` / `include` (`_once`) of a literal path or `__DIR__ . 'path'` | `dirname(__FILE__)`, dynamic includes, a namespace spelt in a different case than its directory |
| Ruby | `.rb` | `require`, `require_relative`, `load`, `autoload` with a string literal, at the start of a line | a `require` after other code on the same line, computed paths |

Rust and PHP names are matched dotted (`crate::db::x` is `db.x`,
`App\Models\User` is `App.Models.User`); the other languages are matched as
written, with `/` and `.` interchangeable. Layers are matched the same way
as for Python, with their leading directories optional: a Java layer at
`src/main/java/com/acme/db/**` is reached by `com.acme.db.Conn`.

When forbid-import rules exist and **none** of a language's files contains an
import the extractor recognises, `check` warns once for that language: the
extractor is probably not seeing this codebase, and a rule that cannot see
imports enforces nothing. `--strict` makes that an error.

## Exit codes, warnings and output

| Exit | Meaning |
|---|---|
| 0 | no violations (and, with `--strict`, no warnings) |
| 1 | violations found (`--fail-on none` makes every run report-only: exit 0) |
| 2 | the configuration or the invocation is wrong: unparsable rule, no rules, bad baseline, `--strict` with warnings |

stdout carries only the report. With `--format json` it is always a bare
array of violations (`[]` when clean) - or empty when the exit code is 2 -
so `adr-drift check --format json | jq` never meets prose. Every warning and
error goes to stderr as `warning: ...` / `error: ...`.

Warnings flag rules and layers that cover nothing, which usually means a typo:
a layer whose paths match no scanned source file, a `forbid-pattern` or
`require-pattern` glob that matches no scanned file, the `from` side of a
`forbid-import` that matches no layer name or path, a `to` side that looks
like a misspelt layer, an unparsable or unreadable source file, and an ADR
directory that is missing, empty or not the only one (see *Where the ADRs
are*). Pattern rules apply to the scanned source files only
(`.py .js .jsx .ts .tsx .mjs .cjs .go .rs .java .rb .php`). `--strict` turns
any warning into exit 2.

### Reports: text, JSON and SARIF

Every violation says how to fix it and which ADR it comes from. In text
output, under each violation:

```
src/util.py:2: matches /\beval\(/
  rule: src/**.py must not contain /\beval\(/ (docs/adr/0002-no-dynamic-eval.md)
  ADR 0002: No dynamic eval (docs/adr/0002-no-dynamic-eval.md)
  fix: Remove or rewrite the code at src/util.py:2 that matches /\beval\(/. ...
```

In `--format json` each violation has the old keys (`rule`, `rule_source`,
`file`, `line`, `detail`) plus `fix_hint` and `adr` (`{"number": 2, "title":
"No dynamic eval", "path": "docs/adr/0002-no-dynamic-eval.md"}`, or `null` for
a rule in `arch.yaml`); a violation of an expired exception also has
`exception_expired`.

`--format sarif` (on `check` and `drift`) writes a SARIF 2.1.0 log, the format
GitHub code scanning reads. Paths are relative to `--root`, so run it from
the repository root for GitHub:

```yaml
- run: adr-drift check --format sarif --fail-on none > adr-drift.sarif
- uses: github/codeql-action/upload-sarif@v3
  with: {sarif_file: adr-drift.sarif}
```

## CI

```yaml
- run: python3 -m adr_drift check --format json --fail-on violations
```

Or use the bundled composite action (needs Python on the runner):

```yaml
- uses: actions/checkout@v4
- uses: calasdan-sketch/adr-drift@main
  with:
    args: --adr-dir doc/adr --baseline .adr-baseline.json   # optional
```

The action runs `python -m adr_drift check <args>`, so `args` takes anything
`check` takes, including `--root` and `--adr-dir` (they are accepted after the
command name). The text is split on spaces without any quoting, so a path
with a space in it cannot be passed.

`--fail-on none` reports without failing. With no rules at all, `check`
exits 2 (nothing is enforced) unless you pass `--allow-empty` (in the action:
`args: --allow-empty`), which is meant for adopting gradually: right after
`init`, until the first rule is enabled.

`drift` needs history. `actions/checkout` fetches a single commit by default,
so give it `fetch-depth: 0` (otherwise `drift` exits 2 and says so). For a
pull request compare against where the branch left the base:
`adr-drift drift --since "$(git merge-base origin/main HEAD)"`.

### pre-commit

```yaml
repos:
  - repo: https://github.com/calasdan-sketch/adr-drift
    rev: v0.2.0
    hooks:
      - id: adr-drift
```

The hook runs `adr-drift check` on the whole repository (rules are about the
architecture, not about one file). Add `args: [--adr-dir, doc/adr]` to pass
options.

### Telling Claude Code when it breaks a rule

A `PostToolUse` hook runs after every edit; exit code 2 hands the message
back to Claude so it can fix the code. In `.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {"type": "command", "command": "adr-drift check >&2 || exit 2"}
        ]
      }
    ]
  }
}
```

`adr-drift check` exits 1 for violations, which a hook only shows to you;
`>&2 || exit 2` moves the report to stderr and turns any failure into 2.

## Limitations

- Only files with a source extension are scanned (`.py .js .jsx .ts .tsx
  .mjs .cjs .go .rs .java .rb .php`), and not inside `.git`, `node_modules`,
  `__pycache__`, `.venv`, `venv`, `dist`, `build`, `.tox` or `vendor`. A
  `forbid-pattern` on YAML, Markdown or shell files matches nothing (you get
  a warning).
- Python is exact; every other language is a heuristic extractor (see
  *Languages: exact support*): path aliases, computed paths and macros are
  not seen. In Python, `importlib.import_module()` and `__import__()` are not
  seen either; every `import` statement counts, also inside functions and
  `if TYPE_CHECKING:` blocks.
- `forbid-pattern` is matched line by line (it cannot span lines);
  `require-pattern` searches the whole file.
- A file belongs to the first layer whose paths match it.
- Pattern violations are identified by file and rule, counted: swapping one
  violation for another in the same file is not seen, and with several
  identical violations in a file `drift` knows how many are new, not always
  which.
- `drift` compares `HEAD` with the ref itself, not with the merge base, and
  ignores uncommitted edits. It needs a git repository with enough history;
  `check` works anywhere.
- Go: only the `go.mod` at the repo root is read.
- Windows: files are always read and written as UTF-8, and on a console that
  can't show the check marks (cp1252 pipes) the report falls back to
  `[ok]`/`[x]`.
- Rules are only as good as the constraints you write - `init` scaffolds
  layers from the folder layout and writes no rule for you.

## Trusted rules

`arch.yaml` and the ADR `## Constraints` sections are your configuration, and
`forbid-pattern` / `require-pattern` take regular expressions from them. A
pattern like `(a+)+$` can take for ever on a long line. When you run
`adr-drift` on pull requests from people you do not trust, run it with the
`arch.yaml` and ADRs of the base branch, not the pull request's own.

Only regular files are read: a link (to a device, or out of the repository),
a named pipe or a file over 4 MiB is reported as not checked.

## Tests

`python3 -m unittest discover -s tests` (or `python -m pytest -q`)

## License

MIT — see [LICENSE](LICENSE).
