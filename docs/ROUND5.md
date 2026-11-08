# Round 5: chains of imports, monorepo scope, the author line

Date: 2026-11-08. Branch `expert-2`. Nothing was published, uploaded, tagged or
pushed. Network use: read-only `pip index versions`, `pip download` of
`adr-drift==0.1.0`, `import-linter` and `tach` (only to read them), and
installing `build`, `twine`, `hypothesis`, `mypy` into a scratch environment.

## How this was done, honestly

Sub-agents could not be spawned in this session, so **one author played each
role in sequence and ran the commands**; every finding below names the
command or test that proved it. These are one careful reviewer's angles, not
independent minds.

## Author line

`Calas Automations Inc.` everywhere an author or owner is named: `pyproject.toml`
`authors` (name only, no email: none existed), `LICENSE`, `adr_drift.__author__`,
SARIF `tool.driver.organization`, the end of `--help`. `--version` is left as
`adr-drift 0.3.0` on purpose (scripts parse it). No `package.json` exists. A
re-grep of the tree (code, tests, docs, bench, CI, fixtures) found no other
owner label; the GitHub URLs (`calasdan-sketch/adr-drift`) are real addresses
and were left alone. Pinned by `tests/test_round5.py::Author`. Proof from the
built wheel: `unzip -p dist/adr_drift-0.3.0-py3-none-any.whl '*METADATA'`
prints `Author: Calas Automations Inc.` and `License-Expression: MIT`;
`twine check --strict` passes on both files. Whether the GitHub repository is
public could not be checked from here (the API refused); the URLs stay in the
metadata and the checklist asks Dan to confirm it.

## Specialists and what each found

| Role | What they ran | Finding | Fixed |
|---|---|---|---|
| Software architect | A three-layer repo (handlers, services, db) where handlers reach db only through services, `forbid-import: handlers -> db`, `check` (`tests/test_round5.py::Transitive::test_forbid_import_alone_does_not_see_it`) | The most common real violation, `A -> B -> C`, passes: `forbid-import` sees direct imports only. | `forbid-transitive` |
| import-linter / ArchUnit maintainer | Downloaded import-linter 2.16 and read `importlinter/contracts/forbidden.py`: `allow_indirect_imports` defaults to false, so its forbidden contract already catches chains; `ignore_imports` exists; contract kinds `independence`, `protected`, `acyclic_siblings`, `layers` | adr-drift's main gap against its strongest Python competitor is indirect imports. ArchUnit's slices and naming rules, and import-linter's `ignore_imports`, are not matched. | chains built; the rest recorded as NOT-DONE |
| Tech lead, ADRs in a monorepo | `/services/api/adr` and `/services/web/adr`, layer globs `services/*/handlers/**`, `check --adr-dir ... --adr-dir ...` | An ADR written for the API service also failed the web service's files, because rules are global. | `Scope:` (README "One ADR per service") |
| Windows path expert (static; **Windows never run**) | Read every `relpath`, `startswith`, `join`; ran `new` with `con`, `nul.`, `AUX `, `a/b\c:d*e?`; ran `--adr-dir 'docs\adr'` on Linux | (1) `relpath` raises `ValueError` across drives (round 4 left it open); (2) the symlink containment test compares case-sensitively; (3) `--adr-dir docs\adr` was "not found" off Windows. Reserved names are safe (numbered file names). Path length over 260 is an `OSError`, which is already exit 2 with a plain message. | (1), (2), (3) fixed |
| Test-methodology reviewer | Differential test against a 15-line reference over 40 seeded import graphs, then with `hypothesis` (60 derandomised examples); metamorphic test (scoped result equals unscoped result filtered); 4 000 random rule lines; `benchmarks/mutants.py` | Mutation run 1: 5 survivors out of 23. One was a real gap (scope globs ANDed instead of ORed was not caught), four were equivalent code (a second guard for something already guaranteed). Hostile run: a `Scope:` of 60 globs was **silently cut to 50**, and a line over 2 000 characters silently cut, which would shrink an ADR's scope without a word. | test added; cut replaced by an error; equivalent mutants documented in the file |
| Upgrader from the published 0.1.0 | `pip download adr-drift==0.1.0 --no-deps`, installed in a scratch venv, built a repo with it (init, new, check, baseline, index), then ran this tree on the same files (`tests/fixtures/from-0.1.0`, `tests/test_round5.py::UpgradeFromPublished`) | Baseline from 0.1.0 (absolute paths and other line numbers in `rule_source`) still grandfathers every violation, `--ratchet` passes; the marker-less index is recognised and regenerated with the marker; JSON shape is the same array. Nothing to migrate. | none needed; pinned by a test |
| Adversarial re-reader (pass B) | Re-read the diff; `baseline` then added an unrelated import | **My own design flaw**: the violation text said `reaches src/db/conn.py through 2 imports`, which is also its identity, so a baseline broke when unrelated code shortened the chain. | detail is now `reaches <to>`; the file and length are the first evidence line |
| Hostile input | 6 000-file import chain; 3 000 files with 20 imports each; a 330 KB `Scope:` line; ESC and bidi characters in a scope and in a file name | Chain report would have been O(n^2) in size and time: now the first 12 steps (`... and 5988 more imports`), 3 s for 6 000 files on a loaded machine. Escapes are printed escaped. | capped; test added |

