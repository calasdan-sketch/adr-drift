"""Governance: waivers that expire, and which ADRs enforce anything.

A baseline hides legacy violations forever, and an ADR with no rule is a
decision nothing checks. This module is the paperwork around that:

* `apply_waivers` suppresses violations covered by a waiver in arch.yaml
  (rule, optional path globs, an owner, an end date, a reason) until the
  date passes - after which the violations come back and a warning names
  the owner. A waiver that matches nothing is reported as stale.
* `coverage` says, for every ADR, whether it has an enforceable rule, plus
  the waivers in force and the paths an ADR mentions that no longer exist.
"""

from __future__ import annotations

import datetime
import os
import re

from . import config as config_mod
from . import rules as rules_mod

#: A waiver this close to its end is called out.
SOON_DAYS = 14

#: A waiver that ends later than this is called out (a year is a decision,
#: not a waiver: write it as an ADR).
LONGEST_DAYS = 366

TODAY_ENV = "ADR_DRIFT_TODAY"


def today() -> datetime.date:
    """Today's date; ADR_DRIFT_TODAY=YYYY-MM-DD pins it (tests, replays)."""
    pinned = os.environ.get(TODAY_ENV)
    if pinned:
        try:
            return datetime.date.fromisoformat(pinned.strip())
        except ValueError:
            raise ValueError(f"{TODAY_ENV}={pinned!r} is not a date like "
                             "2027-03-31") from None
    return datetime.date.today()


def _covers(waiver, violation) -> bool:
    if violation.rule.describe() != waiver.rule:
        return False
    return not waiver.paths or any(
        rules_mod.match_glob(p, violation.file) for p in waiver.paths)


def apply_waivers(violations: list, waivers: list, day: datetime.date,
                  warn, stale: bool = True) -> "tuple[list, list]":
    """-> (violations still to report, [(violation, waiver)] suppressed).

    A waiver applies through its `expires` day. Past it, its violations are
    reported again and `warn` says who owned it. `stale=False` (for `drift`,
    which sees only some files) skips the "matches nothing" warning."""
    kept = list(violations)
    waived: list = []
    for w in waivers:
        hit = [v for v in kept if _covers(w, v)]
        label = f"waiver at {config_mod.ARCH_FILE}:{w.line} ({w.rule}; owner {w.owner})"
        if day > w.expires:
            warn(f"{label} expired on {w.expires.isoformat()}: "
                 f"{len(hit)} violation{'' if len(hit) == 1 else 's'} "
                 "reported again - fix them or renew it with a new date")
            continue
        if not hit:
            if stale:
                warn(f"{label} matches no violation: remove it")
            continue
        left = (w.expires - day).days
        if left > LONGEST_DAYS:
            warn(f"{label} runs {left} days: a waiver is a temporary "
                 f"exception, not more than {LONGEST_DAYS} days")
        if left <= SOON_DAYS:
            warn(f"{label} ends in {left} day{'' if left == 1 else 's'} "
                 f"({w.expires.isoformat()})")
        ids = {id(v) for v in hit}
        kept = [v for v in kept if id(v) not in ids]
        waived.extend((v, w) for v in hit)
    return kept, waived


def waiver_note(waived: list) -> str:
    """One line for stderr: how many violations are waived and by whom."""
    if not waived:
        return ""
    owners = sorted({w.owner for _v, w in waived})
    n = len(waived)
    return (f"note: {n} violation{'' if n == 1 else 's'} waived by "
            f"{config_mod.ARCH_FILE} waivers (owners: {', '.join(owners)}); "
            "`adr-drift coverage` lists them")


# -- coverage ---------------------------------------------------------------

_PROSE_RULE_RE = re.compile(
    r"\b(must not|must never|shall not|may not|never|forbidden|forbid)\b",
    re.IGNORECASE)


def coverage(root: str, loaded, day: datetime.date) -> dict:
    """Which ADRs enforce something. States: `enforced` (in force, at least
    one rule), `no-rule` (in force, none: nothing checks this decision),
    `retired` (superseded / deprecated / rejected)."""
    adrs = []
    for a in loaded.adrs:
        shown = os.path.relpath(a["path"], root).replace(os.sep, "/")
        retired = config_mod.is_inactive_status(a["status"])
        state = ("retired" if retired
                 else "enforced" if a["rules"] else "no-rule")
        dead = sorted({ref for ref, _n in a.get("code_refs", [])
                       if not os.path.exists(os.path.join(
                           root, *ref.split("/")))})
        row = {"path": shown, "number": a["number"], "title": a["title"],
               "status": a["status"], "state": state,
               "rules": len(a["rules"]), "owners": list(a.get("owners", [])),
               "missing_paths": dead}
        if state == "no-rule" and _PROSE_RULE_RE.search(
                a.get("decision", "")):
            row["hint"] = ("the decision reads like a rule ('must not', "
                           "'never'...); consider writing it under "
                           "## Constraints")
        adrs.append(row)
    in_force = [r for r in adrs if r["state"] != "retired"]
    enforced = [r for r in in_force if r["state"] == "enforced"]
    pct = round(100 * len(enforced) / len(in_force)) if in_force else 100
    waivers = []
    for w in loaded.waivers:
        left = (w.expires - day).days
        waivers.append({"rule": w.rule, "owner": w.owner,
                        "expires": w.expires.isoformat(),
                        "days_left": left, "reason": w.reason,
                        "paths": list(w.paths),
                        "state": ("expired" if left < 0 else
                                  "ending" if left <= SOON_DAYS else "active")})
    return {"adrs": adrs, "in_force": len(in_force),
            "enforced": len(enforced), "percent": pct, "waivers": waivers}
