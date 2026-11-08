# ADR Drift

**Your architecture decisions, checked on every pull request.** Write the
rule in the ADR itself (`forbid-import: handlers -> db`), and `adr-drift
check` fails CI when code drifts from it - in Python, JS/TS, Go, Rust, Java,
Ruby and PHP, with no dependencies beyond the Python standard library.

- Rules live in your ADRs (`## Constraints`) or in `arch.yaml`; a superseded,
  deprecated or rejected ADR stops being enforced, and says so.
- Layer imports (direct, or through any chain of imports with
  `forbid-transitive`), layer order, allow-lists, public-API boundaries,
  forbidden or required code patterns, and dependency cycles between layers.
- One repository, many services: an ADR can say `Scope: services/api/**` and
  its rules then judge only those files.
- Resolves what the import statement does not say: tsconfig `paths` and
  `baseUrl`, monorepo workspace packages, the nearest `go.mod`, composer
  PSR-4 namespaces, `importlib.import_module("literal")`.
- Fails closed: a rule that does not parse, a glob that matches nothing or a
  repo with no rules is an error or a warning, never a silent pass.
- Grandfather legacy violations (`baseline`, with a `--ratchet` so the
  baseline only shrinks), check only what a pull request introduced
  (`drift`), or waive a rule for a path with an owner and an end date.
- CI-native output: GitHub annotations on the offending line
  (`--format github`), SARIF for code scanning (`--format sarif`), JSON, and
  a pull-request comment (`--format markdown`) that quotes the ADR behind
  each broken rule, who decided it and who owns the files.
- `adr-drift coverage` lists the ADRs that enforce nothing.

```console
$ adr-drift --root tests/fixtures/shop check      # the example repo in this repository
✗ 4 architecture violation(s):

  src/handlers/orders.py
    src/handlers/orders.py: missing /authenticate\(/
      rule: src/handlers/**.py must contain /authenticate\(/ (docs/adr/0001-layered-architecture.md)
    src/handlers/orders.py:1: imports db.drivers
      rule: handlers must not import db.drivers (arch.yaml)
    src/handlers/orders.py:1: imports db.drivers
      rule: handlers must not import db.drivers (docs/adr/0001-layered-architecture.md)
  src/util.py
    src/util.py:2: matches /\beval\(/
      rule: src/**.py must not contain /\beval\(/ (docs/adr/0002-no-dynamic-eval.md)
$ echo $?
1
```

## The problem

> "The moment an ADR gets merged, it decays into a passive historical record
> unless something mechanical forces it to stay alive."

Developers — and now AI coding agents — merge boundary violations because
nothing checks decisions against code. ArchUnit is Java-only, deptrac is
PHP-only. ADR Drift is language-agnostic, repo-local, and CI-friendly.

## Install

```bash
pip install git+https://github.com/calasdan-sketch/adr-drift   # 0.3.0, this README
pip install adr-drift                                          # PyPI
```

Either provides `adr-drift`; Python 3.10+ and nothing else (no
dependencies). Or run without installing: `python3 -m adr_drift` from the
repo root.

PyPI had only **0.1.0** when this was written (2026-11-07, `pip index
versions adr-drift`). This README describes 0.3.0 (see
[CHANGELOG.md](CHANGELOG.md)): until 0.3.0 is on PyPI, everything newer than
0.1.0 - cycles, `--format github|sarif|markdown`, retired ADRs, the layer
rules, waivers, `coverage`, alias resolution, `suggest` - needs the first
line. `adr-drift --version` tells you which one you have.

## Quick start

```bash
cd your-repo
python3 -m adr_drift init --suggest   # arch.yaml + sample ADR from the folder layout,
                                      # with the rules your code already follows
# add your own rule in arch.yaml and/or a ## Constraints section in an ADR
python3 -m adr_drift check     # verify; exit 1 on violations (CI-ready)
# repo already has legacy violations? grandfather them, then enforce:
python3 -m adr_drift baseline                    # record current violations
python3 -m adr_drift check --baseline .adr-baseline.json
```

## Commands

