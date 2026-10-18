"""`init` and `index`: never destroy a hand-written README, number and read
ADRs the way people write them.

Regressions for the panel finding "init/index overwrite a hand-written
docs/adr/README.md (no backup, no --force); init adds a second 0001 beside
0001-use-postgres.md; index shows Status '-' for MADR front matter and for
`Status:` lines". `index` also died with a traceback (exit 1, the code for
"violations") when there was no ADR directory.
"""

import contextlib
import io
import os
import unittest

from adr_drift import cli as cli_mod
from adr_drift import config as config_mod

from _support import make_repo, run_main

DASH = chr(0x2014)        # what the index shows for "no status"
POSTGRES = "# 1: Use Postgres\n\n## Status\n\nAccepted\n"
HAND = "# How we write ADRs\n\nPlease read this first.\n\n- one\n- two\n"
LEGACY_INDEX = (            # byte for byte what 0.1.1 wrote for one ADR
    "# Architecture Decision Records\n\n"
    "| # | Title | Status |\n"
    "|---|-------|--------|\n"
    "| 0001 | [1: Use Postgres](0001-use-postgres.md) | Accepted |\n")


def adr_dir(root):
    return os.path.join(root, "docs", "adr")


def read(path):
    with open(path, "rb") as f:
        return f.read()


def readme(root):
    return os.path.join(adr_dir(root), "README.md")


def index(root, *extra):
    return run_main("--root", root, "index", *extra)


def init(root, *extra):
    return run_main("--root", root, "init", *extra)


