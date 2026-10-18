"""Where ADRs are read from, and never reading none in silence.

Regressions for the panel finding "a violating ADR in doc/adr (the adr-tools
default), docs/decisions or docs/adr/2024/ is never read: 'all ADRs hold',
exit 0, no warning". ADRs were taken only from the files directly inside
docs/adr, and an empty or missing directory looked the same as a clean one.
"""

import json
import os
import unittest

from adr_drift import config as config_mod

from _support import (GitCase, commit_all, init_git, make_repo, run_main,
                      write_files)

ARCH = ('layers:\n  - name: a\n    paths: ["a/**"]\n'
        '  - name: b\n    paths: ["b/**"]\n'
        'rules:\n  - "forbid-pattern: a/**.py !~ eval"\n')   # some other rule
VIOLATOR = {"a/x.py": "from b import thing\n", "b/__init__.py": "thing = 1\n"}


def adr(*constraints, title="Layers"):
    return (f"# 1: {title}\n\n## Status\n\nAccepted\n\n## Constraints\n\n"
            + "\n".join(constraints) + "\n")


FORBID = adr("- forbid-import: a -> b")      # violated by a/x.py
HARMLESS = adr("- forbid-pattern: a/**.py !~ never_there")


def repo(adrs=None, **more):
    """arch.yaml (an unrelated rule), a/x.py violating a -> b, and `adrs`
    ({relative path: text})."""
    return make_repo({"arch.yaml": ARCH, **VIOLATOR, **(adrs or {}), **more})


def check(root, *extra):
    return run_main("--root", root, "check", *extra)


class TestEveryCandidateDirectory(unittest.TestCase):
    def test_the_candidates_are_the_documented_ones(self):
        self.assertEqual(config_mod.ADR_DIR_CANDIDATES, (
            "docs/adr", "doc/adr", "docs/decisions",
            "docs/architecture/decisions", "adr", "decisions"))
        self.assertEqual(config_mod.DEFAULT_ADR_DIR, "docs/adr")

    def test_an_adr_in_any_candidate_is_read(self):
        for d in config_mod.ADR_DIR_CANDIDATES:
            with self.subTest(dir=d):
                root = repo({f"{d}/0001-layers.md": FORBID})
                code, out, err = check(root)
                self.assertEqual(code, 1, out + err)
                self.assertIn("a/x.py", out)
                self.assertIn(f"({d}/0001-layers.md)", out)
                self.assertNotIn("no ADR files", err)

    def test_the_first_existing_candidate_wins(self):
        root = repo({"docs/adr/0001-ok.md": HARMLESS,
                     "doc/adr/0002-forbid.md": FORBID})
        code, out, err = check(root)
        self.assertEqual(code, 0, out + err)          # doc/adr is not read ...
        self.assertIn("doc/adr", err)                 # ... and that is said
        self.assertIn("NOT read", err)
        self.assertIn("--adr-dir docs/adr --adr-dir doc/adr", err)
        self.assertEqual(check(root, "--strict")[0], 2)

    def test_a_candidate_without_adr_files_is_not_nagged_about(self):
        root = repo({"docs/adr/0001-ok.md": HARMLESS,
                     "decisions/notes.py": "x = 1\n"})
        code, out, err = check(root, "--strict")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(err, "")


