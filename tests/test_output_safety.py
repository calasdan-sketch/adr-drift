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
        files["docs/adr/0003-new.md"] = "# 3: New\n\n## Status\n\nAccepted\n"
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


# -- regressions for the synapse-1 panel review --------------------------------

import os  # noqa: E402
import shutil  # noqa: E402

from _support import GitCase, init_git, write_files  # noqa: E402


@unittest.skipUnless(shutil.which("git"), "needs git")
class MonorepoPathsTest(GitCase):
    """--root inside a repository: GitHub and SARIF name files from the top
    of the repository, where the pull request's lines are."""

    def repo(self):
        top = init_git(make_repo({}))
        write_files(top, {f"packages/api/{k}": v for k, v in BAD.items()})
        return top, os.path.join(top, "packages", "api")

    def test_github_paths_start_at_the_repo_top(self):
        _top, root = self.repo()
        code, out, _err = run_main("--root", root, "check", "--format",
                                   "github")
        self.assertEqual(code, 1)
        self.assertIn("::error file=packages/api/src/handlers/h.py,line=2,",
                      out)

    def test_sarif_paths_start_at_the_repo_top(self):
        _top, root = self.repo()
        _code, out, _err = run_main("--root", root, "check", "--format",
                                    "sarif")
        uri = (json.loads(out)["runs"][0]["results"][0]["locations"][0]
               ["physicalLocation"]["artifactLocation"]["uri"])
        self.assertEqual(uri, "packages/api/src/handlers/h.py")

    def test_json_and_human_keep_root_relative_paths(self):
        _top, root = self.repo()
        _code, out, _err = run_main("--root", root, "check", "--format",
                                    "json")
        self.assertEqual(json.loads(out)[0]["file"], "src/handlers/h.py")

    def test_outside_git_there_is_no_prefix(self):
        _code, out, _err = run_main("--root", make_repo(BAD), "check",
                                    "--format", "github")
        self.assertIn("::error file=src/handlers/h.py,", out)


class RetirementVisibilityTest(unittest.TestCase):
    OLD = ("# 2: Old\n\n## Status\n\n{status}\n\n## Constraints\n\n"
           "- forbid-pattern: src/** !~ import os\n")
    NEW = "# 3: New\n\n## Status\n\nAccepted\n"

    def files(self, status, new=NEW):
        files = dict(BAD)
        files["src/handlers/h.py"] = "import os\n"
        files["docs/adr/0002-old.md"] = self.OLD.format(status=status)
        if new is not None:
            files["docs/adr/0003-new.md"] = new
        return files

    def test_github_warns_on_the_retired_adr(self):
        code, out, _err = run_main("--root", make_repo(self.files(
            "Superseded by [3](0003-new.md)")), "check", "--format", "github")
        self.assertEqual(code, 0)
        self.assertIn("::warning file=docs/adr/0002-old.md,title=adr-drift%3A "
                      "ADR retired::", out)

    def test_sarif_carries_a_notification(self):
        _code, out, _err = run_main("--root", make_repo(self.files(
            "Deprecated")), "check", "--format", "sarif")
        notes = (json.loads(out)["runs"][0]["invocations"][0]
                 ["toolExecutionNotifications"])
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["level"], "warning")
        self.assertIn("not enforced", notes[0]["message"]["text"])

    def test_superseded_without_a_successor_fails(self):
        for status in ("Superseded", "Superseded by 0009",
                       "Superseded by [9](0009-gone.md)"):
            with self.subTest(status=status):
                code, _out, err = run_main("--root", make_repo(self.files(
                    status)), "check")
                self.assertEqual(code, 2)
                self.assertIn("must name the ADR that replaces it", err)

    def test_successor_must_be_in_force(self):
        code, _out, err = run_main("--root", make_repo(self.files(
            "Superseded by 0003", new="# 3: New\n\n## Status\n\nRejected\n")),
            "check")
        self.assertEqual(code, 2)
        self.assertIn("not in force", err)

    def test_supersede_chain_and_loop(self):
        files = self.files("Superseded by 0003",
                           new="# 3: New\n\n## Status\n\nSuperseded by 4\n")
        files["docs/adr/0004-newer.md"] = "# 4\n\n## Status\n\nAccepted\n"
        self.assertEqual(run_main("--root", make_repo(files), "check")[0], 0)
        files["docs/adr/0004-newer.md"] = ("# 4\n\n## Status\n\n"
                                           "Superseded by 0002\n")
        code, _out, err = run_main("--root", make_repo(files), "check")
        self.assertEqual(code, 2)
        self.assertIn("loop", err)

    def test_successor_by_number_with_prefix(self):
        code, _out, _err = run_main("--root", make_repo(self.files(
            "Superseded by ADR-0003")), "check")
        self.assertEqual(code, 0)


