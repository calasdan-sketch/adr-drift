"""Engine: evaluate rules against a scanned repo -> violations."""

from __future__ import annotations

import collections
import dataclasses
import fnmatch
import functools
import os
import re

from . import config as config_mod
from . import diag as diag_mod
from . import langs
from . import resolve as resolve_mod
from . import rules as rules_mod
from . import scanner as scanner_mod


@dataclasses.dataclass
class Violation:
    rule: rules_mod.Rule
    file: str          # repo-relative path
    line: int          # 0 when the whole file is the subject
    detail: str
    #: forbid-cycles: one "file:line imports module" per edge of the cycle.
    #: Shown to people, never part of the identity (see identity()).
    evidence: list = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        out = {
            "rule": self.rule.describe(),
            "rule_source": (f"{self.rule.source}:{self.rule.line_no}"
                            if self.rule.line_no else self.rule.source),
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
        }
        if self.evidence:
            out["evidence"] = list(self.evidence)
        return out


def identity(v: "Violation", renames: "dict | None" = None) -> tuple:
    """What makes a violation *the same one* from run to run: the file, the
    rule and what was found (the imported module / the pattern) - not the
    line, which moves whenever anything above it is edited. `renames` maps
    old paths to new ones, so a moved file keeps its violations."""
    return ((renames or {}).get(v.file, v.file), v.rule.describe(), v.detail)


def _unpaired(current: list, known: list) -> "tuple[list[int], list[int]]":
    """Match `current` and `known` (lists of (identity, line)) one to one.

    Same identity and same line is the same violation; the rest of an
    identity's items are paired in line order (they moved). What is left over
    is new (in `current`) or gone (in `known`): -> (indices into current,
    indices into known). With several identical violations in one file the
    count is exact, which of them is new is a best guess (the last ones).
    """
    cur_by: dict = collections.defaultdict(list)
    old_by: dict = collections.defaultdict(list)
    for i, (key, line) in enumerate(current):
        cur_by[key].append((line, i))
    for j, (key, line) in enumerate(known):
        old_by[key].append((line, j))
    extra_cur: list[int] = []
    extra_old: list[int] = []
    for key in set(cur_by) | set(old_by):
        old_at: dict = collections.defaultdict(list)
        for line, j in old_by.get(key, []):
            old_at[line].append(j)
        cur_left = []
        for line, i in sorted(cur_by.get(key, [])):
            if old_at.get(line):
                old_at[line].pop()
            else:
                cur_left.append((line, i))
        old_left = sorted((line, j) for line, js in old_at.items() for j in js)
        paired = min(len(cur_left), len(old_left))
        extra_cur.extend(i for _line, i in cur_left[paired:])
        extra_old.extend(j for _line, j in old_left[paired:])
    return sorted(extra_cur), sorted(extra_old)


def new_and_fixed(base: "list[Violation]", now: "list[Violation]",
                  renames: "dict | None" = None
                  ) -> "tuple[list[Violation], list[Violation]]":
    """(introduced, fixed) between two evaluations of the same rules."""
    extra_now, extra_base = _unpaired(
        [(identity(v), v.line) for v in now],
        [(identity(v, renames), v.line) for v in base])
    return [now[i] for i in extra_now], [base[j] for j in extra_base]


def split_baseline(violations: "list[Violation]", entries: "list[dict]"
                   ) -> "tuple[list[Violation], list[dict]]":
    """(the violations a baseline does not cover, the baseline entries that
    no longer match any violation).

    Entries match by identity, not line, so editing above a grandfathered
    violation does not resurrect it. An entry without `detail` (hand-made)
    can only match by exact file, line and rule. The second list is what
    `check --ratchet` fails on: a violation that was fixed but is still
    listed would let the same violation back in unnoticed.
    """
    legacy: collections.Counter = collections.Counter()
    legacy_entries: dict = collections.defaultdict(list)
    known: list = []
    known_entries: list = []
    for e in entries:
        line = e.get("line") if isinstance(e.get("line"), int) else 0
        if isinstance(e.get("detail"), str):
            known.append(((e["file"], e["rule"], e["detail"]), line))
            known_entries.append(e)
        else:
            key = (e["file"], line, e["rule"])
            legacy[key] += 1
            legacy_entries[key].append(e)
    remaining: list[Violation] = []
    for v in violations:
        key = (v.file, v.line, v.rule.describe())
        if legacy[key] > 0:
            legacy[key] -= 1
            legacy_entries[key].pop()
        else:
            remaining.append(v)
    extra, gone = _unpaired([(identity(v), v.line) for v in remaining], known)
    stale = [known_entries[j] for j in gone]
    stale += [e for es in legacy_entries.values() for e in es]
    return [remaining[i] for i in extra], stale


