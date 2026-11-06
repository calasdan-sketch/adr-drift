"""Output that leaves the process: terminal-safe text, GitHub annotations,
SARIF.

Everything adr-drift prints can carry text from the repository it checks -
file names, imported module names, ADR paths, rule patterns - and none of
that is trusted. On a terminal an escape sequence in a file name can rewrite
the screen, and a bidi override (U+202E) can make a reviewer read a line
differently from what it says; in GitHub Actions a newline in a message
would start a workflow command of its own. So:

* escape_terminal() makes every control, format (bidi, zero-width) and
  separator character visible as a \\x.. / \\u.... escape. The CLI applies
  it to everything written to stdout and stderr.
* github_annotations() escapes workflow-command data and properties as the
  Actions runner expects (%25, %0D, %0A, and in properties also %3A, %2C).
* sarif_log() is plain json.dumps (ASCII-only output). Text fields go
  through escape_terminal() too, so a file name that is not valid UTF-8
  (Python holds it as lone surrogates) becomes a visible \\udcff escape
  instead of an invalid JSON string; the URI percent-encodes its raw bytes.

Both machine formats name files from the root of the git repository (the
path that GitHub matches against the pull request), not from --root: the
caller passes `prefix`, the --root directory relative to that top level.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import unicodedata
import urllib.parse

INFORMATION_URI = "https://github.com/calasdan-sketch/adr-drift"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"

# Cc (controls, but not \n and \t), Cf (format: bidi controls, zero-width
# characters, the BOM, soft hyphen ...), Cs (lone surrogates, what Python
# makes of a file name that is not valid UTF-8), Zl/Zp (U+2028, U+2029).
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
_KEEP = frozenset("\n\t")


def _is_unsafe(ch: str) -> bool:
    return ch not in _KEEP and unicodedata.category(ch) in _UNSAFE_CATEGORIES


# Fast path: most text is printable ASCII, which needs no per-character look.
_MAYBE_UNSAFE = re.compile(r"[^\n\t\x20-\x7e]")


def _escape_char(ch: str) -> str:
    n = ord(ch)
    if n < 0x100:
        return f"\\x{n:02x}"
    if n < 0x10000:
        return f"\\u{n:04x}"
    return f"\\U{n:08x}"


def escape_terminal(text: str) -> str:
    """`text` with every character that could hide or change what a reader
    sees (terminal escapes, bidi overrides, zero-width characters, a lone
    \\r) written out as a visible escape. Newlines and tabs are kept."""
    if not _MAYBE_UNSAFE.search(text):
        return text
    return "".join(_escape_char(c) if _is_unsafe(c) else c for c in text)


# -- GitHub Actions workflow commands -------------------------------------------

def _gh_data(text: str) -> str:
    return (text.replace("%", "%25").replace("\r", "%0D")
            .replace("\n", "%0A"))


def _gh_property(text: str) -> str:
    return _gh_data(text).replace(":", "%3A").replace(",", "%2C")


def _has_surrogates(text: str) -> bool:
    return any("\ud800" <= c <= "\udfff" for c in text)


def gh_command(kind: str, message: str, file: str = "", line: int = 0,
               title: str = "") -> str:
    """One `::error file=..,line=..,title=..::message` line. A file name
    that is not valid UTF-8 cannot be named to the runner: the annotation
    then has no file (the message still names it, escaped)."""
    props = []
    if file and _has_surrogates(file):
        message = f"{escape_terminal(file)}: {message}"
        file = ""
    if file:
        props.append(f"file={_gh_property(file)}")
        if line and line > 0:
            props.append(f"line={int(line)}")
    if title:
        props.append(f"title={_gh_property(title)}")
    head = f"::{kind}" + (" " + ",".join(props) if props else "")
    return f"{head}::{_gh_data(message)}"


def violation_message(v) -> str:
    """One line saying what is wrong and which rule (and ADR) says so."""
    where = (f"{v.rule.source}:{v.rule.line_no}" if v.rule.line_no
             else v.rule.source)
    text = f"{v.detail} (rule: {v.rule.describe()}, from {where})"
    if v.evidence:
        text += "; " + "; ".join(v.evidence)
    return text


def _join(prefix: str, path: str) -> str:
    return (prefix.rstrip("/") + "/" + path) if prefix else path


def github_annotations(violations, warnings=(), prefix: str = "",
                       retired=()) -> "list[str]":
    """Workflow commands: an error annotation on each violation's line, a
    warning annotation for each warning and each retired ADR (on the ADR
    file: retiring a decision must be as visible as breaking it).
    `retired` holds (path, status, rule count) as config.Loaded.inactive."""
    lines = [gh_command("error", violation_message(v),
                        file=_join(prefix, v.file), line=v.line,
                        title=f"adr-drift: {v.rule.kind}")
             for v in violations]
    lines.extend(gh_command("warning", w, title="adr-drift")
                 for w in warnings)
    lines.extend(gh_command("warning", retired_message(path, status, n),
                            file=_join(prefix, path),
                            title="adr-drift: ADR retired")
                 for path, status, n in retired if n)
    return lines


def retired_message(path: str, status: str, n: int) -> str:
    return (f"{path} is {status!r}: its {n} rule{'' if n == 1 else 's'} "
            f"{'is' if n == 1 else 'are'} not enforced")


# -- SARIF 2.1.0 ---------------------------------------------------------------

def rule_id(rule) -> str:
    """A stable id for a rule: its kind and a short hash of what it says, so
    code scanning keeps one alert history per rule across runs."""
    return f"{rule.kind}/{_digest(rule.describe())[:12]}"


def _uri(path: str) -> str:
    """A repo-relative path as a relative URI reference ("%" and spaces
    percent-encoded, "/" kept). Encoded from the file name's own bytes, so
    a name that is not valid UTF-8 still names the right file."""
    raw = path.replace("\\", "/").encode("utf-8", "surrogateescape")
    return urllib.parse.quote(raw, safe="/")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def _text(text: str) -> str:
    """A SARIF text field: nothing that could hide or mislead a reader."""
    return escape_terminal(text)


def sarif_log(violations, version: str, identity, prefix: str = "",
              retired=()) -> dict:
    """A SARIF 2.1.0 log of `violations`. `identity` is the function that
    says when two violations are the same one (engine.identity): it becomes
    the partial fingerprint, so code scanning tracks an alert across edits
    above it, as `check --baseline` does. `prefix` is the --root directory
    from the top of the git repository; `retired` (as config.Loaded.inactive)
    becomes tool notifications of level "warning"."""
    rules: "list[dict]" = []
    index: "dict[str, int]" = {}
    results: "list[dict]" = []
    seen: collections.Counter = collections.Counter()
    for v in violations:
        rid = rule_id(v.rule)
        if rid not in index:
            index[rid] = len(rules)
            source = (f"{v.rule.source}:{v.rule.line_no}" if v.rule.line_no
                      else v.rule.source)
            rules.append({
                "id": rid,
                "name": v.rule.kind,
                "shortDescription": {"text": _text(v.rule.describe())[:1024]},
                "fullDescription": {
                    "text": _text(f"{v.rule.describe()} - declared in "
                                  f"{source}")[:1024]},
                "helpUri": INFORMATION_URI + "#the-constraint-dsl",
                "defaultConfiguration": {"level": "error"},
                "properties": {"tags": ["architecture", "adr"],
                               "precision": "high"},
            })
        key = "\0".join(str(part) for part in identity(v))
        seen[key] += 1
        fingerprint = _digest(key)
        location: dict = {"artifactLocation": {"uri": _uri(_join(prefix,
                                                                 v.file))}}
        if v.line and v.line > 0:
            location["region"] = {"startLine": int(v.line)}
        results.append({
            "ruleId": rid,
            "ruleIndex": index[rid],
            "level": "error",
            "message": {"text": _text(violation_message(v))},
            "locations": [{"physicalLocation": location}],
            "partialFingerprints": {
                "adrDriftIdentity/v1": f"{fingerprint}:{seen[key]}"},
        })
    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "adr-drift",
                "version": version,
                "informationUri": INFORMATION_URI,
                "rules": rules,
            }},
            "invocations": [{
                "executionSuccessful": True,
                "toolExecutionNotifications": [
                    {"level": "warning",
                     "message": {"text": _text(retired_message(path, status,
                                                               n))},
                     "locations": [{"physicalLocation": {
                         "artifactLocation": {"uri": _uri(_join(prefix,
                                                                path))}}}]}
                    for path, status, n in retired if n],
            }],
            "results": results,
        }],
    }


def sarif_text(violations, version: str, identity, prefix: str = "",
               retired=()) -> str:
    return json.dumps(sarif_log(violations, version, identity, prefix,
                                retired), indent=2)
