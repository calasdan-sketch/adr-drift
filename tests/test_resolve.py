"""Import resolution that needs the repo's config: tsconfig aliases, workspace
packages, nested go.mod, composer psr-4, Python dynamic imports."""

import json
import unittest

from _support import make_repo, run_main

from adr_drift import resolve, scanner

ARCH = ('layers:\n  - name: handlers\n    paths: ["**/handlers/**"]\n'
        '  - name: db\n    paths: ["**/db/**"]\n'
        'rules:\n  - "forbid-import: handlers -> db"\n')


def resolver(files):
    return resolve.Resolver(make_repo(files))


def violations(files):
    root = make_repo(dict(files, **{"arch.yaml": ARCH}))
    code, out, err = run_main("--root", root, "check", "--format", "json",
                              "--adr-dir", "none")
    return code, [(v["file"], v["line"]) for v in json.loads(out)], err


class JsoncTest(unittest.TestCase):
    def test_comments_and_trailing_commas(self):
        text = ('{ // c\n "a": "x // not a comment", /* b */ "b": [1, 2,],\n'
                '}')
        self.assertEqual(json.loads(resolve.strip_jsonc(text)),
                         {"a": "x // not a comment", "b": [1, 2]})

    def test_unterminated_comment_and_string_do_not_hang(self):
        resolve.strip_jsonc('{"a": 1 /* never closed')
        resolve.strip_jsonc('{"a": "never closed')
        resolve.strip_jsonc("/" * 100000)


class TsAliasTest(unittest.TestCase):
    def test_star_alias_with_base_url(self):
        r = resolver({"web/tsconfig.json": json.dumps({"compilerOptions": {
            "baseUrl": ".", "paths": {"@/*": ["src/*"]}}})})
        self.assertEqual(r.js("web/src/handlers/a.ts", "@/db/conn"),
                         ["web.src.db.conn"])

    def test_paths_relative_to_the_config_without_base_url(self):
        r = resolver({"web/tsconfig.json": json.dumps({"compilerOptions": {
            "paths": {"~/*": ["./app/*"]}}})})
        self.assertEqual(r.js("web/x.ts", "~/db/c"), ["web.app.db.c"])

    def test_exact_alias_and_longest_prefix_wins(self):
        r = resolver({"tsconfig.json": json.dumps({"compilerOptions": {
            "baseUrl": ".", "paths": {
                "db": ["src/db/index.ts"], "@a/*": ["src/a/*"],
                "@a/special/*": ["src/special/*"]}}})})
        self.assertEqual(r.js("x.ts", "db"), ["src.db.index"])
        self.assertEqual(r.js("x.ts", "@a/special/q"), ["src.special.q"])
        self.assertEqual(r.js("x.ts", "@a/q"), ["src.a.q"])

    def test_first_existing_substitution(self):
        r = resolver({
            "tsconfig.json": json.dumps({"compilerOptions": {
                "baseUrl": ".", "paths": {"@/*": ["gen/*", "src/*"]}}}),
            "src/m.ts": ""})
        self.assertEqual(r.js("x.ts", "@/m"), ["src.m"])

    def test_bare_specifier_with_base_url_only_when_it_exists(self):
        r = resolver({"tsconfig.json": json.dumps(
            {"compilerOptions": {"baseUrl": "src"}}), "src/db/conn.ts": ""})
        self.assertEqual(r.js("x.ts", "db/conn"), ["src.db.conn"])
        self.assertEqual(r.js("x.ts", "react"), [])      # never guessed

    def test_extends_is_followed(self):
        r = resolver({
            "tsconfig.base.json": json.dumps({"compilerOptions": {
                "baseUrl": ".", "paths": {"@/*": ["lib/*"]}}}),
            "app/tsconfig.json": json.dumps({"extends": "../tsconfig.base"})})
        self.assertEqual(r.js("app/a.ts", "@/db/c"), ["lib.db.c"])

    def test_nearest_config_wins(self):
        r = resolver({
            "tsconfig.json": json.dumps({"compilerOptions": {
                "baseUrl": ".", "paths": {"@/*": ["root/*"]}}}),
            "pkg/tsconfig.json": json.dumps({"compilerOptions": {
                "baseUrl": ".", "paths": {"@/*": ["src/*"]}}})})
        self.assertEqual(r.js("pkg/a.ts", "@/x"), ["pkg.src.x"])
        self.assertEqual(r.js("other/a.ts", "@/x"), ["root.x"])

    def test_alias_cannot_escape_the_repo(self):
        r = resolver({"tsconfig.json": json.dumps({"compilerOptions": {
            "baseUrl": ".", "paths": {"@/*": ["../outside/*"]}}})})
        self.assertEqual(r.js("a.ts", "@/x"), [])

    def test_bad_config_warns_and_resolves_nothing(self):
        r = resolver({"tsconfig.json": "{not json"})
        self.assertEqual(r.js("a.ts", "@/x"), [])
        self.assertTrue(any("tsconfig.json" in w for w in r.warnings))

    def test_extends_loop_and_package_extends_are_reported(self):
        r = resolver({
            "a.json": json.dumps({"extends": "./b.json"}),
            "b.json": json.dumps({"extends": "./a.json"}),
            "tsconfig.json": json.dumps({"extends": ["./a.json",
                                                     "@tsconfig/node18"]})})
        self.assertEqual(r.js("x.ts", "@/y"), [])
        self.assertTrue(r.warnings)

    def test_huge_config_is_refused(self):
        r = resolver({"tsconfig.json": " " * (resolve._MAX_BYTES + 10)})
        self.assertEqual(r.js("a.ts", "x"), [])
        self.assertTrue(r.warnings)

    def test_windows_importer_path(self):
        r = resolver({"web/tsconfig.json": json.dumps({"compilerOptions": {
            "baseUrl": ".", "paths": {"@/*": ["src/*"]}}})})
        self.assertEqual(r.js("web\\src\\a.ts", "@/db/c"), ["web.src.db.c"])