def not_baselined(violations: "list[Violation]",
                  entries: "list[dict]") -> "list[Violation]":
    """The violations a baseline (`adr-drift baseline`) does not cover."""
    return split_baseline(violations, entries)[0]


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _file_layer(layers: list[dict], rel_path: str) -> str:
    for layer in layers:
        for pat in layer.get("paths", []):
            if rules_mod.match_glob(pat, rel_path):
                return layer.get("name", "")
    return ""


def _split_pattern(pat: str) -> "tuple[list[str], list[str]]":
    """A layer path glob as (its literal leading directories, the rest)."""
    lit: list[str] = []
    parts = pat.replace("\\", "/").strip("/").split("/")
    for i, part in enumerate(parts):
        if any(ch in part for ch in "*?["):
            return lit, parts[i:]
        lit.append(part)
    return lit, []


def _layer_path_prefixes(layer: dict) -> list[str]:
    """Dotted module prefixes implied by a layer's path globs.

    Only the literal leading components count (up to the first
    wildcard): "src/db/**" -> ["src.db"], "handlers/**" -> ["handlers"].
    A glob with a wildcard directory and more after it ("src/**/db/**",
    "**/db/**") is not a prefix: see _complex_globs().
    """
    prefixes: list[str] = []
    for pat in layer.get("paths", []):
        lit, rest = _split_pattern(pat)
        if lit and len(rest) < 2:
            prefixes.append(".".join(lit))
    return prefixes


def _complex_globs(layer: dict) -> list[str]:
    """The layer's globs that no prefix describes: a wildcard directory
    followed by more path ("src/**/db/**"). Treating "src" as the layer's
    prefix would call every module under src a member (and "**/db/**" has
    no prefix at all), so a module is tested against the glob itself."""
    return [pat for pat in layer.get("paths", [])
            if len(_split_pattern(pat)[1]) >= 2]


#: The extensions a module's file can have: a module is tried against a
#: layer's glob as <path>, <path>.<ext> and <path>/x.<ext> (a package).
_MODULE_EXTS = tuple(sorted(scanner_mod.PY_EXTS | scanner_mod.GENERIC_EXTS))


def known_suffixes(paths) -> "dict[str, list[str]]":
    """{dotted name: [paths]} for every name by which a file or directory
    of the repo can be reached from the end of its path: src/db/conn.py is
    `conn`, `db.conn` and `src.db.conn`, and the directory src/db (listed
    with a trailing "/") is `db` and `src.db`. An import that is none of
    these (up to a member name) is not about this repo."""
    out: "dict[str, list[str]]" = {}
    done: "set[str]" = set()

    def add(parts: "list[str]", rep: str) -> None:
        for i in range(len(parts)):
            out.setdefault(".".join(parts[i:]), []).append(rep)

    for rel in paths:
        parts = rel.replace("\\", "/").split("/")
        parts[-1] = parts[-1].rsplit(".", 1)[0]
        add(parts, rel)
        for d in range(1, len(parts)):
            key = "/".join(parts[:d])
            if key not in done:
                done.add(key)
                add(parts[:d], key + "/")
    return out


@functools.lru_cache(maxsize=1024)
def _glob_matcher(glob: str):
    """rules.match_glob(glob, path) as a compiled regex's match method."""
    g = glob.replace("\\", "/")
    alts = [fnmatch.translate(g)]
    if "**" in g:
        alts.append(fnmatch.translate(g.replace("**/", "")))
    return re.compile("|".join(alts), re.DOTALL).match


