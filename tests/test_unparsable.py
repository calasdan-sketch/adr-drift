"""A file the scanner cannot read must never exempt itself from the rules.

Regressions for the panel finding "BOM'd .py files bypass every rule, and
a RecursionError / NUL byte tracebacks with exit 1 (= 'violations')":

* a UTF-8 BOM (very common from Windows editors) made `ast.parse` fail, so
  the file was skipped by every import rule and the run exited 0;
* source that Python cannot parse for any reason (syntax error, NUL byte,
  nesting too deep, out of memory) must be reported as unparsable - a
  warning, an error under --strict - never a traceback.
"""

import json
import unittest
from unittest import mock

from adr_drift import scanner as s

from _support import make_repo, run_main

ARCH = ('layers:\n'
        '  - name: a\n    paths: ["a/**"]\n'
        '  - name: b\n    paths: ["b/**"]\n'
        'rules:\n'
        '  - "forbid-import: a -> b"\n')
OTHER = {"b/__init__.py": "thing = 1\n"}
BOM = b"\xef\xbb\xbf"


class TestBom(unittest.TestCase):
    def test_bom_python_file_is_still_checked_by_import_rules(self):
        root = make_repo({"arch.yaml": ARCH, **OTHER,
                          "a/x.py": BOM + b"from b import thing\n"})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertEqual(code, 1, out + err)
        found = json.loads(out)
        self.assertEqual([v["file"] for v in found], ["a/x.py"])
        self.assertEqual(found[0]["line"], 1)
        self.assertNotIn("unparsable", err)

    def test_bom_python_file_with_crlf_and_comment_first(self):
        root = make_repo({"arch.yaml": ARCH, **OTHER, "a/x.py":
                          BOM + b"# header\r\nimport os\r\nfrom b import thing\r\n"})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertEqual(code, 1, err)
        self.assertEqual([v["line"] for v in json.loads(out)], [3])

    def test_bom_text_passed_as_str_is_accepted_too(self):
        imps, eng = s.extract_imports("a/x.py", "\U0000feffimport b\n")
        self.assertEqual((imps, eng), ([("b", 1)], "ast"))

    def test_bom_javascript_file_is_checked(self):
        arch = ARCH.replace("a/**", "a/**").replace("forbid-import: a -> b",
                                                    "forbid-import: a -> b")
        root = make_repo({"arch.yaml": arch, "b/m.js": "export const t = 1;\n",
                          "a/x.js": BOM + b"import { t } from '../b/m';\n"})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertEqual(code, 1, out + err)
        self.assertEqual([v["file"] for v in json.loads(out)], ["a/x.js"])

    def test_pattern_rule_sees_text_without_the_bom(self):
        root = make_repo({
            "arch.yaml": ('layers: []\nrules:\n'
                          '  - "require-pattern: a/**.py =~ ^import os"\n'),
            "a/x.py": BOM + b"import os\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, out + err)  # ^ matches at the real start

    def test_forbid_pattern_anchor_sees_line_one_of_a_bom_js_file(self):
        # per-line rule on a non-Python file: a BOM in front of line 1 must
        # not let `^import` miss (Windows editors add one to JS/TS/Go).
        root = make_repo({
            "arch.yaml": ('layers: []\nrules:\n'
                          '  - "forbid-pattern: a/**.js !~ ^import "\n'),
            "a/x.js": BOM + b"import x from './y';\n"})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertEqual(code, 1, out + err)
        (v,) = json.loads(out)
        self.assertEqual((v["file"], v["line"]), ("a/x.js", 1))

    def test_require_pattern_anchor_is_not_defeated_by_a_bom(self):
        root = make_repo({
            "arch.yaml": ('layers: []\nrules:\n'
                          '  - "require-pattern: a/**.js =~ ^// Copyright"\n'),
            "a/x.js": BOM + b"// Copyright Acme\nexport {};\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, out + err)


class TestCodingCookie(unittest.TestCase):
    def test_latin1_cookie_file_parses_and_is_decoded_properly(self):
        src = (b"# -*- coding: latin-1 -*-\n"
               b"# caf\xe9\n"
               b"from b import thing\n")
        root = make_repo({"arch.yaml": ARCH, **OTHER, "a/x.py": src})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertEqual(code, 1, out + err)
        self.assertEqual([v["line"] for v in json.loads(out)], [3])
        self.assertNotIn("unparsable", err)

    def test_latin1_text_is_decoded_with_the_cookie_for_patterns(self):
        src = b"# coding: latin-1\nname = 'caf\xe9'\n"
        self.assertIn("caf\U000000e9", s.decode_source("a/x.py", src))


class TestUnparsable(unittest.TestCase):
    def test_null_byte_is_a_warning_not_a_traceback(self):
        root = make_repo({"arch.yaml": ARCH, **OTHER,
                          "a/x.py": b"import os\x00\nfrom b import thing\n",
                          "a/y.py": b"from b import thing\n"})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        # a/y.py's violation is still found; a/x.py is reported, not lost
        self.assertEqual(code, 1, out + err)
        self.assertEqual([v["file"] for v in json.loads(out)], ["a/y.py"])
        self.assertIn("a/x.py: unparsable Python", err)
        self.assertNotIn("Traceback", err)

    def test_strict_makes_an_unparsable_file_exit_2(self):
        root = make_repo({"arch.yaml": ARCH, **OTHER,
                          "a/x.py": b"def broken(:\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, out + err)  # default: a warning
        self.assertIn("a/x.py: unparsable Python (syntax error", err)
        code, out, err = run_main("--root", root, "check", "--strict")
        self.assertEqual(code, 2, out + err)
        self.assertIn("--strict", err)

    def test_violations_still_exit_1_under_strict(self):
        root = make_repo({"arch.yaml": ARCH, **OTHER,
                          "a/x.py": b"def broken(:\n",
                          "a/y.py": b"from b import thing\n"})
        code, _out, _err = run_main("--root", root, "check", "--strict")
        self.assertEqual(code, 1)

    def test_json_stdout_is_still_valid_with_an_unparsable_file(self):
        root = make_repo({"arch.yaml": ARCH, **OTHER,
                          "a/x.py": b"def broken(:\n"})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertEqual(json.loads(out), [])

    def test_invalid_utf8_python_is_unparsable_not_a_crash(self):
        root = make_repo({"arch.yaml": ARCH, **OTHER,
                          "a/x.py": b"x = '\xff\xfe'\nfrom b import thing\n"})
        code, out, err = run_main("--root", root, "check")
        self.assertIn("a/x.py: unparsable Python", err)
        self.assertNotIn("Traceback", err)

    def test_pattern_rules_still_apply_to_an_unparsable_file(self):
        root = make_repo({
            "arch.yaml": ('layers: []\nrules:\n'
                          '  - "forbid-pattern: a/**.py !~ eval"\n'),
            "a/x.py": b"def broken(:\n    eval(x)\n"})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertEqual(code, 1, out + err)
        self.assertEqual([(v["file"], v["line"]) for v in json.loads(out)],
                         [("a/x.py", 2)])

    def test_recursion_error_while_parsing_is_unparsable(self):
        # Whether a given source overflows the parser depends on the
        # interpreter (3.11 at ~3500 chained terms, 3.12+ much later, 3.10
        # not at all), so make ast.parse raise it deterministically.
        for exc in (RecursionError("maximum recursion depth exceeded"),
                    MemoryError(), OverflowError("too large")):
            root = make_repo({"arch.yaml": ARCH, **OTHER,
                              "a/x.py": b"import os\n"})
            with mock.patch("ast.parse", side_effect=exc):
                code, out, err = run_main("--root", root, "check")
            self.assertEqual(code, 0, (exc, out, err))
            self.assertIn("a/x.py: unparsable Python", err)
            self.assertNotIn("Traceback", err)
            with mock.patch("ast.parse", side_effect=exc):
                code, _o, _e = run_main("--root", root, "check", "--strict")
            self.assertEqual(code, 2, exc)

    def test_extract_imports_reports_through_the_diagnostics_object(self):
        from adr_drift import diag as diag_mod
        d = diag_mod.Diagnostics()
        with mock.patch("sys.stderr"):
            imps, eng = s.extract_imports("a/x.py", b"def broken(:\n", diag=d)
        self.assertEqual((imps, eng), ([], "ast"))
        self.assertEqual(len(d.warnings), 1)
        self.assertIn("a/x.py", d.warnings[0])

    def test_unreadable_file_is_a_warning(self):
        root = make_repo({"arch.yaml": ARCH, **OTHER,
                          "a/x.py": b"import os\n"})
        real_open = open

        def fake_open(path, *args, **kw):
            if str(path).endswith("x.py"):
                raise PermissionError(13, "Permission denied", path)
            return real_open(path, *args, **kw)

        with mock.patch("builtins.open", side_effect=fake_open):
            code, out, err = run_main("--root", root, "check")
        self.assertEqual(code, 0, out + err)
        self.assertIn("a/x.py: cannot read", err)


class TestLineNumbers(unittest.TestCase):
    """Line numbers are the editor's: only \\n, \\r\\n and \\r end a line."""

    def _lines_of(self, body: bytes):
        root = make_repo({
            "arch.yaml": ('layers: []\nrules:\n'
                          '  - "forbid-pattern: a/**.py !~ eval"\n'),
            "a/x.py": body})
        code, out, err = run_main("--root", root, "check", "--format", "json")
        self.assertEqual(code, 1, out + err)
        return [v["line"] for v in json.loads(out)]

    def test_plain(self):
        self.assertEqual(self._lines_of(b"x = 1\ny = 2\neval(z)\n"), [3])

    def test_crlf(self):
        self.assertEqual(self._lines_of(b"x = 1\r\ny = 2\r\neval(z)\r\n"), [3])

    def test_lone_cr(self):
        self.assertEqual(self._lines_of(b"x = 1\ry = 2\reval(z)\r"), [3])

    def test_form_feed_does_not_start_a_new_line(self):
        self.assertEqual(self._lines_of(b"x = 1\n\x0c\ny = 2\neval(z)\n"), [4])

    def test_unicode_line_separator_does_not_start_a_new_line(self):
        body = "s = 'a\U00002028b'\neval(z)\n".encode("utf-8")
        self.assertEqual(self._lines_of(body), [2])

    def test_last_line_without_newline(self):
        self.assertEqual(self._lines_of(b"x = 1\neval(z)"), [2])

    def test_empty_line_regex_does_not_invent_a_phantom_last_line(self):
        s_lines = s.split_lines("a\nb\n")
        self.assertEqual(s_lines, ["a", "b"])
        self.assertEqual(s.split_lines(""), [])
        self.assertEqual(s.split_lines("a\n\nb"), ["a", "", "b"])


if __name__ == "__main__":
    unittest.main()
