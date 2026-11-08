# Changelog

## 0.3.0 - unreleased

Expert round 2: the rule vocabulary, import resolution and governance that
the panel (an ADR maintainer, an architecture-fitness engineer, a polyglot
monorepo engineer, a CI lead, a governance lead and a skeptical tech lead)
found missing. Benchmark on a generated seven-language corpus
(`benchmarks/`): recall 63.2% -> 100%, precision 85.8% -> 100%.

### Expert round 5 (still 0.3.0, unreleased): monorepos, chains of imports, the author line

Panel (one author played each role and ran each command, see
`docs/ROUND5.md`): a software architect, an import-linter / ArchUnit
maintainer, a tech lead running ADRs in a monorepo, a Windows path expert,
a test-methodology reviewer, and a user upgrading from the published 0.1.0.
Release version stays 0.3.0: PyPI still has only 0.1.0, and everything here
is additive to the unreleased 0.3.0.

Added
- `forbid-transitive: <from> -> <to>`: a file may not reach the target through
  any chain of imports (what import-linter's `forbidden` contract does by
  default). One violation per file, the shortest chain and the import behind
  each step in the report; the finding is "reaches `<to>`", so a baseline
  survives unrelated code lengthening the chain. `check` and `baseline`
  judge it; `drift` says on stderr that it does not.
- `Scope:` in an ADR (a line above the first `##`, or `scope:` in the front
  matter): the ADR's rules judge only those files, for a repository with one
  ADR folder per service. A scope that matches nothing is a warning; an
  absolute path, `..`, `**` alone, more than 50 globs or a line over 2 000
  characters is an error; a sentence after `Scope:` is ignored with a
  warning. `rules-diff` reports a changed scope, `coverage` shows it.
- Author: `__author__` and the SARIF `tool.driver.organization` are
  "Calas Automations Inc."; `--help` ends with it; a test pins the string in
  `pyproject.toml`, `LICENSE`, the code and the wheel's metadata.
- Tests: golden files for all five output formats (`tests/golden/`), a
  differential test of `forbid-transitive` against a plain reference
  (40 seeded graphs, plus `hypothesis` when installed), a metamorphic test
  of `Scope`, generated rule text, state files written by 0.1.0
  (`tests/fixtures/from-0.1.0`), and `benchmarks/mutants.py` (18 one-line
  mutations of the engine; all killed).

Fixed
- Windows: `--adr-dir` or an output path on another drive than the
  repository raised `ValueError` (reported as an internal error) from
  `os.path.relpath`; it is now shown as an absolute path (`adr_drift/paths.py`).
  The symbolic-link containment check compares case-folded paths.
- `--adr-dir docs\adr` (the Windows spelling) is read as `docs/adr` on every
  platform; on Linux and macOS it used to be "not found".
- Not a defect fixed but checked and recorded: files written by 0.1.0
  (baseline with absolute paths, marker-less index, ADRs) work unchanged.

### Expert round 4 (still 0.3.0, unreleased): the first hour after `pip install`

Panel (independent reviewers could not be spawned; one author played each
role and ran each command, see `docs/ROUND4.md`): a first-time user following
only the README, a packaging engineer, a supply-chain reviewer, a
hostile-input fuzzer, a Windows reader, a CI integrator. Registry check:
PyPI has 0.1.0 only, so the next release is 0.3.0 (0.2.0 was never
published); under 0.x it is a minor bump that changes behaviour, see the
notes under 0.2.0 and 0.3.0 *Changed*.

Fixed
- A full disk, a read-only directory or any other file error ended in a
  Python traceback with exit 1, which reads as "violations found". It is now
  `error: ...` and exit 2; so is an internal error (with a plain message;
  `ADR_DRIFT_DEBUG=1` restores the traceback), and Ctrl-C is 130. A report
  that could not be written (`> /dev/full`) is exit 2, not the command's
  own 0 or 1.
- `arch.yaml`, the baseline, ADRs and the index are written as UTF-8 with LF
  on every platform (Windows wrote CRLF) and replaced whole through a
  temporary file, so an interrupted run cannot leave half a file.
