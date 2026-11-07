"""The committed benchmark is a regression gate: on its generated
multi-language corpus every planted violation is found and no decoy is."""

import json
import os
import sys
import tempfile
import unittest

from _support import run_main

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(os.path.dirname(HERE), "benchmarks")


@unittest.skipUnless(os.path.isdir(BENCH), "benchmarks/ not shipped")
class BenchmarkGateTest(unittest.TestCase):
    def test_precision_and_recall(self):
        sys.path.insert(0, BENCH)
        try:
            import bench
            import gen_corpus
        finally:
            sys.path.remove(BENCH)
        for seed in (7, 8):
            with tempfile.TemporaryDirectory() as root:
                truth = gen_corpus.generate(root, 140, seed)
                code, out, _err = run_main("--root", root, "check",
                                           "--format", "json")
                self.assertEqual(code, 1)
                s = bench.score(root, json.loads(out), truth)
                self.assertGreater(s["planted"], 100)
                self.assertEqual(s["false_positives"], 0,
                                 s["decoys_wrongly_flagged"])
                missed = {c: f for c, f in s["recall_by_category"].items()
                          if f.split("/")[0] != f.split("/")[1]}
                self.assertEqual(missed, {})


if __name__ == "__main__":
    unittest.main()
