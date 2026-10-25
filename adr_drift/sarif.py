"""SARIF 2.1.0 output, for GitHub code scanning and other viewers."""

from __future__ import annotations

import hashlib
import urllib.parse

from . import engine as engine_mod

SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI = "https://github.com/calasdan-sketch/adr-drift"

_KIND_HELP = {
    "forbid-import": "A module imports something this architecture rule "
                     "does not allow. Remove the import or go through an "
                     "allowed layer.",
    "forbid-pattern": "The file contains code an architecture rule forbids. "
                      "Remove or rewrite it.",
    "require-pattern": "The file lacks something an architecture rule "
                       "requires. Add it.",
}


def rule_id(rule) -> str:
    """A stable id: the kind plus a short hash of where the rule was written
    and what it says (not its line, which moves)."""
    digest = hashlib.sha1(
        f"{rule.source}\0{rule.describe()}".encode("utf-8")).hexdigest()[:8]
    return f"adr-drift/{rule.kind}/{digest}"


def _rule_entry(rule) -> dict:
    label = engine_mod.adr_label(rule.adr)
    full = f"{rule.describe()} (rule in {rule.where()}"
    full += f", {label})" if label else ")"
    entry = {
        "id": rule_id(rule),
        "name": rule.kind,
        "shortDescription": {"text": rule.describe()},
        "fullDescription": {"text": full},
        "help": {"text": _KIND_HELP[rule.kind]},
        "defaultConfiguration": {"level": "error"},
        "properties": {"source": rule.where()},
    }
    if rule.adr:
        entry["properties"]["adr"] = dict(rule.adr)
    if rule.allow_until:
        entry["properties"]["allowUntil"] = rule.allow_until.isoformat()
    return entry


def _uri(path: str) -> str:
    """A repository-relative path as a URI reference: a space or a '#' in a
    file name must not break it."""
    return urllib.parse.quote(path, safe="/")


def _fingerprint(v) -> str:
    # same identity as a baseline entry: file, rule, what was found - not
    # the line, so the alert survives edits above it
    key = "\0".join(str(x) for x in engine_mod.identity(v))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def build(violations, rules, version: str) -> dict:
    """The SARIF log (a dict) for `violations`; `rules` are all the rules
    that were checked, so the viewer lists the ones that found nothing too."""
    entries: "list[dict]" = []
    index: "dict[str, int]" = {}
    for rule in rules:
        rid = rule_id(rule)
        if rid not in index:
            index[rid] = len(entries)
            entries.append(_rule_entry(rule))
    results = []
    for v in violations:
        rid = rule_id(v.rule)
        if rid not in index:   # a rule that was not passed in
            index[rid] = len(entries)
            entries.append(_rule_entry(v.rule))
        label = engine_mod.adr_label(v.rule.adr)
        text = f"{v.detail}: {v.rule.describe()}."
        if label:
            text += f" {label}."
        text += f" {engine_mod.fix_hint(v)}"
        result = {
            "ruleId": rid,
            "ruleIndex": index[rid],
            "level": "error",
            "message": {"text": text},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": _uri(v.file),
                                         "uriBaseId": "%SRCROOT%"},
                    # SARIF lines start at 1; line 0 means "the whole file"
                    "region": {"startLine": max(v.line, 1)},
                },
            }],
            "partialFingerprints": {"adrDrift/v1": _fingerprint(v)},
        }
        if v.expired:
            result["properties"] = {"exceptionExpired": v.expired}
        results.append(result)
    return {
        "$schema": SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "adr-drift",
                "version": version,
                "informationUri": INFORMATION_URI,
                "rules": entries,
            }},
            "originalUriBaseIds": {"%SRCROOT%": {"description": {
                "text": "The --root directory of the run."}}},
            "results": results,
            "invocations": [{"executionSuccessful": True}],
        }],
    }
