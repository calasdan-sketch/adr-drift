"""Expert round 3: hostile rules and files, the layer graph, suggestions and
the diff of the rules themselves."""

import json
import os
import sys
import time
import unittest
from unittest import mock

from _support import (GitCase, commit_all, init_git, make_repo, run_main,
                      write_files)

LAYERS = ('layers:\n'
          '  - name: a\n    paths: ["src/a/**"]\n'
          '  - name: b\n    paths: ["src/b/**"]\n'
          '  - name: c\n    paths: ["src/c/**"]\n')
STATUS = "# 1: X\n\n## Status\n\nAccepted\n"


def repo(rules, files=None, status=STATUS):
    base = {"arch.yaml": LAYERS + "rules:\n" + rules,
            "docs/adr/0001-x.md": status}
    base.update(files or {})
    return make_repo(base)


@unittest.skipUnless(hasattr(__import__("signal"), "setitimer"),
                     "needs SIGALRM (POSIX)")
class CatastrophicRegex(unittest.TestCase):
    RULE = '  - "forbid-pattern: src/**.js !~ (a+)+$"\n'

    def test_hostile_pattern_is_stopped_with_exit_2(self):
        r = repo(self.RULE, {"src/a/x.js": "a" * 40 + "!\n"})
        started = time.monotonic()
        with mock.patch.dict(os.environ, {"ADR_DRIFT_REGEX_SECONDS": "1"}):
            code, out, err = run_main("--root", r, "check", "--format", "json")
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("backtracks", err)
        self.assertIn("ADR_DRIFT_REGEX_SECONDS", err)

    def test_the_timer_is_cleared_afterwards(self):
        import signal
        r = repo('  - "forbid-pattern: src/**.js !~ eval"\n',
                 {"src/a/x.js": "eval(1)\n"})
        code, _o, _e = run_main("--root", r, "check")
        self.assertEqual(code, 1)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_zero_turns_the_guard_off(self):
        r = repo('  - "forbid-pattern: src/**.js !~ eval"\n',
                 {"src/a/x.js": "eval(1)\n"})
        with mock.patch.dict(os.environ, {"ADR_DRIFT_REGEX_SECONDS": "0"}):
            self.assertEqual(run_main("--root", r, "check")[0], 1)


class DriftFileLimit(GitCase):
    def test_drift_skips_an_oversized_file_with_a_warning(self):
        r = repo('  - "forbid-pattern: src/**.py !~ eval"\n',
                 {"src/a/m.py": "x = 1\n"})
        init_git(r)
        commit_all(r, "one")
        write_files(r, {"src/a/m.py": "eval(1)\n" + "x = 1\n" * 100})
        commit_all(r, "two")
        with mock.patch.dict(os.environ, {"ADR_DRIFT_MAX_FILE_BYTES": "100"}):
            code, _o, err = run_main("--root", r, "drift", "--since", "HEAD~1")
            strict = run_main("--root", r, "drift", "--since", "HEAD~1",
                              "--strict")[0]
        self.assertEqual(code, 0)
        self.assertIn("over the 100-byte limit", err)
        self.assertEqual(strict, 2)


class HostileRegexSyntax(unittest.TestCase):
    def test_deeply_nested_group_is_a_config_error_not_a_traceback(self):
        n = 100000
        r = repo('  - "forbid-pattern: src/**.py !~ ' + "(" * n + "a" + ")" * n
                 + '"\n', {"src/a/m.py": "x = 1\n"})
        code, out, err = run_main("--root", r, "check")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("nested too deeply", err)
        self.assertNotIn("Traceback", err)


class FileLimits(unittest.TestCase):
    RULE = '  - "forbid-pattern: src/**.py !~ eval"\n'

    def test_oversized_file_is_skipped_with_a_warning(self):
        r = repo(self.RULE, {"src/a/big.py": "eval(1)\n" + "x = 1\n" * 100})
        with mock.patch.dict(os.environ, {"ADR_DRIFT_MAX_FILE_BYTES": "100"}):
            code, _o, err = run_main("--root", r, "check")
            strict = run_main("--root", r, "check", "--strict")[0]
        self.assertEqual(code, 0)
        self.assertIn("over the 100-byte limit", err)
        self.assertEqual(strict, 2)       # never a silent pass

    def test_small_file_is_still_checked(self):
        r = repo(self.RULE, {"src/a/ok.py": "eval(1)\n"})
        self.assertEqual(run_main("--root", r, "check")[0], 1)

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges")
    def test_symlink_out_of_the_repo_is_not_read(self):
        outside = make_repo({"secret.py": "eval(1)\n"})
        r = repo(self.RULE, {"src/a/ok.py": "x = 1\n"})
        os.symlink(os.path.join(outside, "secret.py"),
                   os.path.join(r, "src", "a", "link.py"))
        code, out, err = run_main("--root", r, "check")
        self.assertEqual(code, 0, out)
        self.assertIn("src/a/link.py: symbolic link to a file outside", err)
        self.assertEqual(run_main("--root", r, "check", "--strict")[0], 2)

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges")
    def test_symlink_inside_the_repo_is_read(self):
        r = repo(self.RULE, {"src/a/real.py": "eval(1)\n"})
        os.symlink(os.path.join(r, "src", "a", "real.py"),
                   os.path.join(r, "src", "a", "alias.py"))
        code, out, _e = run_main("--root", r, "check", "--format", "json")
        self.assertEqual(code, 1)
        self.assertEqual({v["file"] for v in json.loads(out)},
                         {"src/a/real.py", "src/a/alias.py"})


