"""Import extraction for the languages that are not Python.

Heuristic by design - a small tokenizer and a few statement shapes, not a
parser - but careful in the ways that matter for architecture rules:

* comments never count, and neither does an `import` inside a string
  literal (strings are single tokens);
* statements that span several lines (Go's `import ( ... )`, JS
  `import { a,\\n b } from "m"`, Rust `use a::{b, c};`) are read whole;
* a module is returned the way rules spell it: dotted for Rust and PHP
  (`crate::db::x` -> `db.x`, `App\\Models\\User` -> `App.Models.User`),
  relative paths resolved against the importing file;
* every scan takes time linear in the size of the file, whatever it
  contains: a string that cannot close, a run of `)use`, a `use` list that
  never ends. (A file that would need more work than a file of its size
  should is refused as ExtractionLimit, reported as unparsable.)

README, "Languages: exact support", lists what each language covers.
"""

from __future__ import annotations

import posixpath
import re

#: file extension -> language key
LANG_BY_EXT = {
    ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js", ".mjs": "js",
    ".cjs": "js", ".go": "go", ".rs": "rust", ".java": "java",
    ".php": "php", ".rb": "ruby",
}

#: language key -> name used in messages
LANG_NAMES = {
    "js": "JavaScript/TypeScript", "go": "Go", "rust": "Rust",
    "java": "Java", "php": "PHP", "ruby": "Ruby",
}

JS_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})


def language_of(path: str) -> "str | None":
    return LANG_BY_EXT.get(posixpath.splitext(path)[1].lower())


class ExtractionLimit(Exception):
    """A file asks for more work than a fair file of its size. The caller
    (scanner.extract_imports) reports it as unparsable, like a file that
    nests too deeply, instead of stalling the run."""


# -- tokenizer ---------------------------------------------------------------

# A string literal is read in two steps, so that no input makes the scan
# quadratic. The regex takes the plain case in one match: the opening quote,
# text with no backslash (and, for " and ', no line break), the closing
# quote. Such a scan stops at the first quote, backslash or line break after
# its opener, so two of them never overlap. Every other opening quote - the
# string has an escape in it, or it does not close - comes out of the regex
# alone (group q) and _string_end() reads on from there.
_PLAIN = {'"': r'"[^"\\\n]*"', "'": r"'[^'\\\n]*'", "`": r"`[^`\\]*`"}

# What can end a string or change how it is read: its quote, a backslash (it
# escapes the next character, whatever that is) and, for " and ', a line
# break. A backtick string (JS template literal, Go raw string) may span lines.
_STRING_STOPS = {
    '"': re.compile(r'["\\\n]'),
    "'": re.compile(r"['\\\n]"),
    "`": re.compile(r"[`\\]"),
}

# An unterminated block comment runs to the end of the file (that is what a
# compiler does with it), and consuming it in one match keeps the scan
# linear.
_C_COMMENT = r"/\*.*?(?:\*/|\Z)|//[^\n]*"


def _tokenizer(comment: str, quotes: str) -> "re.Pattern":
    # n: newline, c: comment, s: string literal without escapes, q: any other
    # opening quote, w: word, p: one punctuation mark. Other whitespace
    # matches nothing and is skipped.
    plain = "|".join(_PLAIN[q] for q in quotes)
    return re.compile(
        rf"(?P<n>\n)|(?P<c>{comment})|(?P<s>{plain})|(?P<q>[{quotes}])"
        rf"|(?P<w>[\w$]+)|(?P<p>::|[^\s\w$])", re.S)


_TOKENIZERS = {
    "js": _tokenizer(_C_COMMENT, "\"'`"),
    "go": _tokenizer(_C_COMMENT, "\"'`"),
    "java": _tokenizer(_C_COMMENT, "\"'"),
    "rust": _tokenizer(_C_COMMENT, '"'),
    "php": _tokenizer(_C_COMMENT + r"|\#(?!\[)[^\n]*", "\"'"),
    "ruby": _tokenizer(r"\#[^\n]*", "\"'"),
}


