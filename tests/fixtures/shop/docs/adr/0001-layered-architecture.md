# 1: Layered architecture

## Status

Accepted

## Context

Handlers were importing DB drivers directly, bypassing the service layer.

## Decision

All database access goes through services. Handlers never touch drivers.

## Constraints

- forbid-import: handlers -> db.drivers
- require-pattern: src/handlers/**.py =~ authenticate\(

## Consequences

Handlers stay thin.
