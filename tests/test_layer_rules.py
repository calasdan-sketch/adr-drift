"""layer-order, only-import, public-api; layer globs with a wildcard
directory in the middle."""

import json
import unittest

from _support import GitCase, commit_all, init_git, make_repo, run_main

from adr_drift import engine, rules

LAYERS = ('layers:\n'
          '  - name: handlers\n    paths: ["src/handlers/**"]\n'
          '  - name: services\n    paths: ["src/services/**"]\n'
          '  - name: db\n    paths: ["src/db/**"]\n'
          '  - name: util\n    paths: ["src/util/**"]\n')


def repo(rule_lines, files, layers=LAYERS):
    body = layers + "rules:\n" + "".join(f'  - "{r}"\n' for r in rule_lines)
    base = {"arch.yaml": body, "docs/adr/0001-x.md": "# 1: X\n\n## Status\n\nAccepted\n"}
    base.update(files)
    return make_repo(base)


def check(root, *extra):
    code, out, err = run_main("--root", root, "check", "--format", "json",
                              *extra)
    return code, json.loads(out or "[]"), err


class ParseTest(unittest.TestCase):
    def test_layer_order(self):
        r = rules.parse_rule_line("layer-order: handlers > services > db")
        self.assertEqual(r.names, ["handlers", "services", "db"])
        self.assertIn("downward", r.describe())

    def test_layer_order_errors(self):
        for bad in ("layer-order: a", "layer-order: a > a",
                    "layer-order: a > > b", "layer-order: a > b c",
                    "layer-order: a > *"):
            with self.assertRaises(ValueError, msg=bad):
                rules.parse_rule_line(bad)

    def test_only_import_and_public_api(self):
        r = rules.parse_rule_line("only-import: util -> util, db # why")
        self.assertEqual((r.from_pat, r.names), ("util", ["util", "db"]))
        r = rules.parse_rule_line("public-api: db -> db.api")
        self.assertEqual((r.from_pat, r.names), ("db", ["db.api"]))
        for bad in ("public-api: db", "public-api: db ->", "only-import: a b -> c",
                    "only-import: a -> b c", "public-api: -> x"):
            with self.assertRaises(ValueError, msg=bad):
                rules.parse_rule_line(bad)

    def test_misspelt_names_are_errors_not_silence(self):
        root = repo([], {})
        # an ADR with a typo'd new rule name
        adr = ("# 2: Y\n\n## Status\n\nAccepted\n\n## Constraints\n\n"
               "- layer_order: a > b\n")
        root = make_repo({"arch.yaml": LAYERS + "rules: []\n",
                          "docs/adr/0002-y.md": adr})
        code, _out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("layer-order", err)


class LayerOrderTest(unittest.TestCase):
    FILES = {"src/handlers/h.py": "import src.services.s\nimport src.db.d\n",
             "src/services/s.py": "import src.db.d\n",
             "src/db/d.py": "x = 1\n"}

    def test_downward_is_fine(self):
        root = repo(["layer-order: handlers > services > db"], self.FILES)
        code, got, _ = check(root)
        self.assertEqual((code, got), (0, []))

    def test_upward_is_a_violation(self):
        files = dict(self.FILES, **{"src/db/d.py": "import src.handlers.h\n",
                                    "src/services/s.py": "from src.handlers import h\n"})
        root = repo(["layer-order: handlers > services > db"], files)
        code, got, _ = check(root)
        self.assertEqual(code, 1)
        self.assertEqual(sorted((v["file"], v["line"]) for v in got),
                         [("src/db/d.py", 1), ("src/services/s.py", 1)])
        self.assertIn("above", got[0]["detail"])

    def test_layers_outside_the_order_are_free(self):
        files = {"src/util/u.py": "import src.handlers.h\n",
                 "src/handlers/h.py": "x = 1\n"}
        root = repo(["layer-order: handlers > services > db"], files)
        self.assertEqual(check(root)[0], 0)

    def test_unknown_layer_warns_and_strict_fails(self):
        root = repo(["layer-order: handlers > servces > db"], self.FILES)
        code, _got, err = check(root, "--strict")
        self.assertEqual(code, 2)
        self.assertIn("servces", err)
        self.assertIn("services", err)       # did you mean


class OnlyImportTest(unittest.TestCase):
    def test_only_listed_layers(self):
        files = {"src/util/u.py": "import src.db.d\nimport src.services.s\n"
                                  "import os\nimport requests\n",
                 "src/db/d.py": "x = 1\n", "src/services/s.py": "x = 1\n"}
        root = repo(["only-import: util -> db"], files)
        code, got, _ = check(root)
        self.assertEqual((code, [(v["line"]) for v in got]), (1, [2]))
        self.assertIn("services", got[0]["detail"])

    def test_own_layer_always_allowed(self):
        files = {"src/util/u.py": "import src.util.v\n", "src/util/v.py": "x = 1\n"}
        root = repo(["only-import: util -> db"], files)
        self.assertEqual(check(root)[0], 0)


