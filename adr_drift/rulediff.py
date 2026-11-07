"""`rules-diff`: how the rules themselves changed between two commits.

`check` and `drift` judge the code against the rules. Nothing judged the
rules: a pull request can pass every check by deleting the rule, marking its
ADR superseded, or waiving it. This lists those moves so a reviewer sees
them.
"""

from __future__ import annotations

import os
import tempfile

from . import config as config_mod
from . import githelp


MAX_CONFIG_BYTES = 2 * 1024 * 1024   # an ADR or arch.yaml is never this big


def _safe(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    return not (rel.startswith("/") or ".." in parts
                or (len(rel) > 1 and rel[1] == ":"))


def load_at(root: str, ref: str, adr_dir=None):
    """config_mod.Loaded for the commit `ref` (the working tree is not read),
    or a string saying why it does not load."""
    files = githelp.config_files(root, ref)
    with tempfile.TemporaryDirectory(prefix="adr-drift-") as tmp:
        for rel, data in files.items():
            if not _safe(rel):
                continue
            if len(data) > MAX_CONFIG_BYTES:
                continue
            dest = os.path.join(tmp, *rel.split("/"))
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(data)
            except OSError:
                continue          # a name this file system cannot hold
        try:
            return config_mod.load_all(tmp, adr_dir)
        except config_mod.ConfigError as e:
            return "; ".join(e.problems[:3])
        except ValueError as e:
            return str(e)


def _rules(loaded) -> dict:
    out: dict = {}
    for r in loaded.rules:
        out.setdefault(r.describe(), r.where())
    return out


def compare(old, new) -> dict:
    """{"weakened": [...], "strengthened": [...], "notes": [...]}; each item
    is a one-line string. Weakened: a rule gone, an ADR retired, a waiver
    added, extended or widened."""
    weak: list = []
    strong: list = []
    notes: list = []
    if isinstance(old, str) or isinstance(new, str):
        if isinstance(old, str):
            notes.append("the base commit's rules do not load: " + old)
        if isinstance(new, str):
            weak.append("the rules do not load at HEAD: " + new)
        return {"weakened": weak, "strengthened": strong, "notes": notes}
    a, b = _rules(old), _rules(new)
    retired_now = {p for p, _s, _n in new.inactive}
    for text, where in sorted(a.items()):
        if text not in b:
            weak.append(f"rule removed or no longer enforced: {text} "
                        f"(was {where})")
        elif b[text] != where:
            notes.append(f"rule moved: {text} ({where} -> {b[text]})")
    for text, where in sorted(b.items()):
        if text not in a:
            strong.append(f"rule added: {text} ({where})")
    new_layers = {l["name"]: set(l["paths"]) for l in new.layers}
    for l in old.layers:
        now = new_layers.get(l["name"])
        if now is None:
            weak.append(f"layer removed: {l['name']} (rules naming it "
                        "now match nothing)")
            continue
        lost = sorted(set(l["paths"]) - now)
        if lost:
            weak.append(f"layer {l['name']} no longer covers: "
                        + ", ".join(lost))
        gained = sorted(now - set(l["paths"]))
        if gained:
            notes.append(f"layer {l['name']} now also covers: "
                         + ", ".join(gained))
    old_status = {p: s for p, s, _n in old.inactive}
    for p, s, n in new.inactive:
        if p not in old_status:
            weak.append(f"ADR retired: {p} is now '{s}'"
                        + (f" ({n} rule{'s' if n != 1 else ''} not "
                           "enforced)" if n else ""))
    for p in sorted(set(old_status) - retired_now):
        notes.append(f"ADR back in force: {p}")
    ow = {(w.rule, tuple(w.paths), w.owner): w for w in old.waivers}
    for w in new.waivers:
        key = (w.rule, tuple(w.paths), w.owner)
        prev = ow.get(key)
        scope = ", ".join(w.paths) if w.paths else "every file"
        if prev is None:
            weak.append(f"waiver added: {w.rule} for {scope}, owner "
                        f"{w.owner}, until {w.expires} ({w.reason})")
        elif w.expires > prev.expires:
            weak.append(f"waiver extended: {w.rule} from {prev.expires} to "
                        f"{w.expires}, owner {w.owner}")
    nw = {(w.rule, tuple(w.paths), w.owner) for w in new.waivers}
    for key, w in ow.items():
        if key not in nw:
            strong.append(f"waiver removed: {w.rule}")
    return {"weakened": weak, "strengthened": strong, "notes": notes}
