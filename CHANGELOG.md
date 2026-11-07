# Changelog

## 0.3.0 - unreleased

Expert round 2: the rule vocabulary, import resolution and governance that
the panel (an ADR maintainer, an architecture-fitness engineer, a polyglot
monorepo engineer, a CI lead, a governance lead and a skeptical tech lead)
found missing. Benchmark on a generated seven-language corpus
(`benchmarks/`): recall 63.2% -> 100%, precision 85.8% -> 100%.

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
