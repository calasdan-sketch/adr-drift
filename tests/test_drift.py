"""`drift` and `check --baseline`: git failures, paths, and violation identity.

Regressions for the panel findings:

* `drift` crashed with a traceback (exit 1, which reads as "violations") on
  an unknown ref, in a shallow clone (actions/checkout's default) and on a
  short history (the default --since HEAD~5);
* git C-quotes non-ASCII names, so `drift` said "no drift" for a violation in
  a file with an accented letter in its name that `check` found;
* a violation's identity included its line number, so a docstring above a
  grandfathered import broke `check --baseline`, made `drift` report one NEW
  and one fixed violation, and renames did the same;
* `drift --format json` printed prose when nothing relevant had changed.
"""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from _support import (GitCase, commit_all, git, init_git, make_repo, run_main,
                      write_files)

ARCH = ('layers:\n'
        '  - name: handlers\n    paths: ["src/handlers/**"]\n'
        '  - name: db\n    paths: ["src/db/**"]\n'
        'rules:\n'
        '  - "forbid-import: handlers -> db"\n')
CLEAN = {"arch.yaml": ARCH,
         "src/handlers/a.py": "def ok():\n    return 1\n",
         "src/db/d.py": "def query():\n    return 1\n"}
BAD = "from db import d\n\n\ndef bad():\n    return d.query()\n"


class DriftCase(GitCase):
    def repo(self, files=None):
        root = init_git(make_repo(files or CLEAN))
        commit_all(root, "clean start")
        return root

    def commit(self, root, files, message="change"):
        write_files(root, files)
        commit_all(root, message)

    def drift(self, root, *extra):
        return run_main("--root", root, "drift", *extra)

    def new_files(self, out):
        return [v["file"] for v in json.loads(out)]


class TestGitFailuresAreErrors(DriftCase):
    def test_unknown_ref_exits_2_without_a_traceback(self):
        root = self.repo()
        code, out, err = self.drift(root, "--since", "NOSUCH")
        self.assertEqual(code, 2, out + err)
        self.assertEqual(out, "")
        self.assertIn("unknown git ref 'NOSUCH'", err)
        self.assertNotIn("Traceback", err)

    def test_json_stdout_stays_empty_when_git_fails(self):
        root = self.repo()
        code, out, err = self.drift(root, "--since", "NOSUCH",
                                    "--format", "json")
        self.assertEqual((code, out), (2, ""))

    def test_default_ref_on_a_short_history_is_exit_2_with_a_hint(self):
        root = self.repo()  # one commit: the default HEAD~5 does not exist
        code, out, err = self.drift(root)
        self.assertEqual(code, 2, out + err)
        self.assertIn("HEAD~5", err)
        self.assertIn("--since", err)
        self.assertNotIn("Traceback", err)

    def test_shallow_clone_hints_at_fetch_depth(self):
        # actions/checkout clones with depth 1 by default.
        src = self.repo()
        for i in range(3):
            self.commit(src, {"src/handlers/a.py": f"x = {i}\n"})
        dest = tempfile.mkdtemp()
        git(dest, "clone", "-q", "--depth", "1",
            pathlib.Path(src).as_uri(), ".")
        code, out, err = self.drift(dest, "--since", "HEAD~2")
        self.assertEqual(code, 2, out + err)
        self.assertIn("shallow", err)
        self.assertIn("fetch-depth: 0", err)
        self.assertNotIn("Traceback", err)

    def test_a_ref_that_looks_like_an_option_is_refused(self):
        root = self.repo()
        code, out, err = self.drift(root, "--since=--output=x")
        self.assertEqual(code, 2, out + err)
        self.assertIn("invalid git ref", err)

    def test_git_not_installed_is_exit_2(self):
        root = self.repo()
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("git")):
            code, out, err = self.drift(root, "--since", "HEAD")
        self.assertEqual(code, 2, out + err)
        self.assertIn("git is not installed", err)

    def test_repo_without_commits_is_exit_2(self):
        root = init_git(make_repo(CLEAN))
        code, out, err = self.drift(root, "--since", "HEAD")
        self.assertEqual(code, 2, out + err)
        self.assertIn("no commits", err)

    def test_not_a_repo_is_exit_2(self):
        code, out, err = self.drift(make_repo(CLEAN))
        self.assertEqual(code, 2)
        self.assertIn("not a git repo", err)


