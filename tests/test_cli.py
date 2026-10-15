"""CLI tests: init, check, new, index, drift (temp git repo)."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from adr_drift import cli as cli_mod

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "shop")


def run_cli(*argv) -> int:
    return cli_mod.main(list(argv))


class TestInit(unittest.TestCase):
    def test_init_scaffolds(self):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "src", "api"))
        os.makedirs(os.path.join(tmp, "src", "core"))
        code = run_cli("--root", tmp, "init")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(tmp, "arch.yaml")))
        self.assertTrue(os.path.isfile(
            os.path.join(tmp, "docs", "adr",
                         "0001-record-architecture-decisions.md")))
        self.assertTrue(os.path.isfile(
            os.path.join(tmp, "docs", "adr", "README.md")))

    def test_init_refuses_without_force(self):
        tmp = tempfile.mkdtemp()
        self.assertEqual(run_cli("--root", tmp, "init"), 0)
        self.assertEqual(run_cli("--root", tmp, "init"), 2)
        self.assertEqual(run_cli("--root", tmp, "init", "--force"), 0)


class TestCheck(unittest.TestCase):
    def test_check_finds_violations_exit_1(self):
        self.assertEqual(
            run_cli("--root", FIXTURE, "check", "--format", "json"), 1)

    def test_check_json_shape(self):
        # capture stdout
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            run_cli("--root", FIXTURE, "check", "--format", "json")
        data = json.loads(buf.getvalue())
        self.assertEqual(len(data), 4)
        self.assertTrue(all("rule" in v and "file" in v for v in data))

    def test_check_fail_on_none(self):
        self.assertEqual(
            run_cli("--root", FIXTURE, "check", "--fail-on", "none"), 0)

    def test_check_clean_repo(self):
        tmp = tempfile.mkdtemp()
        shutil.copy(os.path.join(FIXTURE, "arch.yaml"), tmp)
        self.assertEqual(run_cli("--root", tmp, "check"), 0)


class TestBaseline(unittest.TestCase):
    def _copy_fixture(self) -> str:
        tmp = tempfile.mkdtemp()
        root = os.path.join(tmp, "shop")
        shutil.copytree(FIXTURE, root)
        return root

    def test_baseline_writes_current_violations(self):
        root = self._copy_fixture()
        out = os.path.join(tempfile.mkdtemp(), "baseline.json")
        self.assertEqual(
            run_cli("--root", root, "baseline", "--output", out), 0)
        with open(out) as f:
            data = json.load(f)
        self.assertEqual(len(data), 4)
        self.assertTrue(
            all({"file", "line", "rule"} <= set(v) for v in data))

    def test_check_baseline_grandfathers(self):
        root = self._copy_fixture()
        out = os.path.join(tempfile.mkdtemp(), "baseline.json")
        self.assertEqual(
            run_cli("--root", root, "baseline", "--output", out), 0)
        # all 4 violations grandfathered -> check passes
        self.assertEqual(
            run_cli("--root", root, "check", "--baseline", out), 0)

    def test_check_baseline_still_catches_new(self):
        root = self._copy_fixture()
        out = os.path.join(tempfile.mkdtemp(), "baseline.json")
        self.assertEqual(
            run_cli("--root", root, "baseline", "--output", out), 0)
        with open(os.path.join(root, "src", "handlers", "users.py"),
                  "a") as f:
            f.write("\nfrom db.drivers import pg  # new violation\n")
        self.assertEqual(
            run_cli("--root", root, "check", "--baseline", out), 1)

    def test_check_missing_baseline_fails_closed(self):
        # A missing baseline file must error, not silently check clean.
        self.assertEqual(
            run_cli("--root", FIXTURE, "check", "--baseline",
                    "no-such-baseline.json"), 2)


class TestNewAndIndex(unittest.TestCase):
    def test_new_numbers_adrs(self):
        tmp = tempfile.mkdtemp()
        adr_dir = os.path.join(tmp, "docs", "adr")
        self.assertEqual(run_cli("--root", tmp, "new", "Use Postgres"), 0)
        self.assertEqual(run_cli("--root", tmp, "new", "Cache sessions"), 0)
        names = sorted(os.listdir(adr_dir))
        self.assertTrue(names[0].startswith("0001-use-postgres"))
        self.assertTrue(names[1].startswith("0002-cache-sessions"))

    def test_index_lists_adrs(self):
        self.assertEqual(run_cli("--root", FIXTURE, "index"), 0)
        # index writes into the fixture's docs/adr — clean it up
        idx = os.path.join(FIXTURE, "docs", "adr", "README.md")
        try:
            with open(idx) as f:
                content = f.read()
            self.assertIn("0001", content)
            self.assertIn("0002", content)
        finally:
            if os.path.isfile(idx):
                os.unlink(idx)


def git(tmp, *args):
    subprocess.run(["git", "-C", tmp, *args], check=True,
                   capture_output=True)


class TestDrift(unittest.TestCase):
    def _make_repo(self) -> str:
        tmp = tempfile.mkdtemp()
        git(tmp, "init", "-q")
        git(tmp, "config", "user.email", "t@t.t")
        git(tmp, "config", "user.name", "t")
        os.makedirs(os.path.join(tmp, "src", "handlers"))
        os.makedirs(os.path.join(tmp, "src", "db"))
        os.makedirs(os.path.join(tmp, "docs", "adr"))
        with open(os.path.join(tmp, "arch.yaml"), "w") as f:
            f.write('layers:\n'
                    '  - name: handlers\n    paths: ["src/handlers/**"]\n'
                    '  - name: db\n    paths: ["src/db/**"]\n'
                    'rules:\n'
                    '  - "forbid-import: handlers -> db"\n')
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w") as f:
            f.write("def ok():\n    return 1\n")
        with open(os.path.join(tmp, "src", "db", "d.py"), "w") as f:
            f.write("def query():\n    return 1\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "clean start")
        return tmp

    def test_drift_reports_new_violation(self):
        tmp = self._make_repo()
        # Introduce a violation in a new commit.
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w") as f:
            f.write("from db import d\n\ndef bad():\n    return d.query()\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "oops: handler imports db")
        code = run_cli("--root", tmp, "drift", "--since", "HEAD~1")
        self.assertEqual(code, 1)

    def test_drift_clean_when_nothing_new(self):
        tmp = self._make_repo()
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w") as f:
            f.write("def ok():\n    return 2\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "harmless change")
        code = run_cli("--root", tmp, "drift", "--since", "HEAD~1")
        self.assertEqual(code, 0)

    def test_drift_not_a_repo(self):
        tmp = tempfile.mkdtemp()
        self.assertEqual(run_cli("--root", tmp, "drift"), 2)

    def test_drift_reports_fixed(self):
        import io
        from contextlib import redirect_stdout
        tmp = self._make_repo()
        # Commit 1: introduce a violation.
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w") as f:
            f.write("from db import d\n\ndef bad():\n    return d.query()\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "oops: handler imports db")
        # Commit 2: fix it.
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w") as f:
            f.write("def ok():\n    return 1\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "fix: no more db import")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_cli("--root", tmp, "drift", "--since", "HEAD~1")
        out = buf.getvalue()
        # nothing new -> exit 0, but the fix is reported, labeled.
        self.assertEqual(code, 0)
        self.assertIn("1 fixed since HEAD~1", out)
        self.assertIn("src/handlers/a.py", out)

    def test_drift_reports_new_and_fixed_together(self):
        import io
        from contextlib import redirect_stdout
        tmp = self._make_repo()
        os.makedirs(os.path.join(tmp, "src", "handlers"), exist_ok=True)
        # Commit 1: two violations in two files.
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w") as f:
            f.write("from db import d\n")
        with open(os.path.join(tmp, "src", "handlers", "b.py"), "w") as f:
            f.write("from db import d\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "two violations")
        # Commit 2: fix a.py, keep b.py violating.
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w") as f:
            f.write("def ok():\n    return 1\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "fix one")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_cli("--root", tmp, "drift", "--since", "HEAD~1")
        out = buf.getvalue()
        self.assertEqual(code, 0)  # b.py's violation predates the base ref
        self.assertIn("fixed", out)
        self.assertIn("src/handlers/a.py", out)


if __name__ == "__main__":
    unittest.main()
