"""A hostile file must not stall the run: every scan is linear.

Regression for the panel finding "string regexes are quadratic on
unterminated quotes: 80 KB of `"\"\"` = 40 s, so a hostile PR file can stall
CI". Each quote that could not close re-scanned the rest of its line. The
same shape turned up in the Rust visibility walk-back (`)use`), in comma
separated `use` lists, in the ADR parser's HTML-comment and heading regexes
and in the rule-line regex.
"""

import os
import random
import re
import subprocess
import sys
import tempfile
import time
import unittest

from adr_drift import config as config_mod
from adr_drift import diag as diag_mod
from adr_drift import langs
from adr_drift import rules as rules_mod
from adr_drift import scanner

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMIT = 2.0        # seconds; the quadratic versions needed minutes for 200 KB

#: name -> (file name, text): the shapes that were quadratic, 200 KB each.
_N = 100_000
ADVERSARIAL = {
    "double quotes with escapes": ("a.js", '"\\' * _N),
    "single quotes with escapes": ("a.js", "'\\" * _N),
    "backticks with escapes": ("a.js", "`\\" * _N),
    "escaped single quotes, no closer": ("a.js", "\\'" * _N),
    "apostrophes between words": ("a.js", "a\\'b" * (_N // 2)),
    "go": ("a.go", '"\\' * _N),
    "java": ("A.java", "'\\" * _N),
    "php": ("a.php", '"\\' * _N),
    "ruby": ("a.rb", "'\\" * _N),
    "rust": ("a.rs", '"\\' * _N),
    "rust visibility walk-back": ("a.rs", ")use x;\n" * (_N * 2 // 8)),
    "rust use list, one name per line": (
        "a.rs", "use a::{b,\n" + "use,\n" * (_N * 2 // 5)),
    "rust use a, use a,": ("a.rs", "use a,\n" * (_N * 2 // 7)),
    "rust super chain": ("a.rs", "use " + "super::" * 60_000 + "x;\n"),
    "php use list": ("a.php", "<?php\nuse A\\{B,\n" + "use,\n" * (_N * 2 // 5)),
}

_CHILD = r"""
import sys, time
from adr_drift import diag, scanner
for name, (path, text) in eval(sys.stdin.read()).items():
    t0 = time.perf_counter()
    scanner.extract_imports(path, text.encode("utf-8"),
                            diag=diag.Diagnostics(echo=False))
    print("%.3f\t%s" % (time.perf_counter() - t0, name), flush=True)
"""


class TestAdversarialFilesAreScannedInLinearTime(unittest.TestCase):
    def test_every_shape_finishes_fast(self):
        # One child with a hard limit: a regression must fail the test
        # rather than hang the run for minutes.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
        env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        try:
            p = subprocess.run([sys.executable, "-c", _CHILD], env=env,
                               input=repr(ADVERSARIAL).encode("utf-8"),
                               capture_output=True, timeout=90)
        except subprocess.TimeoutExpired:
            self.fail("scanning the adversarial files took more than 90 s "
                      "(a scanner is quadratic again)")
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        seen = {}
        for line in p.stdout.decode("utf-8").splitlines():
            secs, name = line.split("\t", 1)
            seen[name] = float(secs)
        self.assertEqual(sorted(seen), sorted(ADVERSARIAL))
        for name, secs in seen.items():
            self.assertLess(secs, LIMIT, f"{name}: {secs:.2f} s")


# -- the tokenizer must mean what it meant ------------------------------------

# The tokenizer as it was while the strings were regex alternatives (quadratic
# on a quote that cannot close). Kept as the oracle for the linear one.
_DQ = r'"(?:\\.|[^"\\\n])*"'
_SQ = r"'(?:\\.|[^'\\\n])*'"
_BT = r"`(?:\\.|[^`\\])*`"
_C = r"/\*.*?(?:\*/|\Z)|//[^\n]*"


def _oracle_regex(comment, strings):
    return re.compile(
        rf"(?P<n>\n)|(?P<c>{comment})|(?P<s>{strings})|(?P<w>[\w$]+)"
        rf"|(?P<p>::|[^\s\w$])", re.S)


_ORACLE = {
    "js": _oracle_regex(_C, f"{_DQ}|{_SQ}|{_BT}"),
    "go": _oracle_regex(_C, f"{_DQ}|{_SQ}|{_BT}"),
    "java": _oracle_regex(_C, f"{_DQ}|{_SQ}"),
    "rust": _oracle_regex(_C, _DQ),
    "php": _oracle_regex(_C + r"|\#(?!\[)[^\n]*", f"{_DQ}|{_SQ}"),
    "ruby": _oracle_regex(r"\#[^\n]*", f"{_DQ}|{_SQ}"),
}


def oracle_tokens(text, lang):
    if lang == "ruby":
        text = langs._blank_ruby_blocks(text)
    out, line, fresh = [], 1, True
    for m in _ORACLE[lang].finditer(text):
        kind = m.lastgroup
        if kind == "n":
            line += 1
            fresh = True
            continue
        tok = m.group()
        if kind == "c":
            if "\n" in tok:
                line += tok.count("\n")
                fresh = True
            continue
        out.append((kind, tok, line, fresh))
        fresh = False
        if kind == "s" and "\n" in tok:
            line += tok.count("\n")
    return out


def tokens(text, lang):
    t = langs._Tokens(text, lang)
    return list(zip(t.kind, t.text, t.line, t.first))


class TestTokenizerKeepsItsMeaning(unittest.TestCase):
    ALPHABET = ['"', '"', "'", "'", "`", "\\", "\\", "\n", "\n", " ", "a", "b",
                "/", "*", "#", ":", ";", "(", ")", "{", "}", ",", "$", "=",
                "\r", "import ", "use ", "x"]

    def test_random_text_tokenizes_as_before(self):
        rng = random.Random(20261018)
        for lang in sorted(_ORACLE):
            for _ in range(2500):
                text = "".join(rng.choice(self.ALPHABET)
                               for _ in range(rng.randint(0, 40)))
                self.assertEqual(tokens(text, lang), oracle_tokens(text, lang),
                                 f"{lang}: {text!r}")

    def test_hand_picked_awkward_texts_tokenize_as_before(self):
        texts = [
            '"abc', "'abc", "`abc", '"a\\"b', '"a\\', '"\\\n"', '"\\\nx"y"',
            '"\n"\n"', '"a" "b" "c', '" \\" \\" \\"\n"x"', "'\"' \"'\"",
            'x = "it\'s"; import y from "z"', '"\\"\\"\\"\n"ok"\n"\\"\\"',
            '/* " */ "x"', '" /* " */ "', '"a// b"', '// "\n"x"',
            '`a\n"b\n`', '`\\`\\`', '"\\\\"', '"\\\\\\"', '\\"', '\\\\"x"',
        ]
        for lang in sorted(_ORACLE):
            for text in texts:
                with self.subTest(lang=lang, text=text):
                    self.assertEqual(tokens(text, lang),
                                     oracle_tokens(text, lang))

    def test_a_quote_that_cannot_close_does_not_hide_the_next_line(self):
        src = '"\\"\\"\\"\\"\nimport x from "y";\n'
        self.assertEqual(langs.extract("a.js", src), [("y", 2)])
        src = "x = '\\'\\'\\'  import q from 'r';\n"     # same line, SQ
        self.assertEqual(langs.extract("a.js", src), langs.extract("a.js", src))

    def test_a_string_may_continue_over_a_backslash_newline(self):
        src = 'const s = "a\\\nimport z from \'q\'";\nimport x from "y";\n'
        self.assertEqual(langs.extract("a.js", src), [("y", 3)])


class TestRustVisibilityWalkBack(unittest.TestCase):
    def test_visibility_prefixes_still_start_an_item(self):
        src = ("pub(crate) use a::b;\n"
               "pub(in crate::x) use c::d;\n"
               "pub(super) use e::f;\n"
               "pub use g::h;\n"
               "fn f() {} use i::j;\n")
        self.assertEqual(langs.extract("a.rs", src), [
            ("a.b", 1), ("c.d", 2), ("e.f", 3), ("g.h", 4), ("i.j", 5)])

    def test_a_closing_paren_that_is_not_a_visibility_is_not_an_item(self):
        self.assertEqual(langs.extract("a.rs", "f(x) use a::b;\n"), [])
        self.assertEqual(langs.extract("a.rs", "foo) use a::b;\n"), [])


class TestUseListsAreBounded(unittest.TestCase):
    def test_a_hostile_use_list_is_reported_not_slow(self):
        text = "use a,\n" * 2500 + "use a;\n"
        t0 = time.perf_counter()
        with self.assertRaises(langs.ExtractionLimit):
            langs.extract("a.rs", text)
        self.assertLess(time.perf_counter() - t0, LIMIT)

    def test_the_limit_is_a_warning_not_a_silent_pass(self):
        d = diag_mod.Diagnostics(echo=False)
        text = "use a,\n" * 2500 + "use a;\n"
        imports, engine = scanner.extract_imports("a.rs", text.encode(), diag=d)
        self.assertEqual((imports, engine), ([], "regex"))
        self.assertEqual(len(d.warnings), 1)
        self.assertIn("a.rs: unparsable Rust", d.warnings[0])
        self.assertIn("NOT checked", d.warnings[0])

    def test_absurd_nesting_is_a_warning_too(self):
        d = diag_mod.Diagnostics(echo=False)
        text = "use a::{" * 5000
        imports, engine = scanner.extract_imports("a.rs", text.encode(), diag=d)
        self.assertEqual((imports, engine), ([], "regex"))
        self.assertEqual(len(d.warnings), 1)
        self.assertIn("unparsable Rust (use declarations nested too deeply",
                      d.warnings[0])

    def test_big_honest_files_are_untouched(self):
        names = ", ".join(f"n{i}" for i in range(60_000))
        self.assertEqual(len(langs.extract("a.rs", "use big::{" + names + "};\n")),
                         60_000)
        self.assertEqual(len(langs.extract("a.rs", "use a::b;\n" * 30_000)),
                         30_000)
        php = "<?php\n" + "".join(f"use A\\B{i};\n" for i in range(20_000))
        self.assertEqual(len(langs.extract("a.php", php)), 20_000)


class TestConfigRegexesAreLinear(unittest.TestCase):
    def timed(self, fn):
        t0 = time.perf_counter()
        result = fn()
        self.assertLess(time.perf_counter() - t0, LIMIT)
        return result

    def test_unterminated_html_comments(self):
        lines = ["<!--"] * 16_000
        self.assertEqual(self.timed(
            lambda: config_mod._without_html_comments(lines)), lines)

    def test_html_comments_are_blanked_as_before(self):
        old = re.compile(r"<!--.*?-->", re.DOTALL)
        rng = random.Random(7)
        for _ in range(3000):
            text = "".join(rng.choice(["<!--", "-->", "<!-->", "--", "-", "!",
                                       "<", ">", "\n", "a", " "])
                           for _ in range(rng.randint(0, 24)))
            expect = old.sub(lambda m: "\n" * m.group(0).count("\n"),
                             text).split("\n")
            self.assertEqual(
                config_mod._without_html_comments(text.split("\n")), expect,
                repr(text))

    def tmp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def test_a_heading_with_a_long_run_of_blanks(self):
        path = os.path.join(self.tmp(), "0001-x.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# x" + " " * 30_000 + "y\n\n## Constraints\n\n")
        info = self.timed(lambda: config_mod.parse_adr(path))
        self.assertEqual(info["title"].split()[0], "x")
        self.assertEqual(info["title"][-1], "y")

    def test_a_rule_line_with_a_long_run_of_blanks(self):
        line = "forbid-import: a" + " " * 30_000 + "b -> c"
        t0 = time.perf_counter()
        with self.assertRaisesRegex(ValueError, "contains spaces"):
            rules_mod.parse_rule_line(line)
        self.assertLess(time.perf_counter() - t0, LIMIT)
        rule = self.timed(lambda: rules_mod.parse_rule_line(
            "forbid-import:" + " " * 30_000 + "a -> b" + " " * 30_000))
        self.assertEqual((rule.from_pat, rule.to_pat), ("a", "b"))

    def test_the_line_of_an_unknown_layer_key_with_a_blank_line_before_it(self):
        d = self.tmp()
        with open(os.path.join(d, "arch.yaml"), "w", encoding="utf-8") as f:
            f.write(" " * 60_000 + "# note\nlayers:\n  - name: a\n"
                    "    paths: [x]\n    pahts: x\n")
        problems = []
        self.timed(lambda: config_mod.load_arch_yaml(d, problems))
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("arch.yaml:5: layer 'a': "),
                        problems[0])


if __name__ == "__main__":
    unittest.main()
