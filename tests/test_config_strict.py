"""A rule must never vanish silently.

Regressions for the panel finding "rules silently dropped, exit 0": an
unquoted `- forbid-import: a -> b` in arch.yaml, ADR lines in backticks or
numbered lists, `path:` for `paths:` - each used to end as "no rules found"
or "no architecture drift" with exit 0 while the repo was not enforced.
"""

import json
import os
import unittest

from adr_drift import config as c
from adr_drift import rules as r

from _support import make_repo, run_main

ARCH_HEAD = ('layers:\n'
             '  - name: a\n    paths: ["a/**"]\n'
             '  - name: b\n    paths: ["b/**"]\n')
VIOLATOR = {"a/x.py": "from b import thing\n", "b/__init__.py": "thing = 1\n"}


def adr(*constraint_lines, heading="## Constraints"):
    return ("# 1: Layers\n\n## Status\n\nAccepted\n\n"
            + heading + "\n\n" + "\n".join(constraint_lines) + "\n\n"
            "## Consequences\n\nNone.\n")


class TestUnquotedRuleInArchYaml(unittest.TestCase):
    def test_unquoted_forbid_import_is_a_rule_not_dropped(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules:\n"
                          "  - forbid-import: a -> b\n", **VIOLATOR})
        _layers, rules = c.load_arch_yaml(root)
        self.assertEqual([x.kind for x in rules], ["forbid-import"])
        self.assertEqual((rules[0].from_pat, rules[0].to_pat), ("a", "b"))
        self.assertEqual(rules[0].line_no, 7)  # the real line in arch.yaml
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 1, out + err)
        self.assertIn("a/x.py", out)

    def test_unquoted_pattern_rules_are_rules_too(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules:\n"
                          "  - forbid-pattern: a/**.py !~ eval\\(\n"
                          "  - require-pattern: a/**.py =~ import\n"})
        _layers, rules = c.load_arch_yaml(root)
        self.assertEqual([x.kind for x in rules],
                         ["forbid-pattern", "require-pattern"])
        self.assertEqual(rules[0].regex, "eval\\(")

    def test_rule_with_extra_keys_is_an_error(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules:\n"
                          "  - forbid-import: a -> b\n"
                          "    reason: layering\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("arch.yaml:7", err)
        self.assertIn("extra keys", err)

    def test_rules_given_as_one_string_is_an_error(self):
        root = make_repo({"arch.yaml": ARCH_HEAD
                          + 'rules: "forbid-import: a -> b"\n', **VIOLATOR})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2, out + err)
        self.assertIn("'rules' must be a list", err)

    def test_garbage_rule_item_is_an_error(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules:\n"
                          '  - "no more than three layers please"\n'})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("arch.yaml:7", err)
        self.assertIn("not a rule", err)

    def test_arch_yaml_with_a_bom_loads(self):
        root = make_repo({"arch.yaml": b"\xef\xbb\xbf" + (
            ARCH_HEAD + 'rules:\n  - "forbid-import: a -> b"\n').encode()})
        _layers, rules = c.load_arch_yaml(root)
        self.assertEqual(len(rules), 1)


