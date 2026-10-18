"""Import extraction for JS/TS, Go, Java, Rust, PHP and Ruby.

Regressions for the panel finding "forbid-import silently never fires
despite 'any language'": the extractor missed Go grouped imports, PHP `use`,
Rust `::` paths and `mod`, Java `import static` / wildcards, Ruby `require`
and JS multi-line / `export ... from` / `import()` forms. A language whose
files yield no import at all is now reported instead of passing quietly.
"""

import json
import unittest

from adr_drift import langs
from adr_drift import scanner as s

from _support import make_repo, run_main


def mods(path, text):
    return langs.extract(path, text)


class TestJavaScript(unittest.TestCase):
    def test_every_import_form(self):
        src = (
            'import { a,\n'
            '  b } from "./x";\n'                    # 1-2: multi-line
            'import d, * as ns from "y";\n'          # 3
            "import type { T } from '../z/t.js';\n"  # 4
            'export * from "re";\n'                  # 5
            'export { q } from "./q";\n'             # 6
            'const l = await import("lazy");\n'      # 7
            'const r = require("req");\n'            # 8
            'import "side";\n'                       # 9
            'import e = require("ieq");\n')           # 10
        self.assertEqual(mods("a.ts", src), [
            ("x", 1), ("y", 3), ("../z/t.js", 4), ("re", 5), ("q", 6),
            ("lazy", 7), ("req", 8), ("side", 9), ("ieq", 10)])

    def test_multi_line_import_reports_the_line_of_the_keyword(self):
        src = 'x = 1\nimport {\n  a,\n  b,\n} from "pkg";\n'
        self.assertEqual(mods("a.js", src), [("pkg", 2)])

    def test_comments_and_strings_are_not_imports(self):
        src = ('// import c from "commented";\n'
               '/* import e from "block";\n   import g from "block2"; */\n'
               'const s = "import f from \'instr\'";\n'
               "const t = `import h from 'tpl'`;\n"
               'import real from "real";\n')
        self.assertEqual(mods("a.js", src), [("real", 6)])

    def test_url_in_an_import_is_not_a_comment(self):
        src = 'import x from "https://deno.land/x/mod.ts"; // trailing\n'
        self.assertEqual(mods("a.ts", src), [("https://deno.land/x/mod.ts", 1)])

    def test_a_stray_quote_does_not_swallow_following_imports(self):
        src = 'const re = /"/;\nconst it = "it\'s";\nimport x from "y";\n'
        self.assertEqual(mods("a.js", src), [("y", 3)])

    def test_relative_specifiers_resolve_against_the_file(self):
        src = 'import a from "./a.js";\nimport b from "../db/b";\n'
        self.assertEqual(mods("src/handlers/h.ts", src),
                         [("src.handlers.a", 1), ("src.db.b", 2)])

    def test_climbing_out_of_the_repo_keeps_the_spec(self):
        self.assertEqual(mods("h.ts", 'import a from "../../a";\n'),
                         [("../../a", 1)])

    def test_crlf_and_lone_cr_line_numbers(self):
        self.assertEqual(mods("a.js", 'a();\r\nimport x from "y";\r\n'),
                         [("y", 2)])
        self.assertEqual(mods("a.js", 'a();\rimport x from "y";\r'),
                         [("y", 2)])

    def test_member_named_import_is_not_an_import(self):
        self.assertEqual(mods("a.js", 'obj.import = 1;\nfoo.require = bar;\n'), [])


class TestGo(unittest.TestCase):
    def test_single_grouped_and_aliased_imports(self):
        src = ('package main\n'
               'import "fmt"\n'                            # 2
               'import (\n'
               '\t"os"\n'                                  # 4
               '\tstr "strings"\n'                         # 5
               '\t_ "net/http/pprof"\n'                    # 6
               '\t. "dot/import"\n'                        # 7
               '\t// "commented"\n'
               '\t`raw/path`\n'                            # 9
               ')\n'
               'import alias "single/alias"\n')            # 11
        self.assertEqual(mods("main.go", src), [
            ("fmt", 2), ("os", 4), ("strings", 5), ("net/http/pprof", 6),
            ("dot/import", 7), ("raw/path", 9), ("single/alias", 11)])

    def test_import_text_in_a_comment_or_string_is_ignored(self):
        src = ('// import "no"\n/* import (\n"nope"\n) */\n'
               'var s = "import \\"nor\\""\nimport "yes"\n')
        self.assertEqual(mods("a.go", src), [("yes", 6)])

    def test_go_module_prefix_is_stripped(self):
        imps = [("github.com/acme/app/internal/db", 3), ("fmt", 4),
                ("github.com/acme/app", 5), ("github.com/acme/application", 6)]
        self.assertEqual(
            langs.strip_go_module(imps, "github.com/acme/app"),
            [("internal/db", 3), ("fmt", 4), ("github.com/acme/app", 5),
             ("github.com/acme/application", 6)])
        self.assertEqual(langs.strip_go_module(imps, ""), imps)

    def test_go_module_path_is_read_from_go_mod(self):
        root = make_repo({"go.mod": "// module nope\nmodule github.com/acme/app\n\ngo 1.22\n"})
        self.assertEqual(langs.go_module_path(root), "github.com/acme/app")
        root = make_repo({"go.mod": 'module "quoted/mod"\n'})
        self.assertEqual(langs.go_module_path(root), "quoted/mod")
        self.assertEqual(langs.go_module_path(make_repo({})), "")


