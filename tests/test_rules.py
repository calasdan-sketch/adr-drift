"""Rule DSL parsing + pattern matching tests."""

import unittest

from adr_drift import rules as r


class TestParse(unittest.TestCase):
    def test_forbid_import(self):
        rule = r.parse_rule_line("forbid-import: handlers -> db.drivers")
        self.assertEqual(rule.kind, "forbid-import")
        self.assertEqual(rule.from_pat, "handlers")
        self.assertEqual(rule.to_pat, "db.drivers")

    def test_forbid_pattern(self):
        rule = r.parse_rule_line(r"forbid-pattern: src/**.py !~ \beval\(")
        self.assertEqual(rule.kind, "forbid-pattern")
        self.assertEqual(rule.glob, "src/**.py")
        self.assertEqual(rule.regex, r"\beval\(")

    def test_require_pattern(self):
        rule = r.parse_rule_line("require-pattern: src/handlers/**.py =~ auth\\(")
        self.assertEqual(rule.kind, "require-pattern")

    def test_comments_and_blanks(self):
        self.assertIsNone(r.parse_rule_line("# just a comment"))
        self.assertIsNone(r.parse_rule_line("   "))
        # trailing comments are stripped, the rule still parses
        rule = r.parse_rule_line("forbid-import: a -> b # trailing")
        self.assertIsNotNone(rule)
        self.assertEqual(rule.from_pat, "a")
        self.assertEqual(rule.to_pat, "b")

    def test_bullets_stripped_by_caller(self):
        # parse_rule_line itself doesn't strip bullets; config does.
        self.assertIsNone(r.parse_rule_line("- forbid-import: a -> b"))

    def test_bad_rules_raise(self):
        with self.assertRaises(ValueError):
            r.parse_rule_line("forbid-import: only-one-side")
        with self.assertRaises(ValueError):
            r.parse_rule_line("forbid-pattern: src/**.py =~ missing-op-wrong")
        with self.assertRaises(ValueError):
            r.parse_rule_line("require-pattern: src/**.py !~ wrong-op")
        with self.assertRaises(ValueError):
            r.parse_rule_line("forbid-pattern: src/**.py !~ ([unclosed")

    def test_describe(self):
        rule = r.parse_rule_line("forbid-import: handlers -> db.drivers")
        self.assertIn("handlers", rule.describe())
        self.assertIn("db.drivers", rule.describe())


class TestMatchPat(unittest.TestCase):
    def test_exact_layer(self):
        self.assertTrue(r.match_pat("handlers", "handlers"))

    def test_module_prefix(self):
        self.assertTrue(r.match_pat("db.drivers", "db.drivers.pg"))
        self.assertTrue(r.match_pat("db.drivers", "db.drivers"))

    def test_no_partial_word_match(self):
        self.assertFalse(r.match_pat("db.driver", "db.drivers"))
        self.assertFalse(r.match_pat("handle", "handlers"))

    def test_slash_and_dot_equivalent(self):
        self.assertTrue(r.match_pat("src/handlers", "src.handlers"))

    def test_wildcard(self):
        self.assertTrue(r.match_pat("db.*", "db.drivers"))
        self.assertTrue(r.match_pat("*", "anything.at.all"))


class TestMatchGlob(unittest.TestCase):
    def test_doublestar(self):
        self.assertTrue(r.match_glob("src/**.py", "src/a/b/c.py"))
        self.assertTrue(r.match_glob("src/**.py", "src/a.py"))

    def test_no_match(self):
        self.assertFalse(r.match_glob("src/**.py", "tests/a.py"))

    def test_slash_doublestar_slash_also_matches_zero_directories(self):
        # "src/**/x.py" is x.py anywhere under src/, directly in it too.
        self.assertTrue(r.match_glob("src/**/x.py", "src/x.py"))
        self.assertTrue(r.match_glob("src/**/x.py", "src/a/x.py"))
        self.assertTrue(r.match_glob("src/**/x.py", "src/a/b/x.py"))
        self.assertTrue(r.match_glob("**/x.py", "x.py"))
        self.assertTrue(r.match_glob("**/x.py", "a/b/x.py"))
        self.assertTrue(r.match_glob("a/**/b/**/c.py", "a/b/c.py"))
        self.assertTrue(r.match_glob("a/**/b/**/c.py", "a/x/b/y/c.py"))

    def test_slash_doublestar_slash_does_not_overreach(self):
        self.assertFalse(r.match_glob("src/**/x.py", "other/x.py"))
        self.assertFalse(r.match_glob("src/**/x.py", "src/ax.py"))
        self.assertFalse(r.match_glob("src/**/x.py", "src/x.pyc"))
        self.assertFalse(r.match_glob("**/x.py", "ax.py"))
        self.assertFalse(r.match_glob("a/**/b/**/c.py", "a/c.py"))

    def test_windows_separators_and_case(self):
        self.assertTrue(r.match_glob("src/**/x.py", "src\\a\\x.py"))
        self.assertTrue(r.match_glob("src\\**\\x.py", "src/x.py"))
        self.assertFalse(r.match_glob("src/**.py", "SRC/a.py"))  # case-sensitive

    def test_a_single_star_stays_within_the_pattern(self):
        self.assertTrue(r.match_glob("src/*.py", "src/a.py"))
        self.assertFalse(r.match_glob("src/*.py", "src/a.js"))
        self.assertFalse(r.match_glob("src/a?.py", "src/abc.py"))


if __name__ == "__main__":
    unittest.main()