class TestIndexKeepsAHandWrittenReadme(unittest.TestCase):
    def repo(self, readme_text=HAND):
        files = {"docs/adr/0001-use-postgres.md": POSTGRES}
        if readme_text is not None:
            files["docs/adr/README.md"] = readme_text
        return make_repo(files)

    def test_it_refuses_and_leaves_the_file_alone(self):
        root = self.repo()
        code, out, err = index(root)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(read(readme(root)), HAND.encode("utf-8"))
        self.assertIn("error:", err)
        self.assertIn("docs/adr/README.md", err)
        self.assertIn("--force", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("indexed", out)

    def test_force_replaces_it(self):
        root = self.repo()
        code, out, err = index(root, "--force")
        self.assertEqual(code, 0, err)
        text = read(readme(root)).decode("utf-8")
        self.assertNotIn("Please read this first", text)
        self.assertIn("[1: Use Postgres](0001-use-postgres.md)", text)

    def test_what_it_wrote_itself_is_regenerated_without_force(self):
        root = self.repo(None)
        self.assertEqual(index(root)[0], 0)
        first = read(readme(root))
        self.assertEqual(index(root)[0], 0)
        self.assertEqual(read(readme(root)), first)

    def test_it_follows_a_new_adr(self):
        root = self.repo(None)
        self.assertEqual(index(root)[0], 0)
        self.assertEqual(run_main("--root", root, "new", "Cache sessions")[0], 0)
        self.assertEqual(index(root)[0], 0)
        text = read(readme(root)).decode("utf-8")
        self.assertIn("0002-cache-sessions.md", text)

    def test_the_marker_is_the_first_line(self):
        root = self.repo(None)
        index(root)
        # text mode writes the line ends of the platform (CR LF on Windows)
        first = read(readme(root)).decode("utf-8").splitlines()[0]
        self.assertTrue(first.startswith("<!--") and first.endswith("-->"),
                        first)
        self.assertIn("adr-drift", first)
        self.assertEqual(first, cli_mod.INDEX_MARKER)

    def test_an_index_from_before_the_marker_existed_is_still_ours(self):
        for eol in (b"\n", b"\r\n"):
            with self.subTest(eol=eol):
                root = self.repo(LEGACY_INDEX)
                with open(readme(root), "wb") as f:
                    f.write(LEGACY_INDEX.encode("utf-8").replace(b"\n", eol))
                code, out, err = index(root)
                self.assertEqual(code, 0, err)
                self.assertTrue(read(readme(root)).decode("utf-8").startswith(
                    cli_mod.INDEX_MARKER))

    def test_an_old_index_with_something_added_by_hand_is_not_ours(self):
        for extra in ("\nSee also the wiki.\n", "\n| 9 | hand | made |\nTrailing prose\n"):
            with self.subTest(extra=extra):
                root = self.repo(LEGACY_INDEX + extra)
                code, out, err = index(root)
                self.assertEqual(code, 2, err)
                self.assertEqual(read(readme(root)),
                                 (LEGACY_INDEX + extra).encode("utf-8"))

    def test_a_readme_with_a_bom_and_the_marker_is_ours(self):
        root = self.repo(None)
        with open(readme(root), "wb") as f:
            f.write(b"\xef\xbb\xbf" + cli_mod.INDEX_MARKER.encode("utf-8")
                    + b"\n# old\n")
        self.assertEqual(index(root)[0], 0)

    def test_an_empty_readme_has_nothing_to_lose(self):
        for text in ("", "\n\n", "  \r\n"):
            with self.subTest(text=text):
                root = self.repo(text)
                self.assertEqual(index(root)[0], 0)
                self.assertTrue(read(readme(root)).decode("utf-8").startswith(
                    cli_mod.INDEX_MARKER))

    def test_help_says_what_force_does(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            cli_mod.build_parser().parse_args(["index", "--help"])
        self.assertIn("--force", out.getvalue())
        self.assertIn("README", out.getvalue())

    def test_a_readme_that_is_a_directory_is_an_error_not_a_traceback(self):
        root = make_repo({"docs/adr/0001-use-postgres.md": POSTGRES})
        os.makedirs(readme(root))
        for extra in ((), ("--force",)):
            code, out, err = index(root, *extra)
            self.assertEqual(code, 2, err)
            self.assertIn("error:", err)
            self.assertNotIn("Traceback", err)


class TestIndexNeedsAnAdrDirectory(unittest.TestCase):
    def test_no_directory_is_an_error_not_a_traceback(self):
        root = make_repo({"arch.yaml": "layers: []\n"})
        code, out, err = index(root)
        self.assertEqual(code, 2, out + err)
        self.assertIn("error:", err)
        self.assertIn("docs/adr", err)
        self.assertIn("hint:", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(os.path.join(root, "docs")))

    def test_a_missing_adr_dir_option_names_that_directory(self):
        root = make_repo({"docs/adr/0001-use-postgres.md": POSTGRES})
        code, out, err = index(root, "--adr-dir", "doc/adr")
        self.assertEqual(code, 2, out + err)
        self.assertIn("doc/adr", err)
        self.assertNotIn("Traceback", err)

    def test_an_empty_directory_gets_an_empty_index(self):
        root = make_repo({"docs/adr/.keep": ""})
        code, out, err = index(root)
        self.assertEqual(code, 0, err)
        self.assertIn("indexed 0 ADR(s)", out)


class TestInitNumbersTheSample(unittest.TestCase):
    SAMPLE = "record-architecture-decisions.md"

    def names(self, root):
        found = []
        for dirpath, _dirs, files in os.walk(adr_dir(root)):
            found += [os.path.relpath(os.path.join(dirpath, f), adr_dir(root))
                      .replace(os.sep, "/") for f in files]
        return sorted(found)

    def test_a_fresh_repo_gets_0001(self):
        root = make_repo({"src/api/x.py": ""})
        code, out, err = init(root)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.names(root),
                         ["0001-" + self.SAMPLE, "README.md"])

    def test_it_goes_after_the_highest_existing_number(self):
        root = make_repo({"docs/adr/0001-use-postgres.md": POSTGRES,
                          "docs/adr/2024/0007-use-kafka.md": POSTGRES})
        code, out, err = init(root)
        self.assertEqual(code, 0, err)
        self.assertIn("0008-" + self.SAMPLE, self.names(root))
        self.assertNotIn("0001-" + self.SAMPLE, self.names(root))
        with open(os.path.join(adr_dir(root), "0008-" + self.SAMPLE),
                  encoding="utf-8") as f:
            self.assertTrue(f.read().startswith(
                "# 8: Record architecture decisions\n"))
        self.assertIn("0008-" + self.SAMPLE, out)

    def test_it_goes_into_the_directory_that_is_there(self):
        root = make_repo({"doc/adr/0004-x.md": POSTGRES})
        code, out, err = init(root)        # doc/adr exists, so it is the one
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(
            os.path.join(root, "doc", "adr", "0005-" + self.SAMPLE)))
        self.assertFalse(os.path.exists(os.path.join(root, "docs")))

    def test_it_follows_adr_dir(self):
        root = make_repo({"decisions/0012-x.md": POSTGRES,
                          "docs/adr/0001-y.md": POSTGRES})
        code, out, err = init(root, "--adr-dir", "decisions")
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(
            os.path.join(root, "decisions", "0013-" + self.SAMPLE)))
        self.assertTrue(os.path.isfile(
            os.path.join(root, "decisions", "README.md")))
        self.assertEqual(os.listdir(os.path.join(root, "docs", "adr")),
                         ["0001-y.md"])

    def test_an_existing_record_of_it_is_not_added_twice(self):
        for name in ("0001-" + self.SAMPLE, "0003-Record-Architecture-Decisions.md",
                     "2024/0002-" + self.SAMPLE):
            with self.subTest(name=name):
                root = make_repo({f"docs/adr/{name}": POSTGRES,
                                  "docs/adr/0009-other.md": POSTGRES})
                before = self.names(root)
                code, out, err = init(root)
                self.assertEqual(code, 0, err)
                added = [n for n in self.names(root) if n not in before]
                self.assertEqual(added, ["README.md"])
                self.assertIn(os.path.basename(name), out)

    def test_running_it_again_with_force_adds_nothing(self):
        root = make_repo({"src/api/x.py": ""})
        self.assertEqual(init(root)[0], 0)
        before = self.names(root)
        self.assertEqual(init(root)[0], 2)
        self.assertEqual(init(root, "--force")[0], 0)
        self.assertEqual(self.names(root), before)