## Numbers

- Tests before: 553 passed, 182 subtests (pytest). After: 590 passed and 1
  skipped (the `hypothesis` test, which runs when that module is installed:
  591 passed in a scratch venv), 234 subtests; `python -m unittest discover
  -s tests` on the unpacked sdist: 590 tests OK on Python 3.11, 3.12 and 3.13
  (3.10 and 3.14 not available here).
- New files: `tests/test_round5.py` (36 tests), `tests/test_golden.py`
  (5 golden files), `benchmarks/mutants.py`, `adr_drift/paths.py`.
- Benchmark: detection 497 / 497, 0 false alarms, unchanged. Speed on
  19 998 files, round 4 tree vs this tree back to back on a machine with load
  average 8-14: 6.9 s and 5.7 s vs 5.6 s and 4.8 s; "no slower" is all this
  supports. See `benchmarks/README.md`.
- Mutation run: 18 mutants, 18 killed.
- Lint: `ruff --select F` is clean on the package; `mypy adr_drift` reports
  the same pre-existing class of errors as round 4 (19 before; the one added by
  this round was fixed, so no new ones).

## The 20+ ideas, scored

Scores 1-5 (Value, Diff = differentiation from import-linter / Tach /
ArchUnit / dependency-cruiser, Effort and Risk: lower is cheaper/safer).
Rows 14-23 of round 4's NOT-DONE list are carried over (numbered R4-n).

