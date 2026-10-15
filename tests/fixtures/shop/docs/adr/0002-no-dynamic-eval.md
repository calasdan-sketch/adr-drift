# 2: No dynamic eval

## Status

Accepted

## Decision

No eval() anywhere in src/.

## Constraints

- forbid-pattern: src/**.py !~ \beval\(

## Consequences

Safer.
