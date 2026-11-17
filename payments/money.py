"""Amounts in NPR, written the way people in Nepal write them (lakh and crore grouping)."""

from __future__ import annotations

_LAST_GROUP = 3
_OTHER_GROUPS = 2


def format_npr(amount: int) -> str:
    """Return e.g. "NPR 1,25,000": the last three digits, then groups of two."""
    digits = str(amount)
    head, tail = digits[:-_LAST_GROUP], digits[-_LAST_GROUP:]
    groups: list[str] = []
    while head:
        groups.insert(0, head[-_OTHER_GROUPS:])
        head = head[:-_OTHER_GROUPS]
    return f"NPR {','.join([*groups, tail])}"
