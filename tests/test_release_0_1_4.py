"""Regression tests for 0.1.4: an unwritable stdout exits 2 with one line,
ADR files and arch.yaml are read like source files (regular, capped), and a
link that stays inside the repository is checked."""

import errno
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from adr_drift import cli as cli_mod
from adr_drift import scanner

from _support import make_repo, run_main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOP = os.path.join(ROOT, "tests", "fixtures", "shop")

RULES = {"arch.yaml": 'layers:\n  - name: app\n    paths: ["src/**"]\n'
                      'rules:\n  - "forbid-pattern: src/**.py !~ eval\\\\("\n',
         "src/a.py": "x = 1\n"}
ADR = ("# 1. Use layers\n\n## Status\n\nAccepted\n\n## Constraints\n\n"
       "- forbid-pattern: src/**.py !~ eval\\(\n")

HAS_LINKS = hasattr(os, "symlink")
HAS_FULL = os.path.exists("/dev/full")
HAS_ZERO = os.path.exists("/dev/zero")


def _env():
    # Buffered output is what a user has; PYTHONUNBUFFERED would hide the
    # failure until the last byte.
    return {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}


def _cli(*argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, root=None):
    code = ("import sys;from adr_drift.cli import main;"
            "sys.exit(main(sys.argv[1:]))")
    return subprocess.run([sys.executable, "-c", code, *argv], cwd=ROOT,
                          stdout=stdout, stderr=stderr, env=_env(),
                          timeout=120)


@unittest.skipUnless(HAS_FULL, "needs /dev/full")
class TestUnwritableOutput(unittest.TestCase):
    def test_stdout_to_dev_full_exits_2_with_one_error_line(self):
        with open("/dev/full", "wb") as full:
            p = _cli("--root", SHOP, "check", stdout=full)
        err = p.stderr.decode()
        self.assertEqual(p.returncode, 2, err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("Exception ignored", err)
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, err)
        self.assertTrue(lines[0].startswith("error:"), err)

    def test_the_same_for_a_command_that_passes(self):
        # Exit 2 is "could not run", also when the verdict would be 0.
        root = make_repo(RULES)
        with open("/dev/full", "wb") as full:
            p = _cli("--root", root, "check", stdout=full)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertNotIn(b"Traceback", p.stderr)

    def test_stderr_to_dev_full_never_tracebacks(self):
        with open("/dev/full", "wb") as full:
            p = _cli("--root", SHOP, "check", stderr=full)
        self.assertNotIn(b"Traceback", p.stdout)
        self.assertIn(p.returncode, (1, 2))   # the verdict, or "could not say"


