"""Rule-violation detection benchmark: precision, recall and speed.

    python3 benchmarks/bench.py                       # this checkout
    python3 benchmarks/bench.py --code OTHER_CHECKOUT # compare an older one
    python3 benchmarks/bench.py --speed-files 12000   # time a big repo

Generates the corpus of gen_corpus.py (seeded, so every run sees the same
files), runs `adr-drift check --format json` on it and compares the lines
it reports with the ground truth the generator recorded:

* recall    = planted violations reported / planted violations
* precision = reported lines that are planted violations / reported lines
  (a report on a decoy line - the import inside a comment, db_utils - or
  anywhere else counts against precision)

Speed is the wall time of the same command on a larger generated repo.
Nothing here is specific to adr-drift's internals: it only runs the CLI.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_corpus  # noqa: E402


_LAUNCH = ("import resource, subprocess, sys;"
           "p = subprocess.run(sys.argv[1:]);"
           "sys.stderr.write('\\nMAXRSS_KB=%d\\n' % resource.getrusage("
           "resource.RUSAGE_CHILDREN).ru_maxrss);sys.exit(p.returncode)")


def run_cli(code: str, root: str, extra=(), env_extra=None, rss=False):
    """-> (CompletedProcess, seconds[, peak resident MB of the run])."""
    env = dict(os.environ, PYTHONPATH=code, PYTHONDONTWRITEBYTECODE="1")
    env.update(env_extra or {})
    cmd = [sys.executable, "-m", "adr_drift", "check", "--root", root,
           "--format", "json", *extra]
    if rss:
        cmd = [sys.executable, "-c", _LAUNCH, *cmd]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=code)
    secs = time.perf_counter() - t0
    if not rss:
        return p, secs
    mb = 0.0
    for line in p.stderr.splitlines():
        if line.startswith("MAXRSS_KB="):
            mb = int(line.split("=")[1]) / 1024
    return p, secs, mb


def score(root: str, reported: list, truth: dict) -> dict:
    flagged = {(v["file"], int(v["line"])) for v in reported}
    pos = {(f, int(n)): c for f, m in truth.items()
           for n, (k, c) in m.items() if k == "pos"}
    neg = {(f, int(n)): c for f, m in truth.items()
           for n, (k, c) in m.items() if k == "neg"}
    tp = flagged & set(pos)
    fp = flagged - set(pos)
    fn = set(pos) - flagged
    by_cat = collections.defaultdict(lambda: [0, 0])
    for key, cat in pos.items():
        by_cat[cat][1] += 1
        by_cat[cat][0] += key in flagged
    decoy_hits = collections.Counter(neg[k] for k in fp if k in neg)
    return {
        "planted": len(pos), "reported": len(flagged),
        "true_positives": len(tp), "false_positives": len(fp),
        "false_negatives": len(fn),
        "precision": round(len(tp) / len(flagged), 4) if flagged else 1.0,
        "recall": round(len(tp) / len(pos), 4) if pos else 1.0,
        "recall_by_category": {c: f"{a}/{b}" for c, (a, b) in sorted(by_cat.items())},
        "decoys_wrongly_flagged": dict(decoy_hits),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=os.path.dirname(HERE),
                    help="checkout of adr-drift to run (default: this one)")
    ap.add_argument("--files", type=int, default=350)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--speed-files", type=int, default=12000)
    ap.add_argument("--no-speed", action="store_true")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    ap.add_argument("--speed-extra", default="",
                    help="extra check options for the speed run, e.g. '--jobs 1'")
    a = ap.parse_args()
    out: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "corpus")
        truth = gen_corpus.generate(root, a.files, a.seed)
        p, secs = run_cli(a.code, root)
        if p.returncode not in (0, 1):
            print(p.stderr, file=sys.stderr)
            return 2
        out["accuracy"] = score(root, json.loads(p.stdout or "[]"), truth)
        out["accuracy"]["seconds"] = round(secs, 2)
        if not a.no_speed:
            big = os.path.join(tmp, "big")
            gen_corpus.generate(big, a.speed_files, a.seed)
            nfiles = sum(len(f) for _d, _s, f in os.walk(big))
            extra = a.speed_extra.split()
            runs = {}
            mem = 0.0
            for label, opts, env in (("default", extra, None),):
                best = None
                for _ in range(3):
                    p, secs, mb = run_cli(a.code, big, opts, env, rss=True)
                    best = secs if best is None else min(best, secs)
                    mem = max(mem, mb)
                runs[label] = round(best, 2)
            out["speed"] = {"files": nfiles, "cpus": os.cpu_count(),
                            "best_of_3_seconds": runs,
                            "peak_rss_mb": round(mem)}
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        acc = out["accuracy"]
        print(f"planted violations {acc['planted']}  reported {acc['reported']}")
        print(f"precision {acc['precision']:.3f}  recall {acc['recall']:.3f}  "
              f"(TP {acc['true_positives']} FP {acc['false_positives']} "
              f"FN {acc['false_negatives']})")
        for cat, frac in acc["recall_by_category"].items():
            print(f"  {cat:24s} {frac}")
        if acc["decoys_wrongly_flagged"]:
            print("decoys wrongly flagged:", acc["decoys_wrongly_flagged"])
        if "speed" in out:
            print("speed:", out["speed"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
