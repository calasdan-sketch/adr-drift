# Changelog

## 0.2.0

- Tests only: the /dev/zero tests no longer fail where the system refuses the memory cap (macOS raises ValueError for a limit above its hard maximum).

- `--format sarif` on `check` and `drift`: a SARIF 2.1.0 log for GitHub code
  scanning and other viewers.
- Every violation says how to fix it and where the rule came from. Text
  output prints the ADR number and title and a `fix:` line; JSON gets
  `fix_hint` and `adr` (`{number, title, path}`, `null` for `arch.yaml`) next
  to the old keys.
- A `Superseded` or `Deprecated` ADR stops enforcing its constraints
  (`--verbose` says which). A status that only mentions superseding, like
  `Accepted (supersedes ADR-0002)`, still counts.
- Temporary exceptions: end a rule with `allow-until: YYYY-MM-DD`. Its
  violations are allowed through that day, then fail with a message that says
  the exception ended. An exception that has ended is warned about, also when
  it is no longer needed. `--today` sets the date for testing.
- `.pre-commit-hooks.yaml` (hook id `adr-drift`) and a README snippet for a
  Claude Code `PostToolUse` hook.
- `--verbose` on `check` and `drift`.
- Not built: a "new dependency needs an ADR" nudge. Telling a new dependency
  from a moved or renamed one means reading every ecosystem's manifest and
  lock files, and a guess there would produce false alarms.

## 0.1.4

- A report that cannot be written (`> /dev/full`, a full disk) now really
  exits 2 with one `error:` line. In 0.1.3 the last flush at the end of the
  run still failed with a traceback and exit 120. A closed pipe
  (`adr-drift check | head -1`) still keeps the verdict as the exit code.
- ADR files and `arch.yaml` are read like source files: only regular files,
  at most 1 MiB, and an out-of-memory error is reported instead of crashing.
  A link to `/dev/zero` inside `docs/adr` (or named `arch.yaml`) used to eat
  all memory, or be mistaken for "no arch.yaml".
- A link to a file inside the repository is checked as that file; a link that
  leaves the repository is still reported as "not checked".
- The release workflow only publishes a tag that sits on `main`, and the
  build also runs the tests from the unpacked source distribution.

## 0.1.3

- The package page on PyPI is complete again: licence (MIT), author, project
  links and classifiers are part of the build.
- The source distribution carries its tests and fixtures: unpack it and
  `python -m unittest discover -s tests` passes.
- A link in the repository (for example to `/dev/zero`), a named pipe, or a
  source file over 4 MiB no longer hangs or exhausts memory: it is reported
  as "not checked" and the run goes on.
- A report that cannot be written (full disk) or an unwritable stderr now
  exits 2 with one line instead of a traceback and exit 1 (1 means
  "violations found").
- `--root` pointing at something that is not a directory says so
  (`error: --root: not a directory`) instead of "no ADR files found".
- CI on Linux, Windows and macOS (Python 3.10, 3.12, 3.14) and a tagged
  release workflow publishing to PyPI.
- `pip install adr-drift`.

## 0.1.2

- A monorepo `--root` is honoured, ADRs are found in every usual directory,
  options are accepted after the subcommand, scanners run in linear time and a
  hand-written ADR README is kept.

## 0.1.1

- Rules never vanish silently, a BOM and unparsable files are reported, git
  failures exit 2, baselines ignore line numbers, more import languages.

## 0.1.0

- First release: `init`, `check`, `baseline`, `drift`, `new` and `index`.
