"""Engine + config tests against the shop fixture."""

import os
import unittest

from adr_drift import config as c
from adr_drift import engine as e
from adr_drift import rules as rules_mod

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "shop")


class TestConfig(unittest.TestCase):
    def test_load_arch_yaml(self):
        layers, rules = c.load_arch_yaml(FIXTURE)
        self.assertEqual([l["name"] for l in layers],
                         ["handlers", "services", "db"])
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].kind, "forbid-import")

    def test_parse_adr(self):
        adrs = [c.parse_adr(p) for p in c.adr_files(FIXTURE)]
        self.assertEqual(len(adrs), 2)
        by_num = {a["number"]: a for a in adrs}
        self.assertEqual(by_num["0001"]["status"], "Accepted")
        self.assertEqual(len(by_num["0001"]["rules"]), 2)
        self.assertEqual(len(by_num["0002"]["rules"]), 1)

    def test_bad_constraint_raises(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "docs", "adr"))
        p = os.path.join(tmp, "docs", "adr", "0001-x.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# X\n\n## Constraints\n\n- forbid-import: nope\n")
        with self.assertRaises(ValueError):
            c.parse_adr(p)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.layers, self.rules, _ = c.load_all_rules(FIXTURE)

    def test_finds_all_violations(self):
        vs = e.check(FIXTURE, self.layers, self.rules)
        self.assertEqual(len(vs), 4)
        details = {(v.file, v.rule.kind) for v in vs}
        self.assertIn(("src/handlers/orders.py", "forbid-import"), details)
        self.assertIn(("src/handlers/orders.py", "require-pattern"), details)
        self.assertIn(("src/util.py", "forbid-pattern"), details)

    def test_clean_files_not_flagged(self):
        vs = e.check(FIXTURE, self.layers, self.rules)
        files = {v.file for v in vs}
        self.assertNotIn("src/handlers/users.py", files)
        self.assertNotIn("src/services/orders.py", files)
        self.assertNotIn("src/db/drivers.py", files)

    def test_services_may_use_drivers(self):
        # No rule forbids services -> db.drivers, so this must stay clean.
        vs = e.check(FIXTURE, self.layers, self.rules)
        svc = [v for v in vs if v.file == "src/services/orders.py"]
        self.assertEqual(svc, [])

    def test_violation_dict_shape(self):
        vs = e.check(FIXTURE, self.layers, self.rules)
        d = vs[0].as_dict()
        for key in ("rule", "rule_source", "file", "line", "detail"):
            self.assertIn(key, d)

    def test_no_rules_no_violations(self):
        self.assertEqual(e.check(FIXTURE, self.layers, []), [])


class TestSrcLayoutToSide(unittest.TestCase):
    """To-side patterns must resolve layer names on src/-layout repos.

    Regression: the README's own example rule never fired when the
    violating import carried the src. prefix (e.g. "src.db.drivers"),
    because the to-side was only matched textually against the raw
    import module — a silent false negative.
    """

    LAYERS = [
        {"name": "handlers", "paths": ["src/handlers/**"]},
        {"name": "db", "paths": ["src/db/**"]},
    ]

    def _violations(self, import_mod, rule_text):
        rule = rules_mod.parse_rule_line(rule_text, source="arch.yaml")
        entries = [("src/handlers/a.py", "handlers",
                    [(import_mod, 1)], "")]
        return e.evaluate(entries, self.LAYERS, [rule])

    def test_layer_name_to_side_src_prefixed_import(self):
        vs = self._violations("src.db.drivers",
                              "forbid-import: handlers -> db")
        self.assertEqual(len(vs), 1)

    def test_readme_literal_rule_src_prefixed_import(self):
        vs = self._violations("src.db.drivers",
                              "forbid-import: handlers -> db.drivers")
        self.assertEqual(len(vs), 1)

    def test_unprefixed_import_still_fires(self):
        vs = self._violations("db.drivers",
                              "forbid-import: handlers -> db")
        self.assertEqual(len(vs), 1)

    def test_unrelated_module_not_flagged(self):
        # "src.other.x" shares the src/ root but is not the db layer.
        vs = self._violations("src.other.thing",
                              "forbid-import: handlers -> db")
        self.assertEqual(vs, [])

    def test_mismatched_layer_name_still_misses(self):
        vs = self._violations("src.db.drivers",
                              "forbid-import: handlers -> cache")
        self.assertEqual(vs, [])


