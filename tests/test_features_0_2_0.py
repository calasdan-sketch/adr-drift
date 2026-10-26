"""0.2.0: SARIF, fix hints and ADR info, retired ADRs, temporary exceptions
(allow-until), the pre-commit hook file."""

import datetime
import json
import os
import re
import unittest

import adr_drift
from adr_drift import config as config_mod
from adr_drift import engine, miniyaml, rules as rules_mod, sarif

from _support import (GitCase, commit_all, git, init_git, make_repo,
                      run_main, write_files)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOP = os.path.join(ROOT, "tests", "fixtures", "shop")

EVAL_ADR = ("# 3: No eval\n\n## Status\n\n{status}\n\n## Constraints\n\n"
            "- forbid-pattern: src/**.py !~ eval\\({tail}\n")
BASE = {"src/a.py": "x = 1\n", "src/bad.py": "eval(1)\n"}


def repo(status="Accepted", tail="", extra=None):
    files = dict(BASE)
    files["docs/adr/0003-no-eval.md"] = EVAL_ADR.format(status=status,
                                                        tail=tail)
    files.update(extra or {})
    return make_repo(files)


def check(root, *args):
    return run_main("--root", root, "check", *args)


class TestRuleExceptionSyntax(unittest.TestCase):
    def parse(self, line):
        return rules_mod.parse_rule_line(line, source="x.md", line_no=1)

    def test_date_is_read_and_removed_from_the_rule(self):
        r = self.parse("forbid-import: a -> b allow-until: 2026-12-31")
        self.assertEqual(r.allow_until, datetime.date(2026, 12, 31))
        self.assertEqual(r.describe(), "a must not import b")

    def test_equals_sign_also_works(self):
        r = self.parse("forbid-import: a -> b allow-until=2026-01-02")
        self.assertEqual(r.allow_until, datetime.date(2026, 1, 2))

    def test_the_regex_of_a_pattern_rule_is_not_polluted(self):
        r = self.parse("forbid-pattern: src/**.py !~ eval\\( allow-until: "
                       "2027-03-04")
        self.assertEqual(r.regex, "eval\\(")
        self.assertEqual(r.allow_until, datetime.date(2027, 3, 4))

    def test_bad_dates_are_errors_not_permanent_exceptions(self):
        for tail in ("2026-13-01", "2026-02-30", "soon", "", "26-1-1"):
            with self.assertRaises(ValueError, msg=tail) as ctx:
                self.parse(f"forbid-import: a -> b allow-until: {tail}")
            self.assertIn("allow-until", str(ctx.exception))

    def test_a_missing_colon_is_an_error(self):
        with self.assertRaises(ValueError):
            self.parse("forbid-import: a -> b allow-until 2026-12-31")

    def test_the_word_inside_a_regex_is_left_alone(self):
        r = self.parse("forbid-pattern: src/**.py !~ allow-until[:]")
        self.assertIsNone(r.allow_until)
        self.assertEqual(r.regex, "allow-until[:]")
        r = self.parse("forbid-pattern: src/**.py !~ x-allow-until: 2026-01-01")
        self.assertIsNone(r.allow_until)

    def test_a_rule_without_exception_has_none(self):
        self.assertIsNone(self.parse("forbid-import: a -> b").allow_until)

    def test_long_runs_of_blanks_stay_fast(self):
        line = "forbid-import: a -> b" + " " * 200000 + "allow-until"
        with self.assertRaises(ValueError):
            self.parse(line)


