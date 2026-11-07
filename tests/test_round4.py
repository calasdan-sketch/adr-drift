"""Expert round 4: what a stranger gets from `pip install`, and the run
that ends badly (full disk, bug, Ctrl-C) without a traceback or a false
"violations found"."""

import ast
import errno
import io
import os
import re
import sys
import unittest
from unittest import mock

from _support import make_repo, run_main

import adr_drift
from adr_drift import cli as cli_mod

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "adr_drift")

LAYERS = ('layers:\n'
          '  - name: a\n    paths: ["src/a/**"]\n'
          '  - name: b\n    paths: ["src/b/**"]\n')


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


@unittest.skipUnless(os.path.exists(os.path.join(ROOT, "pyproject.toml")),
                     "run from a source tree")
class Packaging(unittest.TestCase):
    def test_no_third_party_import(self):
        std = set(sys.stdlib_module_names) if hasattr(
            sys, "stdlib_module_names") else None
        if std is None:
            self.skipTest("needs Python 3.10+")
        for name in os.listdir(SRC):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(read("adr_drift", name))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    mods = [(node.module or "").split(".")[0]]
                for m in mods:
                    self.assertIn(m, std | {"adr_drift"},
                                  f"{name} imports {m}, which is not stdlib")

    def test_version_is_written_once_and_matches_the_changelog(self):
        pyproject = read("pyproject.toml")
        self.assertNotRegex(pyproject, r'(?m)^version\s*=\s*"')
        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertIn("adr_drift.__version__", pyproject)
        first = re.search(r"(?m)^## (\d+\.\d+\.\d+)", read("CHANGELOG.md"))
        self.assertEqual(first.group(1), adr_drift.__version__)

    def test_metadata_a_registry_page_needs(self):
        p = read("pyproject.toml")
        for needle in ('license = "MIT"', "authors =", "keywords =",
                       "classifiers =", "[project.urls]", "Source =",
                       'requires-python = ">=3.10"', "dependencies = []"):
            self.assertIn(needle, p)
        self.assertIn("MIT License", read("LICENSE"))
        # every Python the classifiers claim is at least the minimum
        self.assertIn("Programming Language :: Python :: 3.10", p)

    def test_the_sdist_manifest_names_what_the_tests_need(self):
        m = read("MANIFEST.in")
        for needle in ("LICENSE", "CHANGELOG.md", "action.sh",
                       "recursive-include tests"):
            self.assertIn(needle, m)

    def test_no_personal_path_or_secret_in_the_package(self):
        for name in os.listdir(SRC):
            if name.endswith(".py"):
                text = read("adr_drift", name)
                self.assertNotRegex(text, r"/home/[a-z]|/Users/[A-Za-z]|"
                                    r"C:\\\\Users")
                self.assertNotRegex(text, r"(?i)api[_-]?key\s*=\s*['\"]\w{8}")


