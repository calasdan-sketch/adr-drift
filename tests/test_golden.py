"""Golden files: the exact bytes of every report format on one fixed
repository (a direct import, a chain of imports, a scoped ADR, a waiver).
A change in any format must show up here as a diff a human reads.

Regenerate on purpose with:  ADR_DRIFT_UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py
"""

import os
import unittest
from unittest import mock

from _support import make_repo, run_main

import adr_drift

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD = os.path.join(HERE, "golden")
UPDATE = os.environ.get("ADR_DRIFT_UPDATE_GOLDEN") == "1"

FILES = {
    "arch.yaml": (
        'layers:\n'
        '  - name: handlers\n    paths: ["src/handlers/**"]\n'
        '  - name: services\n    paths: ["src/services/**"]\n'
        '  - name: db\n    paths: ["src/db/**"]\n'
        'rules:\n'
        '  - "forbid-transitive: handlers -> db"\n'
        'waivers:\n'
        '  - rule: "handlers must not reach db through imports"\n'
        '    paths: ["src/handlers/legacy.py"]\n'
        '    owner: alice\n'
        '    expires: 2030-06-01\n'
        '    reason: "ticket 7"\n'),
    "docs/adr/0001-layers.md": (
        "# 1. Handlers never import the db\n\nStatus: Accepted\n"
        "Deciders: Alice\nScope: src/handlers/b*.py\n\n"
        "## Constraints\n\n- forbid-import: handlers -> db\n"),
    "src/handlers/a.py": "from src.services import svc\nfrom src.db import conn\n",
    "src/handlers/b.py": "import src.db.conn\n",
    "src/handlers/legacy.py": "from src.services import svc\n",
    "src/services/__init__.py": "",
    "src/services/svc.py": "from src.db import conn\n",
    "src/db/__init__.py": "",
    "src/db/conn.py": "X = 1\n",
}
FORMATS = ("human", "json", "github", "sarif", "markdown")


def render(fmt: str) -> str:
    root = make_repo(FILES)
    with mock.patch.dict(os.environ, {"ADR_DRIFT_TODAY": "2030-01-01",
                                      "NO_COLOR": "1"}):
        code, out, _err = run_main("check", "--root", root, "--jobs", "1",
                                   "--format", fmt)
    assert code == 1, (code, out)
    return (out.replace(root, "<root>")
            .replace(adr_drift.__version__, "<version>"))


class Golden(unittest.TestCase):
    def test_each_format_is_byte_for_byte_the_golden_file(self):
        os.makedirs(GOLD, exist_ok=True)
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                got = render(fmt)
                path = os.path.join(GOLD, f"check.{fmt}.txt")
                if UPDATE:
                    with open(path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(got)
                with open(path, encoding="utf-8", newline="") as f:
                    self.assertEqual(f.read(), got)

    def test_the_output_does_not_depend_on_the_job_count(self):
        root = make_repo(FILES)
        outs = {run_main("check", "--root", root, "--jobs", str(j),
                         "--format", "json")[1] for j in (1, 2, 3)}
        self.assertEqual(len(outs), 1)


if __name__ == "__main__":
    unittest.main()
