"""Shared helpers for the test-suite (not a test module).

Everything here is hermetic: temp directories only, and git never sees the
real HOME or the real git configuration.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from adr_drift import cli as cli_mod


def write_files(root: str, files: dict) -> str:
    """Create `files` ({relative/posix/path: text-or-bytes}) under `root`."""
    for rel, body in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        with open(path, "wb") as f:
            f.write(data)
    return root


@contextlib.contextmanager
def chdir(path: str):
    """Run the block with `path` as the working directory."""
    before = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(before)


def make_repo(files: dict) -> str:
    """A fresh temp directory holding `files`."""
    return write_files(tempfile.mkdtemp(), files)


def run_main(*argv) -> "tuple[int, str, str]":
    """Run the CLI in-process -> (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_mod.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def git_env() -> dict:
    """Environment that makes git ignore the machine's own configuration."""
    home = tempfile.mkdtemp()
    return {
        "HOME": home,
        "USERPROFILE": home,
        "XDG_CONFIG_HOME": os.path.join(home, ".config"),
        "GIT_CONFIG_GLOBAL": os.path.join(home, "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }


@contextlib.contextmanager
def hermetic_git():
    """Patch os.environ so every git started in the block is isolated
    (both the test's own git calls and adr-drift's)."""
    with mock.patch.dict(os.environ, git_env()):
        yield


class GitCase(unittest.TestCase):
    """A test case whose every git (the test's own and adr-drift's) runs
    against a throw-away HOME, ignoring the machine's git configuration."""

    def setUp(self):
        super().setUp()
        patch = hermetic_git()
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)


def git(repo: str, *args: str) -> str:
    p = subprocess.run(["git", "-C", repo, *args], check=True,
                       capture_output=True)
    return p.stdout.decode("utf-8", "replace")


def init_git(repo: str) -> str:
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t.t")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "commit.gpgsign", "false")
    return repo


def commit_all(repo: str, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
