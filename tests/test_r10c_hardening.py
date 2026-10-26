"""Round 10c: regex time bombs, scan filters, git-listed files, markdown and
HTML reports. Hermetic: temp repos, fake git identity, no tight timing."""

import json
import os
import signal
import threading
import time
import unittest
from unittest import mock

from _support import (GitCase, commit_all, init_git, make_repo, run_main,
                      write_files)
from adr_drift import engine as engine_mod
from adr_drift import redos
from adr_drift import rules as rules_mod

BOMB = r"(a+)+$"
ARCH = ('layers:\n  - name: app\n    paths: ["src/**"]\n'
        'rules:\n  - "forbid-pattern: src/**.py !~ eval\\\\("\n')


class TestNestedQuantifiersAreRefusedWhenTheRuleLoads(unittest.TestCase):
    def test_the_classic_shapes_are_refused(self):
        for pattern in (r"(a+)+$", r"(a*)*", r"(\w+)+", r"(x{1,3})+",
                        r"^(\w+\s?)*$", r"(a|aa)+", r"(a|a)*b", r"(.*a){12}x"):
            with self.subTest(pattern):
                self.assertTrue(redos.problem(pattern), pattern)
                with self.assertRaisesRegex(ValueError, "exponential"):
                    rules_mod.parse_rule_line(f"forbid-pattern: src/** !~ {pattern}")

    def test_ordinary_patterns_are_accepted(self):
        for pattern in (r"eval\(", r"\s+=\s*\w+", r"(foo|bar)+", r"(\d{4})+",
                        r"[a-z]+(\.[a-z]+)*", r"import\s+os", r"(?:ab)*c"):
            with self.subTest(pattern):
                self.assertIsNone(redos.problem(pattern), pattern)
                rules_mod.parse_rule_line(f"forbid-pattern: src/** !~ {pattern}")

    def test_the_message_says_how_to_rewrite_it(self):
        with self.assertRaisesRegex(ValueError, "Rewrite it"):
            rules_mod.parse_rule_line("require-pattern: src/** =~ (a+)+$")

    def test_a_bad_regex_is_still_reported_as_a_syntax_error(self):
        with self.assertRaisesRegex(ValueError, "bad forbid-pattern regex"):
            rules_mod.parse_rule_line("forbid-pattern: src/** !~ (")

    def test_a_check_with_such_a_rule_exits_2(self):
        root = make_repo({"arch.yaml": 'layers:\n  - name: a\n    paths: ["src/**"]\nrules:\n'
                                       '  - "forbid-pattern: src/**.py !~ (a+)+$"\n',
                          "src/a.py": "x = 1\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("exponential", err + out)


def bomb_rule():
    """A rule that did not go through the loader, as a test-only way to reach
    the deadline."""
    rule = rules_mod.Rule(kind="forbid-pattern", source="test")
    rule.glob, rule.regex = "src/**", BOMB
    return rule


ENTRY = [("src/a.py", "app", [], "a" * 60 + "!\n")]


class TestTheDeadline(unittest.TestCase):
    def setUp(self):
        self.saved = dict(engine_mod.LIMITS)
        self.addCleanup(lambda: engine_mod.LIMITS.update(self.saved))

    @unittest.skipUnless(hasattr(signal, "setitimer"), "POSIX timers only")
    def test_a_catastrophic_pattern_is_stopped_with_a_clear_message(self):
        engine_mod.LIMITS["rule"] = 0.3
        with self.assertRaises(engine_mod.RuleTimeout) as ctx:
            engine_mod.evaluate(ENTRY, [], [bomb_rule()])
        self.assertIn("catastrophic", str(ctx.exception))
        self.assertIn("--rule-timeout", str(ctx.exception))

    def test_the_total_limit_stops_before_the_next_rule(self):
        engine_mod.LIMITS["total"] = 0.0
        with self.assertRaisesRegex(engine_mod.RuleTimeout, "--timeout"):
            engine_mod.evaluate(ENTRY, [], [bomb_rule()])

    def test_without_timers_a_watchdog_ends_the_process_with_the_message(self):
        seen = []
        done = threading.Event()

        def fake_exit(message):
            seen.append(message)
            done.set()
        stub = type("NoTimers", (), {})()        # no setitimer: the Windows case
        with mock.patch.object(engine_mod, "signal", stub), \
                mock.patch.object(engine_mod, "_hard_exit", fake_exit):
            with engine_mod._Deadline(0.05, "too slow"):
                self.assertTrue(done.wait(10))
        self.assertEqual(seen, ["too slow"])

    def test_a_fast_block_never_fires_the_watchdog(self):
        seen = []
        stub = type("NoTimers", (), {})()
        with mock.patch.object(engine_mod, "signal", stub), \
                mock.patch.object(engine_mod, "_hard_exit", seen.append):
            with engine_mod._Deadline(5.0, "too slow"):
                pass
            time.sleep(0.05)
        self.assertEqual(seen, [])

    def test_the_cli_turns_a_timeout_into_exit_2(self):
        root = make_repo({"arch.yaml": ARCH, "src/a.py": "x = 1\n"})
        with mock.patch.object(engine_mod, "evaluate",
                               side_effect=engine_mod.RuleTimeout("rule X took too long")):
            code, out, err = run_main("--root", root, "check", "--timeout", "5")
        self.assertEqual(code, 2)
        self.assertIn("rule X took too long", err)
        self.assertEqual(engine_mod.LIMITS["total"], 5.0)

    def test_the_limits_must_be_positive_numbers(self):
        root = make_repo({"arch.yaml": ARCH, "src/a.py": "x = 1\n"})
        for bad in ("0", "-1", "soon"):
            with self.assertRaises(SystemExit):
                run_main("--root", root, "check", "--rule-timeout", bad)

    def test_each_file_is_split_into_lines_once(self):
        r1, r2 = rules_mod.Rule(kind="forbid-pattern", source="t"), rules_mod.Rule(kind="forbid-pattern", source="t")
        r1.glob = r2.glob = "src/**"
        r1.regex, r2.regex = "zzz", "yyy"
        with mock.patch.object(engine_mod.scanner_mod, "split_lines",
                               wraps=engine_mod.scanner_mod.split_lines) as spy:
            engine_mod.evaluate(ENTRY, [], [r1, r2])
        self.assertEqual(spy.call_count, 1)


class TestScanFilters(GitCase):
    FILES = {"arch.yaml": ARCH + 'exclude: ["src/generated/**"]\n',
             "src/a.py": "x = 1\n", "src/generated/g.py": "eval('1')\n"}

    def test_exclude_removes_paths_from_the_scan(self):
        root = make_repo(self.FILES)
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, out + err)

    def test_without_exclude_the_same_file_is_a_violation(self):
        files = dict(self.FILES, **{"arch.yaml": ARCH})
        root = make_repo(files)
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 1, out + err)

    def test_include_limits_the_scan_to_the_listed_places(self):
        files = dict(self.FILES, **{"arch.yaml": ARCH + 'include: ["src/a.py"]\n',
                                    "src/b.py": "eval('1')\n"})
        root = make_repo(files)
        self.assertEqual(run_main("--root", root, "check")[0], 0)
        files["arch.yaml"] = ARCH + 'include: ["src/b.py"]\n'
        root = make_repo(files)
        self.assertEqual(run_main("--root", root, "check")[0], 1)

    def test_a_bad_exclude_is_a_configuration_error(self):
        root = make_repo(dict(self.FILES, **{"arch.yaml": ARCH + "exclude: 5\n"}))
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2)
        self.assertIn("exclude", err + out)

    def test_drift_honours_exclude_too(self):
        root = init_git(make_repo(dict(self.FILES, **{"src/generated/g.py": "x = 1\n"})))
        commit_all(root, "start")
        write_files(root, {"src/generated/g.py": "eval('1')\n"})
        commit_all(root, "generated change")
        code, out, err = run_main("--root", root, "drift", "--since", "HEAD~1")
        self.assertEqual(code, 0, out + err)


