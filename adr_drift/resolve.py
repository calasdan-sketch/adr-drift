"""Import resolution that needs the repository's own configuration.

The extractors in langs.py read what the source says. Some imports do not
say where they go until a config file is read:

* TypeScript / JavaScript: `import x from "@/db/conn"` goes wherever
  `compilerOptions.paths` says (tsconfig.json / jsconfig.json, `extends`
  followed), a bare `"db/conn"` goes where `baseUrl` says, and
  `"@acme/db"` is a workspace package of the monorepo (its package.json
  `name`);
* Go: the module path is in the nearest `go.mod`, which in a monorepo is
  not the one at the root;
* PHP: `use App\\Db\\Conn;` goes where `composer.json` `autoload.psr-4`
  says.

Every function returns repo-relative, dotted names like the extractors do
("ts/src/db/conn" -> "ts.src.db.conn"), or nothing when it does not know:
it never guesses a target (a bare "react" with a baseUrl is only resolved
when such a file exists). A config that cannot be read is reported through
`warnings`, because aliases it defines are then not resolved.

Everything reads from the working tree, with results cached per directory.
"""

from __future__ import annotations

import glob
import json
import os
import posixpath
import re

from . import langs
from . import miniyaml

_TS_NAMES = ("tsconfig.json", "jsconfig.json")
_MAX_EXTENDS = 8
_MAX_BYTES = 2_000_000          # a config larger than this is not a config


def strip_jsonc(text: str) -> str:
    """`text` without // and /* */ comments and without trailing commas
    (what tsconfig.json allows); strings are left untouched. Linear."""
    out: "list[str]" = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif c == "/" and text[i + 1:i + 2] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c == "/" and text[i + 1:i + 2] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    cleaned = "".join(out)
    return re.sub(r",(\s*[}\]])", r"\1", cleaned)


def _dotted(rel: str, drop_exts=langs.JS_EXTS) -> str:
    root, ext = posixpath.splitext(rel)
    return (root if ext.lower() in drop_exts else rel).replace("/", ".")


class TsConfig:
    """The effective compilerOptions.baseUrl / paths of one tsconfig.json
    (with its `extends` chain)."""

    def __init__(self, base_dir: "str | None", paths: dict,
                 paths_dir: str) -> None:
        self.base_dir = base_dir     # repo-relative dir, or None
        self.paths = paths           # pattern -> [substitution]
        self.paths_dir = paths_dir   # what the substitutions are relative to


