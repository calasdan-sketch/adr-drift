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
git clone <repo> && cd adr-drift
pip install .   # provides the `adr-drift` console script
```

Or run without installing: `python3 -m adr_drift` from the repo root.

## Quick start

```bash
cd your-repo
python3 -m adr_drift init      # scaffold arch.yaml + sample ADR from folder layout
# tighten the rules in arch.yaml, add ## Constraints to your ADRs
python3 -m adr_drift check     # verify; exit 1 on violations (CI-ready)
# repo already has legacy violations? grandfather them, then enforce:
python3 -m adr_drift baseline                    # record current violations
python3 -m adr_drift check --baseline .adr-baseline.json
```

## Commands

| Command | What it does |
|---|---|
| `init` | Infer layers from folder layout, write `arch.yaml` + sample ADR + index |
| `check` | Verify the repo against `arch.yaml` + every ADR's `## Constraints` (`--baseline FILE` grandfathers recorded violations) |
| `baseline` | Write current violations to `.adr-baseline.json` (default) for `check --baseline` |
| `drift --since HEAD~5` | Report violations *introduced* since a git ref, plus violations *fixed* since then (pre-existing ones ignored) |
| `new "title"` | Create the next-numbered ADR from a template |
| `index` | Regenerate the one-sentence-per-ADR index (`docs/adr/README.md`) |

## The constraint DSL

Rules live in `arch.yaml` and in any ADR's `## Constraints` section:

```
forbid-import: handlers -> db.drivers     # layering: handlers may not import drivers
forbid-pattern: src/**.py !~ \beval\(     # no eval() anywhere in src/
require-pattern: src/handlers/**.py =~ authenticate\(   # handlers must authenticate
```

`from`/`to` match layer names or module prefixes (`db.drivers` matches
`db.drivers.pg`); `*` wildcards supported. Comments start with `#`.
To-side patterns resolve against layer paths too: in a `src/`-layout repo
both `db.drivers` and `src.db.drivers` spellings match a `db` layer mapped
to `src/db/**`.

## How it checks

- **Python** is parsed with the real `ast` — no regex false positives from
  comments or strings.
- **JS/TS/Go/Rust/Java/Ruby/PHP** use a heuristic regex import extractor
  (documented as such; `--strict-py-only` disables it).
- `drift` compares violations at HEAD against the base ref via `git show` —
  it never touches your working tree, and `git blame` tags each new
  violation with its introducing commit.

## CI

```yaml
- run: python3 -m adr_drift check --format json --fail-on violations
```

`--fail-on none` reports without failing. JSON output is stable for tooling.

## Honest limits

- The regex fallback for non-Python languages is heuristic — it can miss
  exotic import forms. Python is precise; everything else is best-effort.
- `drift` needs a git repo; `check` works anywhere.
- Rules are only as good as the constraints you write — `init` scaffolds,
  you tighten.

## Tests

55/55 pass: `python3 -m unittest discover -s tests`
