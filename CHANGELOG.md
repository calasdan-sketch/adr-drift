# Changelog

## 0.2.0 - unreleased

Added
- `forbid-cycles: <layers>`: the layers of `arch.yaml` whose names match
  (`*` for all) must not depend on each other in a loop. Each loop is one
  violation naming the shortest loop and the import behind every step (also
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

## 0.1.2 and earlier

See the git history.
