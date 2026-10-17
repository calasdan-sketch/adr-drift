"""Import extraction tests: AST precision + generic fallback."""

import unittest

from adr_drift import scanner as s


class TestPythonImports(unittest.TestCase):
    def test_basic(self):
        src = "import os\nfrom db.drivers import pg\n"
        mods = dict(s.python_imports(src))
        self.assertIn("os", mods)
        self.assertIn("db.drivers", mods)

    def test_line_numbers(self):
        src = "import os\n\nfrom db.drivers import pg\n"
        d = dict(s.python_imports(src))
        self.assertEqual(d["db.drivers"], 3)

    def test_relative_is_local(self):
        src = "from . import sib\nfrom ..pkg import thing\n"
        mods = [m for m, _ in s.python_imports(src)]
        self.assertEqual(mods, ["<local>", "<local>"])

    def test_ignores_comments_and_strings(self):
        src = ('# import fake_mod\n'
               'x = "from notreal import y"\n'
               "import real_mod\n")
        mods = [m for m, _ in s.python_imports(src)]
        self.assertEqual(mods, ["real_mod"])

    def test_syntax_error_raises(self):
        # python_imports lets SyntaxError propagate; extract_imports
        # turns it into a CI-visible warning (see TestExtract).
        with self.assertRaises(SyntaxError):
            s.python_imports("def broken(:\n")


class TestGenericImports(unittest.TestCase):
    def test_typescript(self):
        src = 'import { x } from "./utils";\nimport fs from "fs";\n'
        mods = [m for m, _ in s.generic_imports(src)]
        self.assertIn("./utils", mods)
        self.assertIn("fs", mods)

    def test_go(self):
        src = 'import "fmt"\nimport (\n\t"os"\n)\n'
        mods = [m for m, _ in s.generic_imports(src)]
        self.assertIn("fmt", mods)

    def test_rust_use(self):
        src = "use std::collections::HashMap;\n"
        mods = [m for m, _ in s.generic_imports(src)]
        self.assertTrue(any("std" in m for m in mods))


class TestExtract(unittest.TestCase):
    def test_engine_labels(self):
        imps, eng = s.extract_imports("a.py", "import os\n")
        self.assertEqual(eng, "ast")
        imps, eng = s.extract_imports("a.ts", 'import x from "y";\n')
        self.assertEqual(eng, "regex")
        imps, eng = s.extract_imports("a.ts", 'import x from "y";\n',
                                      strict_py_only=True)
        self.assertEqual(eng, "none")
        imps, eng = s.extract_imports("a.txt", "hello")
        self.assertEqual(eng, "none")

    def test_syntax_error_warns_on_stderr(self):
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            imps, eng = s.extract_imports("src/bad.py", "def broken(:\n")
        self.assertEqual(imps, [])
        self.assertEqual(eng, "ast")
        err = buf.getvalue()
        self.assertIn("src/bad.py", err)
        self.assertIn("warning", err.lower())
        self.assertIn("syntax error", err.lower())



class TestRelativeResolution(unittest.TestCase):
    def test_relative_resolves_against_file_package(self):
        src = "from ..db.drivers import pg\nfrom . import sib\n"
        mods = [m for m, _ in s.python_imports(src, "src/handlers/a.py")]
        self.assertIn("src.db.drivers", mods)
        self.assertIn("src.db.drivers.pg", mods)
        self.assertIn("src.handlers.sib", mods)

    def test_relative_above_root_is_local(self):
        mods = [m for m, _ in s.python_imports("from ... import x\n", "a.py")]
        self.assertEqual(mods, ["<local>"])

    def test_windows_separators(self):
        mods = [m for m, _ in s.python_imports(
            "from .x import y\n", "pkg\\sub\\a.py")]
        self.assertIn("pkg.sub.x.y", mods)


if __name__ == "__main__":
    unittest.main()
