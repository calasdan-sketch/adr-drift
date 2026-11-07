# Round 4: the first hour after `pip install`

Date: 2026-11-07. Branch `expert-2`. Nothing was published, uploaded, tagged
or pushed. Network use: read-only `pip index versions` (registry state) and
installing `build`/`twine` into a scratch environment.

## How this was done, honestly

Independent reviewers (sub-agents) could not be spawned in this session: the
tool set had no way to start one. So **one author played every role below and
ran every command**; the roles are different angles, not different minds.
Treat the findings as one careful reviewer's, not a panel's. The panel
format is useful for the commands each role forced me to run, not for
independence.

Everything below was run from a wheel/sdist built in a scratch directory and
installed into fresh virtual environments (not editable), Python 3.10, 3.12
and 3.13. Python 3.9 correctly refuses the wheel (`requires a different
Python: 3.9.25 not in '>=3.10'`).

## Specialists and what each found

| Role | What they ran | Finding | Fixed |
|---|---|---|---|
| First-time user (README only, clean venv, path with a space) | `pip install` of the wheel, then `init`, `suggest`, `check`, `baseline`, `check --baseline --ratchet`, `coverage`, `drift`, `rules-diff`, `graph`, `new`, `index`, `check --format markdown` from `/tmp/.../qs dir` | Everything ran. Friction: after `init` the user must copy rules from `suggest` by hand before the first useful `check`; `check` right after `init` is exit 2. | `init --suggest` |
| Release / packaging engineer | `python -m build`, `twine check --strict`, `unzip -l`, `tar tzf`, `unzip -p ... METADATA` | Wheel had **no license, author, URLs, keywords or classifiers** (registry page would be bare); version written in two places; no setuptools floor for SPDX license. sdist and wheel contain no tests-in-wheel, secrets, personal paths or junk (checked). Wheel has no `py.typed` and none was added: `mypy adr_drift` reports 19 errors, so a typed marker would be a false promise. | metadata, single-source version (`dynamic`), `setuptools>=77`, tests that pin all of it |
| Supply-chain reviewer | AST scan of every import in `adr_drift/`; `pip list` in the clean venv; grep for personal paths and key-like strings | Zero runtime dependencies confirmed (`pip list` shows only the package). No network use, no `shell=True`, no `eval`/`exec`. git is called with a list, never a shell. | `test_no_third_party_import` keeps it true |
| Hostile-input fuzzer | 50 MB ADR with 20 000 rules; 300-deep directories; non-UTF-8 file name; `arch.yaml` with a UTF-16 BOM; 3 MB random `.py`; `--root` that is a file; `drift` outside git; stdout to `/dev/full` | All safe except the last: **traceback and exit 1** (which means "violations found"). Same class for any `OSError` (read-only directory, permission, vanished file) and any internal bug. | exit 2 + plain message; 130 for Ctrl-C; `ADR_DRIFT_DEBUG=1` for the traceback |
| Windows reader (static; **not run on Windows**) | Read every `open`, `os.replace`, `signal`, `subprocess`, `os.sep`, multiprocessing use | (1) text-mode writes produced CRLF `arch.yaml`/ADRs; (2) `--jobs` above 61 makes `ProcessPoolExecutor` raise `ValueError` on Windows; (3) if `concurrent.futures` failed to import, the `except` clause itself raised; (4) catastrophic-regex guard is off on Windows (already documented); (5) `os.path.relpath` raises `ValueError` across drives (`--adr-dir D:\...` with root on `C:`) - not fixed, now reported as an internal error with exit 2 instead of a traceback. | (1), (2), (3) fixed; (4), (5) open |
| CI integrator | Read `action.yml`, `action.sh`, `ci.yml`, `.pre-commit-hooks.yaml`; ran every exit code path | Exit codes are now unambiguous: 0 pass, 1 violations, 2 anything that stopped the run, 130 interrupt. A truncated `--format json` (full disk) used to look like a pass or a fail; now it is 2. CI already builds, runs `twine check --strict` and the sdist's own tests. | covered by tests |
| Performance engineer | `benchmarks/bench.py` old (55d67a8) vs new, back to back | The machine was shared: load average 16-17 on 2 CPUs while measuring, so absolute seconds are meaningless (6-17 s for a run that is 1.9 s on a quiet machine). Detection numbers are exact and unchanged. | none needed |
| Competitor's maintainer (read-only) | `pip index versions` for import-linter, tach, archunitpython, pytestarch | Current registry versions: import-linter 2.16, tach 0.35.3 (the versions the README table was measured against), archunitpython 1.8.1, pytestarch 5.1.0. Feature claims in the README table were **not** re-fetched this round (marked unverified for round 4); they were sourced on 2026-11-06/07. Their real edge remains: more contract kinds, speed (tach), real graph viewers. | - |

## Registry vs repo

- PyPI: `adr-drift` **0.1.0 only** (`pip index versions adr-drift`, 2026-11-07).
- Repo: 0.3.0 (0.2.0 was never published; its notes live under 0.3.0).
- Recommended next release: **0.3.0**. It is a minor bump in 0.x but it
  *does* change behaviour for existing users (retired ADRs stop being
  enforced; layer globs with a wildcard directory match differently), so it
  is breaking in spirit. Both are in the CHANGELOG under *Changed* with a
  warning. Do not publish as a patch release.