def _string_end(text: str, start: int) -> "tuple[int, int]":
    """Read the string literal whose opening quote is text[start].

    Returns (end, 0) with `end` just past the closing quote, or (-1, stop)
    when the string does not close; `stop` is where reading failed (a line
    break, the end of the text). The quote is then plain punctuation, so a
    regex literal or an apostrophe cannot swallow the code after it.

    Every quote of the same kind between `start` and `stop` fails as well:
    the reading went over it as the second character of an escape (a quote
    that closes would have ended the string there), and from that point the
    two readings are one and the same. The caller keeps `stop`, so a line
    made of escaped quotes is read once, not once per quote.
    """
    quote = text[start]
    find = _STRING_STOPS[quote].search
    size = len(text)
    i = start + 1
    while True:
        m = find(text, i)
        if m is None:
            return -1, size
        k = m.start()
        c = text[k]
        if c == quote:
            return k + 1, 0
        if c != "\\":                    # a line break
            return -1, k
        if k + 1 >= size:                # a backslash at the very end
            return -1, size
        i = k + 2


def _blank_ruby_blocks(text: str) -> str:
    """=begin ... =end block comments -> empty lines (numbering kept)."""
    if "=begin" not in text:
        return text
    out, inside = [], False
    for line in text.split("\n"):
        if inside:
            inside = not line.startswith("=end")
            out.append("")
        elif line.startswith("=begin"):
            inside = True
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


class _Tokens:
    """kind ("w" word, "s" string, "p" punctuation), text, line, first-on-
    line flag of every token that is not a comment."""

    def __init__(self, text: str, lang: str) -> None:
        if lang == "ruby":
            text = _blank_ruby_blocks(text)
        self.kind: "list[str]" = []
        self.text: "list[str]" = []
        self.line: "list[int]" = []
        self.first: "list[bool]" = []
        line, fresh = 1, True            # fresh: no token yet on this line
        pattern = _TOKENIZERS[lang]
        add_kind, add_text = self.kind.append, self.text.append
        add_line, add_first = self.line.append, self.first.append
        dead: "dict[str, int]" = {}      # quote -> no string of it opens before
        pos = 0
        while pos >= 0:
            resume = -1                  # where to go on after a long string
            for m in pattern.finditer(text, pos):
                kind = m.lastgroup
                if kind == "n":
                    line += 1
                    fresh = True
                    continue
                tok = m.group()
                if kind == "c":              # code after a comment is still the
                    if "\n" in tok:          # first code on its line
                        line += tok.count("\n")
                        fresh = True
                    continue
                if kind == "q":
                    start = m.start()
                    end = -1
                    if start >= dead.get(tok, 0):
                        end, stop = _string_end(text, start)
                        if end < 0:
                            dead[tok] = stop
                    if end < 0:
                        kind = "p"           # a quote that opens nothing
                    else:
                        kind, tok, resume = "s", text[start:end], end
                add_kind(kind)
                add_text(tok)
                add_line(line)
                add_first(fresh)
                fresh = False
                if kind == "s" and "\n" in tok:
                    line += tok.count("\n")
                if resume >= 0:
                    break
            pos = resume
        self.n = len(self.kind)
        # Work done on `use` trees so far, and what a file of this size may
        # spend on them (see _spend).
        self.work = 0
        self.budget = _WORK_PER_TOKEN * self.n + _WORK_BASE

    def word(self, i: int) -> str:
        """The word at i, else ""."""
        return self.text[i] if 0 <= i < self.n and self.kind[i] == "w" else ""

    def punct(self, i: int) -> str:
        return self.text[i] if 0 <= i < self.n and self.kind[i] == "p" else ""

    def string(self, i: int) -> "str | None":
        """The contents of the string literal at i, else None."""
        if 0 <= i < self.n and self.kind[i] == "s":
            return self.text[i][1:-1]
        return None

    def starts_statement(self, i: int, after=(";",)) -> bool:
        """First token on its line, or right after one of `after`."""
        return self.first[i] or (i > 0 and self.text[i - 1] in after
                                 and self.kind[i - 1] == "p")


def _resolve_relative(path: str, spec: str, drop_exts=JS_EXTS) -> str:
    """`spec` (a path relative to the importing file) as a dotted module of
    the repo: "../db/x.js" in src/a/b.ts -> "src.db.x". The extension is
    dropped when it is a source extension, so TS's "./x.js means x.ts"
    convention and extensionless specs agree. A path that climbs out of the
    repo is returned unchanged."""
    p = posixpath.normpath(posixpath.join(
        posixpath.dirname(path.replace("\\", "/")), spec))
    if p.startswith(".."):
        return spec
    root, ext = posixpath.splitext(p)
    return (root if ext.lower() in drop_exts else p).replace("/", ".")