def _glob_hits_module(glob: str, mod: str,
                      known: "dict[str, list[str]]") -> bool:
    """Is `mod` something in the repo that `glob` covers? It must name a
    file (optionally followed by up to two member names: com.acme.db.Conn
    .open) or a directory (followed by at most one: `from db import name`);
    the glob is then tried on that file, or inside that directory."""
    pieces = rules_mod._norm(mod).split(".")
    match = _glob_matcher(glob)
    for n in range(len(pieces), max(0, len(pieces) - 3), -1):
        extra = pieces[n:]
        for rep in known.get(".".join(pieces[:n]), ()):
            if rep.endswith("/"):
                if len(extra) > 1:
                    continue
                base = rep + "/".join(extra)
                if match(base) or any(match(base + "/x" + ext)
                                      for ext in _MODULE_EXTS):
                    return True
            elif match(rep):
                return True
    return False


def _in_glob(pat: str, mod: str) -> bool:
    path = rules_mod._norm(mod).replace(".", "/")
    if rules_mod.match_glob(pat, path):
        return True
    return any(rules_mod.match_glob(pat, path + ext)
               or rules_mod.match_glob(pat, path + "/x" + ext)
               for ext in _MODULE_EXTS)


class IdentityIndex:
    """Every name an imported module can be known by, for many modules
    against one list of layers.

    Besides the literal module ("myapp.db.conn") this includes the layer
    name(s) whose paths contain it ("db") and the module spelt with the
    layer's leading directories kept or stripped, in every combination
    ("src.myapp.db.conn", "myapp.db.conn", "db.conn"). A layer mapped to
    src/myapp/db/** is reached by `import myapp.db.conn` (src/ is the
    import root), by `import src.myapp.db.conn` and, for a flat layout,
    by `import db.conn`: without this the rule naming the layer silently
    never fires for the spelling the code actually uses.

    The layers' path prefixes are indexed once (by every suffix that may
    match), so a module costs one dictionary look-up per dotted prefix of
    its own name instead of a pass over every layer, and each module is
    worked out once: O(imports * depth), not O(imports * layers). A layer
    glob that is not a prefix ("src/**/db/**", "**/db/**") is tested
    against the module as a path instead (once per module and glob), and,
    when `known` (known_suffixes() of the repo's files) is given, only a
    module that names something in the repo can be in it - an import of
    github.com/other/lib/db is not in a layer "**/db/**"."""

    def __init__(self, layers: list[dict],
                 known: "dict[str, list[str]] | None" = None):
        self._layers = layers
        self._known = known
        # suffix -> [(layer index, prefix index, k)], k ascending per prefix
        self._by_suffix: dict = collections.defaultdict(list)
        self._parts: dict = {}
        self._complex: "list[tuple[int, str]]" = []
        for li, layer in enumerate(layers):
            for pi, prefix in enumerate(_layer_path_prefixes(layer)):
                parts = prefix.split(".")
                self._parts[(li, pi)] = parts
                for k in range(len(parts)):
                    key = rules_mod._norm(".".join(parts[k:]))
                    self._by_suffix[key].append((li, pi, k))
            self._complex.extend((li, g) for g in _complex_globs(layer))
        self._cache: dict = {}

    def of(self, mod: str) -> "list[str]":
        hit = self._cache.get(mod)
        if hit is not None:
            return hit
        v = rules_mod._norm(mod)
        pieces = v.split(".")
        first_k: dict = {}
        for n in range(1, len(pieces) + 1):
            for li, pi, k in self._by_suffix.get(".".join(pieces[:n]), ()):
                if k < first_k.get((li, pi), k + 1):
                    first_k[(li, pi)] = k
        ids = [mod]
        dotted = mod.replace("/", ".").strip(".")
        for (li, pi) in sorted(first_k):
            k = first_k[(li, pi)]
            parts = self._parts[(li, pi)]
            name = self._layers[li].get("name", "")
            if name and name not in ids:
                ids.append(name)
            canonical = parts[:k] + dotted.split(".")
            for j in range(len(parts)):
                form = ".".join(canonical[j:])
                if form and form not in ids:
                    ids.append(form)
        for li, glob in self._complex:
            name = self._layers[li].get("name", "")
            if not name or name in ids:
                continue
            if self._known is None:
                inside = _in_glob(glob, mod)
            else:
                inside = _glob_hits_module(glob, mod, self._known)
            if inside:
                ids.append(name)
        self._cache[mod] = ids
        return ids


