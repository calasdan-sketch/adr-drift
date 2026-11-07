"""The layer dependency graph: `graph` draws it, `suggest` proposes rules.

Both read what the code does today. `suggest` never writes a file: it prints
rules that already hold, for a person to review and paste.
"""

from __future__ import annotations

from . import engine as engine_mod
from . import report as report_mod


def layer_graph(entries: list, layers: list) -> "dict[str, dict[str, dict]]":
    """{layer: {imported layer: {"count": n, "file": f, "line": l,
    "module": m}}}; the evidence is the first import in (file, line) order,
    so the output is the same on every run. Layers with no edges are keys."""
    names = [l["name"] for l in layers if l.get("name")]
    index = engine_mod.IdentityIndex(
        layers, engine_mod.known_suffixes(e[0] for e in entries)
        if any(engine_mod._complex_globs(l) for l in layers) else None)
    graph: dict = {n: {} for n in names}
    known = set(names)
    for rel, layer, imports, _content in sorted(entries, key=lambda e: e[0]):
        if layer not in known:
            continue
        counted: set = set()    # "from m import n" is m and m.n: one import
        for mod, lineno in sorted(imports, key=lambda m: m[1]):
            for ident in index.of(mod):
                if (ident in known and ident != layer
                        and (lineno, ident) not in counted):
                    counted.add((lineno, ident))
                    edge = graph[layer].get(ident)
                    if edge is None:
                        graph[layer][ident] = {"count": 1, "file": rel,
                                               "line": lineno, "module": mod}
                    else:
                        edge["count"] += 1
    return graph


def cyclic_groups(graph: dict) -> "list[list[str]]":
    """Groups of layers that depend on each other in a loop."""
    return [sorted(g) for g in engine_mod._strongly_connected(graph)
            if len(g) > 1]


def _mm(name: str) -> str:
    """A label Mermaid shows as text: entity codes for the characters it
    would otherwise read as markup."""
    text = report_mod.escape_terminal(name)
    for ch, code in (("&", "#amp;"), ('"', "#quot;"), ("<", "#lt;"),
                     (">", "#gt;"), ("`", "#96;")):
        text = text.replace(ch, code)
    return text


def _dq(name: str) -> str:
    """A double-quoted DOT string."""
    return report_mod.escape_terminal(name).replace("\\", "\\\\").replace(
        '"', '\\"')


def mermaid(graph: dict) -> str:
    groups = cyclic_groups(graph)
    ids = {n: f"n{i}" for i, n in enumerate(graph)}
    lines = ["flowchart TD"]
    for n in graph:
        lines.append(f'  {ids[n]}["{_mm(n)}"]')
    bad = []
    k = 0
    for src in graph:
        for dst, e in sorted(graph[src].items()):
            lines.append(f'  {ids[src]} -->|{e["count"]}| {ids[dst]}')
            if any(src in g and dst in g for g in groups):
                bad.append(k)
            k += 1
    if bad:
        lines.append("  linkStyle " + ",".join(map(str, bad))
                     + " stroke:#c00,stroke-width:2px")
    return "\n".join(lines) + "\n"


def dot(graph: dict) -> str:
    groups = cyclic_groups(graph)
    lines = ["digraph layers {", "  rankdir=TB;"]
    for n in graph:
        lines.append(f'  "{_dq(n)}";')
    for src in graph:
        for dst, e in sorted(graph[src].items()):
            red = any(src in g and dst in g for g in groups)
            style = ' color="red"' if red else ""
            lines.append(f'  "{_dq(src)}" -> "{_dq(dst)}" '
                         f'[label="{e["count"]}"{style}];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def as_json(graph: dict) -> list:
    return [{"from": s, "to": d, **e}
            for s in graph for d, e in sorted(graph[s].items())]


def topological(graph: dict) -> "list[str]":
    """Layers highest first (a layer that imports another is above it);
    ties alphabetical. Only meaningful when there is no cycle."""
    indeg = {n: 0 for n in graph}       # how many layers import n
    for s in graph:
        for d in graph[s]:
            indeg[d] += 1
    order: list = []
    ready = sorted(n for n, k in indeg.items() if k == 0)
    while ready:
        n = ready.pop(0)
        order.append(n)
        for d in sorted(graph[n]):
            indeg[d] -= 1
            if indeg[d] == 0:
                ready.append(d)
        ready.sort()
    return order


def suggest(graph: dict, existing_kinds=()) -> dict:
    """Rules that hold today: {"rules": [...], "notes": [...]}."""
    groups = cyclic_groups(graph)
    rules: list = []
    notes: list = []
    odd = [n for n in graph if not n or any(c in n for c in " \t,>*#:")]
    if odd:
        notes.append("layer names that a rule cannot spell (spaces or any of "
                     ", > * # :): " + ", ".join(sorted(odd)))
        return {"rules": rules, "notes": notes}
    if len(graph) < 2:
        notes.append("fewer than two layers: nothing to suggest")
        return {"rules": rules, "notes": notes}
    if groups:
        for g in groups:
            notes.append("layers that import each other in a loop: "
                         + ", ".join(g) + " - fix or baseline this before "
                         "forbidding cycles")
        return {"rules": rules, "notes": notes}
    if "forbid-cycles" not in existing_kinds:
        rules.append("forbid-cycles: *")
    if "layer-order" not in existing_kinds:
        order = topological(graph)
        rules.append("layer-order: " + " > ".join(order))
        unrelated = [n for n in order if not graph[n]
                     and not any(n in graph[s] for s in graph)]
        if unrelated:
            notes.append("these layers import no other layer and nobody "
                         "imports them, so their place in the order is "
                         "arbitrary (alphabetical): " + ", ".join(unrelated))
        notes.append("layer-order is the order the code follows today; check "
                     "that it is the order you want before you commit it")
    return {"rules": rules, "notes": notes}