class TestStreamsInProcess(unittest.TestCase):
    class _Full(io.StringIO):
        def flush(self):
            raise OSError(errno.ENOSPC, "No space left on device")

    def test_a_failing_final_flush_is_exit_2_and_one_line(self):
        err = io.StringIO()
        with mock.patch.object(sys, "stdout", self._Full()), \
                mock.patch.object(sys, "stderr", err):
            code = cli_mod.main(["--root", SHOP, "check"])
        self.assertEqual(code, 2)
        lines = [ln for ln in err.getvalue().splitlines() if ln]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("No space left", lines[0])

    def test_streams_are_restored_afterwards(self):
        before = (sys.stdout, sys.stderr)
        with mock.patch.object(sys, "stdout", self._Full()) as out:
            with mock.patch.object(sys, "stderr", io.StringIO()) as err:
                cli_mod.main(["--root", SHOP, "check"])
                self.assertIs(sys.stdout, out)
                self.assertIs(sys.stderr, err)
        self.assertEqual(before, (sys.stdout, sys.stderr))

    @unittest.skipIf(os.name == "nt", "pipe semantics differ on Windows")
    def test_a_closed_pipe_still_keeps_the_verdict(self):
        p = subprocess.Popen(
            [sys.executable, "-m", "adr_drift", "--root", SHOP, "check"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT,
            env=_env())
        p.stdout.close()
        err = p.stderr.read()
        p.stderr.close()
        self.assertEqual(p.wait(timeout=60), 1, err)   # violations found
        self.assertNotIn(b"Traceback", err)


@unittest.skipUnless(HAS_LINKS and HAS_ZERO, "needs links and /dev/zero")
class TestConfigFilesAreGuarded(unittest.TestCase):
    def _limited(self, root, *argv):
        """Run the CLI with the address space capped, so the old unbounded
        read fails fast instead of eating the machine."""
        code = ("import resource,sys;"
                "resource.setrlimit(resource.RLIMIT_AS,(2<<30,2<<30));"
                "from adr_drift.cli import main;"
                "sys.exit(main(sys.argv[1:]))")
        if os.name == "nt":
            self.skipTest("no RLIMIT_AS")
        return subprocess.run(
            [sys.executable, "-c", code, "--root", root, *argv], cwd=ROOT,
            capture_output=True, text=True, timeout=60, env=_env())

    def test_an_adr_that_is_a_link_to_dev_zero_is_refused(self):
        root = make_repo(RULES)
        os.makedirs(os.path.join(root, "docs", "adr"))
        os.symlink("/dev/zero", os.path.join(root, "docs", "adr",
                                             "0001-zero.md"))
        p = self._limited(root, "check")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("not a regular file", p.stderr)
        self.assertIn("0001-zero.md", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_arch_yaml_that_is_a_link_to_dev_zero_is_refused(self):
        root = make_repo({"src/a.py": "x = 1\n"})
        os.symlink("/dev/zero", os.path.join(root, "arch.yaml"))
        p = self._limited(root, "check")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("arch.yaml: cannot read", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_index_refuses_such_an_adr_with_one_line(self):
        root = make_repo({"docs/adr/0001-a.md": ADR})
        os.symlink("/dev/zero", os.path.join(root, "docs", "adr",
                                             "0002-zero.md"))
        p = self._limited(root, "index")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("0002-zero.md", p.stderr)
        self.assertNotIn("Traceback", p.stderr)


class TestSizeCapsAndLinks(unittest.TestCase):
    def test_an_oversized_adr_is_refused(self):
        root = make_repo({**RULES, "docs/adr/0001-a.md":
                          ADR + "x" * (scanner.MAX_CONFIG_BYTES + 10)})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2, err)
        self.assertIn("0001-a.md", err + out)
        self.assertIn("bigger than 1 MiB", err + out)

    def test_an_oversized_arch_yaml_is_refused(self):
        root = make_repo({"src/a.py": "x = 1\n",
                          "arch.yaml": "# " + "x" * scanner.MAX_CONFIG_BYTES})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2, err)
        self.assertIn("arch.yaml: cannot read", err + out)

    def test_a_memory_error_while_reading_an_adr_is_a_clean_exit_2(self):
        root = make_repo({**RULES, "docs/adr/0001-a.md": ADR})
        with mock.patch.object(scanner, "read_regular",
                               side_effect=MemoryError):
            code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2, err)
        self.assertNotIn("Traceback", err)

    @unittest.skipUnless(HAS_LINKS, "needs links")
    def test_a_link_to_a_file_inside_the_repo_is_checked(self):
        root = make_repo({**RULES, "src/real.py": "eval(1)\n"})
        os.symlink("real.py", os.path.join(root, "src", "alias.py"))
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 1, out + err)
        self.assertIn("src/alias.py", out)
        self.assertNotIn("not a regular file", err)

    @unittest.skipUnless(HAS_LINKS, "needs links")
    def test_a_link_out_of_the_repo_is_still_skipped(self):
        root = make_repo(RULES)
        outside = make_repo({"secret.py": "eval(1)\n"})
        os.symlink(os.path.join(outside, "secret.py"),
                   os.path.join(root, "src", "s.py"))
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, out + err)
        self.assertIn("src/s.py: not a regular file", err)
        self.assertIn("leaves the repository", err)

    @unittest.skipUnless(HAS_LINKS, "needs links")
    def test_a_link_to_nowhere_is_skipped(self):
        root = make_repo(RULES)
        os.symlink("missing.py", os.path.join(root, "src", "gone.py"))
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, out + err)
        self.assertIn("src/gone.py: not a regular file", err)

    @unittest.skipUnless(HAS_LINKS, "needs links")
    def test_an_adr_link_inside_the_repo_is_read(self):
        root = make_repo({**RULES, "notes/real.md": ADR,
                          "src/b.py": "eval(2)\n"})
        os.makedirs(os.path.join(root, "docs", "adr"))
        os.symlink(os.path.join("..", "..", "notes", "real.md"),
                   os.path.join(root, "docs", "adr", "0001-real.md"))
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 1, out + err)

    @unittest.skipUnless(HAS_LINKS, "needs links")
    def test_an_adr_link_out_of_the_repo_is_refused(self):
        outside = make_repo({"x.md": ADR})
        root = make_repo(RULES)
        os.makedirs(os.path.join(root, "docs", "adr"))
        os.symlink(os.path.join(outside, "x.md"),
                   os.path.join(root, "docs", "adr", "0001-out.md"))
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 2, out + err)
        self.assertIn("leaves the repository", out + err)

    def test_read_regular_reads_a_normal_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.md")
            with open(p, "wb") as f:
                f.write(b"abc")
            self.assertEqual(scanner.read_regular(p, 10, root=d), b"abc")
            with self.assertRaises(scanner.RefusedFile):
                scanner.read_regular(p, 2, root=d)
            with self.assertRaises(scanner.RefusedFile):
                scanner.read_regular(d, 10)   # a directory


if __name__ == "__main__":
    unittest.main()
