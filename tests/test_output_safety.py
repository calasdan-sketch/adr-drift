"""--format github / sarif, terminal-safe output, retired ADRs."""

import json
import unittest

from _support import make_repo, run_main

from adr_drift import config, report

ARCH = ('layers:\n'
        '  - name: handlers\n    paths: ["src/handlers/**"]\n'
        '  - name: db\n    paths: ["src/db/**"]\n'
        'rules:\n'
        '  - "forbid-import: handlers -> db"\n')
ADR = "# 1: X\n\n## Status\n\nAccepted\n"
BAD = {"arch.yaml": ARCH, "docs/adr/0001-x.md": ADR,
       "src/handlers/h.py": "import os\nfrom db import conn\n",
       "src/db/conn.py": "X = 1\n"}


class GithubFormatTest(unittest.TestCase):
    def test_annotation_on_the_line(self):
        code, out, _err = run_main("--root", make_repo(BAD), "check",
                                   "--format", "github")
        self.assertEqual(code, 1)
        first = out.splitlines()[0]
        self.assertTrue(first.startswith(
            "::error file=src/handlers/h.py,line=2,title=adr-drift%3A "
            "forbid-import::imports db"), first)
        self.assertIn("1 architecture violation", out)  # human report follows

    def test_properties_and_data_are_escaped(self):
        line = report.gh_command("error", "a\nb%c\r", file="x,y:z.py",
                                 line=3, title="t:1")
        self.assertEqual(line, "::error file=x%2Cy%3Az.py,line=3,title=t%3A1"
                               "::a%0Ab%25c%0D")

    def test_warnings_become_annotations(self):
        files = dict(BAD)
        files["src/handlers/h.py"] = "import os\n"
        files["arch.yaml"] = ARCH + '  - "forbid-pattern: nope/** !~ x"\n'
        code, out, _err = run_main("--root", make_repo(files), "check",
                                   "--format", "github")
        self.assertEqual(code, 0)
        self.assertIn("::warning title=adr-drift::", out)


class SarifTest(unittest.TestCase):
    def sarif(self, files):
        code, out, err = run_main("--root", make_repo(files), "check",
                                  "--format", "sarif")
        return code, json.loads(out), err

    def test_shape(self):
        code, log, _err = self.sarif(BAD)
        self.assertEqual(code, 1)
        self.assertEqual(log["version"], "2.1.0")
        run = log["runs"][0]
        rule = run["tool"]["driver"]["rules"][0]
        result = run["results"][0]
        self.assertEqual(result["ruleId"], rule["id"])
        self.assertEqual(result["ruleIndex"], 0)
        self.assertEqual(result["level"], "error")
        loc = result["locations"][0]["physicalLocation"]
        self.assertEqual(loc["artifactLocation"]["uri"], "src/handlers/h.py")
        self.assertEqual(loc["region"]["startLine"], 2)
        self.assertIn("adrDriftIdentity/v1", result["partialFingerprints"])

    def test_fingerprint_ignores_the_line(self):
        _c, before, _e = self.sarif(BAD)
        moved = dict(BAD)
        moved["src/handlers/h.py"] = '"""doc"""\n' + BAD["src/handlers/h.py"]
        _c, after, _e = self.sarif(moved)
        fp = [r["runs"][0]["results"][0]["partialFingerprints"]
              for r in (before, after)]
        self.assertEqual(fp[0], fp[1])

    def test_clean_is_an_empty_log(self):
        files = dict(BAD)
        files["src/handlers/h.py"] = "import os\n"
        code, log, _err = self.sarif(files)
        self.assertEqual(code, 0)
        self.assertEqual(log["runs"][0]["results"], [])

    def test_whole_file_violation_has_no_region(self):
        files = dict(BAD)
        files["arch.yaml"] = ARCH + '  - "require-pattern: src/db/** =~ AUTH"\n'
        _code, log, _err = self.sarif(files)
        whole = [r for r in log["runs"][0]["results"]
                 if r["ruleId"].startswith("require-pattern/")]
        self.assertEqual(len(whole), 1)
        self.assertNotIn("region", whole[0]["locations"][0]["physicalLocation"])

    def test_uri_is_percent_encoded(self):
        self.assertEqual(report._uri("src/my file%.py"), "src/my%20file%25.py")

    def test_no_rules_is_still_valid_sarif(self):
        files = {"docs/adr/0001-x.md": ADR, "src/a.py": "X = 1\n"}
        code, out, _err = run_main("--root", make_repo(files), "check",
                                   "--format", "sarif", "--allow-empty")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["runs"][0]["results"], [])


