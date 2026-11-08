"""Expert round 5: the author line, ADR scope (monorepos), forbid-transitive
(chains of imports), Windows path edges, and quality methods that were not
used before: a differential test against a plain reference implementation,
metamorphic (scope) tests and generated rule text."""

import contextlib
import io
import json
import os
import random
import re
import unittest
from unittest import mock

from _support import GitCase, commit_all, init_git, make_repo, run_main

import adr_drift
from adr_drift import cli as cli_mod
from adr_drift import config as config_mod
from adr_drift import paths as paths_mod
from adr_drift import rules as rules_mod

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AUTHOR = "Calas Automations Inc."

LAYERS = ('layers:\n'
          '  - name: handlers\n    paths: ["src/handlers/**"]\n'
          '  - name: services\n    paths: ["src/services/**"]\n'
          '  - name: db\n    paths: ["src/db/**"]\n')


def check(root, *args):
    return run_main("check", "--root", root, "--jobs", "1", *args)


def adr(title, rules, extra=""):
    return (f"# {title}\n\nStatus: Accepted\n{extra}\n## Constraints\n\n"
            + "".join(f"- {r}\n" for r in rules))


class Author(unittest.TestCase):
    def test_the_author_is_one_exact_string_everywhere(self):
        self.assertEqual(adr_drift.__author__, AUTHOR)
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
            self.assertIn(f'authors = [{{ name = "{AUTHOR}" }}]', f.read())
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as f:
            self.assertIn(f"Copyright (c) 2026 {AUTHOR}", f.read())

    def test_sarif_and_help_carry_it(self):
        root = make_repo({"arch.yaml": LAYERS + 'rules:\n  - "forbid-import: handlers -> db"\n',
                          "src/handlers/a.py": "import src.db.c\n",
                          "src/db/c.py": ""})
        _c, out, _e = check(root, "--format", "sarif")
        driver = json.loads(out)["runs"][0]["tool"]["driver"]
        self.assertEqual(driver["organization"], AUTHOR)
        buf = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(buf):
            cli_mod.main(["--help"])
        self.assertIn(AUTHOR, buf.getvalue())
        buf = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(buf):
            cli_mod.main(["--version"])
        self.assertEqual(buf.getvalue().strip(),
                         f"adr-drift {adr_drift.__version__}")

    def test_no_other_owner_label_survives(self):
        bad = re.compile(r"Dan Calas|Copyright[^\n]*calasdan", re.I)
        for dirpath, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", ".venv", "build",
                       "__pycache__", ".mypy_cache", ".ruff_cache",
                       "adr_drift.egg-info", ".pytest_cache")]
            for fn in files:
                if fn.endswith((".py", ".md", ".toml", ".yml", ".yaml")) \
                        and fn != "test_round5.py":
                    with open(os.path.join(dirpath, fn), encoding="utf-8",
                              errors="replace") as f:
                        self.assertIsNone(bad.search(f.read()), fn)


