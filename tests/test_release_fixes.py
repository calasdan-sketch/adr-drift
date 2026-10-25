"""Regression tests for 0.1.3: packaging, hostile files, output failures."""

import io
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

import adr_drift
from adr_drift import cli as cli_mod
from adr_drift import engine, scanner

from _support import chdir, make_repo, run_main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RULES = {"arch.yaml": 'layers:\n  - name: app\n    paths: ["src/**"]\n'
                      'rules:\n  - "forbid-pattern: src/**.py !~ eval\\\\("\n',
         "src/a.py": "x = 1\n"}


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class TestMetadata(unittest.TestCase):
    @unittest.skipIf(tomllib is None, "tomllib needs Python 3.11+")
    def test_pypi_metadata_is_complete(self):
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            meta = tomllib.load(f)
        project = meta["project"]
        self.assertEqual(project["license"], "MIT")
        self.assertEqual(project["license-files"], ["LICENSE"])
        self.assertEqual(project["authors"], [{"name": "Calas Automations Inc."}])
        self.assertTrue(project["keywords"])
        self.assertTrue(any(c.endswith("3.14") for c in project["classifiers"]))
        repo = "https://github.com/calasdan-sketch/adr-drift"
        self.assertEqual(project["urls"]["Homepage"], repo)
        self.assertEqual(project["urls"]["Issues"], repo + "/issues")
        self.assertEqual(meta["tool"]["setuptools"]["packages"], ["adr_drift"])

    def test_the_version_lives_in_two_places_that_agree(self):
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', read("pyproject.toml"))
        self.assertEqual(m.group(1), adr_drift.__version__)
        self.assertEqual(cli_mod.VERSION, adr_drift.__version__)

    def test_the_manifest_ships_the_fixtures_and_the_action(self):
        manifest = read("MANIFEST.in")
        self.assertIn("include action.yml", manifest)
        self.assertIn("recursive-include tests *", manifest)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "action.yml")))

    def test_the_changelog_names_this_version(self):
        self.assertIn("## " + adr_drift.__version__, read("CHANGELOG.md"))

    def test_the_readme_installs_from_pypi(self):
        readme = read("README.md")
        self.assertIn("pip install adr-drift", readme)
        self.assertIn("From source:", readme)


@unittest.skipUnless(hasattr(os, "symlink") and os.path.exists("/dev/zero"),
                     "needs links and /dev/zero")
class TestOnlyRegularFilesAreRead(unittest.TestCase):
    def test_a_link_to_dev_zero_is_reported_not_read(self):
        root = make_repo(RULES)
        os.symlink("/dev/zero", os.path.join(root, "src", "z.py"))
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, err)
        self.assertIn("src/z.py: not a regular file", err)

    def test_a_link_out_of_the_repository_is_not_followed(self):
        root = make_repo(RULES)
        outside = make_repo({"secret.py": "eval(1)\n"})
        os.symlink(os.path.join(outside, "secret.py"),
                   os.path.join(root, "src", "s.py"))
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, out + err)
        self.assertIn("not a regular file", err)

    def test_a_named_pipe_is_not_opened(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("no mkfifo")
        root = make_repo(RULES)
        os.mkfifo(os.path.join(root, "src", "p.py"))
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, err)
        self.assertIn("src/p.py: not a regular file", err)


class TestSizeCap(unittest.TestCase):
    def test_an_oversized_source_file_is_not_checked(self):
        root = make_repo(RULES)
        with mock.patch.object(scanner, "MAX_SOURCE_BYTES", 100):
            with open(os.path.join(root, "src", "big.py"), "w") as f:
                f.write("eval(1)\n" * 50)
            code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, out + err)
        self.assertIn("src/big.py: bigger than", err)

    def test_a_memory_error_while_reading_is_a_warning(self):
        root = make_repo(RULES)
        real = open

        def fake_open(path, *a, **k):
            if str(path).endswith("a.py"):
                raise MemoryError
            return real(path, *a, **k)
        with mock.patch("builtins.open", fake_open):
            code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, out + err)
        self.assertIn("src/a.py: cannot read", err)


class TestUnwritableOutput(unittest.TestCase):
    def violating(self):
        files = dict(RULES)
        files["src/a.py"] = "eval(1)\n"
        return make_repo(files)

    def test_a_full_disk_on_stdout_exits_2_with_one_line(self):
        root = self.violating()

        class Full(io.StringIO):
            def write(self, text):
                raise OSError(28, "No space left on device")
        err = io.StringIO()
        with mock.patch.object(sys, "stdout", Full()), \
                mock.patch.object(sys, "stderr", err):
            code = cli_mod.main(["--root", root, "check"])
        self.assertEqual(code, 2)
        self.assertIn("error: No space left on device", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_an_unwritable_stderr_still_exits_2_on_a_config_error(self):
        root = make_repo({"arch.yaml": "layers: [\n"})

        class Full(io.StringIO):
            def write(self, text):
                raise OSError(28, "No space left on device")
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", Full()):
            code = cli_mod.main(["--root", root, "check"])
        self.assertEqual(code, 2)

    @unittest.skipUnless(os.path.exists("/dev/full"), "needs /dev/full")
    def test_a_real_dev_full_exits_2(self):
        import subprocess
        root = self.violating()
        with open("/dev/full", "w") as full:
            p = subprocess.run([sys.executable, "-m", "adr_drift", "--root",
                                root, "check"], stdout=full,
                               stderr=subprocess.PIPE, text=True,
                               cwd=ROOT)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertNotIn("Traceback", p.stderr)


class TestRootMustBeADirectory(unittest.TestCase):
    def test_a_missing_root_is_named(self):
        missing = os.path.join(tempfile.gettempdir(), "adr-drift-no-such-dir")
        code, out, err = run_main("--root", missing, "check")
        self.assertEqual(code, 2)
        self.assertIn("error: --root: not a directory", err)
        self.assertNotIn("no ADR files found", err)

    def test_a_file_as_root_is_named_after_the_subcommand_too(self):
        root = make_repo(RULES)
        code, out, err = run_main("check", "--root",
                                  os.path.join(root, "arch.yaml"))
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)


if __name__ == "__main__":
    unittest.main()