class PublicApiTest(unittest.TestCase):
    def test_outside_must_use_the_api(self):
        files = {"src/handlers/h.py": "import src.db.api\nimport src.db.conn\n"
                                      "from src.db import api\n"
                                      "from src.db import conn\n",
                 "src/db/api.py": "x = 1\n", "src/db/conn.py": "x = 1\n",
                 "src/db/inner.py": "import src.db.conn\n"}
        root = repo(["public-api: db -> src.db.api"], files)
        code, got, _ = check(root)
        self.assertEqual(code, 1)
        # line 2 and line 4 only: `from src.db import api` is the api
        self.assertEqual([v["line"] for v in got], [2, 4])
        self.assertIn("src.db.conn", got[0]["detail"])

    def test_nothing_in_the_layer_is_flagged(self):
        files = {"src/db/a.py": "import src.db.b\n", "src/db/b.py": "x = 1\n"}
        root = repo(["public-api: db -> src.db.api"], files)
        self.assertEqual(check(root)[0], 0)

    def test_unknown_layer_warning(self):
        root = repo(["public-api: dbx -> x"], {"src/db/a.py": "x = 1\n"})
        _code, _got, err = check(root)
        self.assertIn("not a layer", err)


class AdrIntegrationTest(unittest.TestCase):
    def test_rules_in_an_adr(self):
        adr = ("# 2: Layers\n\n## Status\n\nAccepted\n\n## Constraints\n\n"
               "- layer-order: handlers > services > db\n"
               "- public-api: db -> src.db.api\n")
        root = make_repo({
            "arch.yaml": LAYERS, "docs/adr/0002-layers.md": adr,
            "src/db/d.py": "import src.handlers.h\n",
            "src/handlers/h.py": "import src.db.d\n"})
        code, got, _ = check(root)
        self.assertEqual(code, 1)
        self.assertEqual({v["rule_source"].split(":")[0] for v in got},
                         {"docs/adr/0002-layers.md"})


class MiddleWildcardLayerTest(unittest.TestCase):
    """A layer glob such as src/**/db/** used to make every module under
    src a member of the layer (and **/db/** had no prefix at all)."""
    MID = ('layers:\n  - name: handlers\n    paths: ["src/handlers/**"]\n'
           '  - name: db\n    paths: ["src/**/db/**"]\n'
           '  - name: any\n    paths: ["**/models/**"]\n')

    def test_only_real_members_match(self):
        root = repo(["forbid-import: handlers -> db",
                     "forbid-import: handlers -> any"],
                    {"src/handlers/h.py":
                        "import src.svc.x\nimport src.a.db.conn\n"
                        "import src.a.models.m\nimport github.com.o.db\n",
                     "src/svc/x.py": "x = 1\n", "src/a/db/conn.py": "x = 1\n",
                     "src/a/models/m.py": "x = 1\n"}, layers=self.MID)
        code, got, _ = check(root)
        by_rule = sorted((v["rule"], v["line"]) for v in got)
        self.assertEqual(by_rule, [("handlers must not import any", 3),
                                   ("handlers must not import db", 2)])

    def test_prefix_layers_are_unchanged(self):
        ids = engine.IdentityIndex([{"name": "db", "paths": ["src/db/**"]}])
        self.assertIn("db", ids.of("src.db.conn"))
        self.assertNotIn("db", ids.of("src.dbx.conn"))

    def test_without_the_file_list_the_glob_is_tried_on_the_name(self):
        ids = engine.IdentityIndex([{"name": "db", "paths": ["**/db/**"]}])
        self.assertIn("db", ids.of("a.b.db.c"))


class DriftTest(GitCase):
    def test_new_rule_kinds_work_in_drift(self):
        root = repo(["layer-order: handlers > services > db"],
                    {"src/db/d.py": "x = 1\n", "src/handlers/h.py": "x = 1\n"})
        init_git(root)
        commit_all(root, "base")
        with open(root + "/src/db/d.py", "w") as f:
            f.write("import src.handlers.h\n")
        commit_all(root, "bad")
        code, out, _ = run_main("--root", root, "drift", "--since", "HEAD~1",
                                "--format", "json")
        self.assertEqual(code, 1)
        self.assertEqual([v["file"] for v in json.loads(out)], ["src/db/d.py"])

    def test_mid_wildcard_layer_in_drift_uses_the_whole_tree(self):
        mid = ('layers:\n  - name: handlers\n    paths: ["src/handlers/**"]\n'
               '  - name: db\n    paths: ["**/db/**"]\n')
        root = repo(["forbid-import: handlers -> db"],
                    {"src/handlers/h.py": "x = 1\n", "src/a/db/c.py": "x = 1\n"},
                    layers=mid)
        init_git(root)
        commit_all(root, "base")
        with open(root + "/src/handlers/h.py", "w") as f:
            f.write("import src.a.db.c\n")      # db/c.py itself did not change
        commit_all(root, "bad")
        code, out, _ = run_main("--root", root, "drift", "--since", "HEAD~1",
                                "--format", "json")
        self.assertEqual(code, 1, out)


if __name__ == "__main__":
    unittest.main()