class TestNonAsciiPaths(DriftCase):
    def test_violation_in_a_non_ascii_file_name_is_found(self):
        root = self.repo()
        name = "src/handlers/h\U000000e9llo w\U00004e16.py"
        self.commit(root, {name: BAD})
        code, out, err = self.drift(root, "--since", "HEAD~1",
                                    "--format", "json")
        self.assertEqual(code, 1, out + err)
        self.assertEqual(self.new_files(out), [name])
        # ... the same finding `check` makes
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertIn(name, self.new_files(out))

    def test_human_output_names_the_file_unquoted(self):
        root = self.repo()
        name = "src/handlers/caf\U000000e9.py"
        self.commit(root, {name: BAD})
        code, out, err = self.drift(root, "--since", "HEAD~1")
        self.assertEqual(code, 1)
        self.assertIn(name, out)
        self.assertNotIn("\\303", out)  # git's octal C-quoting


class TestRange(DriftCase):
    """--since is the base; HEAD is the end; only changes in between count."""

    def history(self):
        root = self.repo()                                     # c1 clean
        self.commit(root, {"src/handlers/one.py": BAD})        # c2 violation
        self.commit(root, {"src/handlers/a.py": "x = 2\n"})    # c3 harmless
        self.commit(root, {"src/handlers/two.py": BAD})        # c4 violation
        return root

    def files_since(self, root, ref):
        code, out, err = self.drift(root, "--since", ref, "--format", "json")
        self.assertIn(code, (0, 1), out + err)
        return sorted(self.new_files(out))

    def test_only_violations_introduced_after_the_ref_are_new(self):
        root = self.history()
        self.assertEqual(self.files_since(root, "HEAD~1"),
                         ["src/handlers/two.py"])
        self.assertEqual(self.files_since(root, "HEAD~2"),
                         ["src/handlers/two.py"])
        self.assertEqual(self.files_since(root, "HEAD~3"),
                         ["src/handlers/one.py", "src/handlers/two.py"])
        self.assertEqual(self.files_since(root, "HEAD"), [])

    def test_exit_codes_follow_the_range(self):
        root = self.history()
        self.assertEqual(self.drift(root, "--since", "HEAD~1")[0], 1)
        self.assertEqual(self.drift(root, "--since", "HEAD")[0], 0)
        self.assertEqual(self.drift(root, "--since", "HEAD~1",
                                    "--fail-on", "none")[0], 0)

    def test_new_violations_are_tagged_with_the_introducing_commit(self):
        root = self.history()
        sha = git(root, "rev-parse", "HEAD").strip()[:7]
        code, out, err = self.drift(root, "--since", "HEAD~2")
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"src/handlers/two.py:1 [{sha}]", out)

    def test_a_branch_or_tag_name_works_as_the_ref(self):
        root = self.history()
        git(root, "tag", "base", "HEAD~1")
        self.assertEqual(self.files_since(root, "base"),
                         ["src/handlers/two.py"])

    def test_deleting_a_violating_file_counts_as_fixed(self):
        root = self.repo()
        self.commit(root, {"src/handlers/one.py": BAD})
        os.unlink(os.path.join(root, "src", "handlers", "one.py"))
        commit_all(root, "remove it")
        code, out, err = self.drift(root, "--since", "HEAD~1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("1 fixed since HEAD~1", out)
        self.assertIn("src/handlers/one.py", out)

    def test_uncommitted_changes_are_ignored(self):
        # drift compares commits (HEAD against the ref), as documented.
        root = self.repo()
        write_files(root, {"src/handlers/a.py": BAD})   # not committed
        code, out, err = self.drift(root, "--since", "HEAD")
        self.assertEqual(code, 0, out + err)
        # a commit in range plus a dirty tree: the commit is what counts
        root = self.repo()
        self.commit(root, {"src/handlers/b.py": "y = 1\n"})
        write_files(root, {"src/handlers/b.py": BAD})   # dirty tree
        code, out, err = self.drift(root, "--since", "HEAD~1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("no new drift", out)

    def test_a_dirty_tree_does_not_hide_a_committed_violation(self):
        # HEAD holds the violation; the working tree has an (uncommitted)
        # fix. drift reads HEAD, so the violation is still reported.
        root = self.repo()
        self.commit(root, {"src/handlers/c.py": BAD})
        write_files(root, {"src/handlers/c.py": "y = 1\n"})   # dirty fix
        code, out, err = self.drift(root, "--since", "HEAD~1",
                                    "--format", "json")
        self.assertEqual(code, 1, out + err)
        self.assertEqual(self.new_files(out), ["src/handlers/c.py"])


class TestJsonIsAlwaysJson(DriftCase):
    def test_docs_only_change_prints_an_empty_array(self):
        root = self.repo()
        self.commit(root, {"README.md": "# hi\n"})
        code, out, err = self.drift(root, "--since", "HEAD~1",
                                    "--format", "json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), [])

    def test_docs_only_change_human_message_is_unchanged(self):
        root = self.repo()
        self.commit(root, {"README.md": "# hi\n"})
        code, out, err = self.drift(root, "--since", "HEAD~1")
        self.assertEqual(code, 0)
        self.assertIn("no source files changed since HEAD~1", out)

    def test_no_rules_prints_an_empty_array_when_allowed(self):
        root = self.repo({"arch.yaml": "layers: []\n", "m.py": "x = 1\n"})
        code, out, err = self.drift(root, "--since", "HEAD", "--allow-empty",
                                    "--format", "json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), [])

    def test_no_rules_is_exit_2_by_default(self):
        root = self.repo({"arch.yaml": "layers: []\n", "m.py": "x = 1\n"})
        code, out, err = self.drift(root, "--since", "HEAD")
        self.assertEqual(code, 2)
        self.assertIn("--allow-empty", err)

    def test_new_violation_json_shape_is_unchanged(self):
        root = self.repo()
        self.commit(root, {"src/handlers/b.py": BAD})
        code, out, err = self.drift(root, "--since", "HEAD~1",
                                    "--format", "json")
        self.assertEqual(code, 1)
        (v,) = json.loads(out)
        self.assertEqual(sorted(v), ["detail", "file", "line", "rule",
                                     "rule_source"])
        self.assertEqual(v["file"], "src/handlers/b.py")
        self.assertEqual(v["line"], 1)


class TestDriftIdentity(DriftCase):
    """A violation is the same violation after lines move or a file moves."""

    def legacy(self):
        root = self.repo()
        self.commit(root, {"src/handlers/old.py": BAD}, "legacy violation")
        return root

    def test_lines_shifting_is_neither_new_nor_fixed(self):
        root = self.legacy()
        self.commit(root, {"src/handlers/old.py":
                           '"""Docstring added above."""\n\n' + BAD})
        code, out, err = self.drift(root, "--since", "HEAD~1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("no new drift", out)
        self.assertNotIn("fixed", out)

    def test_renaming_a_violating_file_is_neither_new_nor_fixed(self):
        root = self.legacy()
        git(root, "mv", "src/handlers/old.py", "src/handlers/renamed.py")
        commit_all(root, "rename")
        code, out, err = self.drift(root, "--since", "HEAD~1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("no new drift", out)
        self.assertNotIn("fixed", out)

    def test_renaming_and_editing_keeps_the_identity(self):
        root = self.legacy()
        git(root, "mv", "src/handlers/old.py", "src/handlers/renamed.py")
        write_files(root, {"src/handlers/renamed.py": BAD + "\n\nz = 1\n"})
        commit_all(root, "rename and touch")
        code, out, err = self.drift(root, "--since", "HEAD~1")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("fixed", out)

    def test_moving_a_file_into_a_layer_that_forbids_it_is_new(self):
        root = self.repo({**CLEAN, "src/util/u.py": BAD})  # util: no layer
        git(root, "mv", "src/util/u.py", "src/handlers/u.py")
        commit_all(root, "move into handlers")
        code, out, err = self.drift(root, "--since", "HEAD~1",
                                    "--format", "json")
        self.assertEqual(code, 1, out + err)
        self.assertEqual(self.new_files(out), ["src/handlers/u.py"])

    def test_a_second_identical_violation_is_reported_once(self):
        root = self.legacy()
        self.commit(root, {"src/handlers/old.py": BAD + "\nfrom db import d\n"})
        code, out, err = self.drift(root, "--since", "HEAD~1",
                                    "--format", "json")
        self.assertEqual(code, 1, out + err)
        self.assertEqual(self.new_files(out), ["src/handlers/old.py"])

    def test_fixing_one_of_two_identical_violations_is_one_fixed(self):
        root = self.repo()
        self.commit(root, {"src/handlers/old.py": BAD + "\nfrom db import d\n"})
        self.commit(root, {"src/handlers/old.py": BAD})
        code, out, err = self.drift(root, "--since", "HEAD~1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("1 fixed since HEAD~1", out)

    def test_a_different_import_in_the_same_file_is_new(self):
        root = self.legacy()
        self.commit(root, {"src/handlers/old.py":
                           BAD + "\nfrom db import e2\n"})
        write_files(root, {"src/db/e2.py": "z = 1\n"})
        code, out, err = self.drift(root, "--since", "HEAD~1",
                                    "--format", "json")
        self.assertEqual(code, 1, out + err)


class TestBaselineIdentity(unittest.TestCase):
    ARCH2 = ARCH + '  - "forbid-pattern: src/**.py !~ eval\\\\("\n'

    def files(self, handler=BAD, util="y = eval('1')\n"):
        return {"arch.yaml": self.ARCH2, "src/handlers/a.py": handler,
                "src/db/d.py": "def query():\n    return 1\n",
                "src/util.py": util}

    def setUp(self):
        self.root = make_repo(self.files())
        self.base = os.path.join(tempfile.mkdtemp(), "base.json")
        code, out, err = run_main("--root", self.root, "baseline",
                                  "--output", self.base)
        self.assertEqual(code, 0, err)

    def check(self, *extra):
        return run_main("--root", self.root, "check", "--baseline", self.base,
                        *extra)

    def test_baseline_covers_what_it_recorded(self):
        self.assertEqual(self.check()[0], 0)

    def test_lines_moving_do_not_break_the_baseline(self):
        write_files(self.root, self.files(
            handler='"""Docstring above the import."""\n\n' + BAD,
            util="# one\n# two\ny = eval('1')\n"))
        code, out, err = self.check()
        self.assertEqual(code, 0, out + err)

    def test_a_new_violation_in_another_file_is_still_caught(self):
        write_files(self.root, {"src/handlers/b.py": BAD})
        code, out, err = self.check("--format", "json")
        self.assertEqual(code, 1)
        self.assertEqual([v["file"] for v in json.loads(out)],
                         ["src/handlers/b.py"])

    def test_a_second_identical_violation_in_the_same_file_is_caught(self):
        write_files(self.root, self.files(util="y = eval('1')\nz = eval('2')\n"))
        code, out, err = self.check("--format", "json")
        self.assertEqual(code, 1, out + err)
        found = json.loads(out)
        self.assertEqual([v["file"] for v in found], ["src/util.py"])

    def test_baseline_written_by_0_1_0_keeps_working(self):
        # 0.1.0 wrote file/line/rule/rule_source/detail per violation.
        with open(self.base, encoding="utf-8") as f:
            entries = json.load(f)
        for e in entries:
            self.assertEqual(sorted(e), ["detail", "file", "line", "rule",
                                         "rule_source"])
        write_files(self.root, self.files(
            handler='"""Docstring above the import."""\n\n' + BAD))
        self.assertEqual(self.check()[0], 0)

    def test_a_hand_made_entry_without_detail_matches_file_line_rule(self):
        with open(self.base, "w", encoding="utf-8") as f:
            json.dump([{"file": "src/handlers/a.py", "line": 1,
                        "rule": "handlers must not import db"},
                       {"file": "src/util.py", "line": 1, "rule":
                        "src/**.py must not contain /eval\\(/"}], f)
        self.assertEqual(self.check()[0], 0)
        write_files(self.root, self.files(handler="\n" + BAD))  # line moved
        self.assertEqual(self.check()[0], 1)  # legacy entries are exact

    def test_malformed_baseline_is_exit_2(self):
        for body in ('{"a": 1}', '[1, 2]', '[{"line": 1}]', 'not json'):
            with open(self.base, "w", encoding="utf-8") as f:
                f.write(body)
            code, out, err = self.check()
            self.assertEqual(code, 2, body)
            self.assertNotIn("Traceback", err)

    def test_missing_baseline_is_still_exit_2(self):
        os.unlink(self.base)
        self.assertEqual(self.check()[0], 2)


if __name__ == "__main__":
    unittest.main()