class TestJava(unittest.TestCase):
    def test_plain_static_and_wildcard_imports(self):
        src = ('package com.acme;\n'
               'import com.acme.Foo;\n'                       # 2
               'import static com.acme.util.Bar.baz;\n'       # 3
               'import java.util.*;\n'                        # 4
               '// import no.Way;\n'
               'import static a.B.*;\n')                      # 6
        self.assertEqual(mods("A.java", src), [
            ("com.acme.Foo", 2), ("com.acme.util.Bar.baz", 3),
            ("java.util", 4), ("a.B", 6)])


class TestRust(unittest.TestCase):
    def test_use_trees_are_expanded_to_dotted_paths(self):
        src = ('use std::collections::HashMap;\n'                  # 1
               'use a::{b::{c, d}, e, f as g, h::*};\n'            # 2
               'pub use x::y;\n'                                   # 3
               'pub(crate) use ::abs::path;\n'                     # 4
               'extern crate serde;\n')                            # 5
        self.assertEqual(mods("lib/x.rs", src), [
            ("std.collections.HashMap", 1), ("a.b.c", 2), ("a.b.d", 2),
            ("a.e", 2), ("a.f", 2), ("a.h", 2), ("x.y", 3),
            ("abs.path", 4), ("serde", 5)])

    def test_multi_line_use_group(self):
        src = 'use a::{\n    b,\n    c,\n};\n'
        self.assertEqual(mods("x.rs", src), [("a.b", 1), ("a.c", 1)])

    def test_crate_self_and_super_resolve_against_the_file(self):
        src = ('use crate::db::{self, x::Y};\n'                     # 1
               'use super::sib::Z;\n'                              # 2
               'use self::local;\n')                               # 3
        self.assertEqual(mods("src/handlers/h.rs", src), [
            ("db", 1), ("db.x.Y", 1), ("handlers.sib.Z", 2),
            ("handlers.h.local", 3)])
        # mod.rs *is* its directory's module
        self.assertEqual(mods("src/handlers/mod.rs", 'use super::top;\n'),
                         [("top", 1)])
        self.assertEqual(mods("src/handlers/mod.rs", 'use self::x;\n'),
                         [("handlers.x", 1)])

    def test_mod_declarations_are_imports_of_the_child(self):
        self.assertEqual(mods("src/lib.rs", 'mod db;\npub mod api;\nmod inline { }\n'),
                         [("db", 1), ("api", 2)])
        self.assertEqual(mods("src/handlers/h.rs", 'mod child;\n'),
                         [("handlers.h.child", 1)])

    def test_comments_and_strings_are_not_imports(self):
        src = '// use no::way;\n/* use nor::this; */\nlet s = "use fake::str;";\nuse real::one;\n'
        self.assertEqual(mods("a.rs", src), [("real.one", 4)])

    def test_lifetimes_do_not_start_a_string(self):
        src = "fn f<'a>(x: &'a str) {}\nuse real::one;\n"
        self.assertEqual(mods("a.rs", src), [("real.one", 2)])