- `new` no longer overwrites an ADR another `new` created at the same moment,
  and a title with no letters (`???`) or 400 characters gives a sane file name.
- The package page on PyPI was empty of metadata: license (MIT), author,
  project URLs, keywords, classifiers. The version is written once
  (`adr_drift/__init__.py`), pyproject reads it.

Added
- `init --suggest` writes the rules that already hold in the code (the ones
  `suggest` prints) into the new arch.yaml, so the first `check` passes and
  enforces them.
- `tests/test_round4.py`: stdlib-only imports, version and metadata
  consistency, failed-run exit codes, atomic LF writes, `new` races.

### Expert round 3 (still 0.3.0, unreleased)

Panel: an attacker (a pull request that carries hostile rules and files), a
CI/platform engineer, an end user adopting from the README alone, a
governance auditor, a performance engineer and a competitor's engineer
(import-linter / Tach). Round 2's known gaps (no picture of the layers, no
way to start from the code) were built too.

Fixed
- A pattern rule with a catastrophic regex (`(a+)+$`) hung CI for ever; a
  rule may now spend 20 s (`ADR_DRIFT_REGEX_SECONDS`) and then fails the run
  with exit 2 naming the rule. POSIX only; off on Windows.
- A regex nested thousands of groups deep crashed with a traceback and exit 1
  (which reads as "violations found"); it is now a configuration error.
- A symbolic link to a file outside the repository was read, so a pattern
  rule could probe any file of the machine; it is skipped with a warning.
- A huge source file (80 MB) ran a small runner out of memory; files over
  4 MiB (`ADR_DRIFT_MAX_FILE_BYTES`) are skipped with a warning, in `check`,
  `baseline` and `drift`.

Added
- `graph` (Mermaid, DOT, JSON), `suggest` (rules that already hold) and
  `rules-diff --since REF [--fail-on-weakened]` (rules removed, ADRs retired,
  layers narrowed, waivers added or extended), see the README.
- `engine.scan_entries` (the scan half of `engine.check`),
  `scanner.iter_source_files(root, skipped)` and `scanner.max_file_bytes()`.
- `benchmarks/hostile.py`.

Added
- Import resolution from the repository's own config (`resolve.py`):
  TypeScript/JavaScript `compilerOptions.paths` and `baseUrl` from the
  nearest tsconfig.json / jsconfig.json (JSON with comments, `extends`
  followed, an unreadable file is a warning), monorepo workspace packages
  (`workspaces` in package.json, `pnpm-workspace.yaml`), the nearest `go.mod`
  for Go (monorepos with several modules), composer `psr-4` for PHP.
- Python `importlib.import_module("m")`, `import_module("m")` and
  `__import__("m")` with a literal absolute name are imports.
- Rules: `layer-order: a > b > c`, `only-import: <layer> -> x, y`,
  `public-api: <layer> -> module[, module]`; in arch.yaml and ADRs, in
  `check`, `baseline` and `drift`, with SARIF ids and the usual warnings for
  a layer that does not exist.
- `waivers:` in arch.yaml: a rule, optional path globs, an owner, an end date
  and a reason, all required; expired waivers bring the violations back and
  name the owner; stale, soon-ending and over-a-year waivers warn.
  `ADR_DRIFT_TODAY` pins the date.
- `--format markdown` for `check` and `drift`: a pull-request comment with the
  ADR behind each broken rule (title, status, deciders, the decision), the
  offending lines and their CODEOWNERS owners; escaped, capped at 60 000
  characters.
- `adr-drift coverage [--fail-under PERCENT] [--format json]`: which ADRs in
  force enforce no rule, ADRs that mention paths that no longer exist, the
  waivers; exit 1 under the threshold or with an expired waiver.
- `check --baseline FILE --ratchet`: exit 1 while a fixed violation is still
  in the baseline.
- `--jobs N` for `check` and `baseline`: files are read in N processes (default
  one per CPU up to 8; repositories under 1 500 files use one). The output is
  the same for any N. Pattern-rule file contents are no longer kept in memory
  when no pattern rule exists.
- `benchmarks/`: a seeded multi-language corpus with ground truth
  (`gen_corpus.py`), precision/recall/speed (`bench.py`), and a comparison with
  import-linter, tach and dependency-cruiser (`compare.py`); the corpus is a
  regression test (`tests/test_benchmark.py`).

