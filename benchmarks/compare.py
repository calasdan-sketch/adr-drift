"""Run adr-drift and the tools people compare it to on the same project.

    python3 benchmarks/compare.py [--modules 400] [--keep DIR]

Builds one valid Python project (packages handlers / services / db / db_utils,
every import resolves) and one TypeScript project with a tsconfig alias, plants
the same violations of "handlers must not import db" in both, then runs, when
installed (nothing is installed for you; a missing tool is skipped):

* adr-drift (this checkout)
* import-linter  (`pip install import-linter`)  - Python
* tach           (`pip install tach`)           - Python
* dependency-cruiser (`npm i dependency-cruiser`, `depcruise` on PATH or in
  $DEPCRUISE) - TypeScript / JavaScript

and prints, per tool: violations found / planted, false alarms on the decoys
(an import in a comment or a string, the look-alike package db_utils), and
wall time. Each tool is configured as its own documentation says; the
configurations are written below so they can be checked. Static analysers see
what the import statement says, so the dynamic-import and computed cases are
planted too: they are where tools differ.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)

# (category, python lines; the marked line (>) is the violation)
PY_CASES = [
    ("import", ["> import db.m{k}"]),
    ("from-import", ["> from db import m{k}"]),
    ("from-module", ["> from db.m{k} import f"]),
    ("multiline", ["> from db.m{k} import (", "    f,", ")"]),
    ("in-function", ["def g_{n}():", ">     import db.m{k}"]),
    ("type-checking", ["from typing import TYPE_CHECKING", "if TYPE_CHECKING:",
                       ">     from db.m{k} import f as f_{n}"]),
    ("importlib", ["import importlib", ">v_{n} = importlib.import_module('db.m{k}')"]),
]
PY_DECOYS = [
    ("comment", ["# import db.m{k}"]),
    ("string", ['S_{n} = "import db.m{k}"']),
    ("lookalike", ["import db_utils.m{k}"]),
    ("service", ["import services.m{k}"]),
]
TS_CASES = [
    ("relative", ['> import {{ f }} from "../db/m{k}";']),
    ("alias", ['> import {{ f }} from "@/db/m{k}";']),
    ("require", ['> const r_{n} = require("../db/m{k}");']),
    ("dynamic", ['> const d_{n} = import("../db/m{k}");']),
    ("reexport", ['> export * from "../db/m{k}";']),
    ("multiline", ["import {{", "  f,", '}} from "../db/m{k}";']),
]
TS_DECOYS = [
    ("comment", ['// import {{ f }} from "../db/m{k}";']),
    ("string", ['const s_{n} = \'import x from "../db/m{k}"\';']),
    ("service", ['import {{ f }} from "../services/m{k}";']),
]


def _emit(cases, decoys, n, k, rng_pick):
    lines, truth = [], {}
    for kind, table in (("pos", cases), ("neg", decoys)):
        cat, tmpl = table[rng_pick % len(table)]
        base = len(lines)
        mark = None
        for i, t in enumerate(tmpl):
            if t.startswith(">"):
                t = t[1:]
                t = t[1:] if t.startswith(" ") else t
                mark = i
            lines.append(t.format(n=n, k=k))
        if kind == "pos":
            truth[base + (mark or 0) + 1] = ("pos", cat)
        else:
            for i in range(len(tmpl)):
                truth[base + i + 1] = ("neg", cat)
        lines.append("")
        rng_pick += 3
    return lines, truth


def build(root: str, modules: int) -> dict:
    """-> {"py": {file: {line: (kind, category)}}, "ts": {...}}"""
    truth = {"py": {}, "ts": {}}
    for pkg in ("handlers", "services", "db", "db_utils"):
        os.makedirs(os.path.join(root, "py", pkg), exist_ok=True)
        open(os.path.join(root, "py", pkg, "__init__.py"), "w").close()
    os.makedirs(os.path.join(root, "ts", "src", "handlers"), exist_ok=True)
    for pkg in ("services", "db"):
        os.makedirs(os.path.join(root, "ts", "src", pkg), exist_ok=True)
    with open(os.path.join(root, "ts", "tsconfig.json"), "w") as f:
        json.dump({"compilerOptions": {"baseUrl": ".", "module": "commonjs",
                                       "paths": {"@/*": ["src/*"]}}}, f)
    for k in range(modules):
        for pkg in ("services", "db", "db_utils"):
            with open(os.path.join(root, "py", pkg, f"m{k}.py"), "w") as f:
                f.write("def f():\n    return 1\n")
        for pkg in ("services", "db"):
            with open(os.path.join(root, "ts", "src", pkg, f"m{k}.ts"), "w") as f:
                f.write("export function f(): number { return 1; }\n")
    for n in range(modules):
        k = (n * 7) % modules
        body, t = _emit(PY_CASES, PY_DECOYS, n, k, n)
        rel = f"py/handlers/h{n}.py"
        with open(os.path.join(root, rel), "w") as f:
            f.write("\n".join(body) + "\n" + "\n".join(
                f"def h_{n}_{i}(x):\n    return x + {i}\n" for i in range(8)))
        truth["py"][rel] = t
        body, t = _emit(TS_CASES, TS_DECOYS, n, k, n)
        rel = f"ts/src/handlers/h{n}.ts"
        with open(os.path.join(root, rel), "w") as f:
            f.write("\n".join(body) + "\n" + "\n".join(
                f"export function h{n}_{i}(x: number): number {{ return x + {i}; }}"
                for i in range(8)) + "\n")
        truth["ts"][rel] = t
    arch = ('layers:\n  - name: handlers\n    paths: ["py/handlers/**", "ts/src/handlers/**"]\n'
            '  - name: services\n    paths: ["py/services/**", "ts/src/services/**"]\n'
            '  - name: db\n    paths: ["py/db/**", "ts/src/db/**"]\n'
            'rules:\n  - "forbid-import: handlers -> db"\n')
    with open(os.path.join(root, "arch.yaml"), "w") as f:
        f.write(arch)
    os.makedirs(os.path.join(root, "docs", "adr"), exist_ok=True)
    with open(os.path.join(root, "docs", "adr", "0001-x.md"), "w") as f:
        f.write("# 1: X\n\n## Status\n\nAccepted\n")
    # tool configurations
    with open(os.path.join(root, "py", ".importlinter"), "w") as f:
        f.write("[importlinter]\nroot_packages =\n    handlers\n    services\n"
                "    db\n    db_utils\n\n[importlinter:contract:1]\n"
                "name = handlers must not import db\ntype = forbidden\n"
                "source_modules =\n    handlers\nforbidden_modules =\n    db\n"
                "allow_indirect_imports = False\n")
    with open(os.path.join(root, "py", "tach.toml"), "w") as f:
        f.write('exclude = ["tests"]\nsource_roots = ["."]\n\n'
                '[[modules]]\npath = "handlers"\ndepends_on = [{ path = "services" }, { path = "db_utils" }]\n\n'
                '[[modules]]\npath = "services"\ndepends_on = []\n\n'
                '[[modules]]\npath = "db"\ndepends_on = []\n\n'
                '[[modules]]\npath = "db_utils"\ndepends_on = []\n')
    with open(os.path.join(root, "ts", ".dependency-cruiser.json"), "w") as f:
        json.dump({"forbidden": [{
            "name": "handlers-not-db", "severity": "error",
            "from": {"path": "^src/handlers"}, "to": {"path": "^src/db"}}],
            "options": {"tsConfig": {"fileName": "tsconfig.json"},
                        "doNotFollow": {"path": "node_modules"}}}, f)
    return truth


def _pos(truth: dict) -> set:
    return {(f, n) for f, m in truth.items() for n, (k, _c) in m.items()
            if k == "pos"}


def score(flagged: set, truth: dict) -> dict:
    pos = _pos(truth)
    cats = {(f, n): c for f, m in truth.items() for n, (k, c) in m.items()
            if k == "pos"}
    missed: dict = {}
    for key in pos - flagged:
        missed[cats[key]] = missed.get(cats[key], 0) + 1
    neg = {(f, n): c for f, m in truth.items() for n, (k, c) in m.items() if k == "neg"}
    tp = flagged & pos
    return {"found": f"{len(tp)}/{len(pos)}",
            "false_alarms": len(flagged - pos),
            "on_decoys": len([x for x in flagged if x in neg]),
            "missed": dict(sorted(missed.items()))}


def timed(cmd, cwd, env=None):
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    return p, round(time.perf_counter() - t0, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modules", type=int, default=400)
    ap.add_argument("--keep", default="")
    a = ap.parse_args()
    tmp = a.keep or tempfile.mkdtemp()
    os.makedirs(tmp, exist_ok=True)
    truth = build(tmp, a.modules)
    results = {}
    env = {"PYTHONPATH": CODE, "PYTHONDONTWRITEBYTECODE": "1"}
    # adr-drift on the whole project
    p, secs = timed([sys.executable, "-m", "adr_drift", "check", "--format",
                     "json"], tmp, env)
    got = {"py": set(), "ts": set()}
    for v in json.loads(p.stdout or "[]"):
        got["py" if v["file"].startswith("py/") else "ts"].add(
            (v["file"], str(v["line"])))
    for lang in ("py", "ts"):
        t = {f: {str(n): v for n, v in m.items()} for f, m in truth[lang].items()}
        results[f"adr-drift ({lang})"] = dict(score(got[lang], t), seconds=secs)
    # import-linter
    exe = shutil.which("lint-imports")
    if exe:
        p, secs = timed([exe], os.path.join(tmp, "py"),
                        {"PYTHONPATH": os.path.join(tmp, "py")})
        hits = set()
        for line in (p.stdout + p.stderr).splitlines():
            m = re.match(r"\s*-?\s*(\S+) -> (\S+) \(l\.(\d+)\)", line)
            if m:
                path = "py/" + m.group(1).replace(".", "/") + ".py"
                hits.add((path, m.group(3)))
        t = {f: {str(n): v for n, v in m.items()} for f, m in truth["py"].items()}
        results["import-linter (py)"] = dict(score(hits, t), seconds=secs)
    # tach
    exe = shutil.which("tach")
    if exe:
        p, secs = timed([exe, "check", "--dependencies", "--output", "json"],
                        os.path.join(tmp, "py"))
        hits = set()
        try:
            for item in json.loads(p.stdout):
                err = item.get("Located", item)
                path = "py/" + str(err.get("file_path", ""))
                line = int(err.get("line_number", 0))
                # tach names the line of the imported name; for a statement
                # spread over lines that is not the first one: accept both.
                hits.add((path, str(line), str(line - 1)))
        except (ValueError, AttributeError, TypeError):
            pass
        t = {f: {str(n): v for n, v in m.items()} for f, m in truth["py"].items()}
        hits = {(f, a if (f, a) in _pos(t) else b) for f, a, b in hits}
        results["tach (py)"] = dict(score(hits, t), seconds=secs)
    # dependency-cruiser (reports modules, not lines: file-level)
    exe = os.environ.get("DEPCRUISE") or shutil.which("depcruise")
    if exe:
        p, secs = timed([exe, "src", "--config", ".dependency-cruiser.json",
                         "--output-type", "json"], os.path.join(tmp, "ts"))
        flagged_files = set()
        try:
            for v in json.loads(p.stdout)["summary"]["violations"]:
                flagged_files.add("ts/" + v["from"])
        except (ValueError, KeyError):
            pass
        planted_files = {f for f, m in truth["ts"].items()
                         if any(k == "pos" for k, _c in m.values())}
        results["dependency-cruiser (ts, file level)"] = {
            "found": f"{len(flagged_files & planted_files)}/{len(planted_files)} files",
            "false_alarms": len(flagged_files - planted_files),
            "seconds": secs}
    for name, r in results.items():
        print(f"{name:38s} {r}")
    if not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
