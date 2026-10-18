"""CLI tests: init, check, new, index, drift (temp git repo)."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from adr_drift import cli as cli_mod

from _support import GitCase, make_repo, run_main

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "shop")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_cli(*argv) -> int:
    return cli_mod.main(list(argv))


def run_cli_process(argv, env_extra) -> subprocess.CompletedProcess:
    """Run the CLI in a child interpreter with extra environment.

    Encoding behaviour (stdout codec, default file encoding) is fixed at
    interpreter start-up, so Windows console conditions can only be
    reproduced in a subprocess.
    """
    env = {**os.environ, **env_extra}
    env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "adr_drift", *argv],
                          cwd=REPO_ROOT, env=env, capture_output=True,
                          timeout=120)


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


class TestInitThenCheck(unittest.TestCase):
    def test_flat_repo_init_then_check_exits_2_until_a_rule_is_enabled(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "main.py"), "w", encoding="utf-8") as f:
            f.write("print('hi')\n")
        self.assertEqual(run_cli("--root", tmp, "init"), 0)
        with open(os.path.join(tmp, "arch.yaml"), encoding="utf-8") as f:
            self.assertIn("layers: []", f.read())
        # Nothing is enforced yet: a green check would be a lie (exit 2),
        # unless the caller says they are adopting gradually.
        self.assertEqual(run_cli("--root", tmp, "check"), 2)
        self.assertEqual(run_cli("--root", tmp, "check", "--allow-empty"), 0)

    def test_sample_adr_has_no_live_constraints(self):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "src", "handlers"))
        with open(os.path.join(tmp, "src", "handlers", "h.py"), "w", encoding="utf-8") as f:
            f.write("x = eval('1')\n")  # would trip the sample rules
        self.assertEqual(run_cli("--root", tmp, "init"), 0)
        from adr_drift import config
        _l, rules, _a = config.load_all_rules(tmp)
        self.assertEqual(rules, [])
        self.assertEqual(run_cli("--root", tmp, "check"), 2)
        self.assertEqual(run_cli("--root", tmp, "check", "--allow-empty"), 0)

    def test_check_without_config_still_fails_closed(self):
        self.assertEqual(run_cli("--root", tempfile.mkdtemp(), "check"), 2)


class TestRelativePathsInOutput(unittest.TestCase):
    def test_human_output_has_no_absolute_paths(self):
        import io
        from contextlib import redirect_stdout
        root = os.path.join(tempfile.mkdtemp(), "shop")
        shutil.copytree(FIXTURE, root)
        buf = io.StringIO()
        with redirect_stdout(buf):
            run_cli("--root", root, "check")
        out = buf.getvalue()
        self.assertIn("(docs/adr/0001-layered-architecture.md)", out)
        self.assertNotIn(root, out)


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
        with open(out, encoding="utf-8") as f:
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
                  "a", encoding="utf-8") as f:
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
            with open(idx, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("0001", content)
            self.assertIn("0002", content)
        finally:
            if os.path.isfile(idx):
                os.unlink(idx)


def git(tmp, *args):
    subprocess.run(["git", "-C", tmp, *args], check=True,
                   capture_output=True)


class TestDrift(GitCase):  # GitCase: git ignores the machine's own config
    def _make_repo(self) -> str:
        tmp = tempfile.mkdtemp()
        git(tmp, "init", "-q")
        git(tmp, "config", "user.email", "t@t.t")
        git(tmp, "config", "user.name", "t")
        os.makedirs(os.path.join(tmp, "src", "handlers"))
        os.makedirs(os.path.join(tmp, "src", "db"))
        os.makedirs(os.path.join(tmp, "docs", "adr"))
        with open(os.path.join(tmp, "arch.yaml"), "w", encoding="utf-8") as f:
            f.write('layers:\n'
                    '  - name: handlers\n    paths: ["src/handlers/**"]\n'
                    '  - name: db\n    paths: ["src/db/**"]\n'
                    'rules:\n'
                    '  - "forbid-import: handlers -> db"\n')
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w", encoding="utf-8") as f:
            f.write("def ok():\n    return 1\n")
        with open(os.path.join(tmp, "src", "db", "d.py"), "w", encoding="utf-8") as f:
            f.write("def query():\n    return 1\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "clean start")
        return tmp

    def test_drift_reports_new_violation(self):
        tmp = self._make_repo()
        # Introduce a violation in a new commit.
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w", encoding="utf-8") as f:
            f.write("from db import d\n\ndef bad():\n    return d.query()\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "oops: handler imports db")
        code = run_cli("--root", tmp, "drift", "--since", "HEAD~1")
        self.assertEqual(code, 1)

    def test_drift_clean_when_nothing_new(self):
        tmp = self._make_repo()
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w", encoding="utf-8") as f:
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
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w", encoding="utf-8") as f:
            f.write("from db import d\n\ndef bad():\n    return d.query()\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "oops: handler imports db")
        # Commit 2: fix it.
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w", encoding="utf-8") as f:
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
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w", encoding="utf-8") as f:
            f.write("from db import d\n")
        with open(os.path.join(tmp, "src", "handlers", "b.py"), "w", encoding="utf-8") as f:
            f.write("from db import d\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "two violations")
        # Commit 2: fix a.py, keep b.py violating.
        with open(os.path.join(tmp, "src", "handlers", "a.py"), "w", encoding="utf-8") as f:
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



ACTION_YML = os.path.join(REPO_ROOT, "action.yml")

ARCH_AB = ('layers:\n  - name: a\n    paths: ["a/**"]\n'
           '  - name: b\n    paths: ["b/**"]\n'
           'rules:\n  - "forbid-pattern: a/**.py !~ eval"\n')
ADR_A_B = ("# 1: Layers\n\n## Status\n\nAccepted\n\n## Constraints\n\n"
           "- forbid-import: a -> b\n")


def adr_in_unusual_place():
    """A repo whose only forbid-import rule sits in doc/decisions/ (not in
    docs/adr) and is violated by a/x.py."""
    return make_repo({"arch.yaml": ARCH_AB,
                      "doc/decisions/0001-layers.md": ADR_A_B,
                      "a/x.py": "from b import thing\n",
                      "b/__init__.py": "thing = 1\n"})


class TestAction(unittest.TestCase):
    def test_action_yml_runs_check(self):
        from adr_drift import miniyaml
        path = os.path.join(os.path.dirname(FIXTURE), "..", "..",
                            "action.yml")
        with open(path, encoding="utf-8") as f:
            data = miniyaml.safe_load(f.read())
        self.assertEqual(data["runs"]["using"], "composite")
        step = data["runs"]["steps"][0]
        self.assertIn("adr_drift check", step["run"])
        self.assertEqual(step["shell"], "bash")

    def _check_command(self) -> str:
        """The action's `adr_drift check ...` command, as action.yml has it."""
        from adr_drift import miniyaml
        with open(ACTION_YML, encoding="utf-8") as f:
            data = miniyaml.safe_load(f.read())
        run = data["runs"]["steps"][0]["run"]
        command = run.split("&&")[-1].strip()
        self.assertEqual(command, "python -m adr_drift check $ADR_DRIFT_ARGS")
        return command

    def test_args_input_after_check_may_hold_adr_dir_and_root(self):
        # The action appends its `args` input after `check`; an option that
        # belongs to the whole tool used to be "unrecognized arguments".
        command = self._check_command()
        root = adr_in_unusual_place()
        args = f"--adr-dir doc/decisions --root {root}"
        argv = command.replace("$ADR_DRIFT_ARGS", args).split()[3:]
        self.assertEqual(argv[0], "check")
        code, out, err = run_main(*argv)
        self.assertEqual(code, 1, out + err)
        self.assertIn("a/x.py", out)
        self.assertNotIn("unrecognized", err)

    @unittest.skipIf(os.name == "nt",
                     "on Windows `bash` is usually the WSL launcher, which cannot "
                     "see Windows paths; the action runs under Git Bash there")
    @unittest.skipUnless(shutil.which("bash"), "the action's shell is bash")
    def test_the_actions_command_line_in_a_real_shell(self):
        command = self._check_command().replace(
            "python -m adr_drift", '"$PYBIN" -m adr_drift')
        root = adr_in_unusual_place()
        env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
        env.update(PYBIN=sys.executable, PYTHONPATH=REPO_ROOT,
                   ADR_DRIFT_ARGS="--adr-dir doc/decisions --strict")
        p = subprocess.run(["bash", "-c", command], cwd=root, env=env,
                           capture_output=True, timeout=120)
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn(b"a/x.py", p.stdout)
        self.assertNotIn(b"unrecognized", p.stderr)