Changed
- A layer glob with a wildcard directory in the middle or at the start
  (`src/**/db/**`, `**/db/**`) is now matched against the file an import
  names. Before, `src/**/db/**` made every module under `src` a member of the
  layer (false alarms) and `**/db/**` matched no import at all (missed
  violations). **This can add or remove violations in repositories that use
  such globs.** `drift` reads the file list at HEAD for it.
- Go imports use the nearest `go.mod`, not only the root one.
- `Loaded` gained `waivers`; parsed ADRs gained `owners`, `decision` and
  `code_refs`; `engine.check` gained `jobs`; `engine.evaluate` gained
  `repo_paths`; `engine.split_baseline` (`not_baselined` keeps working).

## 0.2.0 - unreleased

Added
- `forbid-cycles: <layers>`: the layers of `arch.yaml` whose names match
  (`*` for all) must not depend on each other in a loop. Each loop is one
  violation naming the group's shortest loop and the import behind every step (also
  in JSON, as `evidence`). Checked by `check` and `baseline`; `drift` says on
  stderr that it skips these rules, and warns when nothing else is left.
- `--format github` (for `check` and `drift`): a GitHub Actions error
  annotation on each violation's file and line, a warning annotation per
  warning, then the human report.
- `--format sarif` (for `check` and `drift`): a SARIF 2.1.0 log for GitHub
  code scanning, with stable rule ids and line-independent fingerprints.
- A pre-commit hook (`.pre-commit-hooks.yaml`, id `adr-drift`).

Changed
- An ADR whose status starts with `Superseded`, `Deprecated` or `Rejected`
  no longer has its constraints enforced; a `note:` on stderr names it and
  how many rules that drops. Its rules are still parsed (a broken one is
  still an error), and if every rule is retired, `check` exits 2 as for no
  rules. **This can turn a failing check green** where the only violations
  came from a retired ADR - which is the point, but check the notes after
  upgrading.
- Everything printed to the terminal is escaped: control characters,
  terminal escape sequences, bidi overrides and zero-width characters from
  file names, module names or ADR text appear as `\x1b` / `‮` instead of
  acting on the terminal. JSON output was already ASCII-only.
- An unquoted `*` value in `arch.yaml` now says to quote it.

### Fixed after the panel review (still 0.2.0, unreleased)

- `--format github` and `--format sarif` with `--root DIR` inside a git
  repository named files from DIR, so annotations missed the pull request's
  lines and code scanning could not place alerts; they now name them from
  the top of the repository (`git rev-parse --show-prefix`).
- A retired ADR is a `::warning` annotation on the ADR file and a SARIF
  tool notification, not only a note on stderr.
- A `Superseded` ADR with rules must name an ADR that was read and, following
  "superseded by", ends at one in force; otherwise exit 2 and the rules stay.
- The status is no longer read from inside fenced code blocks; front matter
  and document that disagree are a warning, and if only one of them retires
  the ADR it stays in force.
- SARIF no longer crashes (traceback, exit 1) on a file name that is not
  valid UTF-8: the URI is percent-encoded from the name's bytes, text fields
  escaped; a GitHub annotation names such a file in its message. A report
  that cannot be built exits 2 with an empty stdout.
- forbid-cycles reports the shortest loop of the whole group (it was the
  shortest loop through the group's first layer); groups over 500 layers
  keep the cheaper search, as documented.
- Import identities are indexed by layer path suffix and cached per module:
  with many layers `check` was O(imports x layers) (1600 layers: 6.4 s,
  now under 1 s).
- The action no longer expands `*` in `args`: the step is now
  [`action.sh`](action.sh), which splits the input into an array with
  `read -a`. `.gitattributes` keeps it LF on Windows checkouts.
- The source distribution now carries what its own tests need
  (`MANIFEST.in`: `tests/_support.py`, the fixtures, `action.yml`,
  `action.sh`, CHANGELOG, LICENSE); before, the CI job that runs the tests
  from the unpacked sdist could not import its helpers.

## 0.1.2 and earlier

See the git history.
