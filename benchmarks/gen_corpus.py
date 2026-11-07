"""Generate a multi-language repository with a known set of rule violations.

Usage: python3 benchmarks/gen_corpus.py OUT_DIR [--files N] [--seed S]

The corpus is a polyglot monorepo (Python, TypeScript, Go, Java, Rust, PHP,
Ruby) with the same three layers in each language (handlers, services, db)
and one rule - "handlers must not import db" - written inside an ADR.

Every import the rule must catch ("positive") and every look-alike it must
leave alone ("decoy") is placed deliberately, with a category name, and
written to OUT_DIR/truth.json as {file: {line: [kind, category]}}. The
categories are the spellings real code uses: relative paths, tsconfig
aliases, dynamic imports, multi-line statements, re-exports, nested `use`
trees, a go.mod below the root, ... and the decoys that fool a naive scan:
the same text in a comment or a string, `db_utils` next to `db`, a type
named Db.

The generator knows nothing about adr-drift. Same seed, same corpus.
"""

from __future__ import annotations

import argparse
import json
import os
import random

ARCH = """layers:
  - name: handlers
    paths: ["py/handlers/**", "ts/src/handlers/**", "go/internal/handlers/**", "java/src/main/java/com/acme/handlers/**", "rust/src/handlers/**", "php/src/Handlers/**", "ruby/lib/handlers/**"]
  - name: services
    paths: ["py/services/**", "ts/src/services/**", "go/internal/services/**", "java/src/main/java/com/acme/services/**", "rust/src/services/**", "php/src/Services/**", "ruby/lib/services/**"]
  - name: db
    paths: ["py/db/**", "ts/src/**/db/**", "go/internal/db/**", "java/src/main/java/com/acme/db/**", "**/rust/src/db/**", "**/Db/**", "ruby/lib/**/db/**"]
rules: []
"""

ADR = """# 1. Handlers never touch the database

## Status

Accepted

## Decision

Request handlers go through services.

## Constraints

- forbid-import: handlers -> db
"""

TSCONFIG = """{
  // paths are relative to baseUrl
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"],
      "@db/*": ["src/db/*"],
    },
  },
}
"""

COMPOSER = '{"autoload": {"psr-4": {"App\\\\": "src/"}}}\n'
GOMOD = "module example.com/app\n\ngo 1.22\n"

