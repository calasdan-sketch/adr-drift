"""How adr-drift behaves on a hostile repository: wall time and exit code.

Each case is a repository a pull request could contain. Before round 3 the
first hung CI and the second ran a small runner out of memory; now both end
quickly with a warning or an error. Usage: python3 benchmarks/hostile.py
"""

import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.environ.get('ADR_DRIFT_CODE') or os.path.dirname(HERE)
ADR = ("# 1. x\n\n## Status\n\nAccepted\n\n## Constraints\n\n"
       "forbid-pattern: src/**.py !~ %s\n")


def make(rule: str, files: dict) -> str:
    root = tempfile.mkdtemp()
    for rel, body in dict(files, **{"docs/adr/0001-x.md": (ADR % rule).encode()}).items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(body)
    return root


def run(root: str, extra_env: dict) -> "tuple[int, float]":
    env = dict(os.environ, PYTHONPATH=CODE, **extra_env)
    t = time.monotonic()
    p = subprocess.run([sys.executable, "-m", "adr_drift", "--root", root,
                        "check"], env=env, capture_output=True, timeout=300, cwd=root)
    return p.returncode, time.monotonic() - t


def main() -> None:
    cases = [
        ("catastrophic regex (a+)+$ on a 40-char line, budget 2 s",
         make("(a+)+$", {"src/a.py": b"a" * 40 + b"!\n"}),
         {"ADR_DRIFT_REGEX_SECONDS": "2"}),
        ("30 MB source file (limit 4 MiB)",
         make("eval", {"src/big.py": b"x = 1\n" * 5_000_000}), {}),
        ("regex nested 100 000 groups",
         make("(" * 100000 + "a" + ")" * 100000, {"src/a.py": b"x = 1\n"}), {}),
    ]
    print(f"{'case':58} exit  seconds")
    for name, root, env in cases:
        code, secs = run(root, env)
        print(f"{name:58} {code:>4}  {secs:7.1f}")


if __name__ == "__main__":
    main()
