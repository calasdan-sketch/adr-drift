"""Import extraction: precise for Python (AST), heuristic for the rest.

Python files get real AST parsing — no regex false positives from comments
or strings. Other languages (JS/TS/Go/Rust/Java/…) use a regex fallback
that is documented as heuristic: it can miss exotic forms and may catch
an import inside a comment. check --strict-py-only disables the fallback.
"""

from __future__ import annotations

import ast
import os
import posixpath
import re
import sys

PY_EXTS = {".py"}
GENERIC_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
                ".go", ".rs", ".java", ".rb", ".php"}

JS_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}


def _resolve_js(path: str, spec: str) -> str:
    """"./config.js" in src/a/b.ts -> "src.a.config": resolved against the
    importing file and dotted like a Python module. The extension is
    dropped, so TS's "./x.js means x.ts" convention and extensionless
    specs agree."""
    if not spec.startswith(("./", "../")):
        return spec
    p = posixpath.normpath(posixpath.join(
        posixpath.dirname(path.replace("\\", "/")), spec))
    if p.startswith(".."):
        return spec
    root, ext = posixpath.splitext(p)
    return (root if ext.lower() in JS_EXTS else p).replace("/", ".")


# import x.y / from x.y import z / require("x") / import "x" / use x::y …
_GENERIC_IMPORT_RES = [
    re.compile(r'^\s*import\s+(?:type\s+)?[{*]?[^"\']*?from\s+["\']([^"\']+)["\']'),
    re.compile(r'^\s*import\s+["\']([^"\']+)["\']'),
    re.compile(r'require\(\s*["\']([^"\']+)["\']\s*\)'),
    re.compile(r'^\s*use\s+([a-zA-Z0-9_:]+)(?:::|\s*[{;])'),
    re.compile(r'^\s*import\s+\(\s*["\']([^"\']+)["\']'),
    re.compile(r'^\s*import\s+([a-zA-Z0-9_./@-]+)\s*;?\s*$'),
]


def _module_of(path: str) -> tuple[list[str], bool]:
    """Dotted parts of a repo-relative .py path, and whether it's a package."""
    parts = path.replace("\\", "/").strip("/")[:-3].split("/")
    if parts[-1] == "__init__":
        return parts[:-1], True
    return parts, False


def python_imports(source: str, path: str = "") -> list[tuple[str, int]]:
    """(module, line_no) via AST.

    `from m import n` yields both "m" and "m.n" so rules naming either the
    module or the imported submodule match. Relative imports resolve against
    the package of `path` (repo-relative); without a path, or when they climb
    above the repo root, they resolve to "<local>".

    Raises SyntaxError on unparseable source — callers that know the file
    path (extract_imports) turn that into a CI-visible warning instead of
    silently passing the file on every import rule.
    """
    found: list[tuple[str, int]] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.append((a.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module
            if node.level:
                pkg, is_pkg = _module_of(path) if path else ([], False)
                if not is_pkg:
                    pkg = pkg[:-1]
                up = node.level - 1
                if not path or up > len(pkg):
                    found.append(("<local>", node.lineno))
                    continue
                base = pkg[:len(pkg) - up]
                mod = ".".join(base + ([mod] if mod else []))
            if not mod:
                found.append(("<local>", node.lineno))
                continue
            found.append((mod, node.lineno))
            for a in node.names:
                if a.name != "*":
                    found.append((f"{mod}.{a.name}", node.lineno))
    return found


def generic_imports(source: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for i, line in enumerate(source.splitlines(), 1):
        for rx in _GENERIC_IMPORT_RES:
            m = rx.search(line)
            if m:
                found.append((m.group(1), i))
                break
    return found


def extract_imports(path: str, source: str | None = None,
                     strict_py_only: bool = False) -> tuple[list[tuple[str, int]], str]:
    """Returns (imports, engine) where engine is "ast", "regex", or "none"."""
    ext = os.path.splitext(path)[1].lower()
    if source is None:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                source = f.read()
        except OSError:
            return [], "none"
    if ext in PY_EXTS:
        try:
            return python_imports(source, path), "ast"
        except SyntaxError as e:
            # CI-visible: an unparseable file must not silently pass
            # every import rule.
            print(f"warning: {path}: Python syntax error ({e}); "
                  f"skipping import checks for this file", file=sys.stderr)
            return [], "ast"
    if ext in GENERIC_EXTS and not strict_py_only:
        imports = generic_imports(source)
        if ext in JS_EXTS:
            imports = [(_resolve_js(path, m), n) for m, n in imports]
        return imports, "regex"
    return [], "none"


def iter_source_files(root: str) -> list[str]:
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv",
                 "dist", "build", ".tox", "vendor"}
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in PY_EXTS or ext in GENERIC_EXTS:
                out.append(os.path.join(dirpath, fn))
    return sorted(out)