class TestInitKeepsAHandWrittenReadme(unittest.TestCase):
    def repo(self):
        return make_repo({"docs/adr/README.md": HAND,
                          "docs/adr/0001-use-postgres.md": POSTGRES,
                          "src/api/x.py": ""})

    def test_arch_yaml_and_the_sample_are_written_the_readme_is_kept(self):
        root = self.repo()
        code, out, err = init(root)
        self.assertEqual(code, 0, err)
        self.assertEqual(read(readme(root)), HAND.encode("utf-8"))
        self.assertTrue(os.path.isfile(os.path.join(root, "arch.yaml")))
        self.assertTrue(os.path.isfile(os.path.join(
            adr_dir(root), "0002-record-architecture-decisions.md")))
        both = out + err
        self.assertIn("docs/adr/README.md", both)
        self.assertIn("index --force", both)

    def test_force_overwrites_arch_yaml_only(self):
        root = self.repo()
        with open(os.path.join(root, "arch.yaml"), "w", encoding="utf-8") as f:
            f.write("layers: []\n# mine\n")
        self.assertEqual(init(root)[0], 2)
        code, out, err = init(root, "--force")
        self.assertEqual(code, 0, err)
        with open(os.path.join(root, "arch.yaml"), encoding="utf-8") as f:
            self.assertNotIn("# mine", f.read())
        self.assertEqual(read(readme(root)), HAND.encode("utf-8"))

    def test_an_index_it_wrote_before_is_regenerated(self):
        root = make_repo({"src/api/x.py": ""})
        self.assertEqual(init(root)[0], 0)
        self.assertEqual(init(root, "--force")[0], 0)
        self.assertEqual(run_main("--root", root, "new", "Next")[0], 0)
        self.assertEqual(init(root, "--force")[0], 0)
        text = read(readme(root)).decode("utf-8")
        self.assertIn("0002-next.md", text)
        self.assertTrue(text.startswith(cli_mod.INDEX_MARKER))


