"""Waivers that expire, ADR coverage, the baseline ratchet."""

import json
import os
import unittest
from unittest import mock

from _support import make_repo, run_main

from adr_drift import config

ARCH = ('layers:\n  - name: handlers\n    paths: ["src/handlers/**"]\n'
        '  - name: db\n    paths: ["src/db/**"]\n'
        'rules:\n  - "forbid-import: handlers -> db"\n')
WAIVER = ('waivers:\n  - rule: "handlers must not import db"\n'
          '    paths: ["src/handlers/legacy/**"]\n    owner: alice\n'
          '    expires: 2027-03-01\n    reason: ticket 12\n')
FILES = {"src/handlers/legacy/old.py": "import src.db.d\n",
         "src/handlers/new.py": "import src.db.d\n",
         "src/db/d.py": "x = 1\n",
         "docs/adr/0001-x.md": "# 1: X\n\n## Status\n\nAccepted\n"}


def repo(arch=ARCH + WAIVER, files=FILES):
    return make_repo(dict(files, **{"arch.yaml": arch}))


def check(root, today="2027-01-01", *extra):
    with mock.patch.dict(os.environ, {"ADR_DRIFT_TODAY": today}):
        code, out, err = run_main("--root", root, "check", "--format", "json",
                                  *extra)
    return code, json.loads(out or "[]"), err


class WaiverLoadTest(unittest.TestCase):
    def load_error(self, waiver_text):
        root = repo(ARCH + waiver_text)
        code, _out, err = run_main("--root", root, "check")
        return code, err

    def test_valid(self):
        loaded = config.load_all(repo())
        (w,) = loaded.waivers
        self.assertEqual((w.owner, w.expires.isoformat(), w.paths),
                         ("alice", "2027-03-01", ["src/handlers/legacy/**"]))

    def test_each_required_key(self):
        fields = {"rule": '"handlers must not import db"', "owner": "alice",
                  "expires": "2027-03-01", "reason": "ticket 12"}
        for key in fields:
            kept = [f"{k}: {v}" for k, v in fields.items() if k != key]
            text = "waivers:\n  - " + "\n    ".join(kept) + "\n"
            code, err = self.load_error(text)
            self.assertEqual(code, 2, key)
            self.assertIn(f"no '{key}'", err)

    def test_bad_date_unknown_key_unknown_rule(self):
        code, err = self.load_error(WAIVER.replace("2027-03-01", "soon"))
        self.assertEqual(code, 2)
        self.assertIn("date", err)
        code, err = self.load_error(WAIVER.replace("owner:", "ownr:"))
        self.assertIn("did you mean 'owner'", err)
        code, err = self.load_error(
            WAIVER.replace("handlers must not import db",
                           "handlers must not import dbx"))
        self.assertEqual(code, 2)
        self.assertIn("no enforced rule says", err)

    def test_not_a_list(self):
        code, err = self.load_error("waivers: nope\n")
        self.assertEqual(code, 2)


class ApplyTest(unittest.TestCase):
    def test_active_waiver_hides_only_its_paths(self):
        code, got, err = check(repo())
        self.assertEqual([v["file"] for v in got], ["src/handlers/new.py"])
        self.assertIn("1 violation waived", err)
        self.assertIn("alice", err)

    def test_expired_waiver_brings_violations_back_and_names_the_owner(self):
        code, got, err = check(repo(), today="2027-03-02")
        self.assertEqual(len(got), 2)
        self.assertIn("expired on 2027-03-01", err)
        self.assertIn("alice", err)

    def test_last_day_still_applies(self):
        _code, got, _err = check(repo(), today="2027-03-01")
        self.assertEqual(len(got), 1)

    def test_ending_soon_warns(self):
        _code, _got, err = check(repo(), today="2027-02-20")
        self.assertIn("ends in 9 days", err)

    def test_stale_waiver_warns_and_strict_fails(self):
        files = dict(FILES, **{"src/handlers/legacy/old.py": "x = 1\n",
                               "src/handlers/new.py": "x = 1\n"})
        code, _got, err = check(repo(files=files), "2027-01-01", "--strict")
        self.assertIn("matches no violation", err)
        self.assertEqual(code, 2)

    def test_waiver_that_never_ends_is_called_out(self):
        _code, got, err = check(repo(ARCH + WAIVER.replace("2027-03-01", "2999-01-01")))
        self.assertIn("a temporary exception", err)
        self.assertEqual(len(got), 1)           # it still applies

    def test_bad_today_is_an_error(self):
        code, got, err = check(repo(), today="yesterday")
        self.assertEqual(code, 2)
        self.assertIn("ADR_DRIFT_TODAY", err)

    def test_baseline_does_not_swallow_waived_violations(self):
        root = repo()
        with mock.patch.dict(os.environ, {"ADR_DRIFT_TODAY": "2027-01-01"}):
            run_main("--root", root, "baseline")
        with open(os.path.join(root, ".adr-baseline.json")) as f:
            files = [e["file"] for e in json.load(f)]
        self.assertEqual(files, ["src/handlers/new.py"])
        # after the waiver ends the legacy violation is NEW, not grandfathered
        code, got, _ = check(root, "2027-04-01", "--baseline",
                             ".adr-baseline.json")
        self.assertEqual([v["file"] for v in got], ["src/handlers/legacy/old.py"])