class Resolver:
    def __init__(self, root: str, exists=None) -> None:
        self.root = root
        self._exists_fn = exists
        self.warnings: "list[str]" = []
        self._ts_dir: "dict[str, TsConfig | None]" = {}
        self._go_dir: "dict[str, tuple[str, str] | None]" = {}
        self._php_dir: "dict[str, list | None]" = {}
        self._exist: "dict[str, bool]" = {}
        self._packages: "dict[str, str] | None" = None
        self._warned: "set[str]" = set()

    # -- helpers -------------------------------------------------------------

    def _abs(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/")) if rel else self.root

    def _isfile(self, rel: str) -> bool:
        hit = self._exist.get(rel)
        if hit is None:
            hit = (self._exists_fn(rel) if self._exists_fn
                   else os.path.isfile(self._abs(rel)))
            self._exist[rel] = hit
        return hit

    def _isdir(self, rel: str) -> bool:
        key = rel + "/"
        hit = self._exist.get(key)
        if hit is None:
            hit = os.path.isdir(self._abs(rel))
            self._exist[key] = hit
        return hit

    def _module_exists(self, rel: str) -> bool:
        """Is there a source file or a directory for the repo-relative path
        `rel` (an import target without its extension)?"""
        if self._isdir(rel):
            return True
        return any(self._isfile(rel + ext) for ext in langs.JS_EXTS) or (
            self._isfile(rel))

    def _warn(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self.warnings.append(message)

    def _read_json(self, rel: str, jsonc: bool = False):
        try:
            with open(self._abs(rel), "rb") as f:
                data = f.read(_MAX_BYTES + 1)
            if len(data) > _MAX_BYTES:
                raise ValueError("too large")
            text = data.decode("utf-8-sig", "replace")
            return json.loads(strip_jsonc(text) if jsonc else text)
        except (OSError, ValueError, RecursionError) as e:
            self._warn(rel, f"{rel}: cannot be read ({type(e).__name__}); "
                       "what it declares is NOT used to resolve imports")
            return None

    def _ancestors(self, rel_dir: str):
        d = rel_dir
        while True:
            yield d
            if not d:
                return
            d = posixpath.dirname(d)

    # -- TypeScript / JavaScript --------------------------------------------

    def _load_ts(self, rel: str, depth: int = 0, seen=()) -> "dict | None":
        """compilerOptions of the config at `rel` merged over its extends:
        {baseUrl: repo-relative dir | None, paths: {...} | None,
        paths_dir: str}."""
        if depth > _MAX_EXTENDS or rel in seen:
            self._warn(rel, f"{rel}: 'extends' goes too deep or round in a "
                       "loop; its aliases are NOT resolved")
            return None
        data = self._read_json(rel, jsonc=True)
        if not isinstance(data, dict):
            return None
        here = posixpath.dirname(rel)
        merged = {"baseUrl": None, "paths": None, "paths_dir": here}
        parents = data.get("extends")
        if isinstance(parents, str):
            parents = [parents]
        for parent in parents if isinstance(parents, list) else []:
            if not isinstance(parent, str):
                continue
            if not parent.startswith("."):
                self._warn(rel + parent, f"{rel}: extends {parent!r}, a "
                           "package: its paths/baseUrl are NOT read")
                continue
            target = posixpath.normpath(posixpath.join(here, parent))
            if not target.endswith(".json"):
                target += ".json"
            if target.startswith(".."):
                continue
            if not self._isfile(target):
                self._warn(rel + parent,
                           f"{rel}: extends {parent!r}, which is not there")
                continue
            base = self._load_ts(target, depth + 1, seen + (rel,))
            if base:
                for key in ("baseUrl", "paths", "paths_dir"):
                    if base[key] is not None:
                        merged[key] = base[key]
        opts = data.get("compilerOptions")
        if isinstance(opts, dict):
            if isinstance(opts.get("baseUrl"), str):
                base_dir = posixpath.normpath(
                    posixpath.join(here, opts["baseUrl"]))
                merged["baseUrl"] = "" if base_dir == "." else base_dir
            paths = opts.get("paths")
            if isinstance(paths, dict):
                clean = {k: [s for s in v if isinstance(s, str)]
                         for k, v in paths.items() if isinstance(v, list)}
                merged["paths"] = clean
                merged["paths_dir"] = here
        return merged

    def _ts_for_dir(self, rel_dir: str) -> "TsConfig | None":
        if rel_dir in self._ts_dir:
            return self._ts_dir[rel_dir]
        found: "TsConfig | None" = None
        for d in self._ancestors(rel_dir):
            if d != rel_dir and d in self._ts_dir:
                found = self._ts_dir[d]
                break
            cfg_rel = next((posixpath.join(d, n) for n in _TS_NAMES
                            if self._isfile(posixpath.join(d, n))), None)
            if cfg_rel:
                eff = self._load_ts(cfg_rel)
                if eff and (eff["paths"] or eff["baseUrl"] is not None):
                    base = eff["baseUrl"]
                    found = TsConfig(base, eff["paths"] or {},
                                     base if base is not None
                                     else eff["paths_dir"])
                    # TS: paths are relative to baseUrl when it is set
                break
        self._ts_dir[rel_dir] = found
        return found

    def _workspace_packages(self) -> "dict[str, str]":
        """{package name: repo-relative dir} for the workspaces of the root
        package.json (npm / yarn) or pnpm-workspace.yaml."""
        if self._packages is not None:
            return self._packages
        self._packages = pk = {}
        patterns: "list[str]" = []
        if self._isfile("package.json"):
            data = self._read_json("package.json")
            ws = data.get("workspaces") if isinstance(data, dict) else None
            if isinstance(ws, dict):
                ws = ws.get("packages")
            if isinstance(ws, list):
                patterns += [p for p in ws if isinstance(p, str)]
        if self._isfile("pnpm-workspace.yaml"):
            try:
                with open(self._abs("pnpm-workspace.yaml"),
                          encoding="utf-8-sig") as f:
                    y = miniyaml.safe_load(f.read())
                if isinstance(y, dict) and isinstance(y.get("packages"), list):
                    patterns += [p for p in y["packages"]
                                 if isinstance(p, str)]
            except (OSError, ValueError):
                self._warn("pnpm-workspace.yaml",
                           "pnpm-workspace.yaml: cannot be read; workspace "
                           "packages are NOT resolved")
        for pat in patterns:
            if (pat.startswith("!") or ".." in pat.split("/")
                    or os.path.isabs(pat) or re.match(r"[A-Za-z]:", pat)):
                continue                  # stays inside the repository
            for hit in sorted(glob.glob(os.path.join(self.root, pat),
                                        recursive=True)):
                rel = os.path.relpath(hit, self.root).replace(os.sep, "/")
                if rel.startswith("..") or "node_modules" in rel.split("/"):
                    continue
                if os.path.isfile(os.path.join(hit, "package.json")):
                    meta = self._read_json(rel + "/package.json")
                    name = meta.get("name") if isinstance(meta, dict) else None
                    if isinstance(name, str) and name:
                        pk.setdefault(name, rel)
        return pk

    def js(self, importer: str, spec: str) -> "list[str]":
        """Dotted module(s) a non-relative JS/TS specifier stands for."""
        importer = importer.replace("\\", "/")
        out: "list[str]" = []
        cfg = self._ts_for_dir(posixpath.dirname(importer))
        if cfg and cfg.paths:
            hit = self._match_paths(cfg, spec)
            if hit:
                return hit
        if cfg and cfg.base_dir is not None:
            cand = posixpath.normpath(posixpath.join(cfg.base_dir, spec))
            if not cand.startswith("..") and self._module_exists(cand):
                out.append(_dotted(cand))
        name = self._package_for(spec)
        if name:
            out.append(name)
        return out

    def _package_for(self, spec: str) -> str:
        pk = self._workspace_packages()
        if not pk:
            return ""
        parts = spec.split("/")
        best = ""
        for n in (2, 1):                 # @scope/name, then name
            cand = "/".join(parts[:n])
            if len(parts) >= n and cand in pk:
                best = cand
                break
        if not best:
            return ""
        rest = spec[len(best):].lstrip("/")
        return _dotted(posixpath.join(pk[best], rest) if rest else pk[best])

    def _match_paths(self, cfg: TsConfig, spec: str) -> "list[str]":
        best, best_len, star = None, -1, ""
        for pat in cfg.paths:
            if "*" in pat:
                pre, _, post = pat.partition("*")
                if ("*" in post or len(spec) < len(pre) + len(post)
                        or not spec.startswith(pre)
                        or (post and not spec.endswith(post))):
                    continue
                if len(pre) > best_len:
                    best, best_len = pat, len(pre)
                    star = spec[len(pre):len(spec) - len(post)]
            elif pat == spec and len(pat) > best_len:
                best, best_len, star = pat, len(pat), ""
        if best is None:
            return []
        cands = []
        for sub in cfg.paths[best]:
            target = sub.replace("*", star, 1) if "*" in best else sub
            rel = posixpath.normpath(posixpath.join(cfg.paths_dir, target))
            if not rel.startswith(".."):
                cands.append(rel)
        if not cands:
            return []
        if len(cands) > 1:
            # TS tries the substitutions in order and takes the first that
            # exists; so do we (the first one when none does).
            real = [c for c in cands if self._module_exists(c)]
            cands = real[:1] or cands[:1]
        return [_dotted(cands[0])]

    # -- Go -------------------------------------------------------------------

    def _go_for_dir(self, rel_dir: str) -> "tuple[str, str] | None":
        if rel_dir in self._go_dir:
            return self._go_dir[rel_dir]
        found = None
        for d in self._ancestors(rel_dir):
            if d != rel_dir and d in self._go_dir:
                found = self._go_dir[d]
                break
            gm = posixpath.join(d, "go.mod")
            if self._isfile(gm):
                mod = langs.go_module_path(self._abs(d))
                found = (mod, d) if mod else None
                break
        self._go_dir[rel_dir] = found
        return found

    def go(self, importer: str, mod: str) -> str:
        """A Go import path as a repo-relative package path: the module path
        of the nearest go.mod is replaced by that module's directory."""
        found = self._go_for_dir(posixpath.dirname(importer.replace("\\", "/")))
        if not found:
            return mod
        module, base = found
        if mod == module:
            return base or mod
        if mod.startswith(module + "/"):
            return posixpath.join(base, mod[len(module) + 1:])
        return mod

    # -- PHP (composer psr-4) --------------------------------------------------

    def _php_for_dir(self, rel_dir: str) -> "list | None":
        if rel_dir in self._php_dir:
            return self._php_dir[rel_dir]
        found = None
        for d in self._ancestors(rel_dir):
            if d != rel_dir and d in self._php_dir:
                found = self._php_dir[d]
                break
            cj = posixpath.join(d, "composer.json")
            if self._isfile(cj):
                data = self._read_json(cj)
                maps = []
                for sect in ("autoload", "autoload-dev"):
                    block = data.get(sect) if isinstance(data, dict) else None
                    psr = block.get("psr-4") if isinstance(block, dict) else None
                    if not isinstance(psr, dict):
                        continue
                    for ns, dirs in psr.items():
                        dirs = [dirs] if isinstance(dirs, str) else dirs
                        if isinstance(dirs, list):
                            maps.extend((ns.strip("\\").replace("\\", "."),
                                         posixpath.join(d, x.strip("/")))
                                        for x in dirs if isinstance(x, str))
                found = sorted(maps, key=lambda m: -len(m[0])) or None
                break
        self._php_dir[rel_dir] = found
        return found

    def php(self, importer: str, mod: str) -> "list[str]":
        """The path form of a PHP namespace, per composer psr-4."""
        maps = self._php_for_dir(posixpath.dirname(importer.replace("\\", "/")))
        out = []
        for ns, base in maps or ():
            if ns and (mod == ns or mod.startswith(ns + ".")):
                rest = mod[len(ns):].strip(".")
                out.append(_dotted(posixpath.join(base, *rest.split("."))
                                   if rest else base, {".php"}))
                break
            if not ns:
                out.append(_dotted(posixpath.join(base, *mod.split(".")),
                                   {".php"}))
                break
        return out