class TerminalEscapeTest(unittest.TestCase):
    def test_escapes(self):
        cases = {"\x1b[2J": "\\x1b[2J", "a‮b": "a\\u202eb",
                 "z​w": "z\\u200bw", "\r": "\\x0d", "\x7f": "\\x7f",
                 "\udcff": "\\udcff", " ": "\\u2028",
                 "ok\n\tok é ✓": "ok\n\tok é ✓"}
        for raw, want in cases.items():
            self.assertEqual(report.escape_terminal(raw), want, repr(raw))

    def test_hostile_import_name_cannot_reach_the_terminal(self):
        esc = "\x1b]0;pwned\x07"
        files = dict(BAD)
        files["src/handlers/h.js"] = f'import x from "db/{esc}";\n'
        code, out, err = run_main("--root", make_repo(files), "check")
        self.assertEqual(code, 1)
        self.assertNotIn("\x1b", out + err)
        self.assertNotIn("\x07", out + err)
        self.assertIn("\\x1b]0;pwned\\x07", out)

    def test_bidi_in_a_file_name_is_visible(self):
        files = dict(BAD)
        files["src/handlers/evil‮txt.py"] = "from db import conn\n"
        code, out, _err = run_main("--root", make_repo(files), "check")
        self.assertEqual(code, 1)
        self.assertNotIn("‮", out)
        self.assertIn("evil\\u202etxt.py", out)

    def test_json_stays_parseable_and_ascii(self):
        files = dict(BAD)
        files["src/handlers/evil‮txt.py"] = "from db import conn\n"
        _code, out, _err = run_main("--root", make_repo(files), "check",
                                    "--format", "json")
        files_seen = {v["file"] for v in json.loads(out)}
        self.assertIn("src/handlers/evil‮txt.py", files_seen)


class InactiveAdrTest(unittest.TestCase):
    def adr(self, status):
        return (f"# 2: Old\n\n## Status\n\n{status}\n\n## Constraints\n\n"
                "- forbid-pattern: src/** !~ import os\n")

    def test_statuses(self):
        for status, inactive in [("Superseded by [3](0003-x.md)", True),
                                 ("**Rejected**", True), ("deprecated.", True),
                                 ("Accepted", False), ("Proposed", False),
                                 ("{proposed | rejected | accepted}", False),
                                 ("", False)]:
            self.assertEqual(config.is_inactive_status(status), inactive,
                             status)

    def test_superseded_rules_are_not_enforced_and_say_so(self):
        files = dict(BAD)
        files["src/handlers/h.py"] = "import os\n"
        files["docs/adr/0002-old.md"] = self.adr("Superseded by 0003")
        code, out, err = run_main("--root", make_repo(files), "check")
        self.assertEqual(code, 0, out)
        self.assertIn("note: docs/adr/0002-old.md is 'Superseded by 0003': "
                      "its 1 rule is not enforced", err)

    def test_accepted_rules_are_enforced(self):
        files = dict(BAD)
        files["src/handlers/h.py"] = "import os\n"
        files["docs/adr/0002-old.md"] = self.adr("Accepted")
        code, _out, _err = run_main("--root", make_repo(files), "check")
        self.assertEqual(code, 1)

    def test_only_retired_rules_fails_closed(self):
        files = {"docs/adr/0002-old.md": self.adr("Rejected"),
                 "src/a.py": "import os\n"}
        code, _out, err = run_main("--root", make_repo(files), "check")
        self.assertEqual(code, 2)
        self.assertIn("not enforced", err)

    def test_broken_rule_in_retired_adr_is_still_an_error(self):
        files = dict(BAD)
        files["docs/adr/0002-old.md"] = (
            "# 2: Old\n\n## Status\n\nRejected\n\n## Constraints\n\n"
            "- forbid_import: a -> b\n")
        code, _out, err = run_main("--root", make_repo(files), "check")
        self.assertEqual(code, 2)
        self.assertIn("forbid_import", err)


if __name__ == "__main__":
    unittest.main()