class TestPhp(unittest.TestCase):
    def test_use_forms(self):
        src = ('<?php\n'
               'use App\\Models\\User;\n'                          # 2
               'use App\\Models\\{Post, Comment as C};\n'          # 3
               'use function App\\helpers\\f;\n'                   # 4
               'use const App\\K;\n'                               # 5
               'use \\Leading\\Slash;\n'                           # 6
               '// use Nope\\No;\n'
               '# use Nor\\This;\n'
               '/* use Nor\\That; */\n')
        self.assertEqual(mods("a.php", src), [
            ("App.Models.User", 2), ("App.Models.Comment", 3),
            ("App.Models.Post", 3), ("App.helpers.f", 4), ("App.K", 5),
            ("Leading.Slash", 6)])

    def test_require_and_include(self):
        src = ("<?php\n"
               "require_once __DIR__ . '/../db/conn.php';\n"       # 2
               'include "lib/x.php";\n'                            # 3
               "require('./rel.php');\n")                          # 4
        self.assertEqual(mods("src/handlers/h.php", src), [
            ("src.db.conn", 2), ("lib/x.php", 3), ("src.handlers.rel", 4)])

    def test_an_attribute_is_not_a_comment(self):
        src = "<?php\n#[Route('/x')] use App\\Real;\nuse App\\Other;\n"
        self.assertEqual(mods("a.php", src), [("App.Other", 3)])

    def test_closure_use_is_not_an_import(self):
        self.assertEqual(mods("a.php", "<?php\n$f = function () use ($x) {};\n"), [])


class TestRuby(unittest.TestCase):
    def test_require_forms(self):
        src = ('require "json"\n'                                   # 1
               'require_relative "../db/conn"\n'                    # 2
               "require('active_support/core_ext')\n"               # 3
               '# require "nope"\n'
               'autoload :Foo, "foo/bar"\n'                         # 5
               '=begin\nrequire "block"\n=end\n'
               '  load "x.rb"\n')                                   # 9
        self.assertEqual(mods("lib/a.rb", src), [
            ("json", 1), ("db.conn", 2), ("active_support/core_ext", 3),
            ("foo/bar", 5), ("x.rb", 9)])

    def test_a_string_with_a_hash_is_not_a_comment(self):
        src = 'puts "a # b"\nrequire "real"\n'
        self.assertEqual(mods("a.rb", src), [("real", 2)])


class TestWiring(unittest.TestCase):
    def test_extract_imports_uses_the_language_extractors(self):
        imps, eng = s.extract_imports("main.go", 'import (\n\t"os"\n)\n')
        self.assertEqual((imps, eng), ([("os", 2)], "regex"))
        imps, eng = s.extract_imports("main.go", 'import (\n\t"os"\n)\n',
                                      strict_py_only=True)
        self.assertEqual((imps, eng), ([], "none"))

    def test_bytes_with_a_bom_are_fine(self):
        imps, _ = s.extract_imports("a.js", b'\xef\xbb\xbfimport x from "y";\n')
        self.assertEqual(imps, [("y", 1)])

    def test_a_pathological_file_is_reported_not_a_traceback(self):
        from unittest import mock
        with mock.patch("adr_drift.langs.extract",
                        side_effect=RecursionError("deep")):
            from adr_drift import diag as diag_mod
            d = diag_mod.Diagnostics(echo=False)
            imps, eng = s.extract_imports("a.go", b"package a\n", diag=d)
        self.assertEqual((imps, eng), ([], "regex"))
        self.assertIn("a.go: unparsable Go", d.warnings[0])

    def test_absurdly_nested_use_tree_does_not_recurse_forever(self):
        # It is refused (extract_imports reports the file as unparsable),
        # not skipped in silence and not a RecursionError.
        for text in ("use a::{" * 5000,
                     "use a::{" * 5000 + "b" + "};" * 5000):
            with self.assertRaises(langs.ExtractionLimit):
                mods("a.rs", text)
        self.assertEqual(
            mods("a.rs", "use a::{b::{c::{d::{e, f}}}};"),
            [("a.b.c.d.e", 1), ("a.b.c.d.f", 1)])

    def test_go_module_option(self):
        imps, _ = s.extract_imports(
            "a.go", 'import "github.com/acme/app/internal/db"\n',
            go_module="github.com/acme/app")
        self.assertEqual(imps, [("internal/db", 1)])

    def test_generic_imports_defaults_to_the_path_language(self):
        self.assertEqual(s.generic_imports('import (\n\t"os"\n)\n', "m.go"),
                         [("os", 2)])
        self.assertEqual(s.generic_imports('use a::{b, c};\n', "m.rs"),
                         [("a.b", 1), ("a.c", 1)])