| Command | What it does |
|---|---|
| `init` | Infer layers from folder layout, write `arch.yaml` + sample ADR + index (a `README.md` of yours is kept); `--suggest` also writes the rules that already hold in the code (what `suggest` prints), so the first `check` passes and enforces them |
| `check` | Verify the repo against `arch.yaml` + every ADR's `## Constraints` (`--baseline FILE` grandfathers recorded violations; `--strict` fails on warnings; `--allow-empty` accepts a repo with no rules; `--ratchet` also fails when a baselined violation was fixed; `--jobs N` reads files in N processes) |
| `baseline` | Write current violations to `.adr-baseline.json` (default) for `check --baseline` (violations covered by a live waiver are left out) |
| `coverage` | Which ADRs in force enforce a rule, which name paths that no longer exist, and the waivers (`--fail-under PERCENT`; exit 1 on an expired waiver) |
| `drift --since HEAD~5` | Report violations *introduced* between a git ref and `HEAD`, plus violations *fixed* in that range (pre-existing ones ignored) |
| `graph` | Draw how the layers import each other: Mermaid (default, pastes into a pull request or README), Graphviz DOT or JSON, with the number of imports on each edge and layer loops in red |
| `suggest` | Print rules that already hold in the code today (`forbid-cycles: *`, a `layer-order` in the order the code follows) for you to review and paste; prints nothing it cannot back, and a note when the layers already loop. Never writes a file |
| `rules-diff --since REF` | How the *rules* changed between `REF` (default `HEAD~1`) and `HEAD`, from the committed files: rules removed or added, ADRs retired, layers that lost paths, waivers added or extended. `--fail-on-weakened` exits 1 if anything weakened |
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
forbid-cycles: *                          # no two layers depend on each other in a loop
forbid-transitive: handlers -> db         # ... not even through services: any chain of imports
layer-order: handlers > services > db     # a layer imports only layers to its right
only-import: util -> util                 # util may import no other layer of this repo
public-api: db -> db.api, db.models       # outside db, import only these modules
```

`forbid-transitive: <from> -> <to>` is `forbid-import` for chains: a file of
`<from>` must not reach a file of `<to>` through imports of other files of
the repository, however many. One violation per file, naming the shortest
chain and the import behind each step (the first 12 steps; a chain of 6 000
costs one extra line). The finding itself is "this file reaches `<to>`", so a
baseline does not break when unrelated code makes the chain longer:

```
  src/handlers/h.py
    src/handlers/h.py:1: reaches db
      ends in src/db/conn.py after 2 imports
      src/handlers/h.py:1 imports src.services.svc
      src/services/svc.py:1 imports src.db.conn
      rule: handlers must not reach db through imports (arch.yaml)
```

An import is followed only when its name picks out exactly one file or
package of the repository. A name that fits several files (`helper` in two
services) is not followed (so a chain is never invented, but one can be
missed: write the full path in the import or use `forbid-import` on the layer
too), a Python name that is also a standard-library module (`json`) is not
taken for a repo file `util/json.py`, and a library is not a file of the
repository. It reads the same imports `forbid-import` does, so the language
limits below apply. `check` and `baseline` judge it, `drift` does not (the
chain can run through files that did not change; it says so on stderr).
It is the thing import-linter does by default for its `forbidden`
contracts (they catch indirect imports unless `allow_indirect_imports` is
set; read from the 2.16 package source, 2026-11-07).

`forbid-cycles: <layers>` covers the layers of `arch.yaml` whose name matches
`<layers>` (`*` for all, `core*`, one name...). A layer depends on another
when one of its files imports a module of the other (resolved as for
`forbid-import`). Each group of layers caught in a loop is one violation that
names the group's shortest loop (searched from every layer of the group; in
a group of more than 500 layers, the shortest loop through its
alphabetically first layer) and the import behind each step:

```
  src/a/m.py
    src/a/m.py:1: layer cycle a -> b -> c -> a
      a -> b: src/a/m.py:1 imports src.b
      b -> c: src/b/x.py:1 imports src.c.y
      c -> a: src/c/y.py:1 imports src.a
      rule: layers * must not depend on each other in a cycle (arch.yaml)