def write_adr(tmp, text, name="0001-x.md", eol="\n"):
    path = os.path.join(tmp, name)
    with open(path, "wb") as f:
        f.write(text.replace("\n", eol).encode("utf-8"))
    return path


def meta(text, **kw):
    root = make_repo({})
    info = config_mod.parse_adr(write_adr(root, text, **kw), parse_rules=False)
    return info["title"], info["status"]


MADR4 = ("---\n"
         "# These are optional metadata elements. Feel free to remove any.\n"
         'status: "accepted"\n'
         "date: 2024-01-01\n"
         "decision-makers: Dan\n"
         "---\n\n"
         "# Use MADR front matter\n\n"
         "## Context and Problem Statement\n\nWhy.\n")


class TestStatusAndTitle(unittest.TestCase):
    def test_madr_front_matter(self):
        self.assertEqual(meta(MADR4), ("Use MADR front matter", "accepted"))

    def test_the_title_is_not_a_comment_in_the_front_matter(self):
        title, _ = meta(MADR4.replace("# Use MADR front matter\n\n", ""))
        self.assertEqual(title, "x")        # from the file name, 0001-x.md

    def test_front_matter_value_shapes(self):
        for line, want in (("status: accepted", "accepted"),
                           ("Status: Accepted", "Accepted"),
                           ("status: 'proposed'", "proposed"),
                           ('status: "rejected" # not yet', "rejected"),
                           ("status: accepted # reviewed", "accepted"),
                           ("status:superseded by ADR-0005", "superseded by ADR-0005"),
                           ("status:", ""),
                           ("  status: nested", "")):
            with self.subTest(line=line):
                text = f"---\n{line}\ndate: 2024-01-01\n---\n\n# T\n"
                self.assertEqual(meta(text)[1], want)

    def test_front_matter_with_windows_line_endings(self):
        self.assertEqual(meta(MADR4, eol="\r\n"),
                         ("Use MADR front matter", "accepted"))

    def test_front_matter_that_never_closes_is_not_front_matter(self):
        title, status = meta("---\nnot yaml\n\n# Real title\n\n## Context\n")
        self.assertEqual((title, status), ("Real title", ""))

    def test_status_lines_under_the_title(self):
        for line, want in (("* Status: accepted", "accepted"),
                           ("- Status: proposed", "proposed"),
                           ("+ Status: rejected", "rejected"),
                           ("Status: Superseded by 0005", "Superseded by 0005"),
                           ("status: lower", "lower"),
                           ("**Status:** accepted", "accepted"),
                           ("**Status**: accepted", "accepted"),
                           ("* **Status:** accepted", "accepted"),
                           ("Status:", "")):
            with self.subTest(line=line):
                text = f"# Use bullets\n\n{line}\n* Date: 2020-01-01\n\n## Context\n"
                self.assertEqual(meta(text), ("Use bullets", want))

    def test_a_status_heading_still_comes_first(self):
        text = "# T\n\nStatus: draft\n\n## Status\n\nAccepted\n\n## Context\n"
        self.assertEqual(meta(text)[1], "Accepted")
        text = MADR4.replace("## Context and", "## Status\n\nDeprecated\n\n## Context and")
        self.assertEqual(meta(text)[1], "Deprecated")

    def test_an_empty_status_section_does_not_swallow_the_next_heading(self):
        text = "# T\n\n## Status\n\n## Context\n\nSome words.\n"
        self.assertEqual(meta(text)[1], "")
        text = "# T\n\nStatus: accepted\n\n## Status\n\n### Detail\n"
        self.assertEqual(meta(text)[1], "accepted")

    def test_prose_further_down_is_not_a_status(self):
        for text in ("# T\n\n## Context\n\nStatus: unknown for the old system\n",
                     "# T\n\n## Context\n\n* Status: not this one\n",
                     "# T\n\nThe Status: of this is open.\n",
                     "# T\n\n---\n\n## Context\n\nstatus: x\n"):
            with self.subTest(text=text):
                self.assertEqual(meta(text)[1], "")

    def test_a_status_inside_an_html_comment_does_not_count(self):
        text = "# T\n\n<!-- Status: accepted -->\n\n## Context\n"
        self.assertEqual(meta(text)[1], "")
        text = "<!-- # Old title -->\n# New title\n"
        self.assertEqual(meta(text)[0], "New title")

    def test_the_status_heading_in_any_case(self):
        self.assertEqual(meta("# T\n\n## STATUS\n\nProposed\n")[1], "Proposed")

    def test_an_adr_without_a_status_has_none(self):
        self.assertEqual(meta("# Just a title\n\nSome words.\n"),
                         ("Just a title", ""))

    def test_check_still_reads_the_rules_of_an_adr_with_front_matter(self):
        text = MADR4 + "\n## Constraints\n\n- forbid-import: a -> b\n"
        root = make_repo({"docs/adr/0001-x.md": text})
        info = config_mod.parse_adr(os.path.join(adr_dir(root), "0001-x.md"))
        self.assertEqual([r.kind for r in info["rules"]], ["forbid-import"])
        self.assertEqual(info["status"], "accepted")