| # | Idea | Value | Diff | Effort | Risk | Status |
|---|---|---|---|---|---|---|
| 1 | `forbid-transitive`: chains of imports | 5 | 3 | 3 | 2 | DONE |
| 2 | ADR `Scope:` for monorepos | 5 | 5 | 2 | 2 | DONE |
| 3 | Warn when a scope matches nothing; errors for `..`, absolute, `**`, too many | 4 | 4 | 1 | 1 | DONE |
| 4 | `rules-diff` and `coverage` show scope | 3 | 4 | 1 | 1 | DONE |
| 5 | Golden files for all five formats | 3 | 1 | 2 | 1 | DONE |
| 6 | Differential + hypothesis test for the new rule | 4 | 1 | 2 | 1 | DONE |
| 7 | Mutation sanity script | 3 | 1 | 2 | 1 | DONE |
| 8 | Upgrade-path test from 0.1.0 state files | 4 | 1 | 1 | 1 | DONE |
| 9 | Author line pinned in metadata, SARIF, help | 3 | 1 | 1 | 1 | DONE |
| 10 | Cross-drive `relpath` on Windows (R4-17) | 2 | 1 | 2 | 2 | DONE |
| 11 | Case-folded symlink containment | 2 | 1 | 1 | 1 | DONE |
| 12 | `--adr-dir docs\adr` everywhere | 2 | 1 | 1 | 1 | DONE |
| 13 | Capped, deterministic chain evidence | 3 | 1 | 1 | 1 | DONE |
| 14 | `ignore_imports`-style exceptions on a rule (an allowed import, with a reason) | 4 | 2 | 3 | 3 | NOT-DONE (waivers cover files, not single imports; design needs care) |
| 15 | `independence` / `protected` contract kinds (import-linter) | 3 | 1 | 3 | 2 | NOT-DONE (`only-import` / `public-api` cover most uses) |
| 16 | ArchUnit-style naming/slice rules (`forbid-cycles` between slices of a folder, without layers) | 4 | 3 | 4 | 3 | NOT-DONE |
| 17 | Nested `arch.yaml` per service (rules and layers relative to the folder) | 4 | 4 | 5 | 4 | NOT-DONE (`Scope:` solves the ADR half) |
| 18 | `Scope:` for `arch.yaml` rules (a `scope:` key per rule) | 3 | 3 | 2 | 2 | NOT-DONE |
| 19 | `drift` support for `forbid-transitive` (re-read the importers of changed files) | 3 | 3 | 4 | 3 | NOT-DONE |
| 20 | `doctor`: why does this layer match nothing? (R4-16) | 4 | 3 | 3 | 1 | NOT-DONE |
| 21 | Windows-safe catastrophic-regex guard (R4-14) | 3 | 3 | 4 | 3 | NOT-DONE |
| 22 | `py.typed` with a clean mypy (R4-15) | 3 | 2 | 3 | 2 | NOT-DONE (mypy still reports errors) |
| 23 | De-duplicate a rule stated in both `arch.yaml` and an ADR (R4-19) | 2 | 1 | 3 | 3 | NOT-DONE |
| 24 | Trusted publishing and reproducible builds (R4-21) | 3 | 1 | 2 | 2 | NOT-DONE (needs Dan's PyPI settings) |
| 25 | GitHub Action input for `scope`-aware PR comment (group by service) | 2 | 3 | 3 | 2 | NOT-DONE |
| 26 | Re-fetch Tach's docs for the feature table (R4-23) | 2 | 1 | 2 | 1 | NOT-DONE (blocked: the wheel carries no docs; marked unverified) |
| 27 | Python 3.14 classifier (R4-18) | 1 | 1 | 1 | 1 | NOT-DONE (CI lists 3.14; not run here) |

## Competitors, read-only

- import-linter 2.16 (the current PyPI release): read from the downloaded
  wheel's source. Verified: forbidden contracts catch indirect imports by
  default (`allow_indirect_imports`, default false); `ignore_imports` exists;
  contract modules `acyclic_siblings`, `forbidden`, `independence`, `layers`,
  `protected`. README table updated for the first.
- Tach 0.35.3: wheel downloaded, but it carries no documentation, so no new
  claim was made. **Unverified** this round.

## What I could not test

- **Windows and macOS, at all.** Static review plus mocks only (a faked
  `ValueError` from `relpath`, a faked `normcase`). The CI matrix lists them;
  nobody has seen it pass.
- Python 3.10 and 3.14 (3.11, 3.12 and 3.13 were run).
- Independent reviewers.
- Whether the GitHub repository is public (the API refused access).
- `forbid-transitive` on real repositories: it was run on generated graphs,
  a three-layer fixture and two stress shapes, not on a codebase you did not
  generate. The resolver follows only unambiguous names; how many imports it
  skips in a large real repository is unknown (no counter is shown yet).
- Publishing: nothing was uploaded, so the PyPI page rendering is untested.