# category -> template(s). {n} is a unique suffix that keeps names distinct.
# A template is a list of lines; the line of the import is the one marked
# with a leading '>' (the marker is removed).
PY_POS = {
    "py-import": ["> import db.conn{m}"],
    "py-from": ["> from db import conn{m}"],
    "py-from-sub": ["> from db.conn{m} import connect"],
    "py-relative": ["> from ..db import conn{m}"],
    "py-multiline": ["> from db.conn{m} import (", "    connect,", "    close,", ")"],
    "py-in-function": ["def f_{n}():", ">     import db.conn{m}"],
    "py-importlib": ["import importlib", ">x_{n} = importlib.import_module('db.conn{m}')"],
    "py-dunder-import": [">x_{n} = __import__('db.conn{m}')"],
}
PY_NEG = {
    "py-comment": ["# import db.conn{m}  (do not do this)"],
    "py-string": ['MSG_{n} = "import db.conn{m} is forbidden"'],
    "py-docstring": ['"""Docs: from db.conn{m} import connect"""'],
    "py-lookalike": ["import db_utils.conn{m}", "import dbx.conn{m}"],
    "py-service": ["import services.orders{m}"],
    "py-third-party": ["import requests", "import psycopg2"],
    "py-importlib-computed": ["import importlib", "NAME_{n} = 'db.conn{m}'",
                              "y_{n} = importlib.import_module(NAME_{n})"],
    "py-other-import-module": ["x_{n} = registry.import_module('db.conn{m}')"],
    "py-importlib-service": ["import importlib",
                             "z_{n} = importlib.import_module('services.orders{m}')"],
}
TS_POS = {
    "ts-relative": ['> import {{ q }} from "../db/conn{m}";'],
    "ts-relative-js-ext": ['> import {{ q }} from "../db/conn{m}.js";'],
    "ts-alias-at": ['> import {{ q }} from "@/db/conn{m}";'],
    "ts-alias-named": ['> import {{ q }} from "@db/conn{m}";'],
    "ts-dynamic": ['> const m_{n} = await import("../db/conn{m}");'],
    "ts-require": ['> const c_{n} = require("../db/conn{m}");'],
    "ts-reexport": ['> export * from "../db/conn{m}";'],
    "ts-multiline": ["import {{", "  a_{n},", "  b_{n},", '}} from "../db/conn{m}";'],
    "ts-type-import": ['> import type {{ Row }} from "../db/conn{m}";'],
}
TS_NEG = {
    "ts-comment": ['// import {{ q }} from "../db/conn{m}";'],
    "ts-string": ['const s_{n} = \'import x from "../db/conn{m}"\';'],
    "ts-lookalike": ['import {{ q }} from "../db_utils/conn{m}";'],
    "ts-service": ['import {{ q }} from "../services/orders{m}";'],
    "ts-third-party": ['import express from "express";'],
    "ts-bare-package": ['import pg from "db-driver";'],
    "ts-alias-service": ['import {{ q }} from "@/services/orders{m}";'],
}
GO_POS = {
    "go-single": ['> import "example.com/app/internal/db"'],
    "go-group": ["import (", '\t"fmt"', '>\t"example.com/app/internal/db"', ")"],
    "go-alias": ['> import dbx_{n} "example.com/app/internal/db"'],
}
GO_NEG = {
    "go-comment": ['// import "example.com/app/internal/db"'],
    "go-lookalike": ['import "example.com/app/internal/dbutil"'],
    "go-service": ['import "example.com/app/internal/services"'],
    "go-other-module": ['import "github.com/other/app/internal/db"'],
    "go-stdlib": ['import "fmt"'],
}
JAVA_POS = {
    "java-single": ["> import com.acme.db.Conn{m};"],
    "java-wildcard": ["> import com.acme.db.*;"],
    "java-static": ["> import static com.acme.db.Conn{m}.open;"],
}
JAVA_NEG = {
    "java-comment": ["// import com.acme.db.Conn{m};"],
    "java-lookalike": ["import com.acme.dbutil.Conn{m};"],
    "java-service": ["import com.acme.services.Orders{m};"],
    "java-stdlib": ["import java.util.List;"],
}
RUST_POS = {
    "rust-use": ["> use crate::db::conn{m};"],
    "rust-nested": ["> use crate::{{db::conn{m}, services::orders}};"],
    "rust-super": ["> use super::super::db::conn{m};"],
}
RUST_NEG = {
    "rust-comment": ["// use crate::db::conn{m};"],
    "rust-string": ['const S_{n}: &str = "use crate::db::conn{m};";'],
    "rust-lookalike": ["use crate::db_utils::conn{m};"],
    "rust-service": ["use crate::services::orders{m};"],
}
PHP_POS = {
    "php-require-dir": ["> require_once __DIR__ . '/../Db/Conn{m}.php';"],
    "php-use-psr4": ["> use App\\Db\\Conn{m};"],
}
PHP_NEG = {
    "php-comment": ["// use App\\Db\\Conn{m};"],
    "php-service": ["use App\\Services\\Orders{m};"],
    "php-other-vendor": ["use Vendor\\Db\\Conn{m};"],
    "php-lookalike": ["use App\\DbUtils\\Conn{m};"],
}
RUBY_POS = {
    "ruby-require-relative": ["> require_relative '../db/conn{m}'"],
    "ruby-require-parens": ["> require('../db/conn{m}')"],
}
RUBY_NEG = {
    "ruby-comment": ["# require_relative '../db/conn{m}'"],
    "ruby-service": ["require_relative '../services/orders{m}'"],
    "ruby-string": ["S_{n} = \"require_relative '../db/conn{m}'\""],
}

LANGS = {
    # lang: (ext, handler dir, filler fn, header lines, pos, neg)
    "py": (".py", "py/handlers", PY_POS, PY_NEG),
    "ts": (".ts", "ts/src/handlers", TS_POS, TS_NEG),
    "go": (".go", "go/internal/handlers", GO_POS, GO_NEG),
    "java": (".java", "java/src/main/java/com/acme/handlers", JAVA_POS, JAVA_NEG),
    "rust": (".rs", "rust/src/handlers", RUST_POS, RUST_NEG),
    "php": (".php", "php/src/Handlers", PHP_POS, PHP_NEG),
    "ruby": (".rb", "ruby/lib/handlers", RUBY_POS, RUBY_NEG),
}
SIBLINGS = {  # other layers' directories, per language
    "py": ("py/services", "py/db"),
    "ts": ("ts/src/services", "ts/src/db"),
    "go": ("go/internal/services", "go/internal/db"),
    "java": ("java/src/main/java/com/acme/services",
             "java/src/main/java/com/acme/db"),
    "rust": ("rust/src/services", "rust/src/db"),
    "php": ("php/src/Services", "php/src/Db"),
    "ruby": ("ruby/lib/services", "ruby/lib/db"),
}


