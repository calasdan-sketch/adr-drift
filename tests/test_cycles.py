"""forbid-cycles: layers that depend on each other in a loop."""

import json
import unittest

from _support import (GitCase, commit_all, init_git, make_repo, run_main,
                      write_files)

from adr_drift import engine, rules

LAYERS = ('layers:\n'
          '  - name: a\n    paths: ["src/a/**"]\n'
          '  - name: b\n    paths: ["src/b/**"]\n'
          '  - name: c\n    paths: ["src/c/**"]\n')
ADR_DIR = "docs/adr/0001-x.md"
ADR = "# 1: X\n\n## Status\n\nAccepted\n\n## Constraints\n\n- forbid-cycles: *\n"


def repo(files, rule='  - "forbid-cycles: *"\n'):
    base = {"arch.yaml": LAYERS + "rules:\n" + rule,
            "docs/adr/0001-x.md": "# 1: X\n\n## Status\n\nAccepted\n"}
    base.update(files)
    return make_repo(base)


LOOP = {"src/a/m.py": "from src.b import x\n",
        "src/b/x.py": "import src.c.y\n",
        "src/c/y.py": "from src.a import m\n"}


class ParseTest(unittest.TestCase):
    def test_parse(self):
        r = rules.parse_rule_line("forbid-cycles: *")
        self.assertEqual((r.kind, r.from_pat), ("forbid-cycles", "*"))
        self.assertIn("cycle", r.describe())

    def test_spaces_are_an_error(self):
        with self.assertRaisesRegex(ValueError, "one layer name or pattern"):
            rules.parse_rule_line("forbid-cycles: a b")

    def test_typo_is_reported(self):
        self.assertIn("forbid-cycles",
                      rules.rule_like_problem("forbid-cycle: *", True))

    def test_empty_is_reported(self):
        self.assertIn("nothing after the colon",
                      rules.rule_like_problem("forbid-cycles:", True))


class CheckTest(unittest.TestCase):
    def test_three_layer_loop(self):
        code, out, _err = run_main("--root", repo(LOOP), "check")
        self.assertEqual(code, 1)
        self.assertIn("layer cycle a -> b -> c -> a", out)
        self.assertIn("b -> c: src/b/x.py:1 imports src.c.y", out)

    def test_no_loop_passes(self):
        files = dict(LOOP)
        files["src/c/y.py"] = "import os\n"
        code, out, _err = run_main("--root", repo(files), "check")
        self.assertEqual(code, 0, out)

    def test_from_adr_and_json_evidence(self):
        root = repo(LOOP, rule="")  # arch.yaml: no rules, the ADR has one
        write_files(root, {ADR_DIR: ADR})
        code, out, _err = run_main("--root", root, "check", "--format",
                                   "json")
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(len(data[0]["evidence"]), 3)
        self.assertEqual(data[0]["rule_source"], f"{ADR_DIR}:9")

    def test_pattern_limits_layers(self):
        # only a and b are covered: the loop through c does not count
        code, _out, err = run_main("--root", repo(LOOP, '  - "forbid-cycles: '
                                                  '[ab]"\n'), "check")
        self.assertEqual(code, 0, err)

    def test_two_layer_loop(self):
        files = {"src/a/m.py": "import src.b.x\n",
                 "src/b/x.py": "import src.a.m\n",
                 "src/c/y.py": "import os\n"}
        code, out, _err = run_main("--root", repo(files), "check")
        self.assertEqual(code, 1)
        self.assertIn("1 architecture violation", out)

    def test_one_layer_warns_and_strict_fails(self):
        code, _out, err = run_main("--root", repo(LOOP, '  - "forbid-cycles: '
                                                  'a"\n'), "check",
                                   "--strict")
        self.assertEqual(code, 2)
        self.assertIn("a cycle needs at least two", err)

    def test_unquoted_star_in_yaml_says_quote_it(self):
        code, _out, err = run_main("--root", repo(LOOP, "  - forbid-cycles: "
                                                  "*\n"), "check")
        self.assertEqual(code, 2)
        self.assertIn("must be quoted", err)

    def test_baseline_grandfathers_a_cycle(self):
        root = repo(LOOP)
        self.assertEqual(run_main("--root", root, "baseline")[0], 0)
        # an edit above the import moves its line: still the same cycle
        write_files(root, {"src/a/m.py": '"""doc"""\nfrom src.b import x\n'})
        code, out, _err = run_main("--root", root, "check", "--baseline",
                                   ".adr-baseline.json")
        self.assertEqual(code, 0, out)

    def test_deep_chain_has_no_recursion_limit(self):
        n = 5000  # far past Python's default recursion limit
        graph = {f"n{i}": {f"n{i + 1}": ()} for i in range(n)}
        graph[f"n{n}"] = {"n0": ()}
        groups = engine._strongly_connected(graph)
        self.assertEqual([len(g) for g in groups], [n + 1])
        loop = engine._shortest_cycle(graph, groups[0])
        self.assertEqual((loop[0], loop[-1], len(loop)), ("n0", "n0", n + 2))


class DriftTest(GitCase):
    def test_drift_says_it_skips_cycles(self):
        root = init_git(repo(LOOP, '  - "forbid-cycles: *"\n'
                                   '  - "forbid-import: a -> zzz"\n'))
        commit_all(root, "one")
        write_files(root, {"src/a/n.py": "import os\n"})
        commit_all(root, "two")
        code, _out, err = run_main("--root", root, "drift", "--since",
                                   "HEAD~1")
        self.assertEqual(code, 0, err)
        self.assertIn("note: 1 forbid-cycles rule is checked by", err)

    def test_drift_with_only_cycle_rules_warns(self):
        root = init_git(repo(LOOP))
        commit_all(root, "one")
        write_files(root, {"src/a/n.py": "import os\n"})
        commit_all(root, "two")
        code, _out, err = run_main("--root", root, "drift", "--since",
                                   "HEAD~1", "--strict")
        self.assertEqual(code, 2)
        self.assertIn("drift checks nothing", err)


if __name__ == "__main__":
    unittest.main()