# -- JavaScript / TypeScript -------------------------------------------------

def _js_clause_end(t: _Tokens, j: int) -> int:
    """Index of the module string of an `import <clause> from "m"` /
    `export <clause> from "m"` whose clause starts at token j, else -1. A
    clause is names, commas, `*`, `as` and one level of braces."""
    depth = 0
    while j < t.n:
        kind, text = t.kind[j], t.text[j]
        if kind == "w":
            if text in ("import", "export"):
                return -1
            if text == "from" and depth == 0 and t.kind[j + 1:j + 2] == ["s"]:
                return j + 1
        elif kind == "p":
            if text == "{":
                depth += 1
            elif text == "}":
                depth -= 1
                if depth < 0:
                    return -1
            elif text not in (",", "*"):
                return -1
        elif depth == 0:
            return -1
        j += 1
    return -1


def _js(path: str, t: _Tokens) -> "list[tuple[str, int]]":
    found = []

    def add(spec: str, i: int) -> None:
        if spec.startswith(("./", "../")):
            spec = _resolve_relative(path, spec)
        found.append((spec, t.line[i]))

    for i in range(t.n):
        word = t.word(i)
        if not word or t.punct(i - 1) == ".":     # obj.import, app.require
            continue
        if word == "import":
            if t.string(i + 1) is not None:                 # import "m"
                add(t.string(i + 1), i)
            elif t.punct(i + 1) == "(":                      # import("m")
                if t.string(i + 2) is not None:
                    add(t.string(i + 2), i)
            else:                                            # import x from "m"
                end = _js_clause_end(t, i + 1)
                if end >= 0:
                    add(t.string(end), i)
        elif word == "export":
            j = i + 1 + (t.word(i + 1) == "type")
            if t.punct(j) in ("*", "{"):
                end = _js_clause_end(t, j)
                if end >= 0:
                    add(t.string(end), i)
        elif word == "require":
            if (t.punct(i + 1) == "(" and t.string(i + 2) is not None
                    and t.punct(i + 3) == ")"):
                add(t.string(i + 2), i)
    return found


# -- Go ----------------------------------------------------------------------

def _go(path: str, t: _Tokens) -> "list[tuple[str, int]]":
    found = []
    for i in range(t.n):
        if t.word(i) != "import" or not t.starts_statement(i):
            continue
        if t.punct(i + 1) == "(":
            j = i + 2
            while j < t.n and t.punct(j) != ")":
                spec = t.string(j)
                if spec is not None:
                    found.append((spec, t.line[j]))
                elif t.kind[j] == "p" and t.text[j] not in (".", ";"):
                    break                      # not an import group after all
                elif t.word(j) == "import":
                    break
                j += 1
        else:
            j = i + 1
            if t.word(j) or t.punct(j) == ".":   # alias: name, _ or .
                j += 1
            if t.string(j) is not None:
                found.append((t.string(j), t.line[i]))
    return found


# -- Java --------------------------------------------------------------------

def _java(path: str, t: _Tokens) -> "list[tuple[str, int]]":
    found = []
    for i in range(t.n):
        if t.word(i) != "import" or not t.starts_statement(i):
            continue
        j = i + 1 + (t.word(i + 1) == "static")
        parts: "list[str]" = []
        while t.word(j):
            parts.append(t.text[j])
            if t.punct(j + 1) != ".":
                j += 1
                break
            j += 2
            if t.punct(j) == "*":          # wildcard: the package itself
                j += 1
                break
        if parts and t.punct(j) == ";":
            found.append((".".join(parts), t.line[i]))
    return found


# -- `use` trees (Rust and PHP) ---------------------------------------------

_MAX_USE_DEPTH = 16   # real code nests braces two or three deep

#: What a file may spend on reading `use` trees: a number of steps per token
#: plus a base. A fair file spends about one step per token (each `use` is
#: read once); the margin is wide.
_WORK_PER_TOKEN = 8
_WORK_BASE = 10_000