class StatusSpoofTest(unittest.TestCase):
    RULE = "\n## Constraints\n\n- forbid-pattern: src/** !~ import os\n"

    def check(self, adr):
        files = dict(BAD)
        files["src/handlers/h.py"] = "import os\n"
        files["docs/adr/0002-x.md"] = adr
        return run_main("--root", make_repo(files), "check")

    def test_status_in_a_fenced_block_is_ignored(self):
        adr = ("---\nstatus: accepted\n---\n\n# 2: X\n\nAn example:\n\n"
               "```markdown\n## Status\n\nSuperseded by 0001\n```\n" + self.RULE)
        code, out, err = self.check(adr)
        self.assertEqual(code, 1, err)          # still enforced
        self.assertNotIn("not enforced", err)
        self.assertIn("must not contain", out)

    def test_tilde_fence_and_longer_closer(self):
        adr = ("# 2: X\n\n~~~~\n## Status\n\nRejected\n~~~~~\n\n"
               "## Status\n\nAccepted\n" + self.RULE)
        self.assertEqual(self.check(adr)[0], 1)

    def test_front_matter_disagreeing_is_flagged_and_enforced(self):
        adr = ("---\nstatus: accepted\n---\n\n# 2: X\n\n## Status\n\n"
               "Rejected\n" + self.RULE)
        code, _out, err = self.check(adr)
        self.assertEqual(code, 1)               # the rule stays in force
        self.assertIn("the front matter says status 'accepted' but the "
                      "document says 'Rejected'", err)

    def test_agreeing_sources_are_quiet(self):
        adr = ("---\nstatus: rejected\n---\n\n# 2: X\n\n## Status\n\n"
               "Rejected\n" + self.RULE)
        code, _out, err = self.check(adr)
        self.assertEqual(code, 0, err)
        self.assertNotIn("front matter says", err)


@unittest.skipIf(os.name == "nt" or os.uname().sysname == "Darwin",
                 "needs a file system that stores non-UTF-8 names")
class NonUtf8NameTest(unittest.TestCase):
    def repo(self):
        root = make_repo(BAD)
        name = os.path.join(os.fsencode(root), b"src", b"handlers", b"\xff.py")
        with open(name, "wb") as f:
            f.write(b"from db import conn\n")
        return root

    def test_sarif_does_not_crash(self):
        code, out, err = run_main("--root", self.repo(), "check", "--format",
                                  "sarif")
        self.assertEqual(code, 1, err)
        uris = [r["locations"][0]["physicalLocation"]["artifactLocation"]
                ["uri"] for r in json.loads(out)["runs"][0]["results"]]
        self.assertIn("src/handlers/%FF.py", uris)
        out.encode("ascii")  # nothing but ASCII

    def test_github_names_the_file_in_the_message_instead(self):
        code, out, _err = run_main("--root", self.repo(), "check", "--format",
                                   "github")
        self.assertEqual(code, 1)
        bad = [l for l in out.splitlines() if "\\udcff" in l]
        self.assertTrue(bad)
        self.assertTrue(all("file=" not in l for l in bad), bad)

    def test_a_report_error_exits_2(self):
        from unittest import mock
        with mock.patch("adr_drift.report.sarif_text",
                        side_effect=UnicodeEncodeError("utf-8", "x", 0, 1,
                                                       "boom")):
            code, out, err = run_main("--root", self.repo(), "check",
                                      "--format", "sarif")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("cannot write the sarif report", err)

