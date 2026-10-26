"""Refuse regular expressions that can take exponential time.

forbid-pattern / require-pattern rules come from ADRs and arch.yaml, and a
pull request can edit those. `(a+)+$` on a long line of `a`s runs for years
inside Python's re engine, which cannot be interrupted from outside. So the
dangerous shapes are refused when the rule is LOADED, and matching runs under
a deadline as a second line of defence (engine.py).

The check reads the parsed pattern (not the text) and flags:
  * a repeat of a group that itself contains a repeat with a variable count
    ((a+)+, (a*)*, (a|b+)*, (x{1,3})+ ...); a fixed count such as (\\d{4})+ is
    fine;
  * a repeated alternation whose branches can start with the same character
    ((a|ab)+, (a|a)*).
It is a screen, not a proof: a pattern it lets through is still bounded by the
deadline.
"""

from __future__ import annotations

try:                                    # Python 3.11+
    from re import _constants as _c
    from re import _parser as _p
except ImportError:                     # 3.10
    import sre_constants as _c          # type: ignore[no-redef]
    import sre_parse as _p              # type: ignore[no-redef]

_REPEATS = (_c.MAX_REPEAT, _c.MIN_REPEAT)


def _variable(item) -> bool:
    op, av = item
    return op in _REPEATS and av[0] != av[1]


def _can_match(item, ch: int) -> bool:
    """Can this single pattern element match the character code `ch`?
    Anything not understood answers True (the cautious answer)."""
    op, av = item
    c = chr(ch)
    if op == _c.LITERAL:
        return av == ch
    if op == _c.NOT_LITERAL:
        return av != ch
    if op == _c.ANY:
        return c != "\n"
    if op == _c.IN:
        hit, negate = False, False
        for iop, iav in av:
            if iop == _c.NEGATE:
                negate = True
            elif iop == _c.LITERAL:
                hit = hit or iav == ch
            elif iop == _c.RANGE:
                hit = hit or iav[0] <= ch <= iav[1]
            elif iop == _c.CATEGORY:
                name = str(iav)
                if "NOT" in name:
                    return True
                if "DIGIT" in name:
                    hit = hit or c.isdigit()
                elif "SPACE" in name:
                    hit = hit or c.isspace()
                elif "WORD" in name:
                    hit = hit or c.isalnum() or c == "_"
                else:
                    return True
            else:
                return True
        return hit != negate
    return True


def _walk(sub):
    """Every element of a pattern, through groups and branches (not into the
    body of another repeat, which is judged on its own)."""
    for item in sub:
        yield item
        op, av = item
        if op == _c.SUBPATTERN:
            yield from _walk(av[-1])
        elif op == _c.BRANCH:
            for b in av[1]:
                yield from _walk(b)


def _nested_variable_repeats(body):
    for item in _walk(body):
        op, av = item
        if op in _REPEATS:
            if _variable(item):
                yield item
            yield from _nested_variable_repeats(av[2])


def _first_chars(sub) -> set:
    for op, av in sub:
        if op == _c.LITERAL:
            return {av}
        if op == _c.SUBPATTERN:
            return _first_chars(av[-1])
        return set()
    return set()


def _ambiguous_branch(body) -> bool:
    for op, av in _walk(body):
        if op == _c.BRANCH:
            seen: set = set()
            for branch in av[1]:
                if not branch:
                    return True             # (x|) repeated: an empty way to advance
                first = _first_chars(branch)
                if first & seen:
                    return True
                seen |= first
    return False


def _anchored(body) -> int | None:
    """The literal every pass through this body must start with, if any."""
    if body and body[0][0] == _c.LITERAL:
        return body[0][1]
    if body and body[0][0] == _c.SUBPATTERN:
        return _anchored(body[0][1][-1])
    return None


def _scan(sub) -> str | None:
    for item in sub:
        op, av = item
        if op in _REPEATS:
            low, high, body = av
            if high > 1:
                inner = list(_nested_variable_repeats(body))
                anchor = _anchored(body)
                if inner and not (
                        anchor is not None
                        and not any(_can_match(i[1][2][0], anchor)
                                    for i in inner if i[1][2])):
                    return "a repeated group contains another repeat"
                if _variable(item) and _ambiguous_branch(body):
                    return "a repeated alternation has branches that start alike"
            problem = _scan(body)
            if problem:
                return problem
        elif op == _c.SUBPATTERN:
            problem = _scan(av[-1])
            if problem:
                return problem
        elif op == _c.BRANCH:
            for b in av[1]:
                problem = _scan(b)
                if problem:
                    return problem
    return None


def problem(pattern: str) -> str | None:
    """A reason this pattern can run for exponential time, or None."""
    try:
        tree = _p.parse(pattern)
    except Exception:                   # re.compile reports syntax errors itself
        return None
    return _scan(tree)