def _header(lang: str, n: int) -> "list[str]":
    return {
        "py": [f'"""handler {n}."""', ""],
        "ts": [f"// handler {n}", ""],
        "go": ["package handlers", ""],
        "java": ["package com.acme.handlers;", ""],
        "rust": [f"// handler {n}", ""],
        "php": ["<?php", f"// handler {n}", ""],
        "ruby": [f"# handler {n}", ""],
    }[lang]


def _filler(lang: str, n: int, rng: random.Random, count: int) -> "list[str]":
    out = []
    for i in range(count):
        v = rng.randint(0, 999)
        out.extend({
            "py": [f"def handle_{n}_{i}(request):", f"    value = {v} + len(request)",
                   "    return {'ok': True, 'value': value}", ""],
            "ts": [f"export function handle{n}_{i}(req: number): number {{",
                   f"  return req + {v};", "}", ""],
            "go": [f"func Handle{n}_{i}(req int) int {{", f"\treturn req + {v}", "}", ""],
            "java": [f"class H{n}_{i} {{ int run(int r) {{ return r + {v}; }} }}", ""],
            "rust": [f"pub fn handle_{n}_{i}(req: i32) -> i32 {{", f"    req + {v}", "}", ""],
            "php": [f"function handle_{n}_{i}($r) {{ return $r + {v}; }}", ""],
            "ruby": [f"def handle_{n}_{i}(req)", f"  req + {v}", "end", ""],
        }[lang])
    return out


def _materialize(templates, n, m):
    lines, hit = [], None
    for t in templates:
        marked = t.startswith(">")
        text = t[1:] if marked else t
        if marked and text.startswith(" "):
            text = text[1:]
        text = text.format(n=n, m=m)
        if marked:
            hit = len(lines)
        lines.append(text)
    return lines, hit


def generate(out: str, files: int = 350, seed: int = 7) -> dict:
    rng = random.Random(seed)
    truth: "dict[str, dict[str, list]]" = {}

    def write(rel: str, text: str) -> None:
        path = os.path.join(out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    write("arch.yaml", ARCH)
    write("docs/adr/0001-handlers-never-touch-the-db.md", ADR)
    write("ts/tsconfig.json", TSCONFIG)
    write("go/go.mod", GOMOD)
    write("php/composer.json", COMPOSER)

    per_lang = max(1, files // len(LANGS))
    serial = 0
    for lang, (ext, hdir, pos, neg) in LANGS.items():
        sib = SIBLINGS[lang]
        for i in range(per_lang):
            serial += 1
            lines = _header(lang, serial)
            marks: "dict[int, list]" = {}
            # 0-3 positives, 0-3 decoys per handler file
            plan = [("pos", c) for c in rng.sample(sorted(pos), min(len(pos), rng.randint(0, 3)))]
            plan += [("neg", c) for c in rng.sample(sorted(neg), min(len(neg), rng.randint(0, 3)))]
            rng.shuffle(plan)
            for kind, cat in plan:
                tmpl = (pos if kind == "pos" else neg)[cat]
                body, hit = _materialize(tmpl, serial, f"_{serial}")
                base = len(lines)
                lines.extend(body)
                if kind == "pos":
                    marks[base + (hit if hit is not None else 0) + 1] = ["pos", cat]
                else:
                    # a decoy "is" flagged if a violation lands on any of its lines
                    for k in range(len(body)):
                        marks[base + k + 1] = ["neg", cat]
                lines.append("")
            lines.extend(_filler(lang, serial, rng, rng.randint(2, 6)))
            rel = f"{hdir}/h{serial}{ext}"
            write(rel, "\n".join(lines) + "\n")
            truth[rel] = {str(k): v for k, v in marks.items()}
        # the other layers: they may import db freely; none of it is a violation
        for d in sib:
            for i in range(max(1, per_lang // 3)):
                serial += 1
                lines = _header(lang, serial)
                if d.endswith(("services", "Services")):
                    tmpl = rng.choice(sorted(pos))
                    body, _hit = _materialize(pos[tmpl], serial, f"_{serial}")
                    lines.extend(body)       # allowed: services may use db
                lines.extend(_filler(lang, serial, rng, rng.randint(1, 4)))
                write(f"{d}/s{serial}{ext}", "\n".join(lines) + "\n")
    write("truth.json", json.dumps(truth))
    return truth


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--files", type=int, default=350)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    t = generate(a.out, a.files, a.seed)
    pos = sum(1 for f in t.values() for v in f.values() if v[0] == "pos")
    print(f"{len(t)} handler files, {pos} planted violations")


if __name__ == "__main__":
    main()