class Scope(unittest.TestCase):
    FILES = {
        "arch.yaml": LAYERS,
        "services/api/handlers/h.py": "from services.api.db import c\n",
        "services/api/db/c.py": "X=1\n",
        "services/web/handlers/h.py": "from services.web.db import c\n",
        "services/web/db/c.py": "X=1\n",
    }
    GLOBS = ('layers:\n  - name: handlers\n    paths: ["services/*/handlers/**"]\n'
             '  - name: db\n    paths: ["services/*/db/**"]\n')

    def repo(self, scope_line="Scope: services/api/**", rules=None):
        files = dict(self.FILES, **{
            "arch.yaml": self.GLOBS,
            "services/api/adr/0001-x.md": adr(
                "1. api", rules or ["forbid-import: handlers -> db"],
                scope_line + "\n")})
        return make_repo(files)

    def test_without_a_scope_the_rule_hits_both_services(self):
        root = self.repo(scope_line="")
        code, out, _ = check(root, "--adr-dir", "services/api/adr")
        self.assertEqual(code, 1)
        self.assertIn("services/api/handlers/h.py", out)
        self.assertIn("services/web/handlers/h.py", out)

    def test_scope_limits_the_files_a_rule_judges(self):
        code, out, err = check(self.repo(), "--adr-dir", "services/api/adr")
        self.assertEqual(code, 1, err)
        self.assertIn("services/api/handlers/h.py", out)
        self.assertNotIn("services/web", out)

    def test_several_scope_globs_are_alternatives(self):
        root = self.repo("Scope: services/api/**, services/web/**")
        code, out, _e = check(root, "--adr-dir", "services/api/adr")
        self.assertIn("services/api/handlers/h.py", out)
        self.assertIn("services/web/handlers/h.py", out)

    def test_scope_forms_agree(self):
        for line in ("Scope: services/api/**", "- **Scope**: `services/api/**`",
                     "scope: [services/api/**]"):
            with self.subTest(line=line):
                _c, out, _e = check(self.repo(line), "--adr-dir",
                                    "services/api/adr")
                self.assertNotIn("services/web", out)
        front = ("---\nscope:\n  - services/api/**\n---\n"
                 + adr("1. api", ["forbid-import: handlers -> db"]))
        files = dict(self.FILES, **{"arch.yaml": self.GLOBS,
                                    "services/api/adr/0001-x.md": front})
        _c, out, _e = check(make_repo(files), "--adr-dir", "services/api/adr")
        self.assertIn("services/api", out)
        self.assertNotIn("services/web", out)

    def test_a_scope_that_matches_nothing_is_a_warning_and_strict_fails(self):
        root = self.repo("Scope: servces/api/**")
        code, out, err = check(root, "--adr-dir", "services/api/adr")
        self.assertEqual(code, 0)
        self.assertIn("scope of this ADR (servces/api/**) matches no", err)
        code, _o, err = check(root, "--adr-dir", "services/api/adr", "--strict")
        self.assertEqual(code, 2)

    def test_a_scope_that_points_outside_is_refused(self):
        for bad in ("../x/**", "/etc/**", "C:/x/**", "**"):
            with self.subTest(bad=bad):
                code, _o, err = check(self.repo("Scope: " + bad),
                                      "--adr-dir", "services/api/adr")
                self.assertEqual(code, 2)
                self.assertIn("scope", err)

    def test_a_sentence_after_scope_does_not_switch_enforcement_off(self):
        root = self.repo("Scope: this decision covers the API only")
        code, out, err = check(root, "--adr-dir", "services/api/adr")
        self.assertEqual(code, 1)
        self.assertIn("looks like a sentence", err)
        self.assertIn("services/web/handlers/h.py", out)

    def test_scope_is_not_part_of_the_rule_text_so_waivers_still_match(self):
        files = dict(self.FILES, **{
            "arch.yaml": self.GLOBS + (
                'waivers:\n  - rule: "handlers must not import db"\n'
                '    owner: bob\n    expires: 2030-06-01\n    reason: x\n'),
            "services/api/adr/0001-x.md": adr(
                "1. api", ["forbid-import: handlers -> db"],
                "Scope: services/api/**\n")})
        with mock.patch.dict(os.environ, {"ADR_DRIFT_TODAY": "2030-01-01"}):
            code, _o, _e = check(make_repo(files), "--adr-dir",
                                 "services/api/adr")
        self.assertEqual(code, 0)

    def test_rules_diff_calls_a_narrowed_scope_a_weakening(self):
        class Repo(GitCase):
            def runTest(self):
                pass
        case = Repo()
        case.setUp()
        files = dict(self.FILES, **{"arch.yaml": self.GLOBS,
                    "services/api/adr/0001-x.md": adr(
                        "1. api", ["forbid-import: handlers -> db"])})
        root = init_git(make_repo(files))
        commit_all(root, "one")
        with open(os.path.join(root, "services/api/adr/0001-x.md"), "w",
                  encoding="utf-8") as f:
            f.write(adr("1. api", ["forbid-import: handlers -> db"],
                        "Scope: services/web/**\n"))
        commit_all(root, "two")
        code, out, _e = run_main("rules-diff", "--root", root,
                                 "--adr-dir", "services/api/adr",
                                 "--since", "HEAD~1", "--fail-on-weakened")
        self.assertEqual(code, 1, out)
        self.assertIn("no longer enforced", out)
        self.assertIn("[scope: services/web/**]", out)

    def test_scoped_result_equals_unscoped_result_filtered(self):
        """Metamorphic: scope G == the unscoped violations whose file
        matches G."""
        rnd = random.Random(5)
        for _ in range(15):
            files = {"arch.yaml": self.GLOBS}
            for svc in ("api", "web", "jobs"):
                for k in range(rnd.randint(1, 3)):
                    bad = rnd.random() < 0.6
                    files[f"services/{svc}/handlers/h{k}.py"] = (
                        f"from services.{svc}.db import c\n" if bad else "x=1\n")
                files[f"services/{svc}/db/c.py"] = "X=1\n"
            glob = rnd.choice(["services/api/**", "services/*/handlers/h0.py",
                               "services/web/**", "services/jobs/handlers/*.py"])
            rule = "forbid-import: handlers -> db"
            plain = dict(files, **{"docs/adr/0001-a.md": adr("1. a", [rule])})
            scoped = dict(files, **{"docs/adr/0001-a.md": adr(
                "1. a", [rule], f"Scope: {glob}\n")})
            _c, a, _e = check(make_repo(plain), "--format", "json")
            _c, b, _e = check(make_repo(scoped), "--format", "json")
            want = [v for v in json.loads(a)
                    if rules_mod.match_glob(glob, v["file"])]
            got = json.loads(b) if b.strip() else []
            norm = lambda vs: sorted((v["file"], v["line"], v["detail"])
                                     for v in vs)
            self.assertEqual(norm(got), norm(want), glob)