class TestRulesFireInEveryLanguage(unittest.TestCase):
    """End to end: the layer rule that the README sells, per language."""

    def violations(self, files, rule="forbid-import: handlers -> db"):
        arch = ('layers:\n'
                '  - name: handlers\n    paths: ["%s/**"]\n'
                '  - name: db\n    paths: ["%s/**"]\n'
                'rules:\n  - "%s"\n')
        h, d = files.pop("_layers")
        files["arch.yaml"] = arch % (h, d, rule)
        code, out, err = run_main("--root", make_repo(files), "check",
                                  "--format", "json")
        self.assertIn(code, (0, 1), out + err)
        return [(v["file"], v["line"]) for v in json.loads(out)], err

    def test_go_with_a_module_prefix(self):
        found, err = self.violations({
            "_layers": ("internal/handlers", "internal/db"),
            "go.mod": "module github.com/acme/app\n",
            "internal/handlers/h.go": 'package handlers\n\nimport (\n\t"fmt"\n\t"github.com/acme/app/internal/db"\n)\n',
            "internal/db/d.go": "package db\n"})
        self.assertEqual(found, [("internal/handlers/h.go", 5)])

    def test_java_package_layout(self):
        base = "src/main/java/com/acme"
        found, err = self.violations({
            "_layers": (base + "/handlers", base + "/db"),
            base + "/handlers/H.java": "package com.acme.handlers;\nimport static com.acme.db.Conn.*;\n",
            base + "/db/Conn.java": "package com.acme.db;\n"})
        self.assertEqual(found, [(base + "/handlers/H.java", 2)])

    def test_rust_crate_paths(self):
        found, err = self.violations({
            "_layers": ("src/handlers", "src/db"),
            "src/handlers/h.rs": "use crate::db::{self, Conn};\n",
            "src/db/mod.rs": "pub struct Conn;\n"})
        self.assertEqual(found, [("src/handlers/h.rs", 1)])

    def test_php_namespaces_and_requires(self):
        found, err = self.violations({
            "_layers": ("src/handlers", "src/db"),
            "src/handlers/H.php": "<?php\nrequire_once __DIR__ . '/../db/Conn.php';\n",
            "src/db/Conn.php": "<?php\n"})
        self.assertEqual(found, [("src/handlers/H.php", 2)])

    def test_ruby_require_relative(self):
        found, err = self.violations({
            "_layers": ("lib/handlers", "lib/db"),
            "lib/handlers/h.rb": "require_relative '../db/conn'\n",
            "lib/db/conn.rb": "x = 1\n"})
        self.assertEqual(found, [("lib/handlers/h.rb", 1)])

    def test_typescript_multiline_import(self):
        found, err = self.violations({
            "_layers": ("src/handlers", "src/db"),
            "src/handlers/h.ts": "import {\n  a,\n  b,\n} from '../db/conn';\n",
            "src/db/conn.ts": "export const a = 1;\n"})
        self.assertEqual(found, [("src/handlers/h.ts", 1)])


class TestNoImportsWarning(unittest.TestCase):
    ARCH = ('layers:\n  - name: a\n    paths: ["a/**"]\n'
            '  - name: b\n    paths: ["b/**"]\n'
            'rules:\n  - "%s"\n')

    def scan(self, files, rule="forbid-import: a -> b", *extra):
        files["arch.yaml"] = self.ARCH % rule
        return run_main("--root", make_repo(files), "check", *extra)

    def test_a_language_with_no_recognised_import_is_reported_once(self):
        code, out, err = self.scan({
            "a/one.go": "package a\n\nfunc One() {}\n",
            "a/two.go": "package a\n\nfunc Two() {}\n",
            "b/three.go": "package b\n"})
        self.assertEqual(code, 0, out + err)
        self.assertEqual(err.count("no import"), 1, err)
        self.assertIn("3 Go files", err)
        self.assertIn("Languages", err)

    def test_strict_makes_it_an_error(self):
        code, _out, err = self.scan({"a/one.go": "package a\n"},
                                   "forbid-import: a -> b", "--strict")
        self.assertEqual(code, 2, err)

    def test_no_warning_when_some_file_has_imports(self):
        code, out, err = self.scan({
            "a/one.go": 'package a\nimport "fmt"\n', "a/two.go": "package a\n",
            "b/three.go": "package b\n"})
        self.assertNotIn("no import", err)

    def test_no_warning_without_import_rules(self):
        code, out, err = self.scan({"a/one.go": "package a\n"},
                                  "forbid-pattern: a/**.go !~ TODO")
        self.assertNotIn("no import", err)

    def test_no_warning_for_python_or_with_strict_py_only(self):
        code, out, err = self.scan({"a/one.py": "x = 1\n"})
        self.assertNotIn("no import", err)
        code, out, err = self.scan({"a/one.go": "package a\n"},
                                  "forbid-import: a -> b", "--strict-py-only")
        self.assertNotIn("no import", err)


if __name__ == "__main__":
    unittest.main()
