# 2: No handlers import db

## Status

Accepted

## Context

Why this decision is being made.

## Decision

What we decided.

## Constraints

Machine-checkable rules ADR Drift enforces in CI. Examples:

- forbid-import: handlers -> db
- forbid-pattern: src/**.py !~ \beval\(
- require-pattern: src/handlers/**.py =~ authenticate\(

## Consequences

What follows from this decision.