def _spend(t: _Tokens, steps: int) -> None:
    """Count work done on `use` trees; give up when a file has spent far more
    than a fair file of its size. A `use` list that never ends (`use a,` on
    every line) is one statement that reaches to the end of the file, read
    again from every one of its `use`: that is quadratic, and a run must not
    stall on one hostile file."""
    t.work += steps
    if t.work > t.budget:
        raise ExtractionLimit("too much work to read its use declarations")


def _use_tree(t: _Tokens, j: int, prefix: "list[str]", sep: str,
              depth: int = 0) -> "tuple[list[list[str]], int]":
    """Parse one `use` tree starting at token j -> (paths, next index).
    `a::{b::{c, d}, e as f, self, h::*}` gives a::b::c, a::b::d, a::e, a,
    a::h. A glob contributes the path before it."""
    segs = list(prefix)
    out: "list[list[str]]" = []
    if depth > _MAX_USE_DEPTH:
        # Not skipped in silence: a file that nests this deep is refused, so
        # that it is reported, and so that each of its `use` does not cost
        # a descent of sixteen levels.
        raise ExtractionLimit("use declarations nested too deeply to read")
    _spend(t, 1 + len(prefix) // 16)     # copying a long prefix is not free
    while j < t.n:
        _spend(t, 1)
        kind, text = t.kind[j], t.text[j]
        if kind == "w":
            if text == "as":
                j += 2                      # skip the alias name
                continue
            segs.append(text)
        elif text == sep:
            pass
        elif text == "*":
            pass
        elif text == "{":
            j += 1
            while j < t.n:
                sub, j = _use_tree(t, j, segs, sep, depth + 1)
                out.extend(sub)
                if t.punct(j) == ",":
                    j += 1
                    continue
                break
            if t.punct(j) == "}":
                j += 1
            return out, j
        else:
            break
        j += 1
    if len(segs) > len(prefix):
        if segs[-1] == "self" and prefix:
            segs.pop()
        _spend(t, len(segs) // 16)
        out.append(segs)
    return out, j


def _use_statement(t: _Tokens, j: int, sep: str
                   ) -> "tuple[list[list[str]], bool]":
    """Comma-separated use trees from token j, and whether a `;` ends them."""
    paths: "list[list[str]]" = []
    while True:
        sub, j = _use_tree(t, j, [], sep)
        paths.extend(sub)
        if t.punct(j) == ",":
            j += 1
            continue
        return paths, t.punct(j) == ";"


# -- Rust --------------------------------------------------------------------

def _rust_module(path: str) -> "list[str]":
    """The module path of a Rust source file inside its crate:
    src/handlers/h.rs -> [handlers, h]; src/handlers/mod.rs -> [handlers];
    src/lib.rs and src/main.rs -> []."""
    parts = posixpath.splitext(path.replace("\\", "/"))[0].split("/")
    if "src" in parts:
        parts = parts[len(parts) - parts[::-1].index("src"):]
    if parts and parts[-1] == "mod":
        parts = parts[:-1]
    elif len(parts) == 1 and parts[0] in ("lib", "main"):
        parts = []
    return parts


def _rust_path(segs: "list[str]", module: "list[str]") -> str:
    """crate::db::x -> db.x; self:: / super:: resolve against the file's
    module; anything else keeps its path."""
    if segs and segs[0] == "crate":
        segs = segs[1:]
    elif segs and segs[0] in ("self", "super"):
        base = list(module)
        k = 0
        while k < len(segs) and segs[k] in ("self", "super"):
            if segs[k] == "super" and base:
                base.pop()
            k += 1
        segs = base + segs[k:]
    return ".".join(segs)


def _rust_item_start(t: _Tokens, i: int) -> bool:
    """Is token i (use / mod / extern) the start of an item, possibly behind
    `pub` or `pub(crate)`?"""
    j = i - 1
    if t.punct(j) == ")":                       # pub(crate) / pub(in a::b)
        # No parenthesis stands inside a visibility, so the walk back ends
        # at the first one: each `)` is walked over once, whatever the
        # file holds (`)use x;` on every line, say).
        k = j - 1
        while k >= 0 and t.punct(k) not in ("(", ")"):
            k -= 1
        if t.punct(k) != "(" or t.word(k - 1) != "pub":
            return False
        j = k - 2
    elif t.word(j) == "pub":
        j -= 1
    return j < 0 or t.starts_statement(j + 1, after=(";", "{", "}", "]"))


def _rust(path: str, t: _Tokens) -> "list[tuple[str, int]]":
    module = _rust_module(path)
    found = []
    for i in range(t.n):
        word = t.word(i)
        if word not in ("use", "mod", "extern") or not _rust_item_start(t, i):
            continue
        if word == "use":
            paths, ended = _use_statement(t, i + 1, "::")
            if ended:
                for segs in paths:
                    mod = _rust_path(segs, module)
                    if mod:
                        found.append((mod, t.line[i]))
        elif word == "mod":
            if t.word(i + 1) and t.punct(i + 2) == ";":
                found.append((".".join(module + [t.text[i + 1]]), t.line[i]))
        elif t.word(i + 1) == "crate" and t.word(i + 2) and t.punct(i + 3) == ";":
            found.append((t.text[i + 2], t.line[i]))
    return found


# -- PHP ---------------------------------------------------------------------

def _php(path: str, t: _Tokens) -> "list[tuple[str, int]]":
    found = []
    for i in range(t.n):
        word = t.word(i).lower()
        if word == "use" and t.starts_statement(i, after=(";", "{", "}")):
            j = i + 1
            if t.word(j).lower() in ("function", "const"):
                j += 1
            paths, ended = _use_statement(t, j, "\\")
            if ended:
                for segs in paths:
                    found.append((".".join(segs), t.line[i]))
        elif word in ("require", "require_once", "include", "include_once"):
            j = i + 1 + (t.punct(i + 1) == "(")
            here = t.word(j) == "__DIR__" and t.punct(j + 1) == "."
            if here:
                j += 2
            spec = t.string(j)
            if spec is None:
                continue
            if here:               # __DIR__ . '/x.php' is relative to this file
                spec = "./" + spec.lstrip("/")
            if spec.startswith(("./", "../")):
                spec = _resolve_relative(path, spec, {".php"})
            found.append((spec, t.line[i]))
    return found


# -- Ruby --------------------------------------------------------------------

def _ruby(path: str, t: _Tokens) -> "list[tuple[str, int]]":
    found = []
    for i in range(t.n):
        word = t.word(i)
        if word not in ("require", "require_relative", "load", "autoload") \
                or not t.first[i]:
            continue
        j = i + 1
        if word == "autoload":               # autoload :Const, "path"
            while j < t.n and t.line[j] == t.line[i] and t.string(j) is None:
                j += 1
        elif t.punct(j) == "(":
            j += 1
        spec = t.string(j)
        if spec is None:
            continue
        if word == "require_relative" or spec.startswith(("./", "../")):
            spec = _resolve_relative(path, spec, {".rb"})
        found.append((spec, t.line[i]))
    return found


_EXTRACTORS = {"js": _js, "go": _go, "java": _java, "rust": _rust,
               "php": _php, "ruby": _ruby}


def extract(path: str, text: str, lang: "str | None" = None
            ) -> "list[tuple[str, int]]":
    """(module, line) for every import of a non-Python source file, in line
    order. `lang` defaults to the language of `path` (JavaScript/TypeScript
    when the extension is unknown)."""
    lang = lang or language_of(path) or "js"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    found = _EXTRACTORS[lang](path, _Tokens(text, lang))
    return sorted(set(found), key=lambda item: (item[1], item[0]))


# -- Go modules --------------------------------------------------------------

_GO_MODULE = re.compile(r'(?m)^[ \t]*module[ \t]+("?)([^\s"]+)\1')


def go_module_path(root: str) -> str:
    """The `module` path declared in <root>/go.mod ("" if there is none)."""
    try:
        with open(posixpath.join(root.replace("\\", "/"), "go.mod"),
                  encoding="utf-8-sig", errors="replace") as f:
            m = _GO_MODULE.search(f.read())
    except OSError:
        return ""
    return m.group(2) if m else ""


def strip_go_module(imports: "list[tuple[str, int]]", module: str
                    ) -> "list[tuple[str, int]]":
    """Go imports name packages by module path (github.com/acme/app/internal/db),
    while layers name directories (internal/db): drop the module prefix from
    the repo's own packages so the two meet."""
    if not module:
        return imports
    prefix = module + "/"
    return [(mod[len(prefix):] if mod.startswith(prefix) else mod, line)
            for mod, line in imports]
