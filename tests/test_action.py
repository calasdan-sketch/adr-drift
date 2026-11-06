"""action.yml: the `args` input reaches adr-drift word for word."""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

ACTION = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "action.yml")


SCRIPT = os.path.join(os.path.dirname(ACTION), "action.sh")


def run_script() -> str:
    """What the composite step runs: action.yml calls action.sh."""
    with open(ACTION, encoding="utf-8") as f:
        assert 'run: bash "$GITHUB_ACTION_PATH/action.sh"' in f.read()
    with open(SCRIPT, encoding="utf-8") as f:
        return f.read()


@unittest.skipUnless(shutil.which("bash") and os.name != "nt",
                     "needs bash and a POSIX shell environment")
class ActionArgsTest(unittest.TestCase):
    def run_action(self, args: str) -> "list[str]":
        work = tempfile.mkdtemp()
        bindir = os.path.join(work, "bin")
        os.makedirs(bindir)
        log = os.path.join(work, "argv.log")
        fake = os.path.join(bindir, "python")
        with open(fake, "w") as f:
            # records the arguments of `python -m adr_drift check ...`
            f.write('#!/bin/sh\n[ "$2" = adr_drift ] && '
                    f'printf "%s\\n" "$@" > "{log}"\nexit 0\n')
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        for name in ("a.py", "b.py"):  # what a `*` would expand to
            open(os.path.join(work, name), "w").close()
        env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"],
                   ADR_DRIFT_ARGS=args, GITHUB_ACTION_PATH=work)
        subprocess.run(["bash", "-c", run_script()], cwd=work, env=env,
                       check=True)
        with open(log) as f:
            return f.read().splitlines()

    def test_no_wildcard_expansion(self):
        self.assertEqual(self.run_action('--strict --adr-dir *'),
                         ["-m", "adr_drift", "check", "--strict",
                          "--adr-dir", "*"])

    def test_empty_args(self):
        self.assertEqual(self.run_action(""), ["-m", "adr_drift", "check"])

    def test_newlines_split_too(self):
        self.assertEqual(self.run_action("--format github\n--strict\n"),
                         ["-m", "adr_drift", "check", "--format", "github",
                          "--strict"])

    def test_shell_syntax_is_just_text(self):
        self.assertEqual(self.run_action("$(touch pwned) ;id"),
                         ["-m", "adr_drift", "check", "$(touch", "pwned)",
                          ";id"])


if __name__ == "__main__":
    unittest.main()
