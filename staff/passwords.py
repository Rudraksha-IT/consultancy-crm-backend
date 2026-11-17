"""The password rules every Staff password must meet (spec 18c); the frontend shows the same."""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import StrEnum

MIN_PASSWORD_LENGTH = 8


class PasswordRule(StrEnum):
    """One rule, named as the API returns it when unmet."""

    MIN_LENGTH = "min_length"
    CAPITAL = "capital"
    SMALL = "small"
    NUMBER = "number"
    SYMBOL = "symbol"


_CHECKS: dict[PasswordRule, Callable[[str], bool]] = {
    PasswordRule.MIN_LENGTH: lambda password: len(password) >= MIN_PASSWORD_LENGTH,
    PasswordRule.CAPITAL: lambda password: re.search(r"[A-Z]", password) is not None,
    PasswordRule.SMALL: lambda password: re.search(r"[a-z]", password) is not None,
    PasswordRule.NUMBER: lambda password: re.search(r"[0-9]", password) is not None,
    PasswordRule.SYMBOL: lambda password: re.search(r"[^A-Za-z0-9]", password) is not None,
}


def unmet_password_rules(password: str) -> list[PasswordRule]:
    """Return the rules `password` doesn't meet yet, in the order they're shown."""
    return [rule for rule in PasswordRule if not _CHECKS[rule](password)]