class FailedRuns(unittest.TestCase):
    def test_a_full_disk_under_the_report_is_exit_2_not_a_traceback(self):
        class Full(io.StringIO):
            def write(self, s):
                raise OSError(errno.ENOSPC, "No space left on device")

        r = make_repo({"arch.yaml": LAYERS})
        err = io.StringIO()
        with mock.patch.object(sys, "stdout", Full()), \
                mock.patch.object(sys, "stderr", err):
            code = cli_mod.main(["--root", r, "check", "--allow-empty"])
        self.assertEqual(code, 2)
        self.assertIn("No space left", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_a_write_that_fails_is_exit_2(self):
        r = make_repo({"src/a/x.py": "x = 1\n"})
        with mock.patch("os.replace",
                        side_effect=PermissionError(13, "Permission denied")):
            code, out, err = run_main("--root", r, "init")
        self.assertEqual(code, 2)
        self.assertIn("cannot read or write a file", err)
        self.assertNotIn("Traceback", err)
        # no half-written file, no temporary file left behind
        self.assertEqual([n for n in os.listdir(r) if n.startswith(".adr")],
                         [])

    def test_a_bug_is_exit_2_with_a_plain_message(self):
        r = make_repo({"arch.yaml": LAYERS})
        with mock.patch.object(cli_mod, "cmd_check",
                               side_effect=RuntimeError("boom")):
            code, out, err = run_main("--root", r, "check")
        self.assertEqual(code, 2)
        self.assertIn("internal error", err)
        self.assertIn("RuntimeError: boom", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")

    def test_debug_variable_shows_the_traceback(self):
        r = make_repo({"arch.yaml": LAYERS})
        with mock.patch.object(cli_mod, "cmd_check",
                               side_effect=RuntimeError("boom")), \
                mock.patch.dict(os.environ, {"ADR_DRIFT_DEBUG": "1"}):
            with self.assertRaises(RuntimeError):
                run_main("--root", r, "check")

    def test_ctrl_c_is_130(self):
        r = make_repo({"arch.yaml": LAYERS})
        with mock.patch.object(cli_mod, "cmd_check",
                               side_effect=KeyboardInterrupt()):
            code, out, err = run_main("--root", r, "check")
        self.assertEqual(code, 130)
        self.assertNotIn("Traceback", err)


class Writing(unittest.TestCase):
    def test_files_are_written_with_lf_and_leave_nothing_behind(self):
        r = make_repo({"src/a/x.py": "x = 1\n"})
        self.assertEqual(run_main("--root", r, "init")[0], 0)
        self.assertEqual(run_main("--root", r, "new", "Use queues")[0], 0)
        for rel in ("arch.yaml", "docs/adr/0002-use-queues.md",
                    "docs/adr/README.md"):
            with open(os.path.join(r, *rel.split("/")), "rb") as f:
                self.assertNotIn(b"\r", f.read(), rel)
        self.assertEqual([n for n in os.listdir(r) if n.startswith(".adr")],
                         [])

    def test_a_replaced_file_keeps_its_permissions(self):
        if os.name == "nt":
            self.skipTest("POSIX modes")
        r = make_repo({"src/a/x.py": "x = 1\n"})
        run_main("--root", r, "init")
        path = os.path.join(r, "arch.yaml")
        os.chmod(path, 0o664)
        run_main("--root", r, "init", "--force")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o664)

    def test_a_symlinked_file_stays_a_link(self):
        if not hasattr(os, "symlink") or os.name == "nt":
            self.skipTest("needs symlinks")
        r = make_repo({"src/a/x.py": "x = 1\n", "real/arch.yaml": "old\n"})
        os.symlink(os.path.join(r, "real", "arch.yaml"),
                   os.path.join(r, "arch.yaml"))
        self.assertEqual(run_main("--root", r, "init", "--force")[0], 0)
        self.assertTrue(os.path.islink(os.path.join(r, "arch.yaml")))
        with open(os.path.join(r, "real", "arch.yaml")) as f:
            self.assertIn("layers:", f.read())

    def test_new_with_a_title_that_makes_no_slug(self):
        r = make_repo({"docs/adr/0001-x.md": "# 1: X\n"})
        code, out, _ = run_main("--root", r, "new", "???")
        self.assertEqual(code, 0)
        self.assertIn("0002-untitled.md", out)

    def test_new_caps_the_file_name(self):
        r = make_repo({"docs/adr/0001-x.md": "# 1: X\n"})
        code, out, _ = run_main("--root", r, "new", "word " * 200)
        self.assertEqual(code, 0)
        name = out.split("docs/adr/")[1].strip()
        self.assertLess(len(name), 80)

    def test_new_never_overwrites_a_number_taken_meanwhile(self):
        r = make_repo({"docs/adr/0001-x.md": "# 1: X\n"})
        real = cli_mod.config_mod.next_adr_number
        # a second `new` already took 0002 after this one counted
        with mock.patch.object(cli_mod.config_mod, "next_adr_number",
                               return_value=1):
            code, out, _ = run_main("--root", r, "new", "x")
        self.assertEqual(code, 0)
        self.assertIn("0002-x.md", out)
        with open(os.path.join(r, "docs/adr/0001-x.md")) as f:
            self.assertEqual(f.read(), "# 1: X\n")
        self.assertTrue(real)


class InitSuggest(unittest.TestCase):
    FILES = {"src/a/m.py": "import b\n", "src/b/n.py": "x = 1\n"}

    def test_the_first_check_after_init_suggest_passes_and_enforces(self):
        r = make_repo(self.FILES)
        code, out, _ = run_main("--root", r, "init", "--suggest")
        self.assertEqual(code, 0)
        self.assertIn("layer-order: a > b", out)
        self.assertEqual(run_main("--root", r, "check")[0], 0)
        # now the rule is live: a back edge is a violation
        with open(os.path.join(r, "src/b/n.py"), "w") as f:
            f.write("import a\n")
        code, out, _ = run_main("--root", r, "check")
        self.assertEqual(code, 1)

    def test_without_the_flag_nothing_is_written(self):
        r = make_repo(self.FILES)
        run_main("--root", r, "init")
        with open(os.path.join(r, "arch.yaml")) as f:
            self.assertNotIn("init --suggest", f.read())

    def test_nothing_to_suggest_leaves_the_file_alone(self):
        r = make_repo({"src/a/m.py": "x = 1\n"})
        code, out, _ = run_main("--root", r, "init", "--suggest")
        self.assertEqual(code, 0)
        self.assertIn("Next: enable at least one rule", out)


if __name__ == "__main__":
    unittest.main()
