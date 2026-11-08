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

## hostile.py (round 3)

`python3 benchmarks/hostile.py` times three repositories a pull request could
carry (2026-11-07, 2 CPUs, a shared machine, so read the times as rough):

| case | exit | seconds |
|---|---|---|
| catastrophic regex `(a+)+$`, budget 2 s | 2 | 2.5 |
| 30 MB source file | 0 (warning) | 0.2 |
| regex nested 100 000 groups | 2 | 0.3 |

The round 2 code did not finish the first case in 250 s (killed); the
80 MB file case in round 2 was killed for memory on this machine (once, by
hand, not part of the script). Detection numbers above are unchanged by
round 3 (recall and precision 100%; the speed figure moved between 3.8 and
6.5 s on 20 000 files across five runs, round 2 code 6.5 to 11.6 s on the
same machine, because the machine's disk was busy: no speed change is
claimed).

## compare.py

`compare.py` builds one valid Python and one TypeScript project, plants one
violation per handler file and runs adr-drift, import-linter, tach and
dependency-cruiser when they are installed (`pip install import-linter tach`,
`npm i dependency-cruiser`; set `DEPCRUISE` to the binary if it is not on
PATH). Each tool gets the configuration written in the script. Nothing is
installed for you. See the main README for the numbers.

## Round 4 check (2026-11-07, shared machine)

Detection unchanged: 497 / 497 found, 0 false alarms, for the round-3 commit
and for round 4. Speed on 20 000 files, best of 3, with a load average of 8
to 17 on 2 CPUs because other jobs shared the machine: round 3 6.1 s and
11.2 s, round 4 8.5 s and 7.4 s in two back-to-back pairs. That is noise, not
a difference; the scan code did not change in round 4 (only error handling
and the Windows job limit). The 1.9 s above is from a quiet machine.

## Round 5 (2026-11-08)

Detection on the generated corpus is unchanged by round 5: 497 / 497, 0 false
alarms. Speed, `bench.py --code <round-4 tree>` against this tree back to back
on a machine with a load average of 8-14 on 2 CPUs: round 4 6.9 s and 5.7 s,
round 5 5.6 s and 4.8 s for 19 998 files (the seconds are not comparable with
the table above; only "no slower" is supported). `forbid-transitive` is not
in this benchmark: by hand, 6 000 files in one chain took about 3 s and 3 000
files with 20 imports each about 5 s on the same loaded machine.

`mutants.py` flips one condition at a time in a copy of the engine, the rule
parser and the config reader (18 mutations) and runs the new tests against
each: 18 killed, 0 survived. Five more mutants were tried and dropped as
equivalent (see the comment in the file).