class Transitive(unittest.TestCase):
    def repo(self, extra=None, rules=("forbid-transitive: handlers -> db",)):
        files = {
            "arch.yaml": LAYERS + "rules:\n"
            + "".join(f'  - "{r}"\n' for r in rules),
            "src/handlers/h.py": "from src.services import svc\nimport json\n",
            "src/services/__init__.py": "",
            "src/services/svc.py": "from src.db import conn\n",
            "src/db/__init__.py": "",
            "src/db/conn.py": "X=1\n",
            "src/util/json.py": "from src.db import conn\n",
        }
        files.update(extra or {})
        return make_repo(files)

    def test_a_two_step_chain_is_found_with_every_step(self):
        code, out, _e = check(self.repo(), "--format", "json")
        self.assertEqual(code, 1)
        (v,) = json.loads(out)
        self.assertEqual(v["file"], "src/handlers/h.py")
        self.assertEqual(v["detail"], "reaches db")
        self.assertEqual(v["evidence"], [
            "ends in src/db/conn.py after 2 imports",
            "src/handlers/h.py:1 imports src.services.svc",
            "src/services/svc.py:1 imports src.db.conn"])

    def test_forbid_import_alone_does_not_see_it(self):
        code, _o, _e = check(self.repo(rules=("forbid-import: handlers -> db",)))
        self.assertEqual(code, 0)

    def test_a_standard_library_name_is_not_a_repo_file_of_that_name(self):
        # `import json` must not follow src/util/json.py (which reaches db).
        code, out, _e = check(self.repo({"src/handlers/h.py": "import json\n"}))
        self.assertEqual(code, 0, out)

    def test_an_import_that_fits_two_files_is_not_followed(self):
        files = {"src/handlers/h.py": "import helper\n",
                 "src/services/helper.py": "from src.db import conn\n",
                 "src/util/helper.py": "x=1\n"}
        code, out, _e = check(self.repo(files))
        self.assertEqual(code, 0, out)

    def test_it_works_through_a_cycle_and_stops(self):
        files = {"src/services/svc.py": "from src.handlers import h\nfrom src.db import conn\n"}
        code, out, _e = check(self.repo(files), "--format", "json")
        self.assertEqual(code, 1)
        self.assertEqual(len(json.loads(out)), 1)

    def test_baseline_and_ratchet_roundtrip(self):
        root = self.repo()
        self.assertEqual(run_main("baseline", "--root", root)[0], 0)
        self.assertEqual(check(root, "--baseline", ".adr-baseline.json",
                               "--ratchet")[0], 0)
        os.remove(os.path.join(root, "src/services/svc.py"))
        with open(os.path.join(root, "src/services/svc.py"), "w") as f:
            f.write("x=1\n")
        self.assertEqual(check(root, "--baseline", ".adr-baseline.json",
                               "--ratchet")[0], 1)

    def test_drift_says_it_cannot_judge_it(self):
        class Repo(GitCase):
            def runTest(self):
                pass
        Repo().setUp()
        root = init_git(self.repo())
        commit_all(root, "a")
        code, out, err = run_main("drift", "--root", root, "--since", "HEAD")
        self.assertIn("forbid-transitive", err)
        self.assertIn("not by `drift`", err)

    def test_a_misspelt_rule_name_is_caught(self):
        root = self.repo(rules=("forbid-transitve: handlers -> db",))
        code, _o, err = check(root)
        self.assertEqual(code, 2)
        self.assertIn("forbid-transitive", err)

    def test_target_that_is_no_layer_is_warned_like_forbid_import(self):
        root = self.repo(rules=("forbid-transitive: handlrs -> db",))
        _c, _o, err = check(root)
        self.assertIn("enforces nothing", err)