class TestExplicitDirectories(unittest.TestCase):
    def test_adr_dir_pins_the_directory_and_silences_the_search(self):
        root = repo({"docs/adr/0001-ok.md": HARMLESS,
                     "doc/adr/0002-forbid.md": FORBID})
        code, out, err = check(root, "--adr-dir", "doc/adr")
        self.assertEqual(code, 1, out + err)
        self.assertNotIn("NOT read", err)

    def test_adr_dir_may_be_repeated(self):
        root = repo({"docs/adr/0001-ok.md": HARMLESS,
                     "doc/adr/0002-forbid.md": FORBID})
        code, out, err = check(root, "--adr-dir", "docs/adr",
                               "--adr-dir", "doc/adr")
        self.assertEqual(code, 1, out + err)
        self.assertEqual(err, "")

    def test_repeating_it_before_and_after_the_command_keeps_both(self):
        root = repo({"docs/adr/0001-ok.md": HARMLESS,
                     "doc/adr/0002-forbid.md": FORBID})
        code, out, err = run_main("--root", root, "--adr-dir", "docs/adr",
                                  "check", "--adr-dir", "doc/adr")
        self.assertEqual(code, 1, out + err)

    def test_a_file_reached_twice_is_read_once(self):
        root = repo({"docs/adr/0001-forbid.md": FORBID})
        code, out, err = check(root, "--adr-dir", "docs/adr",
                               "--adr-dir", "docs", "--format", "json")
        self.assertEqual(code, 1, err)
        self.assertEqual(len(json.loads(out)), 1)

    def test_a_missing_directory_is_a_warning_with_a_hint(self):
        root = repo({"doc/adr/0001-forbid.md": FORBID})
        code, out, err = check(root, "--adr-dir", "docs/adr")
        self.assertEqual(code, 0, out + err)       # nothing was read ...
        self.assertIn("docs/adr not found", err)    # ... and it says so
        self.assertIn("doc/adr", err)
        self.assertIn("--adr-dir doc/adr", err)
        self.assertIn("0 ADRs read", out)
        self.assertNotIn("all ADRs hold", out)
        self.assertEqual(check(root, "--adr-dir", "docs/adr", "--strict")[0], 2)

    def test_an_empty_directory_name_is_refused(self):
        root = repo()
        with self.assertRaises(SystemExit) as cm:
            check(root, "--adr-dir", "")
        self.assertEqual(cm.exception.code, 2)


class TestSubdirectories(unittest.TestCase):
    def test_adrs_in_subdirectories_are_read(self):
        root = repo({"docs/adr/2024/0001-forbid.md": FORBID})
        code, out, err = check(root)
        self.assertEqual(code, 1, out + err)
        self.assertIn("(docs/adr/2024/0001-forbid.md)", out)

    def test_deeply_nested_and_each_year_directory(self):
        root = repo({"docs/adr/2023/0001-ok.md": HARMLESS,
                     "docs/adr/2024/q3/0002-forbid.md": FORBID})
        code, out, err = check(root)
        self.assertEqual(code, 1, out + err)

    def test_readmes_hidden_and_vendored_directories_are_skipped(self):
        root = repo({"docs/adr/0001-ok.md": HARMLESS,
                     "docs/adr/README.md": FORBID,
                     "docs/adr/2024/README.md": FORBID,
                     "docs/adr/.drafts/0009-forbid.md": FORBID,
                     "docs/adr/node_modules/x/0009-forbid.md": FORBID})
        code, out, err = check(root)
        self.assertEqual(code, 0, out + err)

    def test_the_order_is_stable_across_platforms(self):
        root = repo({"docs/adr/0002-b.md": HARMLESS,
                     "docs/adr/0001-a.md": HARMLESS,
                     "docs/adr/2024/0003-c.md": HARMLESS})
        found = config_mod.find_adrs(root)
        rel = [os.path.relpath(p, root).replace(os.sep, "/")
               for p in found.files]
        self.assertEqual(rel, ["docs/adr/0001-a.md", "docs/adr/0002-b.md",
                               "docs/adr/2024/0003-c.md"])