class TestSyntaxErrorWarning(unittest.TestCase):
    def test_check_warns_and_keeps_other_violations(self):
        import io
        import tempfile
        from contextlib import redirect_stderr
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "arch.yaml"), "w", encoding="utf-8") as f:
            f.write('layers:\n'
                    '  - name: a\n    paths: ["a/**"]\n'
                    '  - name: b\n    paths: ["b/**"]\n'
                    'rules:\n'
                    '  - "forbid-import: a -> b"\n')
        os.makedirs(os.path.join(tmp, "a"))
        with open(os.path.join(tmp, "a", "bad.py"), "w", encoding="utf-8") as f:
            f.write("def broken(:\n")
        with open(os.path.join(tmp, "a", "ok.py"), "w", encoding="utf-8") as f:
            f.write("from b import thing\n")
        layers, rules = c.load_arch_yaml(tmp)
        buf = io.StringIO()
        with redirect_stderr(buf):
            vs = e.check(tmp, layers, rules)
        self.assertIn("a/bad.py", buf.getvalue())
        # the parseable violator is still caught; the broken file is not
        # silently treated as clean — it is skipped with a warning.
        self.assertEqual([v.file for v in vs], ["a/ok.py"])



class TestRelativeAndFromImports(unittest.TestCase):
    """Relative imports and from-import names must reach import rules."""

    def _repo(self, files: dict) -> str:
        import tempfile
        tmp = tempfile.mkdtemp()
        for rel, body in files.items():
            p = os.path.join(tmp, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
        return tmp

    def _check(self, root, rule):
        layers = [{"name": "handlers", "paths": ["app/handlers/**"]},
                  {"name": "db", "paths": ["app/db/**"]}]
        return e.check(root, layers, [rules_mod.parse_rule_line(rule)])

    def test_relative_import_resolves_to_module(self):
        root = self._repo({
            "app/__init__.py": "", "app/db/__init__.py": "",
            "app/db/drivers/pg.py": "",
            "app/handlers/__init__.py": "",
            "app/handlers/orders.py": "from ..db.drivers import pg\n"})
        v = self._check(root, "forbid-import: handlers -> db.drivers")
        self.assertEqual([(x.file, x.line) for x in v],
                         [("app/handlers/orders.py", 1)])

    def test_from_import_name_matches_submodule_rule(self):
        root = self._repo({
            "app/db/drivers.py": "",
            "app/handlers/orders.py": "from app.db import drivers\n"})
        v = self._check(root, "forbid-import: handlers -> db.drivers")
        self.assertEqual(len(v), 1)  # reported once, not once per name

    def test_relative_import_in_package_init(self):
        root = self._repo({
            "app/handlers/__init__.py": "from .. import db\n",
            "app/db/__init__.py": ""})
        v = self._check(root, "forbid-import: handlers -> db")
        self.assertEqual(len(v), 1)

    def test_ts_relative_js_specifier_maps_to_ts_module(self):
        # TS convention: "./x.js" refers to x.ts.
        root = self._repo({
            "app/db/config.ts": "export const x = 1;\n",
            "app/handlers/a.ts":
                'import { x } from "../db/config.js";\n'})
        v = self._check(root, "forbid-import: handlers -> db.config")
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].detail, "imports app.db.config")


class TestRepoRelativeRuleSource(unittest.TestCase):
    def test_adr_rule_source_is_repo_relative(self):
        _layers, all_rules, _adrs = c.load_all_rules(FIXTURE)
        adr_sources = {r.source for r in all_rules
                       if r.source != c.ARCH_FILE}
        self.assertEqual(adr_sources, {
            "docs/adr/0001-layered-architecture.md",
            "docs/adr/0002-no-dynamic-eval.md"})


if __name__ == "__main__":
    unittest.main()