class TestTemporaryExceptions(unittest.TestCase):
    def test_violations_are_allowed_until_the_date_inclusive(self):
        root = repo(tail=" allow-until: 2026-06-30")
        for day in ("2026-01-01", "2026-06-30"):
            code, out, err = check(root, "--today", day)
            self.assertEqual(code, 0, (day, out, err))
            self.assertIn("allowed by temporary exceptions", err)

    def test_the_day_after_they_fail_with_a_clear_message(self):
        root = repo(tail=" allow-until: 2026-06-30")
        code, out, err = check(root, "--today", "2026-07-01")
        self.assertEqual(code, 1, err)
        self.assertIn("src/bad.py", out)
        self.assertIn("temporary exception for this rule ended on "
                      "2026-06-30", out)
        self.assertIn("ended on 2026-06-30 (1 day ago)", err)

    def test_expired_exception_is_in_the_json(self):
        root = repo(tail=" allow-until: 2026-06-30")
        code, out, _ = check(root, "--today", "2026-08-01", "--format",
                             "json")
        (v,) = json.loads(out)
        self.assertEqual(v["exception_expired"], "2026-06-30")
        self.assertIn("ended on 2026-06-30", v["fix_hint"])

    def test_allowed_violations_are_not_in_the_json(self):
        root = repo(tail=" allow-until: 2026-06-30")
        code, out, _ = check(root, "--today", "2026-01-01", "--format",
                             "json")
        self.assertEqual((code, json.loads(out)), (0, []))

    def test_an_expired_exception_without_violations_is_reported(self):
        root = repo(tail=" allow-until: 2026-06-30",
                    extra={"src/bad.py": "x = 2\n"})
        code, out, err = check(root, "--today", "2026-07-10")
        self.assertEqual(code, 0, err)
        self.assertIn("no longer needed", err)
        self.assertIn("delete 'allow-until'", err)
        code, out, err = check(root, "--today", "2026-07-10", "--strict")
        self.assertEqual(code, 2, err)

    def test_verbose_lists_what_the_exception_lets_through(self):
        root = repo(tail=" allow-until: 2026-06-30")
        _, _, quiet = check(root, "--today", "2026-06-20")
        _, _, loud = check(root, "--today", "2026-06-20", "--verbose")
        self.assertNotIn("src/bad.py", quiet)
        self.assertIn("--verbose lists them", quiet)
        self.assertIn("allowed until 2026-06-30: src/bad.py:1", loud)
        self.assertIn("still valid 10 more days", loud)

    def test_last_day_says_today(self):
        root = repo(tail=" allow-until: 2026-06-30")
        _, _, err = check(root, "--today", "2026-06-30", "--verbose")
        self.assertIn("still valid today", err)

    def test_a_bad_date_stops_the_run_with_the_line(self):
        root = repo(tail=" allow-until: 2026-02-30")
        code, out, err = check(root)
        self.assertEqual(code, 2)
        self.assertIn("0003-no-eval.md:", err)
        self.assertIn("not a real date", err)

    def test_arch_yaml_rules_can_have_exceptions_too(self):
        root = make_repo({
            "arch.yaml": 'rules:\n  - "forbid-pattern: src/**.py !~ eval\\\\('
                         ' allow-until: 2026-03-01"\n',
            **BASE})
        self.assertEqual(check(root, "--today", "2026-03-01")[0], 0)
        self.assertEqual(check(root, "--today", "2026-03-02")[0], 1)

    def test_other_rules_are_not_excused(self):
        root = repo(tail=" allow-until: 2026-06-30",
                    extra={"arch.yaml": 'rules:\n  - "forbid-pattern: '
                                        'src/**.py !~ x = 1"\n'})
        code, out, _ = check(root, "--today", "2026-01-01")
        self.assertEqual(code, 1)
        self.assertIn("src/a.py", out)
        self.assertNotIn("src/bad.py", out)

    def test_apply_exceptions_directly(self):
        r = rules_mod.parse_rule_line("forbid-import: a -> b allow-until: "
                                      "2026-05-05")
        v = engine.Violation(r, "f.py", 3, "imports b")
        kept, allowed = engine.apply_exceptions([v], datetime.date(2026, 5, 5))
        self.assertEqual((kept, allowed), ([], [v]))
        kept, allowed = engine.apply_exceptions([v], datetime.date(2026, 5, 6))
        self.assertEqual(allowed, [])
        self.assertEqual(kept[0].expired, "2026-05-05")
        self.assertEqual(kept[0].detail, v.detail)   # identity is unchanged

    def test_baseline_does_not_record_allowed_violations(self):
        root = repo(tail=" allow-until: 2999-01-01")
        code, out, err = run_main("--root", root, "baseline")
        self.assertEqual(code, 0, err)
        with open(os.path.join(root, ".adr-baseline.json")) as f:
            self.assertEqual(json.load(f), [])

    def test_bad_today_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            check(repo(), "--today", "tomorrow")