class Differential(unittest.TestCase):
    """forbid-transitive against a 15-line reference that knows the import
    graph the generator drew (no parsing, no name resolution)."""

    @staticmethod
    def build(rnd):
        layers = ["l0", "l1", "l2"]
        mods = [(l, i) for l in layers for i in range(rnd.randint(2, 5))]
        edges = {m: set() for m in mods}
        for m in mods:
            for t in rnd.sample(mods, rnd.randint(0, 3)):
                if t != m:
                    edges[m].add(t)
        files = {"arch.yaml": "layers:\n" + "".join(
            f'  - name: {l}\n    paths: ["pkg/{l}/**"]\n' for l in layers)
            + 'rules:\n  - "forbid-transitive: l0 -> l2"\n'}
        for (l, i), outs in edges.items():
            lines = []
            for (tl, ti) in sorted(outs):
                style = rnd.choice(("import", "from-mod", "from-pkg"))
                if style == "import":
                    lines.append(f"import pkg.{tl}.m{ti}")
                elif style == "from-mod":
                    lines.append(f"from pkg.{tl}.m{ti} import x")
                else:
                    lines.append(f"from pkg.{tl} import m{ti}")
            files[f"pkg/{l}/m{i}.py"] = "\n".join(lines) + "\nx = 1\n"
        return files, edges

    @staticmethod
    def reference(edges):
        """{file: shortest number of imports to reach any l2 file}."""
        want = {}
        for start in edges:
            if start[0] != "l0":
                continue
            seen, frontier, n = {start}, [start], 0
            hit = None
            while frontier and hit is None:
                n += 1
                nxt = []
                for cur in frontier:
                    for t in sorted(edges[cur]):
                        if t in seen:
                            continue
                        seen.add(t)
                        if t[0] == "l2":
                            hit = n
                        nxt.append(t)
                frontier = nxt
            if hit:
                want[f"pkg/{start[0]}/m{start[1]}.py"] = hit
        return want

    def run_case(self, seed):
        files, edges = self.build(random.Random(seed))
        code, out, err = check(make_repo(files), "--format", "json")
        self.assertIn(code, (0, 1), err)
        got = {v["file"]: int(re.search(r"after (\d+) import", v["evidence"][0]).group(1))
               for v in (json.loads(out) if out.strip() else [])}
        self.assertEqual(got, self.reference(edges), f"seed {seed}")

    def test_forty_seeded_graphs(self):
        for seed in range(40):
            with self.subTest(seed=seed):
                self.run_case(seed)

    def test_hypothesis_graphs_if_installed(self):
        try:
            from hypothesis import HealthCheck, given, settings
            from hypothesis import strategies as st
        except ImportError:
            self.skipTest("hypothesis is not installed (optional)")

        @settings(max_examples=60, deadline=None, derandomize=True,
                  suppress_health_check=list(HealthCheck))
        @given(st.integers(min_value=0, max_value=10**6))
        def prop(seed):
            self.run_case(seed)
        prop()


class RuleText(unittest.TestCase):
    def test_parse_rule_line_never_raises_anything_but_valueerror(self):
        rnd = random.Random(11)
        alphabet = list("abc -> !~=~*:,#\\/.()[]\"'") + [
            "forbid-import", "forbid-transitive", "layer-order", "public-api"]
        for _ in range(4000):
            text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 14)))
            try:
                r = rules_mod.parse_rule_line(text)
            except ValueError:
                continue
            if r is not None:
                self.assertTrue(r.describe())

    def test_describe_roundtrips_a_waiver_target(self):
        r = rules_mod.parse_rule_line("forbid-transitive: a -> b")
        self.assertEqual(r.describe(), "a must not reach b through imports")


