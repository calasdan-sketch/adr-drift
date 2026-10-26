"""Pull-request comment text (markdown) and the HTML drift trend page."""

from __future__ import annotations

import html

from . import engine as engine_mod

MARKER = "<!-- adr-drift -->"
MAX_LISTED = 25


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").replace("`", "'")


def markdown(violations, title: str, adr_count: int, fixed=()) -> str:
    """A comment for a pull request. Every violation says which rule, which
    ADR and how to fix it (the same fields as --format json)."""
    out = [MARKER, f"### {title}", ""]
    if not violations:
        out.append(f"No architecture drift ({adr_count} ADR"
                   f"{'' if adr_count == 1 else 's'} read).")
    else:
        n = len(violations)
        out.append(f"**{n} architecture violation{'' if n == 1 else 's'}**")
        out.append("")
        out.append("| where | what | rule | ADR | how to fix |")
        out.append("|---|---|---|---|---|")
        for v in violations[:MAX_LISTED]:
            where = f"{v.file}:{v.line}" if v.line else v.file
            out.append("| `%s` | %s | %s | %s | %s |" % (
                _cell(where), _cell(v.detail), _cell(v.rule.describe()),
                _cell(engine_mod.adr_label(v.rule.adr) or "arch.yaml"),
                _cell(engine_mod.fix_hint(v))))
        if n > MAX_LISTED:
            out.append("")
            out.append(f"... and {n - MAX_LISTED} more (run `adr-drift check` "
                       "for the full list).")
    if fixed:
        out.append("")
        out.append(f"{len(fixed)} violation{'' if len(fixed) == 1 else 's'} fixed.")
    return "\n".join(out) + "\n"


def html_page(points, since: str, rules_summary) -> str:
    """A self-contained page: one row per commit with the violations it added
    and fixed, a bar chart of both, and the rules involved.

    `points`: list of dicts {commit, subject, new, fixed}."""
    esc = html.escape
    peak = max([1] + [p["new"] for p in points] + [p["fixed"] for p in points])
    rows = []
    for p in points:
        rows.append(
            "<tr><td><code>%s</code></td><td>%s</td>"
            "<td class=n>%d</td><td class=f>%d</td>"
            "<td><span class=bar style=\"width:%dpx\"></span>"
            "<span class=bar2 style=\"width:%dpx\"></span></td></tr>" % (
                esc(p["commit"][:8]), esc(p["subject"][:80]), p["new"], p["fixed"],
                int(120 * p["new"] / peak), int(120 * p["fixed"] / peak)))
    net = sum(p["new"] for p in points) - sum(p["fixed"] for p in points)
    rules = "".join("<li>%s: %d</li>" % (esc(r), c)
                    for r, c in sorted(rules_summary.items(), key=lambda kv: -kv[1]))
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>adr-drift trend</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:56rem;padding:0 1rem;color:#1b1b1b;background:#fff}
@media (prefers-color-scheme:dark){body{color:#e6e6e6;background:#161616}}
table{border-collapse:collapse;width:100%%}td,th{padding:.3rem .5rem;border-bottom:1px solid #8884;text-align:left}
.n{color:#b3261e}.f{color:#1a7f37}.bar,.bar2{display:inline-block;height:.7rem;margin-right:2px}
.bar{background:#b3261e}.bar2{background:#1a7f37}
</style></head><body>
<h1>Architecture drift since %s</h1>
<p>%d commit(s). Violations added: %d. Fixed: %d. Net: %+d.</p>
<table><tr><th>commit</th><th>subject</th><th>new</th><th>fixed</th><th></th></tr>
%s
</table>
<h2>Rules involved (violations added)</h2><ul>%s</ul>
<p><small>Made by adr-drift from the git history; no network, no script.</small></p>
</body></html>
""" % (esc(since), len(points), sum(p["new"] for p in points),
       sum(p["fixed"] for p in points), net, "\n".join(rows), rules or "<li>none</li>")
