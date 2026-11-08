"""Mutation sanity: flip one key condition at a time in a COPY of the package
and check that the test-suite notices.

    python3 benchmarks/mutants.py [--tests "tests/test_round5.py ..."]

Each line of MUTANTS replaces text that must occur exactly once. A mutant is
KILLED when the selected tests fail, SURVIVED when they still pass (a test
gap: look at it), SKIPPED when the text is not found (the code moved on, update
the list). Nothing in the working tree is touched; the copy lives in a
temporary directory that is removed at the end.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Mutants that were tried and are equivalent (the code they change is a
# second guard for something already guaranteed; no test can tell them apart):
# `rel in tset` before `rel not in nxt`, `target != rel` (a self-import never
# lowers a distance), sorting `graph.back` (it is built in sorted order), and
# a package import with two member names (the file branch matches first).
MUTANTS = [
    ("engine.py", "if src in dist:\n                continue", "if False:\n                continue", "transitive BFS revisits files"),
    ("engine.py", "if rel in tset or rel not in nxt or not _from_hit(rule, rel, layer):", "if rel in tset or rel not in nxt:", "transitive ignores the from side"),
    ("engine.py", "if len(cands) == 1:\n                found = sorted(cands)", "if len(cands) >= 1:\n                found = sorted(cands)", "ambiguous imports are followed"),
    ("engine.py", "if python and pieces[0] in sys.stdlib_module_names:", "if False:", "stdlib name follows a repo file"),
    ("engine.py", "if rule.scope else all_entries)", "if False else all_entries)", "scope ignored"),
    ("engine.py", "steps = dist[rel]", "steps = dist[rel] + 1", "chain length off by one"),
    ("rules.py", "return not self.scope or any(match_glob(g, rel) for g in self.scope)", "return True", "in_scope always true"),
    ("rules.py", "return not self.scope or any(match_glob(g, rel) for g in self.scope)", "return not self.scope or all(match_glob(g, rel) for g in self.scope)", "scope globs ANDed, not ORed"),
    ("config.py", "rule.scope = list(scope)", "pass", "ADR scope not attached"),
    ("config.py", 'if g.startswith("/") or re.match(r"[A-Za-z]:", g):', "if False:", "absolute scope allowed"),
    ("config.py", 'if ".." in g.split("/"):', "if False:", "scope may climb out"),
    ("config.py", "raise _ProseScope(", "raise ValueError(", "prose scope hard-fails"),
    ("engine.py", "if rule.scope and not any(rule.in_scope(rel)", "if False and not any(rule.in_scope(rel)", "no warning for a scope matching nothing"),
    ("rulediff.py", "if r.scope else", "if False else", "rules-diff blind to scope"),
    ("report.py", '"organization": __author__,', '"organization": "x",', "author not in SARIF"),
    ("cli.py", 'if r.kind in ("forbid-cycles", "forbid-transitive")]', 'if r.kind in ("forbid-cycles",)]', "drift tries to judge transitive"),
    ("paths.py", "except ValueError:", "except KeyError:", "cross-drive relpath crashes"),
    ("scanner.py", "real = os.path.normcase(os.path.realpath(path))", "real = os.path.realpath(path)", "case-folding off in symlink check"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tests", default="tests/test_round5.py tests/test_golden.py "
                    "tests/test_cycles.py tests/test_layer_rules.py tests/test_governance.py")
    args = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="adr-mut-")
    try:
        dst = os.path.join(tmp, "w")
        shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".mypy_cache", ".ruff_cache",
            ".pytest_cache", "*.egg-info", "build", "dist"))
        killed = survived = skipped = 0
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONPATH", None)
        for fname, old, new, why in MUTANTS:
            path = os.path.join(dst, "adr_drift", fname)
            with open(path, encoding="utf-8") as f:
                orig = f.read()
            if orig.count(old) != 1:
                print(f"SKIPPED   {why}  ({fname}: text not found once)")
                skipped += 1
                continue
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(orig.replace(old, new))
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pytest", "-x", "-q", "-p",
                     "no:cacheprovider", *args.tests.split()],
                    cwd=dst, env=env, capture_output=True, text=True,
                    timeout=900)
            finally:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(orig)
            if r.returncode == 0:
                survived += 1
                print(f"SURVIVED  {why}  ({fname})")
            else:
                killed += 1
                print(f"killed    {why}")
        print(f"\n{killed} killed, {survived} survived, {skipped} skipped "
              f"of {len(MUTANTS)}")
        return 1 if survived else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