class TestTheIndexShowsThem(unittest.TestCase):
    def rows(self, files):
        root = make_repo(files)
        code, out, err = index(root)
        self.assertEqual(code, 0, err)
        text = read(readme(root)).decode("utf-8")
        return [l for l in text.splitlines() if l.startswith("| 0")]

    def test_every_style_shows_its_status(self):
        rows = self.rows({
            "docs/adr/0001-madr4.md": MADR4,
            "docs/adr/0002-madr2.md": "# Use bullets\n\n* Status: accepted\n\n## Context\n",
            "docs/adr/0003-plain.md": "# Plain\n\nStatus: Superseded by 0005\n",
            "docs/adr/0004-heading.md": POSTGRES,
            "docs/adr/0005-none.md": "# No status here\n",
        })
        self.assertEqual(rows, [
            "| 0001 | [Use MADR front matter](0001-madr4.md) | accepted |",
            "| 0002 | [Use bullets](0002-madr2.md) | accepted |",
            "| 0003 | [Plain](0003-plain.md) | Superseded by 0005 |",
            "| 0004 | [1: Use Postgres](0004-heading.md) | Accepted |",
            "| 0005 | [No status here](0005-none.md) | " + DASH + " |",
        ])

    def test_a_pipe_in_a_cell_does_not_break_the_table(self):
        status = "{proposed | rejected | accepted}"
        rows = self.rows({"docs/adr/0001-x.md":
                          f'---\nstatus: "{status}"\n---\n\n# Pick a | b\n'})
        self.assertEqual(rows, [
            "| 0001 | [Pick a \\| b](0001-x.md) | "
            "{proposed \\| rejected \\| accepted} |"])


class TestNamingTheReadme(unittest.TestCase):
    def test_it_is_named_with_forward_slashes_on_every_platform(self):
        # os.path.join would give docs/adr\\README.md on Windows.
        for adr_rel in ("docs/adr", "docs\\adr", "docs/adr/", "docs\\adr\\"):
            with self.subTest(adr_rel=adr_rel):
                self.assertEqual(cli_mod._shown(adr_rel, "README.md"),
                                 "docs/adr/README.md")
        self.assertEqual(cli_mod._shown("adr", "README.md"), "adr/README.md")


if __name__ == "__main__":
    unittest.main()