class TestRetiredAdrs(unittest.TestCase):
    def test_superseded_adr_stops_enforcing(self):
        for status in ("Superseded by ADR-0007", "superseded",
                       "**Superseded** by [0007](0007.md)", "Deprecated"):
            code, out, err = check(repo(status=status))
            # the only rule was retired: nothing is enforced (exit 2) or,
            # with arch.yaml rules, the run is green; never a violation
            self.assertIn(code, (0, 2), (status, out, err))
            self.assertNotIn("src/bad.py", out, status)

    def test_verbose_says_so(self):
        root = repo(status="Superseded by ADR-0007",
                    extra={"arch.yaml": 'rules:\n  - "forbid-pattern: '
                                        'src/**.py !~ nothing_here"\n'})
        code, out, err = check(root, "--verbose")
        self.assertEqual(code, 0, err)
        self.assertIn("0003-no-eval.md", err)
        self.assertIn("superseded", err)
        self.assertIn("not enforced", err)
        self.assertNotIn("not enforced", check(root)[2])

    def test_when_retiring_leaves_no_rules_the_run_says_why(self):
        root = make_repo({"src/a.py": "x = 1\n", "docs/adr/0003-no-eval.md":
                          EVAL_ADR.format(status="Deprecated", tail="")})
        code, out, err = check(root)
        self.assertEqual(code, 2)
        self.assertIn("1 ADR superseded or deprecated", err)
        code, out, err = check(root, "--verbose")
        self.assertIn("this ADR is deprecated", err)

    def test_accepted_adr_that_mentions_superseding_still_enforces(self):
        code, out, _ = check(repo(status="Accepted (supersedes ADR-0002)"))
        self.assertEqual(code, 1)
        self.assertIn("src/bad.py", out)

    def test_a_stale_broken_line_in_a_retired_adr_does_not_stop_the_run(self):
        root = repo(status="Deprecated",
                    extra={"docs/adr/0003-no-eval.md":
                           "# 3: Old\n\n## Status\n\nDeprecated\n\n"
                           "## Constraints\n\n- forbid-import: a ->\n",
                           "arch.yaml": 'rules:\n  - "forbid-pattern: '
                                        'src/**.py !~ eval\\\\("\n'})
        code, out, err = check(root)
        self.assertEqual(code, 1, err)       # arch.yaml's rule still fires
        self.assertNotIn("error:", err)

    def test_status_helper(self):
        self.assertEqual(config_mod.retired_status("Superseded by 4"),
                         "superseded")
        self.assertEqual(config_mod.retired_status("Deprecated"), "deprecated")
        for s in ("", "Accepted", "Proposed", "Rejected",
                  "Accepted, superseded later"):
            self.assertEqual(config_mod.retired_status(s), "", s)


class TestFixHintAndAdr(unittest.TestCase):
    def test_json_has_fix_hint_and_adr(self):
        code, out, _ = run_main("--root", SHOP, "check", "--format", "json")
        data = json.loads(out)
        self.assertEqual(code, 1)
        for v in data:
            self.assertTrue(v["fix_hint"].strip())
            for key in ("rule", "rule_source", "file", "line", "detail"):
                self.assertIn(key, v)
        evals = [v for v in data if "eval" in v["rule"]]
        self.assertEqual(evals[0]["adr"], {
            "number": 2, "title": "No dynamic eval",
            "path": "docs/adr/0002-no-dynamic-eval.md"})
        arch = [v for v in data if v["rule_source"].startswith("arch.yaml")]
        self.assertIsNone(arch[0]["adr"])

    def test_hint_is_specific_to_the_kind(self):
        _, out, _ = run_main("--root", SHOP, "check", "--format", "json")
        hints = {v["detail"]: v["fix_hint"] for v in json.loads(out)}
        self.assertIn("Remove the import of db.drivers",
                      hints["imports db.drivers"])
        self.assertIn("Add code that matches", hints["missing /authenticate\\(/"])
        self.assertIn("Remove or rewrite", hints["matches /\\beval\\(/"])

    def test_text_prints_adr_number_and_title_for_every_adr_violation(self):
        _, out, _ = run_main("--root", SHOP, "check")
        self.assertIn("ADR 0002: No dynamic eval", out)
        self.assertIn("ADR 0001: Layered architecture", out)
        self.assertEqual(out.count("fix: "), 4)
        # arch.yaml's own rule has no ADR line, only the fix line
        block = out.split("rule: handlers must not import db.drivers "
                          "(arch.yaml)")[1].split("rule:")[0]
        self.assertNotIn("ADR ", block)

    def test_title_prefix_is_removed_only_when_it_is_a_number(self):
        root = make_repo({"docs/adr/0004-x.md":
                          "# 3-tier layout\n\n## Constraints\n\n"
                          "- forbid-pattern: src/**.py !~ zzz\n",
                          "src/a.py": "zzz = 1\n"})
        _, out, _ = run_main("--root", root, "check", "--format", "json")
        self.assertEqual(json.loads(out)[0]["adr"]["title"], "3-tier layout")
        self.assertEqual(json.loads(out)[0]["adr"]["number"], 4)

    def test_baseline_file_keeps_its_old_shape(self):
        root = make_repo({"arch.yaml": 'rules:\n  - "forbid-pattern: '
                                       'src/**.py !~ eval"\n', **BASE})
        run_main("--root", root, "baseline")
        with open(os.path.join(root, ".adr-baseline.json")) as f:
            (entry,) = json.load(f)
        self.assertEqual(sorted(entry), ["detail", "file", "line", "rule",
                                         "rule_source"])


