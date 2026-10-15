"""Import extraction: precise for Python (AST), heuristic for the rest.

Python files get real AST parsing — no regex false positives from comments
or strings. Other languages (JS/TS/Go/Rust/Java/…) use a regex fallback
that is documented as heuristic: it can miss exotic forms and may catch
an import inside a comment. check --strict-py-only disables the fallback.
"""

from __future__ import annotations

import ast
import os
import re
import sys

PY_EXTS = {".py"}
GENERIC_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
                ".go", ".rs", ".java", ".rb", ".php"}

# import x.y / from x.y import z / require("x") / import "x" / use x::y …
_GENERIC_IMPORT_RES = [
    re.compile(r'^\s*import\s+(?:type\s+)?[{*]?[^"\']*?from\s+["\']([^"\']+)["\']'),
    re.compile(r'^\s*import\s+["\']([^"\']+)["\']'),
    re.compile(r'require\(\s*["\']([^"\']+)["\']\s*\)'),
    re.compile(r'^\s*use\s+([a-zA-Z0-9_:]+)(?:::|\s*[{;])'),
    re.compile(r'^\s*import\s+\(\s*["\']([^"\']+)["\']'),
    re.compile(r'^\s*import\s+([a-zA-Z0-9_./@-]+)\s*;?\s*$'),
]


def python_imports(source: str) -> list[tuple[str, int]]:
    """(module, line_no) via AST. Relative imports resolve to "<local>".

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
            if node.level:
                found.append(("<local>", node.lineno))
            elif node.module:
                found.append((node.module, node.lineno))
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
            return python_imports(source), "ast"
        except SyntaxError as e:
            # CI-visible: an unparseable file must not silently pass
            # every import rule.
            print(f"warning: {path}: Python syntax error ({e}); "
                  f"skipping import checks for this file", file=sys.stderr)
            return [], "ast"
    if ext in GENERIC_EXTS and not strict_py_only:
        return generic_imports(source), "regex"
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