## The 20+ ideas, scored

Scores 1-5. Value = user value, Diff = differentiation from import-linter /
tach / dependency-cruiser, Effort and Risk: lower is cheaper/safer.
Priority is by value and diff over effort and risk; DONE means built,
tested and in the CHANGELOG this round.

| # | Idea | Value | Diff | Effort | Risk | Status |
|---|---|---|---|---|---|---|
| 1 | Complete PyPI metadata (license, author, URLs, keywords, classifiers) | 5 | 1 | 1 | 1 | DONE |
| 2 | Single-source version, pyproject reads `__version__` | 3 | 1 | 1 | 1 | DONE |
| 3 | Every file/IO error is exit 2 with a plain message, never a traceback + exit 1 | 5 | 2 | 2 | 1 | DONE |
| 4 | Internal error caught: exit 2, plain message, `ADR_DRIFT_DEBUG=1` for the traceback | 4 | 1 | 1 | 2 | DONE |
| 5 | A report that could not be written is exit 2 (full disk under `> report`) | 4 | 1 | 2 | 1 | DONE |
| 6 | Ctrl-C is 130 without a traceback | 2 | 1 | 1 | 1 | DONE |
| 7 | Atomic, LF-only writes for arch.yaml, baseline, ADRs, index | 4 | 1 | 2 | 2 | DONE |
| 8 | `new` cannot overwrite a concurrent `new`; sane slug for `???` / long titles | 3 | 1 | 1 | 1 | DONE |
| 9 | `init --suggest`: first `check` passes and already enforces proven rules | 5 | 5 | 2 | 2 | DONE |
| 10 | Windows: cap `--jobs` at 61; no `except` that can itself fail | 2 | 1 | 1 | 1 | DONE |
| 11 | Test that the package imports only the standard library | 4 | 3 | 1 | 1 | DONE |
| 12 | Test that version, CHANGELOG and metadata agree | 3 | 1 | 1 | 1 | DONE |
| 13 | Honest README: PyPI has 0.1.0, which command needs which version | 4 | 1 | 1 | 1 | DONE |
| 14 | Windows-safe catastrophic-regex guard (run pattern rules in a killable subprocess) | 3 | 3 | 4 | 3 | NOT-DONE (needs a worker process design; the hang on Windows is documented) |
| 15 | `py.typed` + clean `mypy` (19 errors today) | 3 | 2 | 3 | 2 | NOT-DONE (would be a false promise until mypy is clean) |
| 16 | `doctor` command: why does my layer match zero files? | 4 | 3 | 3 | 1 | NOT-DONE (warnings already name the unmatched globs; a doctor is a round-5 candidate) |
| 17 | Wrap `os.path.relpath` for cross-drive paths on Windows | 2 | 1 | 2 | 2 | NOT-DONE (rare; now exit 2 not a traceback) |
| 18 | Add a Python 3.14 classifier | 1 | 1 | 1 | 1 | NOT-DONE (CI lists 3.14 but it was not run here: unverified) |
| 19 | De-duplicate identical rules stated in `arch.yaml` and an ADR (the README example shows one violation twice) | 2 | 1 | 3 | 3 | NOT-DONE (reporting both sources is deliberate; revisit with users) |
| 20 | Shell completion scripts | 1 | 1 | 2 | 1 | NOT-DONE (low value) |
| 21 | Reproducible builds (`SOURCE_DATE_EPOCH`) and a signed release (trusted publishing) | 3 | 1 | 2 | 2 | NOT-DONE (the checklist tells Dan to use PyPI trusted publishing; not wired here) |
| 22 | A published GitHub release / tag for each version | 2 | 1 | 1 | 1 | NOT-DONE (forbidden this round: no tags) |
| 23 | Re-fetch competitor docs for the feature table | 2 | 1 | 2 | 1 | NOT-DONE (versions re-checked, features unverified this round) |

## Tests and numbers

- Before round 4: 534 passed (pytest), 182 subtests.
- After: 553 passed (pytest), 182 subtests, 41 s; new file `tests/test_round4.py` (19 tests);
  two existing tests were changed on purpose (the version test now reads the
  single source; a non-pipe flush failure is exit 2 instead of an exception).
- Benchmark (recall/precision on the generated corpus): unchanged,
  497 / 497 and 0 false alarms before and after. Speed was measured on a
  machine with a load average of 16 on 2 CPUs, shared with other jobs, so the
  seconds are not comparable with the committed 1.9 s; see
  `benchmarks/README.md` for the figures and what they are worth.

## What I could not test

- **Windows, at all** (static review only). macOS also not run.
- Python 3.11 and 3.14 (not installed here; 3.10, 3.12 and 3.13 were).
- The npm/Node side does not exist for this package.
- Publishing: no upload, no TestPyPI. Whether the PyPI page renders the
  README is only checked with `twine check --strict`.
- A real read-only file system or permission-denied case: the session runs
  as root, so permission errors were simulated by patching `os.replace` and
  with `/dev/full`.
- Independent reviewers (see above).
