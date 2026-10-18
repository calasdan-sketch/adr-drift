"""Warnings that must reach a human (and, under --strict, fail the run)."""

from __future__ import annotations

import sys


class Diagnostics:
    """Collects warnings and prints each one to stderr as it happens.

    stdout stays reserved for the report (human text or the JSON array), so
    a warning can never corrupt `adr-drift check --format json | jq`.
    """

    def __init__(self, echo: bool = True) -> None:
        self.echo = echo  # False: collect only (e.g. the base side of drift)
        self.warnings: "list[str]" = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        if self.echo:
            print(f"warning: {message}", file=sys.stderr)
