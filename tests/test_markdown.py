"""CODEOWNERS, ADR owners and the pull-request comment (--format markdown)."""

import re
import time
import unittest
from unittest import mock

from _support import make_repo, run_main

from adr_drift import codeowners, config, report

ARCH = ('layers:\n  - name: handlers\n    paths: ["src/handlers/**"]\n'
        '  - name: db\n    paths: ["src/db/**"]\n'
        'rules:\n  - "forbid-import: handlers -> db"\n')
ADR = ("---\nstatus: accepted\ndeciders: Ann Lee, @evil-bot\n---\n"
       "# Handlers stay off the DB\n\n## Decision\n\nGo through services.\n\n"
       "## Constraints\n\n- forbid-import: handlers -> db\n")


class CodeOwnersTest(unittest.TestCase):
    def test_last_match_wins_and_anchoring(self):
        c = codeowners.CodeOwners(
            "* @all\n*.js @js\n/docs/* @docs # note\n/build/logs/ @ops\n"
            "apps/ @apps\n/secret\n[Section]\n!negated @x\n")
        want = {"a.py": ["@all"], "x/y.js": ["@js"], "docs/a.md": ["@docs"],
                "docs/sub/a.md": ["@all"], "build/logs/a/b": ["@ops"],
                "pkg/apps/x.py": ["@apps"], "secret": [], "xsecret": ["@all"]}
        for path, owners in want.items():
            self.assertEqual(c.owners(path), owners, path)

    def test_hostile_patterns_do_not_hang_or_crash(self):
        text = "\n".join(["*" * 300 + " @a", "[" * 50 + " @b", "(" * 80 + " @c",
                          "a/**/**/**/**/**/b @d", "\\" * 10 + " @e"])
        text += ("\n" + "/".join(["**"] * 40) + "/b @f\n" + "*" * 200 + "x @g\n"
                 + "a/**/b/**/c/**/d/**/e/**/f @h")
        c = codeowners.CodeOwners(text)
        t0 = time.perf_counter()
        c.owners("a/" + "b/" * 200 + "c")
        c.owners("a" * 5000)
        self.assertLess(time.perf_counter() - t0, 2.0)   # no backtracking blow-up

    def test_load_from_github_dir(self):
        root = make_repo({".github/CODEOWNERS": "* @x\n"})
        self.assertEqual(codeowners.load(root).owners("a"), ["@x"])
        self.assertEqual(codeowners.load(make_repo({})).owners("a"), [])


class AdrMetadataTest(unittest.TestCase):
    def test_owner_forms(self):
        lines = ADR.split("\n")
        self.assertEqual(config.adr_owners(lines), ["Ann Lee", "@evil-bot"])
        self.assertEqual(config.adr_owners(
            "---\nowners:\n  - a\n  - b\n---\n# T\n".split("\n")), ["a", "b"])
        self.assertEqual(config.adr_owners(
            "# T\n\n* Deciders: x, y and z\n* Status: ok\n".split("\n")),
            ["x", "y", "z"])
        self.assertEqual(config.adr_owners("# T\n\n## Status\n\nok\n".split("\n")), [])
        self.assertEqual(config.adr_owners(
            "# T\n\n```\nOwner: nobody\n```\n".split("\n")), [])

    def test_decision_excerpt_is_bounded(self):
        text = "# T\n\n## Decision Outcome\n\n" + "word " * 500 + "\n"
        d = config.adr_decision(text.split("\n"))
        self.assertLessEqual(len(d), 400)

    def test_code_refs(self):
        refs = config.adr_code_refs(
            "see `src/db/` and `a/b.py`, not `x` nor `../up/x` nor "
            "`https://x/y` nor `a b/c`".split("\n"))
        self.assertEqual([r for r, _ in refs], ["src/db", "a/b.py"])


class EscapeTest(unittest.TestCase):
    def test_nothing_from_the_repo_acts_on_markdown(self):
        out = report._md("[x](http://e) <img src=x onerror=1> @org/team "
                         "# h | cell *b* `c` \x1b[31m‮")
        for bad in ("<img", "](http", "@org", "|", "\x1b", "‮"):
            self.assertNotIn(bad, out.replace("\\|", ""), bad)
        self.assertIn("&#64;org/team", out)

    def test_code_span_survives_backticks(self):
        self.assertEqual(report._code("a`b"), "``a`b``")
        self.assertEqual(report._code("`"), "`` ` ``")


class CommentTest(unittest.TestCase):
    def root(self, adr=ADR, extra=None):
        files = {"arch.yaml": "layers:\n  - name: handlers\n    paths: [\"src/handlers/**\"]\n"
                              "  - name: db\n    paths: [\"src/db/**\"]\nrules: []\n",
                 "docs/adr/0001-handlers.md": adr,
                 ".github/CODEOWNERS": "/src/handlers/ @org/web\n",
                 "src/handlers/h.py": "import src.db.d\n", "src/db/d.py": "x=1\n"}
        files.update(extra or {})
        return make_repo(files)

    def test_comment_has_adr_owner_and_code_owner(self):
        code, out, _ = run_main("--root", self.root(), "check", "--format",
                                "markdown")
        self.assertEqual(code, 1)
        self.assertIn("### adr-drift: 1 architecture violation", out)
        self.assertIn("**handlers must not import db**", out)
        self.assertIn("Handlers stay off the DB", out)
        self.assertIn("status: accepted", out)
        self.assertIn("Go through services.", out)
        self.assertIn("`Ann Lee`", out)
        self.assertIn("`@org/web`", out)
        self.assertIn("`src/handlers/h.py`", out)
        # an @-name is only ever inside a code span: it cannot notify anyone
        for m in re.finditer(r"@[\w-]+", out):
            self.assertEqual(out[m.start() - 1], "`", out[m.start() - 5:m.end()])

    def test_hostile_adr_text(self):
        adr = ("# <script>alert(1)</script> [click](http://x)\n\n## Status\n\n"
               "Accepted\n\n## Decision\n\n@everyone ![i](http://t/p.png)\n\n"
               "## Constraints\n\n- forbid-import: handlers -> db\n")
        _c, out, _ = run_main("--root", self.root(adr), "check", "--format",
                              "markdown")
        self.assertNotIn("<script>", out)
        self.assertIsNone(re.search(r"(?<!\\)\]\(", out))
        self.assertNotIn("@everyone", out)

    def test_clean_repo(self):
        root = self.root(extra={"src/handlers/h.py": "x = 1\n"})
        code, out, _ = run_main("--root", root, "check", "--format", "markdown")
        self.assertEqual(code, 0)
        self.assertIn("no architecture drift", out)

    def test_size_cap(self):
        files = {f"src/handlers/h{i}.py": "import src.db.d\n" for i in range(30)}
        with mock.patch.object(report, "MARKDOWN_LIMIT", 900):
            _c, out, _ = run_main("--root", self.root(extra=files), "check",
                                  "--format", "markdown")
        self.assertLessEqual(len(out), 1200)
        self.assertIn("report cut", out)

    def test_row_cap_per_rule(self):
        files = {f"src/handlers/h{i}.py": "import src.db.d\n" for i in range(40)}
        _c, out, _ = run_main("--root", self.root(extra=files), "check",
                              "--format", "markdown")
        self.assertIn("and 16 more", out)

    def test_no_rules_markdown_is_not_a_traceback(self):
        root = make_repo({"arch.yaml": "layers: []\n"})
        code, out, err = run_main("--root", root, "check", "--format",
                                  "markdown", "--allow-empty")
        self.assertEqual(code, 0)
        self.assertIn("no architecture drift", out)


if __name__ == "__main__":
    unittest.main()