class WorkspaceTest(unittest.TestCase):
    FILES = {
        "package.json": json.dumps({"workspaces": ["packages/*"]}),
        "packages/db/package.json": json.dumps({"name": "@acme/db"}),
        "packages/web/package.json": json.dumps({"name": "@acme/web"}),
        "node_modules/x/package.json": json.dumps({"name": "x"}),
    }

    def test_package_name_resolves_to_its_directory(self):
        r = resolver(self.FILES)
        self.assertEqual(r.js("packages/web/a.ts", "@acme/db"),
                         ["packages.db"])
        self.assertEqual(r.js("packages/web/a.ts", "@acme/db/src/conn"),
                         ["packages.db.src.conn"])
        self.assertEqual(r.js("a.ts", "@acme/dbx"), [])   # not a prefix match

    def test_pnpm_workspace_file(self):
        r = resolver({
            "pnpm-workspace.yaml": "packages:\n  - 'libs/*'\n",
            "libs/core/package.json": json.dumps({"name": "core"})})
        self.assertEqual(r.js("a.ts", "core/x"), ["libs.core.x"])

    def test_escaping_pattern_is_ignored(self):
        r = resolver({"package.json": json.dumps(
            {"workspaces": ["../*", "!x", "/etc/*", "C:/x/*"]})})
        self.assertEqual(r.js("a.ts", "anything"), [])


class GoPhpTest(unittest.TestCase):
    def test_nearest_go_mod(self):
        r = resolver({"svc/go.mod": "module example.com/svc\n",
                      "go.mod": "module example.com/root\n"})
        self.assertEqual(r.go("svc/internal/h/a.go",
                              "example.com/svc/internal/db"),
                         "svc/internal/db")
        self.assertEqual(r.go("cmd/a.go", "example.com/root/internal/db"),
                         "internal/db")
        self.assertEqual(r.go("svc/a.go", "fmt"), "fmt")
        self.assertEqual(r.go("svc/a.go", "example.com/other/x"),
                         "example.com/other/x")

    def test_no_go_mod_leaves_imports_alone(self):
        r = resolver({})
        self.assertEqual(r.go("a.go", "example.com/x/y"), "example.com/x/y")

    def test_psr4(self):
        r = resolver({"php/composer.json": json.dumps({"autoload": {
            "psr-4": {"App\\": "src/", "App\\Tests\\": ["tests/"]}}})})
        self.assertEqual(r.php("php/src/H/a.php", "App.Db.Conn"),
                         ["php.src.Db.Conn"])
        self.assertEqual(r.php("php/src/H/a.php", "App.Tests.T"),
                         ["php.tests.T"])
        self.assertEqual(r.php("php/src/H/a.php", "Vendor.Db.Conn"), [])
        self.assertEqual(r.php("elsewhere/a.php", "App.Db"), [])


class PythonDynamicTest(unittest.TestCase):
    def imports(self, src):
        return [m for m, _n in scanner.python_imports(src, "a.py")]

    def test_importlib_and_dunder(self):
        got = self.imports("import importlib\n"
                           "importlib.import_module('db.conn')\n"
                           "__import__('db.other')\n"
                           "from importlib import import_module\n"
                           "import_module('db.third')\n")
        for m in ("db.conn", "db.other", "db.third"):
            self.assertIn(m, got)

    def test_not_imports(self):
        got = self.imports("registry.import_module('db.conn')\n"
                           "importlib.import_module(name)\n"
                           "importlib.import_module('.rel', 'pkg')\n"
                           "importlib.import_module()\n"
                           "importlib.import_module('')\n")
        self.assertEqual(got, [])


class EndToEndTest(unittest.TestCase):
    def test_alias_violation_found(self):
        code, got, _err = violations({
            "ts/tsconfig.json": json.dumps({"compilerOptions": {
                "baseUrl": ".", "paths": {"@/*": ["src/*"]}}}),
            "ts/src/handlers/a.ts": 'import x from "@/db/conn";\n',
            "ts/src/handlers/b.ts": 'import x from "@/handlers/other";\n',
            "ts/src/db/conn.ts": "export const q = 1;\n"})
        self.assertEqual((code, got), (1, [("ts/src/handlers/a.ts", 1)]))

    def test_nested_go_mod_violation_found(self):
        code, got, _err = violations({
            "go/go.mod": "module example.com/app\n",
            "go/internal/handlers/h.go":
                'package h\nimport "example.com/app/internal/db"\n',
            "go/internal/db/d.go": "package db\n"})
        self.assertEqual((code, got), (1, [("go/internal/handlers/h.go", 2)]))

    def test_other_module_is_not_a_violation(self):
        code, got, _err = violations({
            "go/go.mod": "module example.com/app\n",
            "go/internal/handlers/h.go":
                'package h\nimport "github.com/other/app/internal/db"\n',
            "go/internal/db/d.go": "package db\n"})
        self.assertEqual(got, [])

    def test_python_dynamic_import_violation(self):
        code, got, _err = violations({
            "py/handlers/h.py":
                "import importlib\nm = importlib.import_module('db.conn')\n",
            "py/db/conn.py": "x = 1\n"})
        self.assertEqual(got, [("py/handlers/h.py", 2)])


if __name__ == "__main__":
    unittest.main()