class TestUnknownKeys(unittest.TestCase):
    def test_path_for_paths_is_an_error_with_a_suggestion(self):
        root = make_repo({"arch.yaml": (
            'layers:\n  - name: a\n    path: ["a/**"]\n'
            '  - name: b\n    paths: ["b/**"]\n'), **VIOLATOR})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2, out + err)
        self.assertIn("arch.yaml:3", err)
        self.assertIn("unknown key 'path'", err)
        self.assertIn("did you mean 'paths'?", err)

    def test_misspelt_top_level_key_is_an_error(self):
        root = make_repo({"arch.yaml": ARCH_HEAD.replace("layers", "layer")
                          + "rules:\n  - \"forbid-import: a -> b\"\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("unknown key 'layer'", err)
        self.assertIn("did you mean 'layers'?", err)

    def test_x_prefixed_note_keys_are_allowed(self):
        root = make_repo({"arch.yaml": "x-owner: platform team\n" + ARCH_HEAD
                          + 'rules:\n  - "forbid-import: a -> b"\n'})
        _layers, rules = c.load_arch_yaml(root)
        self.assertEqual(len(rules), 1)

    def test_layer_paths_may_be_a_single_glob_string(self):
        root = make_repo({"arch.yaml": 'layers:\n  - name: a\n'
                          '    paths: "a/**"\n'})
        layers, _rules = c.load_arch_yaml(root)
        self.assertEqual(layers, [{"name": "a", "paths": ["a/**"]}])

    def test_layer_without_paths_is_an_error(self):
        root = make_repo({"arch.yaml": "layers:\n  - name: a\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("needs 'paths'", err)


class TestAdrConstraintLines(unittest.TestCase):
    def _parse(self, *lines, heading="## Constraints"):
        root = make_repo({"docs/adr/0001-layers.md": adr(*lines, heading=heading)})
        return c.parse_adr(os.path.join(root, "docs", "adr", "0001-layers.md"),
                           display="docs/adr/0001-layers.md")

    def test_backticked_numbered_and_starred_rules_all_parse(self):
        info = self._parse(
            "- `forbid-import: a -> b`",
            "1. forbid-import: a -> c",
            "2) forbid-pattern: a/**.py !~ eval",
            "* require-pattern: a/**.py =~ import",
            "- [ ] forbid-import: b -> a",
            "- **forbid-import:** c -> a",
            "- `forbid-import: c -> b` because layering")
        self.assertEqual(len(info["rules"]), 7)
        self.assertEqual(info["rules"][0].to_pat, "b")
        self.assertEqual(info["rules"][-1].from_pat, "c")

    def test_rule_lines_carry_the_real_file_line_number(self):
        info = self._parse("- forbid-import: a -> b")
        # heading is on line 7 of the document, the rule on line 9
        self.assertEqual(info["rules"][0].line_no, 9)
        self.assertEqual(info["rules"][0].where(),
                         "docs/adr/0001-layers.md:9")

    def test_commented_out_examples_and_prose_stay_inert(self):
        info = self._parse(
            "Machine-checkable rules go here.",
            "    # - forbid-import: a -> b",
            "- Data flows a -> b -> c in production, see the diagram.",
            "- We call this the forbid-import style, see the README.",
            "")
        self.assertEqual(info["rules"], [])

    def test_misspelt_rule_name_is_an_error_naming_file_and_line(self):
        for bad, hint in (("- forbid_import: a -> b", "forbid-import"),
                          ("- forbid-imports: a -> b", "forbid-import"),
                          ("- Forbid-Import: a -> b", "forbid-import"),
                          ("- forbid-patern: a/** !~ x", "forbid-pattern")):
            root = make_repo({"docs/adr/0001-layers.md": adr(bad)})
            code, out, err = run_main("--root", root, "check")
            self.assertEqual(code, 2, bad)
            self.assertIn("docs/adr/0001-layers.md:9", err, bad)
            self.assertIn(hint, err, bad)

    def test_bare_arrow_bullet_is_an_error(self):
        root = make_repo({"docs/adr/0001-layers.md": adr("- a -> b")})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("docs/adr/0001-layers.md:9", err)
        self.assertIn("forbid-import: a -> b", err)

    def test_keyword_buried_in_a_bullet_is_an_error(self):
        root = make_repo({"docs/adr/0001-layers.md":
                          adr("- Rule: forbid-import: a -> b")})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("must start with its name", err)

    def test_explanation_glued_to_a_target_is_an_error(self):
        root = make_repo({"docs/adr/0001-layers.md":
                          adr("- forbid-import: a -> b because layering")})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("contains spaces", err)

    def test_all_problems_are_reported_together(self):
        root = make_repo({
            "arch.yaml": ARCH_HEAD.replace("paths", "path"),
            "docs/adr/0001-layers.md": adr("- forbid_import: a -> b",
                                           "- a -> b")})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("arch.yaml", err)
        self.assertIn("docs/adr/0001-layers.md:9", err)
        self.assertIn("docs/adr/0001-layers.md:10", err)

    def test_near_miss_constraints_heading_warns(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + 'rules:\n  - "forbid-import: a -> b"\n',
                          "docs/adr/0001-layers.md":
                          adr("- forbid-import: b -> a", heading="### Constraints"),
                          **VIOLATOR})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 1, out + err)
        self.assertIn("docs/adr/0001-layers.md:7", err)
        self.assertIn("is not '## Constraints'", err)

    def test_html_commented_rules_are_examples_not_rules(self):
        info = self._parse("<!-- forbid-import: a -> b -->",
                           "<!--", "- forbid-import: b -> a", "-->")
        self.assertEqual(info["rules"], [])

    def test_line_numbers_survive_a_multiline_html_comment(self):
        info = self._parse("<!--", "notes", "-->", "- forbid-import: a -> b")
        self.assertEqual(info["rules"][0].line_no, 12)

    def test_missing_colon_after_the_rule_name_is_an_error(self):
        root = make_repo({"docs/adr/0001-layers.md":
                          adr("- forbid-import a -> b")})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("docs/adr/0001-layers.md:9", err)
        self.assertIn("followed by a colon", err)

    def test_rule_with_a_prefix_word_is_an_error_even_unlisted(self):
        root = make_repo({"docs/adr/0001-layers.md":
                          adr("Rule: forbid-import: a -> b")})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("must start with its name", err)

    def test_prose_that_only_names_a_keyword_is_not_a_rule(self):
        info = self._parse("See forbid-import: in the README for details.")
        self.assertEqual(info["rules"], [])

    def test_index_survives_a_bad_constraint(self):
        root = make_repo({"docs/adr/0001-layers.md": adr("- a -> b")})
        code, out, err = run_main("--root", root, "index")
        self.assertEqual(code, 0, err)
        self.assertNotIn("Traceback", err)


class TestRuleParsing(unittest.TestCase):
    def test_spaces_inside_a_target_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "contains spaces"):
            r.parse_rule_line("forbid-import: handlers -> db because layering")

    def test_clean_rule_text(self):
        self.assertEqual(r.clean_rule_text("  1. `x: y` and more"),
                         ("x: y", True))
        self.assertEqual(r.clean_rule_text("plain text"),
                         ("plain text", False))


class TestNothingEnforced(unittest.TestCase):
    """Zero rules must not look like success (exit 2) unless asked for."""

    def test_no_rules_is_exit_2_by_default(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules: []\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("no rules found", err)
        self.assertIn("--allow-empty", err)

    def test_allow_empty_accepts_it(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules: []\n"})
        code, out, err = run_main("--root", root, "check", "--allow-empty")
        self.assertEqual(code, 0)
        self.assertIn("no rules found", out)

    def test_fail_on_none_still_means_report_only(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules: []\n"})
        code, _out, _err = run_main("--root", root, "check",
                                    "--fail-on", "none")
        self.assertEqual(code, 0)

    def test_json_stdout_is_valid_json_when_empty_is_allowed(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules: []\n"})
        code, out, err = run_main("--root", root, "check", "--allow-empty",
                                  "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])
        self.assertIn("no rules found", err)  # the prose goes to stderr

    def test_json_stdout_is_empty_on_a_config_error(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules: []\n"})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")  # never a bogus "[]" next to exit 2


class TestRulesThatMatchNothing(unittest.TestCase):
    def test_glob_matching_no_file_warns(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules:\n"
                          '  - "forbid-pattern: srcc/**.py !~ eval"\n',
                          "a/x.py": "x = 1\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, out + err)
        self.assertIn("arch.yaml:7", err)
        self.assertIn("'srcc/**.py' matches no scanned source file", err)

    def test_from_side_matching_nothing_warns(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules:\n"
                          '  - "forbid-import: handlrs -> b"\n', **VIOLATOR})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0)
        self.assertIn("'handlrs' matches no layer name or source path", err)

    def test_layer_matching_no_file_warns(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules:\n"
                          '  - "forbid-import: a -> b"\n',
                          "a/x.py": "x = 1\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertIn("layer 'b'", err)
        self.assertIn("matches no scanned source file", err)

    def test_misspelt_layer_on_the_to_side_gets_a_suggestion(self):
        root = make_repo({"arch.yaml": ARCH_HEAD.replace("name: b", "name: services")
                          .replace('"b/**"', '"services/**"') + "rules:\n"
                          '  - "forbid-import: a -> servces"\n',
                          "a/x.py": "x = 1\n", "services/s.py": "s = 1\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0)
        self.assertIn("did you mean 'services'?", err)

    def test_valid_rule_about_an_external_package_is_not_flagged(self):
        # Forbidding a third-party package nobody imports is the normal,
        # compliant state: no warning (and so nothing for --strict to fail).
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules:\n"
                          '  - "forbid-import: a -> requests"\n',
                          "a/x.py": "x = 1\n", "b/y.py": "y = 1\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0)
        self.assertNotIn("requests", err)

    def test_strict_turns_warnings_into_exit_2(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules:\n"
                          '  - "forbid-pattern: srcc/**.py !~ eval"\n',
                          "a/x.py": "x = 1\n"})
        code, out, err = run_main("--root", root, "check", "--strict")
        self.assertEqual(code, 2)
        self.assertIn("--strict", err)

    def test_clean_repo_with_matching_rules_has_no_warnings(self):
        root = make_repo({"arch.yaml": ARCH_HEAD + "rules:\n"
                          '  - "forbid-import: a -> b"\n',
                          "a/x.py": "x = 1\n", "b/y.py": "y = 1\n"})
        code, out, err = run_main("--root", root, "check", "--strict")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
