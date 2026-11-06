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


class PanelFindingsTest(unittest.TestCase):
    """Regressions for the synapse-1 panel review."""

    def test_reports_the_shortest_loop_of_the_whole_group(self):
        # a->b->c->d->a plus c->b: the 2-step b->c->b is the shortest
        graph = {"a": {"b": ()}, "b": {"c": ()}, "c": {"d": (), "b": ()},
                 "d": {"a": ()}}
        group = engine._strongly_connected(graph)[0]
        self.assertEqual(engine._shortest_cycle(graph, group),
                         ["b", "c", "b"])

    def test_shortest_loop_matches_brute_force(self):
        import itertools
        import random
        rng = random.Random(7)
        for _ in range(300):
            names = [chr(97 + i) for i in range(rng.randint(2, 6))]
            graph = {n: {m: () for m in names
                         if m != n and rng.random() < 0.35} for n in names}
            for group in engine._strongly_connected(graph):
                if len(group) < 2:
                    continue
                best = None
                for size in range(2, len(group) + 1):
                    for perm in itertools.permutations(group, size):
                        if all(perm[(i + 1) % size] in graph[perm[i]]
                               for i in range(size)):
                            best = size
                            break
                    if best:
                        break
                loop = engine._shortest_cycle(graph, group)
                self.assertEqual(len(loop) - 1, best, (graph, group, loop))

    def test_identity_index_matches_the_reference(self):
        import random
        rng = random.Random(3)
        words = ["src", "app", "db", "core", "a.b", "web", "lib"]
        for _ in range(300):
            layers = []
            for i in range(rng.randint(0, 5)):
                path = "/".join(rng.choice(words)
                                for _ in range(rng.randint(1, 3)))
                tail = rng.choice(["/**", "/*", "", "/**/x*"])
                layers.append({"name": rng.choice(words + [f"L{i}", ""]),
                               "paths": [path + tail]})
            index = engine.IdentityIndex(layers)
            for _ in range(10):
                sep = rng.choice([".", "/"])
                mod = sep.join(rng.choice(words)
                               for _ in range(rng.randint(1, 4)))
                self.assertEqual(index.of(mod),
                                 engine._import_identities(mod, layers),
                                 (mod, layers))

    def test_many_layers_is_not_quadratic(self):
        import time
        def run(n):
            layers = [{"name": f"l{i}", "paths": [f"src/l{i}/**"]}
                      for i in range(n)]
            entries = [(f"src/l{i}/m.py", f"l{i}",
                        [(f"l{(i + 1) % n}.m", 1)], "") for i in range(n)]
            r = rules.parse_rule_line("forbid-cycles: *")
            t = time.perf_counter()
            found = engine.evaluate(entries, layers, [r])
            self.assertEqual(len(found), 1)
            return time.perf_counter() - t
        small, big = run(250), run(2000)
        # 8x the layers: quadratic would be ~64x; allow generous noise
        self.assertLess(big, small * 30 + 0.5)