class TestSarif(unittest.TestCase):
    def sarif(self, *args, root=SHOP):
        code, out, err = run_main("--root", root, "check", "--format",
                                  "sarif", *args)
        return code, json.loads(out), err

    def test_log_shape(self):
        code, log, _ = self.sarif()
        self.assertEqual(code, 1)
        self.assertEqual(log["version"], "2.1.0")
        self.assertIn("sarif-2.1.0", log["$schema"])
        (run,) = log["runs"]
        driver = run["tool"]["driver"]
        self.assertEqual(driver["name"], "adr-drift")
        self.assertEqual(driver["version"], adr_drift.__version__)
        self.assertEqual(len(run["results"]), 4)

    def test_every_result_points_at_a_rule_and_a_place(self):
        _, log, _ = self.sarif()
        (run,) = log["runs"]
        rules = run["tool"]["driver"]["rules"]
        ids = [r["id"] for r in rules]
        self.assertEqual(len(ids), len(set(ids)))
        for res in run["results"]:
            self.assertEqual(rules[res["ruleIndex"]]["id"], res["ruleId"])
            self.assertEqual(res["level"], "error")
            loc = res["locations"][0]["physicalLocation"]
            self.assertGreaterEqual(loc["region"]["startLine"], 1)
            self.assertNotIn("\\", loc["artifactLocation"]["uri"])
            self.assertTrue(res["message"]["text"])
            self.assertEqual(len(res["partialFingerprints"]["adrDrift/v1"]),
                             40)

    def test_whole_file_violations_get_line_one(self):
        _, log, _ = self.sarif()
        missing = [r for r in log["runs"][0]["results"]
                   if "authenticate" in r["message"]["text"]]
        self.assertEqual(
            missing[0]["locations"][0]["physicalLocation"]["region"],
            {"startLine": 1})

    def test_rules_without_violations_are_listed_too(self):
        root = repo(extra={"src/bad.py": "x = 1\n"})
        code, log, _ = self.sarif(root=root)
        self.assertEqual(code, 0)
        self.assertEqual(log["runs"][0]["results"], [])
        self.assertEqual(len(log["runs"][0]["tool"]["driver"]["rules"]), 1)

    def test_fingerprint_ignores_the_line(self):
        _, a, _ = self.sarif(root=repo())
        moved = repo(extra={"src/bad.py": "\n\n\neval(1)\n"})
        _, b, _ = self.sarif(root=moved)
        fa = a["runs"][0]["results"][0]["partialFingerprints"]
        fb = b["runs"][0]["results"][0]["partialFingerprints"]
        self.assertEqual(fa, fb)

    def test_adr_is_named_in_message_and_properties(self):
        _, log, _ = self.sarif()
        res = [r for r in log["runs"][0]["tool"]["driver"]["rules"]
               if r["properties"].get("adr")]
        self.assertTrue(res)
        self.assertEqual(res[0]["properties"]["adr"]["number"], 1)
        self.assertTrue(any("ADR 0002: No dynamic eval" in r["message"]["text"]
                            for r in log["runs"][0]["results"]))

    def test_nothing_to_enforce_still_prints_a_valid_log(self):
        root = make_repo({"src/a.py": "x = 1\n"})
        code, out, err = run_main("--root", root, "check", "--format",
                                  "sarif", "--allow-empty")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["runs"][0]["results"], [])

    def test_file_names_are_percent_encoded(self):
        root = make_repo({"arch.yaml": 'rules:\n  - "forbid-pattern: '
                                       'src/**.py !~ eval"\n',
                          "src/my file #1.py": "eval(1)\n"})
        _, log, _ = self.sarif(root=root)
        uri = log["runs"][0]["results"][0]["locations"][0][
            "physicalLocation"]["artifactLocation"]["uri"]
        self.assertEqual(uri, "src/my%20file%20%231.py")

    def test_build_is_pure_json(self):
        json.dumps(sarif.build([], [], "0"))

    def test_stdout_is_only_the_log(self):
        root = repo(tail=" allow-until: 2999-01-01")
        code, out, err = run_main("--root", root, "check", "--format",
                                  "sarif")
        self.assertEqual(code, 0)
        json.loads(out)
        self.assertIn("note:", err)


