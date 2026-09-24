"""Version ranges for catalog entries and checks.

A range is a comma separated list of clauses, and every clause must hold:

    ">=2.1100.0"    2.1100.0 and later
    "<13"           before 13
    ">=6,<7"        6.x only

Versions compare as tuples of integers, so "2.10" is later than "2.9".
Suffixes such as "-beta" are ignored.
"""

from __future__ import annotations

import re

_CLAUSE = re.compile(r"^\s*(>=|<=|==|>|<)\s*(\d+(?:\.\d+)*)\s*$")
_NUMBER = re.compile(r"\d+(?:\.\d+)*")
RANGE_PATTERN = r"^\s*(>=|<=|==|>|<)\s*\d+(\.\d+)*\s*(,\s*(>=|<=|==|>|<)\s*\d+(\.\d+)*\s*)*$"


def parse(version: str) -> tuple[int, ...] | None:
    """Return the first dotted number in `version`, or None."""
    match = _NUMBER.search(version or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)), b + (0,) * (size - len(b))


def matches(version: str, spec: str) -> bool:
    """True when `version` is inside the range `spec`."""
    got = parse(version)
    if got is None:
        raise ValueError(f"not a version: {version!r}")
    for clause in spec.split(","):
        match = _CLAUSE.match(clause)
        if not match:
            raise ValueError(f"not a version range: {spec!r}")
        operator, bound = match.group(1), tuple(int(p) for p in match.group(2).split("."))
        left, right = _pad(got, bound)
        ok = {
            ">=": left >= right,
            "<=": left <= right,
            ">": left > right,
            "<": left < right,
            "==": left == right,
        }[operator]
        if not ok:
            return False
    return True
