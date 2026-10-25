# Changelog

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
