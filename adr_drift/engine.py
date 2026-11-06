"""Engine: evaluate rules against a scanned repo -> violations."""

from __future__ import annotations

import collections
import dataclasses
import os
import re

from . import config as config_mod
from . import diag as diag_mod
from . import langs
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


def not_baselined(violations: "list[Violation]",
                  entries: "list[dict]") -> "list[Violation]":
    """The violations a baseline (`adr-drift baseline`) does not cover.

    Entries match by identity, not line, so editing above a grandfathered
    violation does not resurrect it. An entry without `detail` (hand-made)
    can only match by exact file, line and rule.
    """
    legacy: collections.Counter = collections.Counter()
    known: list = []
    for e in entries:
        line = e.get("line") if isinstance(e.get("line"), int) else 0
        if isinstance(e.get("detail"), str):
            known.append(((e["file"], e["rule"], e["detail"]), line))
        else:
            legacy[(e["file"], line, e["rule"])] += 1
    remaining: list[Violation] = []
    for v in violations:
        key = (v.file, v.line, v.rule.describe())
        if legacy[key] > 0:
            legacy[key] -= 1
        else:
            remaining.append(v)
    extra, _gone = _unpaired([(identity(v), v.line) for v in remaining], known)
    return [remaining[i] for i in extra]


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _file_layer(layers: list[dict], rel_path: str) -> str:
    for layer in layers:
        for pat in layer.get("paths", []):
            if rules_mod.match_glob(pat, rel_path):
                return layer.get("name", "")
    return ""


def _layer_path_prefixes(layer: dict) -> list[str]:
    """Dotted module prefixes implied by a layer's path globs.

    Only the literal leading components count (up to the first
    wildcard): "src/db/**" -> ["src.db"], "handlers/**" -> ["handlers"].
    """
    prefixes: list[str] = []
    for pat in layer.get("paths", []):
        lit: list[str] = []
        for part in pat.replace("\\", "/").strip("/").split("/"):
            if any(ch in part for ch in "*?["):
                break
            lit.append(part)
        if lit:
            prefixes.append(".".join(lit))
    return prefixes


def _import_identities(mod: str, layers: list[dict]) -> list[str]:
    """Every name an imported module can be known by.

    Besides the literal module ("myapp.db.conn") this includes the layer
    name(s) whose paths contain it ("db") and the module spelt with the
    layer's leading directories kept or stripped, in every combination
    ("src.myapp.db.conn", "myapp.db.conn", "db.conn"). A layer mapped to
    src/myapp/db/** is reached by `import myapp.db.conn` (src/ is the
    import root), by `import src.myapp.db.conn` and, for a flat layout,
    by `import db.conn`: without this the rule naming the layer silently
    never fires for the spelling the code actually uses.
    """
    ids = [mod]
    dotted = mod.replace("/", ".").strip(".")
    for layer in layers:
        name = layer.get("name", "")
        for prefix in _layer_path_prefixes(layer):
            parts = prefix.split(".")
            for k in range(len(parts)):
                # the layer's path with its first k directories stripped
                if not rules_mod.match_pat(".".join(parts[k:]), mod):
                    continue
                if name and name not in ids:
                    ids.append(name)
                canonical = (parts[:k] + dotted.split("."))
                for j in range(len(parts)):  # never strips the layer's own dir
                    form = ".".join(canonical[j:])
                    if form and form not in ids:
                        ids.append(form)
                break
    return ids


class IdentityIndex:
    """_import_identities() for many modules against one list of layers,
    without its cost: the layers' path prefixes are indexed once (by every
    suffix _import_identities() tries), so a module costs one dictionary
    look-up per dotted prefix of its own name instead of a pass over every
    layer, and each module is worked out once. A repo with L layers and N
    imports was O(N * L); it is now about O(N * depth). Same answers, in the
    same order, as _import_identities() (tests compare the two)."""

    def __init__(self, layers: list[dict]):
        self._layers = layers
        # suffix -> [(layer index, prefix index, k)], k ascending per prefix
        self._by_suffix: dict = collections.defaultdict(list)
        self._parts: dict = {}
        for li, layer in enumerate(layers):
            for pi, prefix in enumerate(_layer_path_prefixes(layer)):
                parts = prefix.split(".")
                self._parts[(li, pi)] = parts
                for k in range(len(parts)):
                    key = rules_mod._norm(".".join(parts[k:]))
                    self._by_suffix[key].append((li, pi, k))
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
        self._cache[mod] = ids
        return ids


def _from_hit(rule: rules_mod.Rule, rel: str, layer: str) -> bool:
    """Does the rule's "from" side cover this file? It matches the file's
    layer name OR a module prefix of its path (so rules work without
    layers)."""
    if layer and rules_mod.match_pat(rule.from_pat, layer):
        return True
    mod_prefix = rel.replace("/", ".").rsplit(".", 1)[0]
    return rules_mod.match_pat(rule.from_pat, mod_prefix)


def check(root: str, layers: list[dict], all_rules: list[rules_mod.Rule],
          strict_py_only: bool = False,
          diag: "diag_mod.Diagnostics | None" = None) -> list[Violation]:
    """Scan `root` and evaluate every rule.

    Warnings (a rule or layer that covers no file, ...) go to `diag`, which
    prints them on stderr; the caller decides whether they fail the run.
    """
    diag = diag if diag is not None else diag_mod.Diagnostics()
    entries: list[tuple[str, str, list[tuple[str, int]], str]] = []
    go_module = langs.go_module_path(root)
    seen: dict[str, list[int]] = {}   # language -> [files, files with imports]
    for path in scanner_mod.iter_source_files(root):
        rel = _rel(root, path)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            # A file that cannot be read is not checked: never silently.
            diag.warn(f"{rel}: cannot read ({e.strerror or type(e).__name__})"
                      "; not checked")
            continue
        imports, engine = scanner_mod.extract_imports(
            rel, data, strict_py_only=strict_py_only, diag=diag,
            go_module=go_module)
        if engine == "regex":
            counts = seen.setdefault(langs.language_of(rel) or "js", [0, 0])
            counts[0] += 1
            counts[1] += bool(imports)
        content = scanner_mod.decode_source(rel, data)
        entries.append((rel, _file_layer(layers, rel), imports, content))
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
             all_rules: list[rules_mod.Rule]) -> list[Violation]:
    """Evaluate rules against pre-scanned entries.

    Each entry: (rel_path, layer, imports[(module, line)], content).
    Lets `drift` check historical git content through the same engine.
    """
    violations: list[Violation] = []
    index = IdentityIndex(layers)
    for rule in all_rules:
        if rule.kind == "forbid-cycles":
            violations.extend(_cycle_violations(rule, entries, layers,
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