class TestOptionsBeforeAndAfterTheSubcommand(unittest.TestCase):
    """--root and --adr-dir are accepted on both sides of the subcommand."""

    def test_every_subcommand_takes_them(self):
        for cmd in (["init"], ["check"], ["baseline"], ["drift"], ["index"],
                    ["new", "A title"]):
            args = cli_mod.parse_args(
                [*cmd, "--root", "/r", "--adr-dir", "d/adr"])
            self.assertEqual((args.root, args.adr_dir), ("/r", ["d/adr"]), cmd)

    def test_options_before_the_subcommand_are_not_reset_by_it(self):
        # argparse copies a sub-parser's defaults over the top-level values
        args = cli_mod.parse_args(
            ["--root", "/r", "--adr-dir", "d/adr", "check"])
        self.assertEqual((args.root, args.adr_dir), ("/r", ["d/adr"]))

    def test_defaults_are_unchanged(self):
        args = cli_mod.parse_args(["check"])
        self.assertEqual((args.root, args.adr_dir), (os.getcwd(), None))

    def test_adr_dir_on_both_sides_is_joined(self):
        args = cli_mod.parse_args(["--adr-dir", "a", "--adr-dir", "b",
                                   "check", "--adr-dir", "c"])
        self.assertEqual(args.adr_dir, ["a", "b", "c"])

    def test_both_spellings_give_the_same_result(self):
        root = adr_in_unusual_place()
        before = run_main("--root", root, "--adr-dir", "doc/decisions",
                          "check", "--format", "json")
        after = run_main("check", "--format", "json", "--root", root,
                         "--adr-dir", "doc/decisions")
        self.assertEqual(before, after)
        self.assertEqual(before[0], 1, before)

    def test_help_of_a_subcommand_lists_them(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            run_cli("check", "--help")
        self.assertIn("--adr-dir", out.getvalue())
        self.assertIn("--root", out.getvalue())


class TestVersion(unittest.TestCase):
    """The version lives in three places; they must not drift apart."""

    def test_pyproject_package_and_cli_agree(self):
        import re
        import adr_drift
        with open(os.path.join(REPO_ROOT, "pyproject.toml"),
                  encoding="utf-8") as f:
            m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', f.read())
        self.assertIsNotNone(m, "pyproject.toml has no version")
        self.assertEqual(adr_drift.__version__, m.group(1))
        self.assertEqual(cli_mod.VERSION, m.group(1))

    def test_version_flag_prints_it(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            run_cli("--version")
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(out.getvalue().strip(),
                         "adr-drift " + cli_mod.VERSION)


class TestSym(unittest.TestCase):
    """sym() keeps the glyph when stdout can encode it, else ASCII."""

    @staticmethod
    def _stdout(encoding):
        return redirect_stdout(io.TextIOWrapper(io.BytesIO(),
                                                encoding=encoding))

    def test_cp1252_falls_back_to_ascii_markers(self):
        with self._stdout("cp1252"):
            self.assertEqual(cli_mod.sym("✓"), "[ok]")
            self.assertEqual(cli_mod.sym("✗"), "[x]")
            self.assertEqual(cli_mod.sym("—"), "—")  # cp1252 has em dash

    def test_ascii_console_replaces_em_dash(self):
        with self._stdout("ascii"):
            self.assertEqual(cli_mod.sym("—"), "--")
            self.assertEqual(cli_mod.sym("é"), "?")  # unmapped: no crash

    def test_utf8_and_stringio_keep_glyphs(self):
        with self._stdout("utf-8"):
            self.assertEqual(cli_mod.sym("✓"), "✓")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli_mod.sym("✗"), "✗")


class TestWindowsConsole(unittest.TestCase):
    """Regressions seen on Windows 11 / Python 3.11 with a cp1252 console.

    Printing ✓/✗ to cp1252 stdout raised UnicodeEncodeError, and `init`
    wrote its sample ADR (which contains an em dash) in the locale encoding
    so `index`/`check` then failed to read it back as UTF-8.
    """

    def _repo_with_non_ascii_violation(self) -> str:
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "src", "handlers"))
        os.makedirs(os.path.join(tmp, "src", "db"))
        os.makedirs(os.path.join(tmp, "docs", "adr"))
        with open(os.path.join(tmp, "arch.yaml"), "w", encoding="utf-8") as f:
            f.write('layers:\n'
                    '  - name: handlers\n    paths: ["src/handlers/**"]\n'
                    '  - name: db\n    paths: ["src/db/**"]\n')
        with open(os.path.join(tmp, "docs", "adr", "0001-schichten.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 1: Schichten — Ärger mit Ümlauten\n\n"
                    "## Status\n\nAkzeptiert\n\n"
                    "## Constraints\n\n- forbid-import: handlers -> db\n")
        # é is in cp1252, α is not: both must print without a traceback.
        with open(os.path.join(tmp, "src", "handlers", "café_α.py"),
                  "w", encoding="utf-8") as f:
            f.write("from db import drivers\n")
        return tmp

    def test_cp1252_stdout_non_ascii_path_and_adr(self):
        tmp = self._repo_with_non_ascii_violation()
        p = run_cli_process(["--root", tmp, "check", "--fail-on", "none"],
                            {"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(b"Traceback", p.stderr)
        out = p.stdout.decode("cp1252", errors="replace")
        self.assertIn("[x] 1 architecture violation(s)", out)
        self.assertIn("src/handlers/café_?.py", out)
        self.assertIn("docs/adr/0001-schichten.md", out)
        # and the clean message on the same console
        p = run_cli_process(["--root", tmp, "check", "--format", "json"],
                            {"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertEqual(len(json.loads(p.stdout.decode("ascii"))), 1)

    @staticmethod
    def _run_on_stream(encoding, *argv) -> tuple[int, bytes]:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding=encoding, errors="replace")
        with redirect_stdout(stream):
            code = run_cli(*argv)
            stream.flush()
        return code, raw.getvalue()

    def test_cp1252_stdout_in_process(self):
        # Same crash reproduced without a subprocess: a cp1252 text stream
        # (strict, as a real console is) must not blow up on the glyphs.
        tmp = self._repo_with_non_ascii_violation()
        raw = io.BytesIO()
        with redirect_stdout(io.TextIOWrapper(raw, encoding="cp1252")) as s:
            code = run_cli("--root", tmp, "check", "--fail-on", "none")
            s.flush()
        self.assertEqual(code, 0)
        self.assertIn(b"[x] 1 architecture violation(s)", raw.getvalue())
        clean = tempfile.mkdtemp()
        shutil.copy(os.path.join(FIXTURE, "arch.yaml"), clean)
        shutil.copytree(os.path.join(FIXTURE, "docs"),
                        os.path.join(clean, "docs"))
        code, out = self._run_on_stream("cp1252", "--root", clean, "check")
        self.assertEqual(code, 0)
        self.assertIn(
            b"[ok] no architecture drift \x97 all ADRs hold (2 read)", out)
        # pure-ASCII console: the em dash falls back too
        code, out = self._run_on_stream("ascii", "--root", clean, "check")
        self.assertIn(
            b"[ok] no architecture drift -- all ADRs hold (2 read)", out)
        # no ADR at all: never "all ADRs hold"
        bare = tempfile.mkdtemp()
        shutil.copy(os.path.join(FIXTURE, "arch.yaml"), bare)
        code, out = self._run_on_stream("cp1252", "--root", bare, "check")
        self.assertEqual(code, 0)
        self.assertIn(b"[ok] no architecture drift \x97 0 ADRs read", out)
        self.assertNotIn(b"all ADRs hold", out)
        code, out = self._run_on_stream("ascii", "--root", tempfile.mkdtemp(),
                                        "check", "--fail-on", "none")
        self.assertEqual(code, 0)
        self.assertIn(b"no rules found -- run", out)

    def test_init_index_check_under_non_utf8_locale(self):
        # CPython's open() asks the C locale for its default encoding, so
        # locale.getpreferredencoding can't be monkeypatched for this: run
        # a child with a non-UTF-8 locale instead (ASCII on POSIX; on
        # Windows LC_ALL is ignored and the real ANSI code page applies).
        env = {"LC_ALL": "C", "LANG": "C", "LC_CTYPE": "C",
               "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
               "PYTHONIOENCODING": "cp1252"}
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "src", "api"))
        p = run_cli_process(["--root", tmp, "init"], env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(b"Traceback", p.stderr)
        sample = os.path.join(tmp, "docs", "adr",
                              "0001-record-architecture-decisions.md")
        with open(sample, "rb") as f:
            self.assertIn("—".encode("utf-8"), f.read())  # UTF-8 on disk
        # A UTF-8 ADR with non-ASCII text (ASCII file name) is read back
        # by index and check under the same locale.
        with open(os.path.join(tmp, "docs", "adr", "0002-umlauts.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 2: Ärger — Ümlaute\n\n## Status\n\nAkzeptiert\n\n"
                    "## Constraints\n\n- forbid-pattern: src/**.py !~ eval\n")
        p = run_cli_process(["--root", tmp, "index"], env)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(tmp, "docs", "adr", "README.md"),
                  encoding="utf-8") as f:
            self.assertIn("Ärger — Ümlaute", f.read())
        p = run_cli_process(["--root", tmp, "check"], env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(b"Traceback", p.stderr)
        self.assertIn(b"[ok] no architecture drift", p.stdout)


if __name__ == "__main__":
    unittest.main()
