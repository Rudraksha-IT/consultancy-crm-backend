from __future__ import annotations


class InvalidCodeError(Exception):
    """The Code is wrong, expired, used, replaced or out of attempts (never saying which)."""


class CodeLimitReachedError(Exception):
    """Too many Codes were sent to this person for this purpose in the last hour."""


class WrongCodeScopeError(Exception):
    """A Platform Admin's Code was asked for inside a Consultancy (only the Platform has them)."""
