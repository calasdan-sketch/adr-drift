"""Import extraction: precise for Python (AST), heuristic for the rest.

Python files get real AST parsing — no regex false positives from comments
or strings. Other languages (JS/TS/Go/Rust/Java/Ruby/PHP, see langs.py) use
a regex extractor that is documented as heuristic: it ignores comments and
string literals and reads multi-line statements, but it is not a parser and
can miss exotic forms. check --strict-py-only disables it.
"""

from __future__ import annotations

import ast
import io
import os
import re
import sys
import tokenize

from . import langs

PY_EXTS = {".py"}
GENERIC_EXTS = set(langs.LANG_BY_EXT)

#: Everything ast.parse can raise for source it cannot (or should not) read:
#: SyntaxError (also a bad encoding or coding cookie), ValueError (a NUL
#: byte, before 3.12), RecursionError (deeply nested expressions; the depth
#: depends on the interpreter), MemoryError and OverflowError.
PARSE_ERRORS = (SyntaxError, ValueError, RecursionError, MemoryError,
                OverflowError)

_BOM = "\U0000feff"
_NEWLINE_RE = re.compile(r"\r\n|\r|\n")


def decode_source(path: str, data) -> str:
    """Text of a source file, as its language would read it.

    A UTF-8 BOM is dropped (Windows editors add one; left in, it defeats
    `^` anchors), a Python file's PEP 263 coding cookie is honoured, and
    bytes that do not decode become U+FFFD instead of failing the run.
    """
    if isinstance(data, str):
        return data[1:] if data.startswith(_BOM) else data
    if os.path.splitext(path)[1].lower() in PY_EXTS:
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
            return data.decode(encoding, "replace")
        except (SyntaxError, LookupError, ValueError):
            pass  # unknown / conflicting cookie: ast.parse reports it
    return data.decode("utf-8-sig", "replace")


def split_lines(text: str) -> "list[str]":
    """Lines numbered as an editor numbers them: only \\n, \\r\\n and \\r
    end a line (str.splitlines() also splits on form feeds, U+2028 and
    friends, which would shift every later line number)."""
    lines = _NEWLINE_RE.split(text)
    if lines and lines[-1] == "":
        lines.pop()  # a trailing newline does not start another line
    return lines


def describe_parse_error(e: BaseException) -> str:
    """One short phrase for why a file could not be parsed."""
    if isinstance(e, SyntaxError):
        where = f", line {e.lineno}" if getattr(e, "lineno", None) else ""
        return f"syntax error: {e.msg}{where}"
    if isinstance(e, RecursionError):
        return "nested too deeply to parse"
    if isinstance(e, MemoryError):
        return "out of memory while parsing"
    if isinstance(e, langs.ExtractionLimit):
        return str(e)
    return f"{type(e).__name__}: {e}"


def _module_of(path: str) -> tuple[list[str], bool]:
    """Dotted parts of a repo-relative .py path, and whether it's a package."""
    parts = path.replace("\\", "/").strip("/")[:-3].split("/")
    if parts[-1] == "__init__":
        return parts[:-1], True
    return parts, False


def _dynamic_import_name(call: "ast.Call") -> str:
    """The module named by `importlib.import_module("m")`, `import_module("m")`
    or `__import__("m")` when it is a string literal and absolute; else "".
    A call on some other object (`registry.import_module("m")`) is not an
    import, and a computed name cannot be known."""
    fn = call.func
    if isinstance(fn, ast.Attribute):
        ok = (fn.attr == "import_module" and isinstance(fn.value, ast.Name)
              and fn.value.id == "importlib")
    else:
        ok = isinstance(fn, ast.Name) and fn.id in ("import_module",
                                                    "__import__")
    if not ok or not call.args:
        return ""
    first = call.args[0]
    if (isinstance(first, ast.Constant) and isinstance(first.value, str)
            and first.value and not first.value.startswith(".")):
        return first.value
    return ""


def python_imports(source, path: str = "") -> list[tuple[str, int]]:
    """(module, line_no) via AST. `source` is bytes (BOM and coding cookie
    honoured, as Python itself does) or str.

    `from m import n` yields both "m" and "m.n" so rules naming either the
    module or the imported submodule match. Relative imports resolve against
    the package of `path` (repo-relative); without a path, or when they climb
    above the repo root, they resolve to "<local>".

    Raises one of PARSE_ERRORS on source that cannot be parsed — callers
    that know the file path (extract_imports) turn that into a CI-visible
    warning instead of silently passing the file on every import rule.
    """
    found: list[tuple[str, int]] = []
    if isinstance(source, str) and source.startswith(_BOM):
        source = source[1:]
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dynamic_import_name(node)
            if name:
                found.append((name, node.lineno))
        elif isinstance(node, ast.Import):
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