class TestGitListedFiles(GitCase):
    def test_gitignored_files_are_not_scanned_but_new_untracked_ones_are(self):
        root = init_git(make_repo({"arch.yaml": ARCH, ".gitignore": "src/ignored/\n",
                                   "src/a.py": "x = 1\n"}))
        commit_all(root, "start")
        write_files(root, {"src/ignored/i.py": "eval('1')\n", "src/new.py": "eval('2')\n"})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertEqual(code, 1, out + err)
        files = [v["file"] for v in json.loads(out)]
        self.assertEqual(files, ["src/new.py"])

    def test_outside_git_the_folder_is_walked(self):
        root = make_repo({"arch.yaml": ARCH, "src/a.py": "eval('1')\n"})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertEqual([v["file"] for v in json.loads(out)], ["src/a.py"])


class TestMarkdownAndHtml(GitCase):
    def repo(self):
        root = init_git(make_repo({"arch.yaml": ARCH, "src/a.py": "x = 1\n"}))
        commit_all(root, "start")
        write_files(root, {"src/a.py": "eval('1')\n"})
        commit_all(root, "adds eval")
        write_files(root, {"src/a.py": "x = 2\n"})
        commit_all(root, "removes eval")
        return root

    def test_check_markdown_is_a_pr_comment_with_rule_and_fix(self):
        root = make_repo({"arch.yaml": ARCH, "src/a.py": "eval('1')\n"})
        code, out, err = run_main("--root", root, "check", "--format", "markdown")
        self.assertEqual(code, 1)
        self.assertIn("<!-- adr-drift -->", out)
        self.assertIn("| where | what | rule | ADR | how to fix |", out)
        self.assertIn("src/a.py:1", out)
        self.assertIn("Remove or rewrite", out)

    def test_markdown_for_a_clean_repo_says_so(self):
        root = make_repo({"arch.yaml": ARCH, "src/a.py": "x = 1\n"})
        code, out, err = run_main("--root", root, "check", "--format", "markdown")
        self.assertEqual(code, 0)
        self.assertIn("No architecture drift", out)

    def test_table_cells_cannot_break_out_of_the_table(self):
        from adr_drift import report
        self.assertEqual(report._cell("a|b\nc"), "a\\|b c")

    def test_drift_markdown(self):
        root = init_git(make_repo({"arch.yaml": ARCH, "src/a.py": "x = 1\n"}))
        commit_all(root, "start")
        write_files(root, {"src/a.py": "eval('1')\n"})
        commit_all(root, "bad")
        code, out, err = run_main("--root", root, "drift", "--since", "HEAD~1",
                                  "--format", "markdown")
        self.assertEqual(code, 1, err)
        self.assertIn("new since HEAD~1", out)

    def test_the_html_report_lists_each_commit_with_added_and_fixed(self):
        root = self.repo()
        page = os.path.join(root, "trend.html")
        code, out, err = run_main("--root", root, "report", "--html", page,
                                  "--since", "HEAD~2")
        self.assertEqual(code, 0, out + err)
        with open(page, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("adds eval", text)
        self.assertIn("removes eval", text)
        self.assertIn("Violations added: 1. Fixed: 1. Net: +0.", text)
        self.assertNotIn("<script", text)

    def test_the_html_escapes_commit_subjects(self):
        from adr_drift import report
        page = report.html_page([{"commit": "abc", "subject": "<b>x</b>&", "new": 0, "fixed": 0}],
                                "HEAD~1", {})
        self.assertIn("&lt;b&gt;x&lt;/b&gt;&amp;", page)

    def test_an_unknown_ref_is_exit_2(self):
        root = self.repo()
        code, out, err = run_main("--root", root, "report", "--html",
                                  os.path.join(root, "t.html"), "--since", "nope")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
