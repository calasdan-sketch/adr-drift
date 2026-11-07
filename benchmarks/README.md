# Benchmarks

Everything here runs the `adr-drift` command line and nothing else, so it can
be pointed at any checkout.

```console
$ python3 benchmarks/bench.py                         # this checkout
$ git archive <older commit> | tar -x -C /tmp/old     # an older one ...
$ python3 benchmarks/bench.py --code /tmp/old         # ... for a before/after
$ python3 benchmarks/bench.py --speed-extra "--jobs 1"
$ python3 benchmarks/compare.py                       # other tools, if installed
```

`gen_corpus.py` writes a seeded polyglot repository (Python, TypeScript, Go,
Java, Rust, PHP, Ruby) with one ADR rule, `forbid-import: handlers -> db`, and
records the line of every planted violation and of every decoy. `bench.py`
reports recall (planted violations found), precision (reported lines that are
planted violations) and wall time and peak memory on a larger generated repo.
`tests/test_benchmark.py` runs the same generator on two seeds and fails the
suite when a planted violation is missed or a decoy is flagged.

The categories are the import spellings I know real code uses; the corpus is
not a sample of real repositories, so 100% means "everything the generator
plants", not "everything".

## Result (2026-11-07, 2 CPUs, Python 3.13, 350 handler files; speed on 20 000 files)

| | recall | precision | false alarms | time, memory |
|---|---|---|---|---|
| 0.2.0 (commit 40baedd) | 63.2% (314/497) | 85.8% | 52 | 1.9 s, 53 MB |
| 0.3.0 | 100% (497/497) | 100% | 0 | 1.9 s, 70 MB (`--jobs 2`); 2.3 s with `--jobs 1` |

What 0.2.0 missed (recall by category, found/planted): Go imports of a
module whose `go.mod` is below the root 0/73; PHP PSR-4 `use` 0/30 and
`require __DIR__ . ...` into a layer written `**/Db/**` 0/35; Python
`importlib.import_module("m")` 0/10 and `__import__("m")` 0/15; TypeScript
tsconfig aliases `@/db/x` and `@db/x` 0/20. It flagged decoys it should not:
a layer glob `ts/src/**/db/**` or `ruby/lib/**/db/**` made every module
under `ts/src` or `ruby/lib` a member of the db layer (`ts-service` 11,
`ruby-service` 26, `ts-lookalike` 15).

## compare.py

`compare.py` builds one valid Python and one TypeScript project, plants one
violation per handler file and runs adr-drift, import-linter, tach and
dependency-cruiser when they are installed (`pip install import-linter tach`,
`npm i dependency-cruiser`; set `DEPCRUISE` to the binary if it is not on
PATH). Each tool gets the configuration written in the script. Nothing is
installed for you. See the main README for the numbers.