CHAIN = {"src/a/m.py": "import src.b.x\n", "src/b/x.py": "import src.c.y\n",
         "src/c/y.py": "x = 1\n"}
LOOP = dict(CHAIN, **{"src/c/y.py": "import src.a.m\n"})


class Graph(unittest.TestCase):
    def test_mermaid_has_every_layer_and_edge_counts(self):
        r = repo('  - "forbid-cycles: *"\n', CHAIN)
        code, out, _e = run_main("--root", r, "graph")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("flowchart TD"))
        self.assertEqual(out.count("-->"), 2)
        self.assertNotIn("linkStyle", out)

    def test_cycle_edges_are_marked(self):
        r = repo('  - "forbid-cycles: *"\n', LOOP)
        _c, mer, _e = run_main("--root", r, "graph")
        self.assertIn("linkStyle 0,1,2", mer)
        _c, dot, _e = run_main("--root", r, "graph", "--format", "dot")
        self.assertEqual(dot.count('color="red"'), 3)

    def test_json_edges_and_a_from_import_counts_once(self):
        r = repo('  - "forbid-cycles: *"\n',
                 {"src/a/m.py": "from src.b import x\n",
                  "src/b/x.py": "y = 1\n"})
        _c, out, _e = run_main("--root", r, "graph", "--format", "json")
        edges = json.loads(out)
        self.assertEqual([(e["from"], e["to"], e["count"]) for e in edges],
                         [("a", "b", 1)])

    def test_no_layers_is_an_error(self):
        r = make_repo({"docs/adr/0001-x.md":
                       STATUS + "\n## Constraints\n\nforbid-pattern: **.py !~ x\n"})
        self.assertEqual(run_main("--root", r, "graph")[0], 2)


class GraphEscaping(unittest.TestCase):
    def test_markup_in_a_layer_name_is_inert(self):
        from adr_drift import graphs
        g = {'a"<b>\x1b[31m': {"z": {"count": 1}}, "z": {}}
        m, d = graphs.mermaid(g), graphs.dot(g)
        for text in (m, d):
            self.assertNotIn("\x1b", text)
        self.assertNotIn("<b>", m)
        self.assertIn("#quot;", m)
        self.assertIn('a\\"', d)

    def test_unspellable_layer_names_get_no_rule(self):
        from adr_drift import graphs
        out = graphs.suggest({"a b": {}, "c": {}})
        self.assertEqual(out["rules"], [])
        self.assertIn("a b", out["notes"][0])


class Suggest(unittest.TestCase):
    def test_orders_an_acyclic_graph(self):
        r = repo('  - "forbid-pattern: src/**.py !~ eval"\n', CHAIN)
        code, out, _e = run_main("--root", r, "suggest")
        self.assertEqual(code, 0)
        self.assertIn('"forbid-cycles: *"', out)
        self.assertIn('"layer-order: a > b > c"', out)

    def test_suggested_rules_pass_when_added(self):
        r = repo('  - "forbid-pattern: src/**.py !~ eval"\n', CHAIN)
        _c, out, _e = run_main("--root", r, "suggest", "--format", "json")
        rules = json.loads(out)["rules"]
        write_files(r, {"arch.yaml": LAYERS + "rules:\n" + "".join(
            f'  - "{x}"\n' for x in rules)})
        self.assertEqual(run_main("--root", r, "check")[0], 0)

    def test_a_cycle_gets_no_rules_but_a_note(self):
        r = repo('  - "forbid-pattern: src/**.py !~ eval"\n', LOOP)
        _c, out, err = run_main("--root", r, "suggest")
        self.assertIn("nothing to suggest", out)
        self.assertIn("a, b, c", err)

    def test_existing_kinds_are_not_suggested_again(self):
        r = repo('  - "forbid-cycles: *"\n  - "layer-order: a > b > c"\n',
                 CHAIN)
        self.assertIn("nothing to suggest", run_main("--root", r, "suggest")[1])