def _import_identities(mod: str, layers: list[dict]) -> list[str]:
    """IdentityIndex(layers).of(mod), for one module."""
    return IdentityIndex(layers).of(mod)


def _from_hit(rule: rules_mod.Rule, rel: str, layer: str) -> bool:
    """Does the rule's "from" side cover this file? It matches the file's
    layer name OR a module prefix of its path (so rules work without
    layers)."""
    if layer and rules_mod.match_pat(rule.from_pat, layer):
        return True
    mod_prefix = rel.replace("/", ".").rsplit(".", 1)[0]
    return rules_mod.match_pat(rule.from_pat, mod_prefix)


#: Below this many files a process pool costs more than it saves.
PARALLEL_MIN_FILES = 1500
_WORKER: dict = {}


def _init_worker(root: str, strict_py_only: bool, go_module: str,
                 need_content: bool) -> None:
    _WORKER.update(root=root, strict=strict_py_only, go=go_module,
                   content=need_content,
                   resolver=resolve_mod.Resolver(root))


def _scan_file(path: str) -> tuple:
    """Read and parse one file -> (rel, imports, engine, content, warnings,
    read error, config warnings it caused). Runs in a worker process (and
    in this one when serial): it returns plain data, so the answer does not
    depend on where it ran."""
    root = _WORKER["root"]
    resolver = _WORKER["resolver"]
    before = len(resolver.warnings)
    rel = _rel(root, path)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return rel, [], "none", "", [], e.strerror or type(e).__name__, []
    sink = diag_mod.Diagnostics(echo=False)
    imports, engine = scanner_mod.extract_imports(
        rel, data, strict_py_only=_WORKER["strict"], diag=sink,
        go_module=_WORKER["go"], resolver=resolver)
    content = scanner_mod.decode_source(rel, data) if _WORKER["content"] else ""
    return (rel, imports, engine, content, sink.warnings, "",
            resolver.warnings[before:])