class TestDriftNewFeatures(GitCase):
    def make(self):
        root = init_git(make_repo({
            "arch.yaml": 'rules:\n  - "forbid-pattern: src/**.py !~ eval\\\\('
                         ' allow-until: 2026-06-30"\n',
            "src/a.py": "x = 1\n"}))
        commit_all(root, "start")
        write_files(root, {"src/b.py": "eval(2)\n"})
        commit_all(root, "bad")
        return root

    def drift(self, root, *args):
        return run_main("--root", root, "drift", "--since", "HEAD~1", *args)

    def test_sarif_of_new_violations(self):
        root = self.make()
        code, out, _ = self.drift(root, "--format", "sarif", "--today",
                                  "2026-07-01")
        self.assertEqual(code, 1)
        (res,) = json.loads(out)["runs"][0]["results"]
        self.assertEqual(res["locations"][0]["physicalLocation"][
            "artifactLocation"]["uri"], "src/b.py")

    def test_exception_covers_new_violations_until_the_date(self):
        root = self.make()
        self.assertEqual(self.drift(root, "--today", "2026-06-30")[0], 0)
        code, out, err = self.drift(root, "--today", "2026-07-01")
        self.assertEqual(code, 1)
        self.assertIn("ended on 2026-06-30", out)

    def test_drift_json_has_the_new_keys(self):
        root = self.make()
        _, out, _ = self.drift(root, "--format", "json", "--today",
                               "2026-07-01")
        (v,) = json.loads(out)
        self.assertIn("fix_hint", v)
        self.assertIn("adr", v)

    def test_drift_with_no_changes_in_sarif(self):
        root = self.make()
        write_files(root, {"README.txt": "hi"})
        commit_all(root, "docs")
        code, out, _ = self.drift(root, "--format", "sarif")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["runs"][0]["results"], [])


class TestPreCommitHookFile(unittest.TestCase):
    def load(self):
        with open(os.path.join(ROOT, ".pre-commit-hooks.yaml"),
                  encoding="utf-8") as f:
            return miniyaml.safe_load(f.read())

    def test_hook_definition(self):
        (hook,) = self.load()
        self.assertEqual(hook["id"], "adr-drift")
        self.assertEqual(hook["entry"], "adr-drift check")
        self.assertEqual(hook["language"], "python")
        self.assertIs(hook["pass_filenames"], False)
        self.assertIs(hook["always_run"], True)

    def test_entry_is_a_real_console_script(self):
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r'(?m)^adr-drift = "adr_drift\.cli:main"')

    def test_it_ships_in_the_sdist(self):
        with open(os.path.join(ROOT, "MANIFEST.in"), encoding="utf-8") as f:
            self.assertIn("include .pre-commit-hooks.yaml", f.read())

    def test_readme_documents_hooks_and_flags(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            text = f.read()
        for needle in ("PostToolUse", "- id: adr-drift", "allow-until",
                       "--format sarif", "Superseded"):
            self.assertIn(needle, text)
        self.assertRegex(text, r"rev: v" + re.escape(adr_drift.__version__))

    def test_claude_hook_snippet_is_valid_json(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            text = f.read()
        block = text.split("PostToolUse", 1)[1]
        block = block.split("```json", 1)[1].split("```", 1)[0]
        cfg = json.loads(block)
        cmd = cfg["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(cmd, "adr-drift check >&2 || exit 2")


class TestVersions(unittest.TestCase):
    def test_version_is_one_number_everywhere(self):
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
            m = re.search(r'(?m)^version = "([^"]+)"', f.read())
        self.assertEqual(m.group(1), adr_drift.__version__)
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r"(?m)^## " + re.escape(
                adr_drift.__version__) + r"$")


if __name__ == "__main__":
    unittest.main()