class RulesDiff(GitCase):
    def make(self):
        r = repo('  - "forbid-cycles: *"\n  - "layer-order: a > b > c"\n',
                 CHAIN)
        init_git(r)
        commit_all(r, "one")
        return r

    def test_no_change(self):
        r = self.make()
        write_files(r, {"src/a/new.py": "x = 1\n"})
        commit_all(r, "two")
        code, out, _e = run_main("--root", r, "rules-diff", "--since", "HEAD~1")
        self.assertEqual(code, 0)
        self.assertIn("no rule changes", out)

    def test_removed_rule_and_waiver_are_weakening(self):
        r = self.make()
        write_files(r, {"arch.yaml": LAYERS + 'rules:\n  - "forbid-cycles: *"\n'
                        'waivers:\n  - rule: "layers * must not depend on '
                        'each other in a cycle"\n    owner: bob\n'
                        '    expires: 2099-01-01\n    reason: later\n'})
        commit_all(r, "weaken")
        code, out, _e = run_main("--root", r, "rules-diff", "--since", "HEAD~1")
        self.assertEqual(code, 0)
        self.assertIn("rule removed", out)
        self.assertIn("may only import downward", out)
        self.assertIn("waiver added", out)
        code, _o, _e = run_main("--root", r, "rules-diff", "--since", "HEAD~1",
                                "--fail-on-weakened")
        self.assertEqual(code, 1)

    def test_retiring_an_adr_is_weakening(self):
        r = repo('  - "forbid-cycles: *"\n', CHAIN)
        write_files(r, {"docs/adr/0002-y.md":
                        "# 2: Y\n\n## Status\n\nAccepted\n\n## Constraints\n\n"
                        "forbid-pattern: src/**.py !~ eval\n"})
        init_git(r)
        commit_all(r, "one")
        write_files(r, {"docs/adr/0003-z.md": "# 3: Z\n\n## Status\n\nAccepted\n",
                        "docs/adr/0002-y.md":
                        "# 2: Y\n\n## Status\n\nSuperseded by 0003\n\n"
                        "## Constraints\n\nforbid-pattern: src/**.py !~ eval\n"})
        commit_all(r, "retire")
        _c, out, _e = run_main("--root", r, "rules-diff", "--since", "HEAD~1",
                               "--format", "json")
        weak = json.loads(out)["weakened"]
        self.assertTrue(any("ADR retired" in w for w in weak), weak)

    def test_added_rule_is_strengthening_and_working_tree_is_ignored(self):
        r = self.make()
        write_files(r, {"arch.yaml": LAYERS + 'rules:\n  - "forbid-cycles: *"\n'
                        '  - "layer-order: a > b > c"\n'
                        '  - "forbid-pattern: src/**.py !~ eval"\n'})
        commit_all(r, "strengthen")
        write_files(r, {"arch.yaml": LAYERS})        # uncommitted, ignored
        _c, out, _e = run_main("--root", r, "rules-diff", "--since", "HEAD~1",
                               "--fail-on-weakened")
        self.assertIn("rule added", out)
        self.assertNotIn("Weakened", out)

    def test_narrowing_a_layer_is_weakening(self):
        r = self.make()
        write_files(r, {"arch.yaml": LAYERS.replace('"src/c/**"', '"src/c/only/**"')
                        + 'rules:\n  - "forbid-cycles: *"\n'
                        '  - "layer-order: a > b > c"\n'})
        commit_all(r, "narrow")
        _c, out, _e = run_main("--root", r, "rules-diff", "--since", "HEAD~1",
                               "--format", "json")
        self.assertIn("layer c no longer covers: src/c/**",
                      json.loads(out)["weakened"])

    def test_bad_ref_is_exit_2(self):
        r = self.make()
        self.assertEqual(run_main("--root", r, "rules-diff", "--since",
                                  "nope")[0], 2)

    def test_hostile_path_in_history_is_not_written_outside(self):
        from adr_drift import rulediff
        self.assertFalse(rulediff._safe("../x.md"))
        self.assertFalse(rulediff._safe("/etc/x.md"))
        self.assertFalse(rulediff._safe("C:/x.md"))
        self.assertTrue(rulediff._safe("docs/adr/0001-x.md"))


if __name__ == "__main__":
    unittest.main()