def generic_imports(source: str, path: str = "",
                    resolve_js=None) -> list[tuple[str, int]]:
    """(module, line_no) for a non-Python source file; the language comes
    from `path`'s extension (JavaScript/TypeScript when there is none)."""
    return langs.extract(path, source, resolve_js=resolve_js)


def extract_imports(path: str, source=None, strict_py_only: bool = False,
                    diag=None, go_module: str = "", resolver=None
                    ) -> tuple[list[tuple[str, int]], str]:
    """Returns (imports, engine) where engine is "ast", "regex", or "none".

    `source` is the file's bytes (preferred: a BOM or coding cookie is then
    honoured) or its text. A file that cannot be parsed yields no imports and
    a warning - to `diag` (which --strict can turn into a failure), or to
    stderr when there is none - because it must not silently pass every
    import rule. `go_module` is the module path from go.mod: Go imports of
    the repo's own packages are returned relative to the repo root.
    `resolver` (resolve.Resolver) adds what only the repository's config
    files can say: tsconfig aliases, workspace packages, the nearest go.mod,
    composer psr-4 namespaces.
    """
    ext = os.path.splitext(path)[1].lower()
    if source is None:
        try:
            with open(path, "rb") as f:
                source = f.read()
        except OSError:
            return [], "none"

    def unparsable(what: str, e: BaseException) -> None:
        message = (f"{path}: unparsable {what} ({describe_parse_error(e)}); "
                   "its imports are NOT checked")
        if diag is not None:
            diag.warn(message)
        else:
            print(f"warning: {message}", file=sys.stderr)

    if ext in PY_EXTS:
        try:
            return python_imports(source, path), "ast"
        except PARSE_ERRORS as e:
            unparsable("Python", e)
            return [], "ast"
    if ext in GENERIC_EXTS and not strict_py_only:
        try:
            imports = generic_imports(
                decode_source(path, source), path,
                resolver.js if resolver is not None else None)
        except (RecursionError, MemoryError, langs.ExtractionLimit) as e:
            unparsable(langs.LANG_NAMES[langs.language_of(path)], e)
            return [], "regex"
        if ext == ".go":
            if resolver is not None:
                imports = [(resolver.go(path, m), n) for m, n in imports]
            else:
                imports = langs.strip_go_module(imports, go_module)
        elif ext == ".php" and resolver is not None:
            extra = [(r, n) for m, n in imports for r in resolver.php(path, m)]
            imports = imports + extra
        return imports, "regex"
    return [], "none"


DEFAULT_MAX_FILE_BYTES = 4 * 1024 * 1024


def max_file_bytes() -> int:
    """Largest source file read, in bytes (ADR_DRIFT_MAX_FILE_BYTES; 0 = no
    limit). A generated or minified file of hundreds of megabytes would
    otherwise be read whole into memory (an 80 MB file ran a small CI
    runner out of memory)."""
    try:
        return max(0, int(os.environ.get("ADR_DRIFT_MAX_FILE_BYTES",
                                         DEFAULT_MAX_FILE_BYTES)))
    except ValueError:
        return DEFAULT_MAX_FILE_BYTES


def _inside(root_real: str, path: str) -> bool:
    real = os.path.realpath(path)
    return real == root_real or real.startswith(root_real.rstrip(os.sep) + os.sep)


def iter_source_files(root: str, skipped: "list | None" = None) -> list[str]:
    """Source files under `root`. A symbolic link whose target is outside
    `root` is not read (a pull request could otherwise make a pattern rule
    read, and so probe, any file of the CI machine); it is added to
    `skipped` as (path, reason) so the caller can warn."""
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv",
                 "dist", "build", ".tox", "vendor"}
    out: list[str] = []
    root_real = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in PY_EXTS or ext in GENERIC_EXTS:
                full = os.path.join(dirpath, fn)
                if os.path.islink(full) and not _inside(root_real, full):
                    if skipped is not None:
                        skipped.append((os.path.relpath(full, root).replace(
                            os.sep, "/"),
                            "symbolic link to a file outside the repository"))
                    continue
                out.append(full)
    return sorted(out)