class CoverageTest(unittest.TestCase):
    ADRS = {
        "docs/adr/0001-a.md": "---\ndeciders: Ann\n---\n# 1: A\n\n## Status\n\nAccepted\n\n"
                              "## Constraints\n\n- forbid-import: handlers -> db\n",
        "docs/adr/0002-b.md": "# 2: B\n\n## Status\n\nAccepted\n\n## Decision\n\n"
                              "We must never log secrets. See `config/nope/`.\n",
        "docs/adr/0003-c.md": "# 3: C\n\n## Status\n\nSuperseded by 0001\n",
    }

    def run_cov(self, *extra, today="2027-01-01"):
        root = make_repo(dict(self.ADRS, **{"arch.yaml": ARCH + WAIVER,
                                            "src/handlers/legacy/old.py": "x=1\n"}))
        with mock.patch.dict(os.environ, {"ADR_DRIFT_TODAY": today}):
            return run_main("--root", root, "coverage", *extra)

    def test_json(self):
        code, out, _ = self.run_cov("--format", "json")
        cov = json.loads(out)
        states = {a["number"]: a["state"] for a in cov["adrs"]}
        self.assertEqual(states, {"0001": "enforced", "0002": "no-rule",
                                  "0003": "retired"})
        self.assertEqual((cov["in_force"], cov["enforced"], cov["percent"]),
                         (2, 1, 50))
        b = cov["adrs"][1]
        self.assertEqual(b["missing_paths"], ["config/nope"])
        self.assertIn("hint", b)
        self.assertEqual(cov["adrs"][0]["owners"], ["Ann"])
        self.assertEqual(cov["waivers"][0]["state"], "active")
        self.assertEqual(code, 0)

    def test_fail_under(self):
        code, _out, err = self.run_cov("--fail-under", "80")
        self.assertEqual(code, 1)
        self.assertIn("under --fail-under", err)
        self.assertEqual(self.run_cov("--fail-under", "50")[0], 0)

    def test_expired_waiver_fails(self):
        code, out, err = self.run_cov(today="2028-01-01")
        self.assertEqual(code, 1)
        self.assertIn("expired", out)

    def test_human_output_names_the_gap(self):
        _code, out, _ = self.run_cov()
        self.assertIn("1 of 2 ADRs in force", out)
        self.assertIn("nothing checks this decision", out)


class RatchetTest(unittest.TestCase):
    def test_fixed_violation_still_in_baseline_fails(self):
        root = repo(ARCH, FILES)
        run_main("--root", root, "baseline")
        base = os.path.join(root, ".adr-baseline.json")
        self.assertEqual(run_main("--root", root, "check", "--baseline", base,
                                  "--ratchet")[0], 0)
        with open(os.path.join(root, "src/handlers/new.py"), "w") as f:
            f.write("x = 1\n")                       # fixed
        code, _out, err = run_main("--root", root, "check", "--baseline", base)
        self.assertEqual(code, 0)                    # plain baseline: fine
        code, _out, err = run_main("--root", root, "check", "--baseline", base,
                                   "--ratchet")
        self.assertEqual(code, 1)
        self.assertIn("--ratchet", err)
        self.assertIn("adr-drift baseline", err)
        # rewriting the baseline locks the gain in
        run_main("--root", root, "baseline")
        self.assertEqual(run_main("--root", root, "check", "--baseline", base,
                                  "--ratchet")[0], 0)

    def test_new_violation_still_fails_and_ratchet_needs_baseline(self):
        root = repo(ARCH, FILES)
        self.assertEqual(run_main("--root", root, "check", "--ratchet")[0], 2)

    def test_hand_made_entry_is_stale_when_it_matches_nothing(self):
        root = repo(ARCH, FILES)
        base = os.path.join(root, "b.json")
        with open(base, "w") as f:
            json.dump([{"file": "src/gone.py", "line": 1,
                        "rule": "handlers must not import db"}], f)
        code, _o, err = run_main("--root", root, "check", "--baseline", base,
                                 "--ratchet", "--fail-on", "none")
        self.assertEqual(code, 0)                   # report-only never fails
        code, _o, err = run_main("--root", root, "check", "--baseline", base,
                                 "--ratchet")
        self.assertEqual(code, 1)


class JobsTest(unittest.TestCase):
    def test_same_answer_for_any_job_count(self):
        files = {f"src/handlers/h{i}.py": "import src.db.d\nimport json\n"
                 for i in range(40)}
        files["src/db/d.py"] = "x = 1\n"
        files["src/handlers/bad.py"] = "def (:\n"
        root = repo(ARCH, dict(files, **{"docs/adr/0001-x.md": "# 1: X\n"}))
        from adr_drift import engine
        with mock.patch.object(engine, "PARALLEL_MIN_FILES", 1):
            a = run_main("--root", root, "check", "--format", "json",
                         "--jobs", "1")
            b = run_main("--root", root, "check", "--format", "json",
                         "--jobs", "3")
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[1], b[1])
        self.assertEqual(sorted(a[2].splitlines()), sorted(b[2].splitlines()))
        self.assertEqual(len(json.loads(a[1])), 40)

    def test_bad_jobs_value(self):
        root = repo(ARCH, FILES)
        with self.assertRaises(SystemExit):
            run_main("--root", root, "check", "--jobs", "-1")


if __name__ == "__main__":
    unittest.main()
