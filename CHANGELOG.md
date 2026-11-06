# Changelog

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