def _scan_all(paths: list, root: str, strict_py_only: bool, go_module: str,
              need_content: bool, jobs: int) -> list:
    """The _scan_file() result of every path, in path order."""
    args = (root, strict_py_only, go_module, need_content)
    if jobs > 1 and len(paths) >= PARALLEL_MIN_FILES:
        try:
            import concurrent.futures as futures
            with futures.ProcessPoolExecutor(
                    max_workers=jobs, initializer=_init_worker,
                    initargs=args) as pool:
                chunk = max(16, len(paths) // (jobs * 8))
                return list(pool.map(_scan_file, paths, chunksize=chunk))
        except (OSError, ImportError, futures.process.BrokenProcessPool):
            pass                      # no pool here: do it in this process
    _init_worker(*args)
    return [_scan_file(p) for p in paths]


def default_jobs() -> int:
    return max(1, min(os.cpu_count() or 1, 8))


def check(root: str, layers: list[dict], all_rules: list[rules_mod.Rule],
          strict_py_only: bool = False,
          diag: "diag_mod.Diagnostics | None" = None,
          jobs: int = 0) -> list[Violation]:
    """Scan `root` and evaluate every rule.

    Warnings (a rule or layer that covers no file, ...) go to `diag`, which
    prints them on stderr; the caller decides whether they fail the run.
    `jobs` is the number of worker processes (0: one per CPU, up to 8);
    the answer is the same for any value.
    """
    diag = diag if diag is not None else diag_mod.Diagnostics()
    entries: list[tuple[str, str, list[tuple[str, int]], str]] = []
    go_module = langs.go_module_path(root)
    need_content = any(r.kind in ("forbid-pattern", "require-pattern")
                       for r in all_rules)
    seen: dict[str, list[int]] = {}   # language -> [files, files with imports]
    results = _scan_all(
        scanner_mod.iter_source_files(root), root, strict_py_only, go_module,
        need_content, jobs if jobs > 0 else default_jobs())
    config_warnings: list = []        # each worker reads the same configs
    for rel, imports, engine, content, warnings, error, cfg in results:
        config_warnings.extend(w for w in cfg if w not in config_warnings)
        if error:
            # A file that cannot be read is not checked: never silently.
            diag.warn(f"{rel}: cannot read ({error}); not checked")
            continue
        for w in warnings:
            diag.warn(w)
        if engine == "regex":
            counts = seen.setdefault(langs.language_of(rel) or "js", [0, 0])
            counts[0] += 1
            counts[1] += bool(imports)
        entries.append((rel, _file_layer(layers, rel), imports, content))
    for message in config_warnings:
        diag.warn(message)
    for message in coverage_warnings(entries, layers, all_rules):
        diag.warn(message)
    for message in no_import_warnings(seen, all_rules):
        diag.warn(message)
    return evaluate(entries, layers, all_rules)


def no_import_warnings(seen: dict, all_rules: list[rules_mod.Rule]
                       ) -> list[str]:
    """One warning per language whose files all came back without a single
    import while forbid-import rules exist: the extractor is heuristic, and
    a rule that cannot see any import enforces nothing."""
    if not any(r.kind == "forbid-import" for r in all_rules):
        return []
    out = []
    for lang in sorted(seen):
        files, with_imports = seen[lang]
        if files and not with_imports:
            name = langs.LANG_NAMES.get(lang, lang)
            out.append(
                f"{files} {name} file{'' if files == 1 else 's'} scanned and "
                f"no import found in any: {name} import extraction is "
                "heuristic (see README, 'Languages: exact support'), so "
                "forbid-import rules may not see these files")
    return out


def _unknown_target(rule: rules_mod.Rule, layers: list[dict],
                    entries: list) -> str:
    """" (did you mean 'services'?)"-style note when the "to" side of a
    forbid-import rule looks like a misspelt layer or directory of this
    repo, else "". An unknown name that resembles nothing is probably a
    third-party package, which is a fine thing to forbid."""
    to = rule.to_pat
    if "*" in to:
        return ""
    head = to.replace("/", ".").strip(".").split(".")[0]
    names = [l["name"] for l in layers if l.get("name")]
    known = set(names)
    tops: set[str] = set()
    for rel, _layer, imports, _content in entries:
        parts = rel.split("/")
        parts[-1] = parts[-1].rsplit(".", 1)[0]
        known.update(parts)
        tops.add(parts[0])
        known.update(mod.split(".")[0] for mod, _line in imports)
    if head in known:
        return ""
    return rules_mod.did_you_mean(head, sorted(set(names) | tops))


def coverage_warnings(entries: list, layers: list[dict],
                      all_rules: list[rules_mod.Rule]) -> list[str]:
    """Rules and layers that cover nothing.

    A rule that covers no file enforces nothing, and the usual cause is a
    typo ("srcc/**", a misspelt layer): say so instead of passing quietly.
    """
    if not (layers or all_rules):
        return []
    if not entries:
        exts = " ".join(sorted(scanner_mod.PY_EXTS | scanner_mod.GENERIC_EXTS))
        return [f"no source files were found to scan (looked for: {exts}), "
                "so no rule can match anything"]
    out: list[str] = []
    used = {layer for _rel, layer, _imports, _content in entries if layer}
    empty: list[str] = []
    for layer in layers:
        name = layer.get("name", "")
        if name in used:
            continue
        paths = layer.get("paths", [])
        globs = ", ".join(paths)
        shadowed = any(rules_mod.match_glob(p, rel)
                       for rel, _l, _i, _c in entries for p in paths)
        if shadowed:
            out.append(f"{config_mod.ARCH_FILE}: layer '{name}' "
                       f"(paths: {globs}) gets no source file: earlier "
                       "layers claim every file its paths match")
        else:
            out.append(f"{config_mod.ARCH_FILE}: layer '{name}' "
                       f"(paths: {globs}) matches no scanned source file")
        empty.append(name)
    for rule in all_rules:
        where = rule.where()
        if rule.kind == "forbid-import":
            if (not any(_from_hit(rule, rel, layer)
                        for rel, layer, _i, _c in entries)
                    and not any(rules_mod.match_pat(rule.from_pat, n)
                                for n in empty)):
                out.append(f"{where}: '{rule.from_pat}' matches no layer "
                           "name or source path, so rule "
                           f"'{rule.describe()}' enforces nothing")
            hint = _unknown_target(rule, layers, entries)
            if hint:
                out.append(f"{where}: rule '{rule.describe()}': target "
                           f"'{rule.to_pat}' is not a layer or a path in "
                           f"this repo{hint}")
        elif rule.kind in ("layer-order", "only-import", "public-api"):
            known = {l.get("name", "") for l in layers}
            wanted = (rule.names if rule.kind == "layer-order"
                      else [rule.from_pat])
            missing = [n for n in wanted if "*" not in n and n not in known]
            if missing:
                out.append(f"{where}: {', '.join(repr(m) for m in missing)}"
                           f" {'is' if len(missing) == 1 else 'are'} not "
                           f"{'a layer' if len(missing) == 1 else 'layers'}"
                           f" in {config_mod.ARCH_FILE}, so rule "
                           f"'{rule.describe()}' enforces nothing"
                           + rules_mod.did_you_mean(missing[0], sorted(known)))
            elif rule.kind != "layer-order" and not any(
                    layer and rules_mod.match_pat(rule.from_pat, layer)
                    for _r, layer, _i, _c in entries):
                out.append(f"{where}: no scanned source file is in layer "
                           f"'{rule.from_pat}', so rule '{rule.describe()}' "
                           "enforces nothing")
        elif rule.kind == "forbid-cycles":
            hit = [l["name"] for l in layers
                   if rules_mod.match_pat(rule.from_pat, l.get("name", ""))]
            if len(hit) < 2:
                out.append(f"{where}: '{rule.from_pat}' matches "
                           f"{len(hit)} layer{'' if len(hit) == 1 else 's'} "
                           f"in {config_mod.ARCH_FILE}; a cycle needs at "
                           f"least two, so rule '{rule.describe()}' "
                           "enforces nothing")
        elif not any(rules_mod.match_glob(rule.glob, rel)
                     for rel, _l, _i, _c in entries):
            out.append(f"{where}: '{rule.glob}' matches no scanned source "
                       f"file, so rule '{rule.describe()}' enforces nothing")
    return out


def evaluate(entries: list[tuple[str, str, list[tuple[str, int]], str]],
             layers: list[dict],
             all_rules: list[rules_mod.Rule],
             repo_paths=None) -> list[Violation]:
    """Evaluate rules against pre-scanned entries.

    Each entry: (rel_path, layer, imports[(module, line)], content).
    Lets `drift` check historical git content through the same engine.
    `repo_paths` is every file of the repository when `entries` are only
    some of them (drift); it is used to tell an import of the repo from an
    import of a library for layer globs such as "**/db/**".
    """
    violations: list[Violation] = []
    needs_files = any(_complex_globs(l) for l in layers)
    index = IdentityIndex(
        layers, known_suffixes(repo_paths if repo_paths is not None
                       else (e[0] for e in entries))
        if needs_files else None)
    for rule in all_rules:
        if rule.kind == "forbid-cycles":
            violations.extend(_cycle_violations(rule, entries, layers,
                                                index))
        elif rule.kind in ("layer-order", "only-import", "public-api"):
            violations.extend(_layer_rule_violations(rule, entries, layers,
                                                     index))
        elif rule.kind == "forbid-import":
            for rel, layer, imports, _content in entries:
                if not _from_hit(rule, rel, layer):
                    continue
                hit_lines: set[int] = set()
                for mod, lineno in imports:
                    # "from m import n" yields m and m.n: report a line once.
                    if lineno in hit_lines:
                        continue
                    if any(rules_mod.match_pat(rule.to_pat, ident)
                           for ident in index.of(mod)):
                        hit_lines.add(lineno)
                        violations.append(Violation(
                            rule=rule, file=rel, line=lineno,
                            detail=f"imports {mod}"))
        else:
            rx = re.compile(rule.regex)
            for rel, _layer, _imports, content in entries:
                if not rules_mod.match_glob(rule.glob, rel):
                    continue
                if rule.kind == "forbid-pattern":
                    for i, line in enumerate(
                            scanner_mod.split_lines(content), 1):
                        if rx.search(line):
                            violations.append(Violation(
                                rule=rule, file=rel, line=i,
                                detail=f"matches /{rule.regex}/"))
                else:  # require-pattern
                    if not rx.search(content):
                        violations.append(Violation(
                            rule=rule, file=rel, line=0,
                            detail=f"missing /{rule.regex}/"))
    violations.sort(key=lambda v: (v.file, v.line))
    return violations


# -- layer-order, only-import, public-api ----------------------------------

def _layer_rule_violations(rule: rules_mod.Rule, entries: list,
                           layers: list[dict], index: "IdentityIndex"
                           ) -> "list[Violation]":
    """The three rules that judge each import by the layer it lands in."""
    names = {l.get("name", "") for l in layers if l.get("name")}
    rank = {n: i for i, n in enumerate(rule.names)}
    out: list[Violation] = []
    for rel, layer, imports, _content in entries:
        if rule.kind == "layer-order" and layer not in rank:
            continue
        if rule.kind == "only-import" and not _from_hit(rule, rel, layer):
            continue
        if rule.kind == "public-api" and rules_mod.match_pat(
                rule.from_pat, layer):
            continue                      # inside the layer: anything goes
        by_line: dict = {}
        for mod, lineno in imports:
            by_line.setdefault(lineno, []).append(mod)
        for lineno in sorted(by_line):
            bad: "list[tuple[str, str]]" = []     # (module, why)
            good: "list[str]" = []
            for mod in by_line[lineno]:
                idents = index.of(mod)
                into = [i for i in idents if i in names]
                if rule.kind == "layer-order":
                    above = [i for i in into if i in rank
                             and rank[i] < rank[layer]]
                    if above:
                        bad.append((mod, f"layer '{above[0]}' is above "
                                         f"'{layer}'"))
                elif rule.kind == "only-import":
                    if not into or layer in into:
                        continue
                    if any(rules_mod.match_pat(a, i) for a in rule.names
                           for i in idents):
                        continue
                    bad.append((mod, f"layer '{into[0]}' is not one of "
                                     f"{', '.join(rule.names)}"))
                else:                                   # public-api
                    hits = [i for i in into
                            if rules_mod.match_pat(rule.from_pat, i)]
                    if not hits:
                        continue
                    if any(rules_mod.match_pat(a, i) for a in rule.names
                           for i in idents):
                        good.append(mod)
                    else:
                        bad.append((mod, f"not through "
                                         f"{', '.join(rule.names)}"))
            # `from db import api` yields "db" and "db.api": the line is
            # fine when a longer import on it is allowed; the report names
            # the most specific module that is not.
            bad = [b for b in bad
                   if not any(g.startswith(b[0] + ".") for g in good)]
            if bad:
                mod, why = max(bad, key=lambda b: (len(b[0]), b[0]))
                out.append(Violation(rule=rule, file=rel, line=lineno,
                                     detail=f"imports {mod} ({why})"))
    return out


# -- forbid-cycles --------------------------------------------------------------

def _layer_edges(rule: rules_mod.Rule, entries: list, layers: list[dict],
                 index: "IdentityIndex | None" = None
                 ) -> "dict[str, dict[str, tuple[str, int, str]]]":
    """{layer: {imported layer: (file, line, module)}} for the layers the
    rule covers; the evidence kept for an edge is its first import in
    (file, line) order, so the report is the same on every run."""
    covered = [l["name"] for l in layers
               if rules_mod.match_pat(rule.from_pat, l.get("name", ""))]
    names = set(covered)
    index = index if index is not None else IdentityIndex(layers)
    edges: dict = {name: {} for name in covered}
    for rel, layer, imports, _content in sorted(entries, key=lambda e: e[0]):
        if layer not in names:
            continue
        for mod, lineno in sorted(imports, key=lambda m: m[1]):
            for ident in index.of(mod):
                if ident in names and ident != layer:
                    edges[layer].setdefault(ident, (rel, lineno, mod))
    return edges


def _strongly_connected(graph: "dict[str, dict]") -> "list[list[str]]":
    """Tarjan's algorithm, iterative (no recursion limit on deep graphs):
    the groups of nodes that can all reach each other, each sorted."""
    index: dict = {}
    low: dict = {}
    on_stack: set = set()
    stack: list = []
    groups: list = []
    counter = 0
    for start in sorted(graph):
        if start in index:
            continue
        work = [(start, iter(sorted(graph[start])))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph.get(child, {})))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                group = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    group.append(member)
                    if member == node:
                        break
                groups.append(sorted(group))
    return groups