class WindowsEdges(unittest.TestCase):
    def test_a_path_on_another_drive_is_shown_not_a_crash(self):
        with mock.patch("os.path.relpath", side_effect=ValueError("drive")):
            shown = paths_mod.rel_posix("D:\\adr\\0001-x.md", "C:\\repo")
        self.assertTrue(shown.endswith("0001-x.md"))

    def test_inside_check_ignores_case_and_separator_form(self):
        from adr_drift import scanner
        with mock.patch("os.path.normcase", side_effect=lambda p: p.lower()):
            self.assertTrue(scanner._inside("/Tmp/Repo", "/Tmp/Repo"))

    def test_a_backslash_in_scope_is_read_as_a_slash(self):
        lines = ["# t", "Scope: services\\api\\**"]
        self.assertEqual(config_mod.adr_scope(lines), ["services/api/**"])


if __name__ == "__main__":
    unittest.main()


class UpgradeFromPublished(unittest.TestCase):
    """State files written by adr-drift 0.1.0 (the version on PyPI): its
    arch.yaml, ADRs, marker-less index and baseline (absolute paths and other
    line numbers in `rule_source`) must keep working."""

    SRC = os.path.join(HERE, "fixtures", "from-0.1.0")

    def copy(self):
        import shutil
        import tempfile
        dst = os.path.join(tempfile.mkdtemp(), "project")
        shutil.copytree(self.SRC, dst)
        return dst

    def test_the_old_baseline_still_grandfathers_everything(self):
        root = self.copy()
        code, out, err = run_main("check", "--root", root, "--baseline",
                                  "adr-baseline.json", "--ratchet")
        self.assertEqual(code, 0, out + err)

    def test_without_the_baseline_the_same_violations_are_found(self):
        code, out, _e = run_main("check", "--root", self.copy())
        self.assertEqual(code, 1)
        self.assertIn("handlers must not import db", out)

    def test_the_old_index_is_regenerated_not_refused(self):
        root = self.copy()
        code, out, err = run_main("index", "--root", root)
        self.assertEqual(code, 0, out + err)
        with open(os.path.join(root, "docs/adr/README.md"),
                  encoding="utf-8") as f:
            self.assertIn("generated by adr-drift index", f.read())


class ScopeLimits(unittest.TestCase):
    def test_too_many_or_too_long_is_an_error_not_a_silent_cut(self):
        many = "Scope: " + ", ".join(f"a/{i}/**" for i in range(60))
        with self.assertRaises(ValueError):
            config_mod.adr_scope(["# t", many])
        with self.assertRaises(ValueError):
            config_mod.adr_scope(["# t", "Scope: " + "x" * 2500])


class TransitiveIdentity(unittest.TestCase):
    def test_a_baseline_survives_a_new_shorter_way_to_the_same_target(self):
        t = Transitive()
        root = t.repo()
        self.assertEqual(run_main("baseline", "--root", root)[0], 0)
        with open(os.path.join(root, "src/handlers/h.py"), "a") as f:
            f.write("import src.db.conn\n")          # now 1 step, not 2
        code, out, err = check(root, "--baseline", ".adr-baseline.json",
                               "--ratchet")
        self.assertEqual(code, 0, out + err)


class Escaping(unittest.TestCase):
    def test_a_hostile_scope_is_escaped_in_warnings_and_coverage(self):
        files = {"arch.yaml": LAYERS, "src/handlers/a.py": "x=1\n",
                 "docs/adr/0001-x.md": adr(
                     "1. x", ["forbid-import: handlers -> db"],
                     "Scope: \x1b[2Jfoo/**\n")}
        root = make_repo(files)
        _c, _o, err = check(root)
        self.assertNotIn("\x1b", err)
        _c, out, _e = run_main("coverage", "--root", root)
        self.assertNotIn("\x1b", out)


class BackslashAdrDir(unittest.TestCase):
    def test_a_windows_spelling_of_adr_dir_finds_the_directory_anywhere(self):
        root = make_repo({"arch.yaml": LAYERS,
                          "src/handlers/a.py": "import src.db.c\n",
                          "src/db/c.py": "",
                          "docs/adr/0001-x.md": adr(
                              "1. x", ["forbid-import: handlers -> db"])})
        code, out, err = check(root, "--adr-dir", "docs\\adr")
        self.assertEqual(code, 1, err)
        self.assertNotIn("not found", err)