class TestNeverReadingNoneInSilence(unittest.TestCase):
    def test_zero_adrs_is_a_warning_and_the_summary_says_so(self):
        root = repo()                                 # no ADR directory at all
        code, out, err = check(root)
        self.assertEqual(code, 0, out + err)
        self.assertIn("no ADR files found", err)
        self.assertIn("docs/adr", err)                # where it looked
        self.assertIn("--adr-dir", err)               # what to do
        self.assertIn("0 ADRs read", out)
        self.assertNotIn("all ADRs hold", out)
        self.assertEqual(check(root, "--strict")[0], 2)

    def test_an_empty_adr_directory_is_the_same(self):
        root = repo({"docs/adr/README.md": "# index\n"})
        code, out, err = check(root)
        self.assertIn("no ADR files found", err)
        self.assertIn("0 ADRs read", out)

    def test_the_summary_counts_the_adrs_read(self):
        root = make_repo({
            "arch.yaml": ARCH, "a/x.py": "x = 1\n", "b/y.py": "y = 1\n",
            "docs/adr/0001-a.md": HARMLESS, "docs/adr/2024/0002-b.md": HARMLESS})
        code, out, err = check(root, "--strict")
        self.assertEqual(code, 0, out + err)
        self.assertIn("all ADRs hold (2 read)", out)
        self.assertEqual(err, "")

    def test_one_adr_is_not_pluralised(self):
        root = make_repo({"arch.yaml": ARCH, "a/x.py": "x = 1\n",
                          "docs/adr/0001-a.md": HARMLESS})
        code, out, err = check(root)
        self.assertIn("(1 read)", out)

    def test_json_stays_a_bare_array_and_the_warning_goes_to_stderr(self):
        root = repo()
        code, out, err = check(root, "--format", "json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), [])
        self.assertIn("no ADR files found", err)

    def test_baseline_reads_adrs_from_the_candidate_directories(self):
        root = repo({"doc/adr/0001-forbid.md": FORBID})
        code, out, err = run_main("--root", root, "baseline")
        self.assertEqual(code, 0, err)
        with open(os.path.join(root, ".adr-baseline.json"),
                  encoding="utf-8") as f:
            self.assertEqual([v["file"] for v in json.load(f)], ["a/x.py"])


class TestWritingCommandsFollowTheSearch(unittest.TestCase):
    def test_new_writes_into_the_directory_that_exists(self):
        root = repo({"doc/adr/0004-old.md": HARMLESS,
                     "doc/adr/2024/0007-newer.md": HARMLESS})
        code, out, err = run_main("--root", root, "new", "Use Postgres")
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(
            os.path.join(root, "doc", "adr", "0008-use-postgres.md")))
        self.assertFalse(os.path.exists(os.path.join(root, "docs")))

    def test_new_with_nothing_yet_creates_docs_adr(self):
        root = repo()
        self.assertEqual(run_main("--root", root, "new", "First")[0], 0)
        self.assertTrue(os.path.isfile(
            os.path.join(root, "docs", "adr", "0001-first.md")))

    def test_new_honours_adr_dir_given_after_the_command(self):
        root = repo()
        self.assertEqual(run_main("new", "Second", "--root", root,
                                  "--adr-dir", "decisions")[0], 0)
        self.assertTrue(os.path.isfile(
            os.path.join(root, "decisions", "0001-second.md")))


class TestDriftReadsThemToo(GitCase):
    def history(self, adrs):
        root = init_git(make_repo({
            "arch.yaml": ARCH, "a/x.py": "x = 1\n",
            "b/__init__.py": "thing = 1\n", **adrs}))
        commit_all(root, "start")
        write_files(root, {"a/x.py": "from b import thing\n"})
        commit_all(root, "a imports b")
        return root

    def test_drift_uses_the_discovered_adrs(self):
        root = self.history({"doc/adr/2024/0001-forbid.md": FORBID})
        code, out, err = run_main("--root", root, "drift", "--since", "HEAD~1")
        self.assertEqual(code, 1, out + err)
        self.assertIn("a/x.py", out)
        self.assertIn("doc/adr/2024/0001-forbid.md", out)

    def test_a_clean_drift_says_how_many_adrs_it_read(self):
        root = self.history({"doc/adr/0001-ok.md": HARMLESS})
        code, out, err = run_main("--root", root, "drift", "--since", "HEAD~1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("no new drift since HEAD~1 (1 file(s) changed; "
                      "1 ADR read)", out)

    def test_drift_with_no_adrs_warns_and_counts_zero(self):
        root = self.history({})
        code, out, err = run_main("--root", root, "drift", "--since", "HEAD~1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("no ADR files found", err)
        self.assertIn("0 ADRs read", out)


if __name__ == "__main__":
    unittest.main()