def _loop_from(graph: dict, members: set, start: str) -> "list[str]":
    """The shortest loop from `start` back to it through `members` only
    (breadth first, neighbours in name order): [start, ..., start], or []."""
    parent: dict = {}
    queue = [start]
    seen = {start}
    while queue:
        nxt = []
        for node in queue:
            for child in sorted(graph.get(node, {})):
                if child not in members:
                    continue
                if child == start:
                    path = [node]
                    while path[-1] != start:
                        path.append(parent[path[-1]])
                    return list(reversed(path)) + [start]
                if child not in seen:
                    seen.add(child)
                    parent[child] = node
                    nxt.append(child)
        queue = nxt
    return []


#: Up to this many layers in one group the loop reported is the shortest
#: of the whole group (a search from every member, O(k * E)); a larger
#: group gets the shortest loop through its alphabetically first layer.
EXHAUSTIVE_LOOP_SEARCH = 500


def _shortest_cycle(graph: dict, group: "list[str]") -> "list[str]":
    """The shortest loop in the group, searched from every member (see
    EXHAUSTIVE_LOOP_SEARCH); among loops of equal length the one whose
    sequence of names sorts first. It starts and ends at its own
    alphabetically first layer: [a, b, ..., a]."""
    members = set(group)
    best: "list[str]" = []
    starts = group if len(group) <= EXHAUSTIVE_LOOP_SEARCH else group[:1]
    for start in starts:
        loop = _loop_from(graph, members, start)
        if not loop:
            continue
        body = loop[:-1]
        first = body.index(min(body))
        loop = body[first:] + body[:first] + [body[first]]
        if not best or (len(loop), loop) < (len(best), best):
            best = loop
    return best