```

In `arch.yaml` quote it (`- "forbid-cycles: *"`): unquoted, YAML reads `*` as
an alias and adr-drift says so. A pattern that covers fewer than two layers
is a warning.

`layer-order: a > b > c` lists layers highest first; importing a layer that
is higher than the file's own is a violation (skipping a level is fine, and
layers not in the list are free). `only-import: <layer> -> x, y` is an
allow-list over the layers of `arch.yaml`: the layer may import its own
files and layers `x` and `y`; libraries and the standard library are not
restricted (use `forbid-import` for those). `public-api: <layer> -> m1, m2`
says code outside the layer may import only the named modules of it (and
`from db import api` counts as importing `db.api`). Each takes a layer
name that must exist in `arch.yaml` (a misspelt one is a warning, and
`--strict` makes it an error). They work in `check`, `baseline` and `drift`.

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

A layer glob with a wildcard directory in the middle (`src/**/db/**`) or at
the start (`**/db/**`) is matched against the file the import names, not as
a prefix: an import counts as in the layer only when it names a file or
directory of the repository that the glob covers (so `github.com/other/lib/db`
is not in `**/db/**`). Before 0.3.0 such a glob made every module under its
literal prefix a member and `**/db/**` matched nothing.

### Waivers: an exception with an owner and an end date

A baseline hides a violation for ever. A waiver hides it until a date:

```yaml
# arch.yaml
waivers:
  - rule: "handlers must not import db"      # exactly as `check` prints it
    paths: ["src/handlers/legacy/**"]        # optional; default: every file
    owner: alice
    expires: 2027-03-31                      # the last day it applies
    reason: "billing migration, ticket 123"
```

`rule`, `owner`, `expires` and `reason` are required, `expires` must be a
real date, and a `rule` that no enforced rule says is an error (exit 2),
so a typo cannot waive nothing and look like a waiver. Until the end date
the covered violations are left out of the report (a `note:` on stderr
says how many and whose). After it they are reported again and a warning
names the owner; a waiver that matches no violation (the code was fixed) is
reported as stale, one that ends within 14 days, and one that runs more
than a year, are called out - `--strict` turns each warning into exit 2.
`adr-drift coverage` lists all waivers. Waivers live in `arch.yaml`, so put
`arch.yaml` under CODEOWNERS: anyone who can edit it can waive a rule.
`ADR_DRIFT_TODAY=2027-01-01` pins "today" (for tests and replays).

### The ratchet

`adr-drift check --baseline .adr-baseline.json --ratchet` also fails (exit 1)
when a violation listed in the baseline no longer exists: run `adr-drift
baseline`, commit it, and the fixed violation cannot silently return. The
count of grandfathered violations can then only go down.

### Which ADRs enforce anything

```console
$ adr-drift coverage
1 of 2 ADRs in force enforce at least one rule (50%)
  ✓ docs/adr/0001-layers.md  accepted  1 rule  [Alice, Bob]
  ✗ docs/adr/0002-no-secrets.md  Accepted  0 rules
      nothing checks this decision: add a ## Constraints section
      hint: the decision reads like a rule ('must not', 'never'...); ...
      mentions config/vault, which does not exist
```

A decision that is in force (anything but superseded, deprecated or
rejected) with no rule is a decision nothing checks. `--fail-under 80` makes
that a CI gate; `--format json` is for dashboards. Owners come from the ADR
(`deciders:` / `owner:` in the front matter, or an `Owner:` / `Deciders:` line
under the title). The hint is a keyword heuristic (`must not`, `never`...),
not a judgement. "Mentions ... which does not exist" is any `` `path/like/this` ``
in the ADR that is not in the repository: a decision pointing at code that
moved.

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

### One ADR per service: `Scope`

In a repository with several services, an ADR kept in `services/api/adr/`
usually means the API service. Say so and its rules judge only those files:

```markdown
# 7. API handlers never touch the database

Status: Accepted
Scope: services/api/**, libs/api-common/**

## Constraints

- forbid-import: handlers -> db
```

`Scope:` is a line above the first `##` heading, or `scope:` in the front
matter (`scope: [a/**, b/**]` or a list). The globs are relative to the
repository root and are alternatives. Without `Scope` the rules apply
everywhere, as before. The scope narrows the files a rule *judges*; what a
rule looks through (the imports of other files, for `forbid-transitive`) is
still the whole repository. Because a scope that matches nothing would make
the rule enforce nothing and look green, it is a warning (`--strict`: exit
2); an absolute path, `..`, `**` alone, more than 50 globs or a line over
2 000 characters is an error; a `Scope:` line that reads like a sentence
(it has spaces) is ignored with a warning, so prose never switches
enforcement off. `rules-diff` reports a changed scope as the old rule gone
and a new one added, and `coverage` shows each ADR's scope. A waiver names
a rule by its text, which the scope does not change.

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
as a dash. Fenced code blocks (```` ``` ```` or `~~~`) are never read for
the title or the status, so an example `## Status` inside one is not the
ADR's status. When the front matter and the document give different
statuses, that is a warning; if one of them retires the ADR and the other
does not, the ADR stays in force.

### Retired ADRs are not enforced

A decision that was superseded, deprecated or rejected must not keep failing
CI. When the first word of an ADR's status is `Superseded`, `Deprecated` or
`Rejected` (any case, any Markdown around it: `Superseded by [5](0005-x.md)`,
`**Rejected**`), its constraints are not enforced, and `check`, `baseline`
and `drift` say so on stderr:
`note: docs/adr/0002-old.md is 'Superseded by 0005': its 1 rule is not
enforced`. With `--format github` that is a `::warning` annotation on the
ADR file, and in SARIF a tool notification of level `warning`: retiring a
decision is as visible on the pull request as breaking it. Its rules are
still parsed - a broken rule is still an error - and when every rule comes
from a retired ADR, `check` exits 2 as for no rules at all. Every other
status (`Proposed`, `Accepted`, none) is enforced.

A `Superseded` ADR with rules must say what replaced it, and that must be in
force: its status names an ADR that was read, by a Markdown link to the file
(`Superseded by [5](0005-use-kafka.md)`) or by number (`Superseded by 0005`,
`by ADR-5`), and following "superseded by" from there ends at an ADR that is
not superseded, deprecated or rejected. Otherwise - no successor named, one
that was not read, a retired one, a loop - it is an error (exit 2) and the
rules stay. `Deprecated` and `Rejected` need no successor.

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

### Rules can be weakened too: `rules-diff`

`check` and `drift` judge the code. A pull request can also pass them by
changing the rules: delete one, mark its ADR `Superseded`, give a layer
narrower `paths`, add a waiver. `adr-drift rules-diff --since
"$(git merge-base origin/main HEAD)"` lists exactly those moves (it reads
`arch.yaml` and the Markdown files of the two commits out of git, not your
working tree); put it in the pull-request job next to `check`, with
`--fail-on-weakened` if a weakening should need a human to re-run the job.
It does not say whether a change is wrong, only that it is there. A rule
written in two places (arch.yaml and an ADR) is compared by its text, so
removing one of the two copies is not shown.

### Hostile repositories

The rules and the code both come from the pull request being checked, so
adr-drift treats them as untrusted:

- A pattern rule that runs longer than 20 s on the whole repository (a regex
  such as `(a+)+$` backtracks without end) stops the run with exit 2 and
  names the rule; `ADR_DRIFT_REGEX_SECONDS` changes the budget (0 = off).
  Python's `re` has no timeout of its own, so this uses a timer signal: it
  works on Linux and macOS in the main thread and **is off on Windows**,
  where such a rule still hangs.
- A regex that Python cannot compile (nested thousands of groups deep) is a
  configuration error, not a traceback.
- A source file larger than 4 MiB (`ADR_DRIFT_MAX_FILE_BYTES`, 0 = off) is
  not read, in `check`, `baseline` and `drift`: a warning, exit 2 under
  `--strict`. An 80 MB generated file used to be read whole.
- A symbolic link to a file outside the repository is not read, so a rule
  cannot be used to probe files of the CI machine; a warning says so. Links
  that stay inside the repository are read.

`python3 benchmarks/hostile.py` times these cases.

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
against the file's package; `importlib.import_module("m")`, `import_module("m")`
and `__import__("m")` with a literal absolute name count too, a computed name
or a method of some other object does not). The rest is a heuristic extractor - read what it covers:

| Language | Files | Recognised | Not recognised |
|---|---|---|---|
| JavaScript / TypeScript | `.js .jsx .ts .tsx .mjs .cjs` | `import x from "m"`, `import { a, b } from "m"` (also over several lines), `import * as ns from "m"`, `import type ... from "m"`, `import "m"`, `export ... from "m"`, `export * from "m"`, `import("m")`, `require("m")`, `import x = require("m")`; relative specifiers resolve against the file; tsconfig/jsconfig `paths` and `baseUrl` (`extends` followed), and workspace packages (`workspaces` in the root package.json, `pnpm-workspace.yaml`), resolve to their directory | bundler aliases (webpack, vite), tsconfig `references` and `extends` of a package, non-literal `import(x)` / `require(x)`, `.vue` / `.svelte` files |
| Go | `.go` | `import "m"`, aliased / `.` / `_` imports, grouped `import ( ... )`, raw-string paths; the module path of the nearest `go.mod` is replaced by that module's directory (`github.com/acme/app/internal/db` in `svc/` is `svc/internal/db`) | `go.work`, build tags |
| Java | `.java` | `import a.b.C;`, `import static a.b.C.m;`, wildcards (`a.b.*` is `a.b`) | uses of a class in the same package (there is no import), fully qualified names in code |
| Rust | `.rs` | `use a::b;`, nested trees `use a::{b::{c, d}, e as f, self}`, globs, `pub use`, `pub(crate) use`, `extern crate`, `mod x;`; `crate::`, `self::` and `super::` resolve against the file's module | macro-generated `use`, `#[path = "..."]` modules, paths used in code without a `use` |
| PHP | `.php` | `use A\B;`, `use A\{B, C as D}`, `use function` / `use const`, `require` / `include` (`_once`) of a literal path or `__DIR__ . 'path'`; composer `autoload` / `autoload-dev` `psr-4` maps a namespace to its directory | `dirname(__FILE__)`, dynamic includes, classmap / PSR-0 autoloading, a namespace spelt in a different case than its directory without a psr-4 entry |
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
| 2 | the run did not finish or its output cannot be trusted: unparsable rule, no rules, bad baseline, `--strict` with warnings, a file that cannot be read or written (full disk, read-only directory), a report that could not be written, an internal error (a bug: the message says so; `ADR_DRIFT_DEBUG=1` shows the traceback) |
| 130 | interrupted (Ctrl-C) |

stdout carries only the report. With `--format json` it is always a bare
array of violations (`[]` when clean) - or empty when the exit code is 2 -
so `adr-drift check --format json | jq` never meets prose. Every warning and
error goes to stderr as `warning: ...` / `error: ...`.

| `--format` | stdout |
|---|---|
| `human` (default) | the report above |
| `json` | a bare JSON array; each item has `rule`, `rule_source`, `file`, `line`, `detail` (and `evidence` for a cycle) |
| `github` | one GitHub Actions `::error` annotation per violation (on its file and line) and one `::warning` per warning, then the human report |
| `markdown` | a pull-request comment: for each broken rule, the ADR it comes from (title, status, who decided it, the decision in a sentence), then a table of the offending lines with their CODEOWNERS owners; at most 25 rows per rule and 60 000 characters; everything from the repository is escaped (no link, tag or @-mention can come out of an ADR) |
| `sarif` | a SARIF 2.1.0 log for GitHub code scanning; each rule gets a stable id, each result a fingerprint that ignores the line number (the same identity `check --baseline` uses) |

Everything printed for people - paths, module names, ADR statuses - comes
from the repository being checked, so it is not trusted: control characters,
terminal escape sequences, bidi overrides (U+202E) and zero-width characters
are printed as visible escapes (`\x1b`, `\u202e`), so a file name cannot
rewrite your terminal or make a line read differently from what it is. In
annotations, newlines and `%` are escaped as the Actions runner expects, so a
message cannot start a workflow command of its own. JSON and SARIF are
ASCII-only; a file name that is not valid UTF-8 is percent-encoded from its
bytes in a SARIF URI, and named (escaped) in the message of a GitHub
annotation that then has no file. A report that cannot be written at all is
an `error:` and exit 2, with nothing on stdout.

GitHub annotations and SARIF name files from the top of the git repository,
because that is what GitHub matches against the pull request: with
`--root packages/api` a violation in `src/x.py` is reported as
`packages/api/src/x.py` (found with `git rev-parse --show-prefix`; outside a
git repository nothing is added). `human` and `json` keep naming files from
`--root`.

Warnings flag rules and layers that cover nothing, which usually means a typo:
a layer whose paths match no scanned source file, a `forbid-pattern` or
`require-pattern` glob that matches no scanned file, the `from` side of a
`forbid-import` that matches no layer name or path, a `to` side that looks
like a misspelt layer, an unparsable or unreadable source file, and an ADR
directory that is missing, empty or not the only one (see *Where the ADRs
are*). Pattern rules apply to the scanned source files only
(`.py .js .jsx .ts .tsx .mjs .cjs .go .rs .java .rb .php`). `--strict` turns
any warning into exit 2.

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
command name). The step ([`action.sh`](action.sh)) gets the text through the
environment and splits it on whitespace into separate arguments, with no
quoting and no wildcard expansion (`*` reaches adr-drift as `*`), so a path
with a space in it cannot be passed.

Annotations on the pull request's lines: `args: --format github`.

Alerts in the repository's code scanning tab (the job still fails on a
violation; the upload runs anyway):

```yaml
permissions:
  contents: read
  security-events: write   # upload-sarif needs it
steps:
  - uses: actions/checkout@v4
  - run: pip install adr-drift && adr-drift check --format sarif > adr-drift.sarif
  - if: always()
    uses: github/codeql-action/upload-sarif@v3
    with:
      sarif_file: adr-drift.sarif
```

A comment on the pull request that names the ADR and the owners (needs
`pull-requests: write`; a pull request from a fork gets a read-only token,
so the comment step cannot post there; not run on GitHub by the author of
this change - treat the snippet as a starting point):

```yaml
permissions:
  contents: read
  pull-requests: write
steps:
  - uses: actions/checkout@v4
  - run: pip install adr-drift
  - id: drift
    run: adr-drift check --format markdown > drift.md
    continue-on-error: true
  - if: github.event_name == 'pull_request'
    run: gh pr comment "$PR" --body-file drift.md
    env: { GH_TOKEN: "${{ github.token }}", PR: "${{ github.event.pull_request.number }}" }
  - if: steps.drift.outcome == 'failure'
    run: exit 1
```

[pre-commit](https://pre-commit.com): this repository ships a hook.

```yaml
repos:
  - repo: https://github.com/calasdan-sketch/adr-drift
    rev: main            # better: a release tag
    hooks:
      - id: adr-drift
```

`--fail-on none` reports without failing. With no rules at all, `check`
exits 2 (nothing is enforced) unless you pass `--allow-empty` (in the action:
`args: --allow-empty`), which is meant for adopting gradually: right after
`init`, until the first rule is enabled.

`drift` needs history. `actions/checkout` fetches a single commit by default,
so give it `fetch-depth: 0` (otherwise `drift` exits 2 and says so). For a
pull request compare against where the branch left the base:
`adr-drift drift --since "$(git merge-base origin/main HEAD)"`.

## How it compares

### Measured (re-runnable)

Two benchmarks ship in [`benchmarks/`](benchmarks/) and use only the
command line.

**1. Detection on a generated polyglot corpus** (`python3
benchmarks/bench.py`): 350 handler files in seven languages (Python,
TypeScript, Go, Java, Rust, PHP, Ruby; a tsconfig alias, a `go.mod` below the
root, PSR-4, layer globs with a wildcard in the middle, ...), 497 planted
violations of one rule written in an ADR, and decoys: the same import in a
comment, in a string, `db_utils` next to `db`, an import of another module
that happens to share a name. The generator is seeded and knows nothing about
adr-drift, but I chose its categories - they are the spellings I know real
code uses, not a sample of real repositories.

| | planted found (recall) | reported lines that are real (precision) | 20 000 files, 2 CPUs |
|---|---|---|---|
| adr-drift 0.2.0 (before this round) | 63.2% (314 / 497) | 85.8% (52 false alarms) | 1.9 s, 53 MB (one process) |
| adr-drift 0.3.0 | 100% (497 / 497) | 100% (0 false alarms) | 1.9 s, 70 MB (2 processes); 2.3 s with `--jobs 1` |

The two speed figures were measured in round 2 on a quieter machine; on the
busier one used in round 3 the same 20 000 files took 3.8-6.5 s (round 2's
code 6.5-11.6 s there), so compare runs on one machine, not columns.

It is a regression gate too (`tests/test_benchmark.py`). Do not read 100% as
"finds everything": it is 100% of what the generator plants. Not planted, so
not measured: computed `import(x)`, bundler aliases, `.vue` / `.svelte`,
tsconfig `references`, macro-generated Rust.

**2. The same project, other tools** (`python3 benchmarks/compare.py`, which
skips a tool that is not installed): one valid Python project and one
TypeScript project with a tsconfig alias, 2 500 handler files each, one
violation planted in each file. 2026-11-07, import-linter 2.16 (grimp 3.19),
tach 0.35.3, dependency-cruiser 18.7.2, Python 3.13, 2 CPUs.

| tool | what it was asked | found | false alarms | missed | time |
|---|---|---|---|---|---|
| adr-drift 0.3.0 | `handlers` must not import `db` (Python + TS in one run) | 2 500 / 2 500 (Python), 2 500 / 2 500 (TS) | 0 | - | 1.3 s for both (17 500 files) |
| import-linter | forbidden contract, Python | 2 143 / 2 500 | 0 | `importlib.import_module("db.m")` | 1.0 s |
| tach | `handlers` does not depend on `db` | 1 786 / 2 500 | 0 | `importlib` calls and `if TYPE_CHECKING:` imports (tach ignores these by default; unverified that it is a documented choice) | 0.5 s |
| dependency-cruiser | forbidden rule, tsconfig alias | 2 500 / 2 500 files (reports files, not lines) | 0 | - | 4.4 s |

Read it plainly: on plain static imports these tools tie. adr-drift's edge
here is the dynamic `importlib` literal and one config for several
languages; it is **not** faster than tach, which read the 7 500 Python files in 0.5 s
while adr-drift took 1.3 s for those and the TypeScript files together
(2 CPUs, and the machine was shared: expect noise). A run on your repository, your layout and your
rules is worth more than this table.

### Features

Checked on 2026-11-06 and 2026-11-07 against each project's own pages
(linked); the cells say only what those pages say - corrections welcome.
Where those tools are stronger, the table says so.

| | adr-drift | [import-linter](https://import-linter.readthedocs.io/en/stable/contract_types/) | [Tach](https://github.com/tach-org/tach) | [dependency-cruiser](https://github.com/sverweij/dependency-cruiser) | [deptrac](https://deptrac.github.io/deptrac/formatters) |
|---|---|---|---|---|---|
| Languages | Python (exact); JS/TS, Go, Rust, Java, Ruby, PHP (heuristic) | Python | Python | JavaScript, TypeScript, CoffeeScript (+ .vue, .svelte) | PHP |
| Where the rules live | your ADRs and `arch.yaml` | its own config (INI / TOML) | `tach.toml` | its own config file | its own config (unverified) |
| Code patterns (forbid / require a regex) | yes | not on the linked page | not on the linked page | not on the linked page | not on the linked page |
| Dependency cycles | between layers | `acyclic_siblings` contract | yes | yes (`--init` adds the rule) | not on the linked page |
| Indirect imports (a to b through c) | `forbid-transitive` | `forbidden` contracts (indirect by default; `allow_indirect_imports`) | not on the linked page | not on the linked page | - |
| Layer order / allow-lists / public API | `layer-order`, `only-import`, `public-api` | layers, independence, protected contracts | interfaces, `depends_on` | `forbidden` rules by path | - |
| Other contract types | - | protected, independence | deprecated deps, ownership | orphans, missing / dev-only package.json deps | - |
| Rule waivers with owner and expiry | yes (`waivers`) | not on the linked page | not on the linked page | not on the linked page | not on the linked page |
| Grandfathering / adoption | `baseline` (+ `--ratchet`) + `drift` | not on the linked page | "incremental adoption" | not described on the README | `--formatter=baseline` |
| Reads decision records, reports which ADRs enforce nothing | yes (`coverage`) | no | no | no | no |
| CI output | human, JSON, GitHub annotations, SARIF, PR comment (Markdown) | not on the linked page | terminal, DOT, JSON | err, dot, html, mermaid, json, csv | table, GitHub Actions, JUnit, Code Climate, JSON, Graphviz, Mermaid |
| Graph pictures | `graph` (Mermaid, DOT, JSON; text only, no web viewer) | not on the linked page | yes (DOT, web) | yes (dot, mermaid, html) | yes (Graphviz, Mermaid) |
| Install footprint | Python stdlib only | Python package | Rust-built Python package | Node.js package | PHP package |

Also relevant, not compared cell by cell: [ArchUnit](https://www.archunit.org/)
(Java and Kotlin tests with the richest rule vocabulary I know of),
[pytest-archon](https://github.com/jwbargsten/pytest-archon) (architecture
rules as pytest tests), [deptry](https://github.com/fpgmaas/deptry) (checks
declared against imported third-party packages - a different problem).

What they do better: import-linter has more contract kinds and a real
import graph; Tach also checks that modules use each other's public
interface, is faster and has a VS Code extension; dependency-cruiser resolves
real module paths (webpack and tsconfig aliases) and draws the graph;
deptrac emits more report formats; ArchUnit understands the language's type
system, not just imports. adr-drift is the one that reads the decision
records themselves, spans languages in one config, checks code patterns,
and keeps waivers and the baseline honest.

## Limitations

- Only files with a source extension are scanned (`.py .js .jsx .ts .tsx
  .mjs .cjs .go .rs .java .rb .php`), and not inside `.git`, `node_modules`,
  `__pycache__`, `.venv`, `venv`, `dist`, `build`, `.tox` or `vendor`. A
  `forbid-pattern` on YAML, Markdown or shell files matches nothing (you get
  a warning).
- Python is exact; every other language is a heuristic extractor (see
  *Languages: exact support*): path aliases, computed paths and macros are
  not seen. In Python, `importlib.import_module(name)` with a computed name is not
  seen; every `import` statement counts, also inside functions and
  `if TYPE_CHECKING:` blocks.
- `forbid-pattern` is matched line by line (it cannot span lines);
  `require-pattern` searches the whole file.
- A file belongs to the first layer whose paths match it.
- Pattern violations are identified by file and rule, counted: swapping one
  violation for another in the same file is not seen, and with several
  identical violations in a file `drift` knows how many are new, not always
  which.
- `forbid-transitive` follows an import only when its name picks out one file
  or package of the repository (see its section); the shortest chain is
  reported, not every chain; it needs the whole repository, so `drift` leaves
  it to `check`. A scoped ADR (`Scope:`) judges only the files in its scope.
- `forbid-cycles` works on the layers of `arch.yaml` only (not on modules
  or packages without a layer), reports one loop per group of layers that
  depend on each other (fix it and the next one may show), and is checked by
  `check` and `baseline`, not by `drift`: a loop can run through files the
  range did not touch (`drift` says so on stderr).
- `drift` compares `HEAD` with the ref itself, not with the merge base, and
  ignores uncommitted edits. It needs a git repository with enough history;
  `check` works anywhere.
- Go: the nearest `go.mod` is read, `go.work` is not. Config files are read
  from the working tree, also by `drift` (a tsconfig changed in the range is
  not compared).
- Windows: files are always read and written as UTF-8 with LF line ends, and
  on a console that can't show the check marks (cp1252 pipes) the report
  falls back to `[ok]`/`[x]`. The test suite runs on Windows in CI
  (`.github/workflows/ci.yml`), but the author has **never run it on
  Windows**: round 4 reviewed the code for it by reading only (paths, line
  ends, `os.replace`, console encoding, signals) and fixed what that found.
- `suggest` only sees imports between the layers of `arch.yaml`; its
  `layer-order` fixes an order for layers that never import each other
  (alphabetical, and it says so), and its output describes the code today,
  not what you intend.
- `rules-diff` compares the rule texts, layers and waivers; it does not
  re-check the code, and ignores ADRs and `arch.yaml` files over 2 MiB.
- The regex time budget needs POSIX signals (see *Hostile repositories*).
  Config files read by the import resolvers (tsconfig, go.mod, composer)
  are still followed through symbolic links and not size-limited.
- Rules are only as good as the constraints you write - `init` scaffolds
  layers from the folder layout and writes no rule for you.

## Tests

`python3 -m unittest discover -s tests` (or `python -m pytest -q`)

Besides example tests: golden files for every output format
(`tests/golden/`, regenerate on purpose with `ADR_DRIFT_UPDATE_GOLDEN=1`), a
differential test of `forbid-transitive` against a plain reference
(`tests/test_round5.py`; also run with `hypothesis` when it is installed),
and `python3 benchmarks/mutants.py`, which flips one condition at a time in a
copy of the code and reports any change the tests do not notice.

`python3 benchmarks/bench.py` re-runs the detection and speed benchmark;
`python3 benchmarks/compare.py` runs the tools above on one project (see
[benchmarks/README.md](benchmarks/README.md)).

## License

MIT — see [LICENSE](LICENSE).