def _cycle_violations(rule: rules_mod.Rule, entries: list,
                      layers: list[dict],
                      index: "IdentityIndex | None" = None
                      ) -> "list[Violation]":
    """One violation per group of layers that depend on each other in a
    loop: it names the group's shortest loop (_shortest_cycle) and the
    import behind each step.
    The file and line are those of the loop's first import; the identity is
    the loop itself."""
    graph = _layer_edges(rule, entries, layers, index)
    out: list[Violation] = []
    for group in _strongly_connected(graph):
        if len(group) < 2:
            continue
        loop = _shortest_cycle(graph, group)
        if not loop:
            continue
        evidence = []
        for a, b in zip(loop, loop[1:]):
            rel, lineno, mod = graph[a][b]
            evidence.append(f"{a} -> {b}: {rel}:{lineno} imports {mod}")
        first_file, first_line, _mod = graph[loop[0]][loop[1]]
        more = len(group) - (len(loop) - 1)
        detail = "layer cycle " + " -> ".join(loop)
        if more > 0:
            others = ", ".join(sorted(set(group) - set(loop)))
            detail += (f" (and {more} more layer{'' if more == 1 else 's'} "
                       f"caught in loops with these: {others})")
        out.append(Violation(rule=rule, file=first_file, line=first_line,
                             detail=detail, evidence=evidence))
    return out
