from __future__ import annotations

from enum import StrEnum


class TokenRefusal(StrEnum):
    """Why a login token was refused: logged as a reason code, never told to the caller."""

    MALFORMED = "malformed"
    INVALID = "invalid"
    EXPIRED = "expired"
    WRONG_CONSULTANCY = "wrong_consultancy"
    WRONG_KIND = "wrong_kind"
    MISSING_CLAIM = "missing_claim"
    OUT_OF_DATE = "out_of_date"
    REVOKED = "revoked"


class InvalidTokenError(Exception):
    """A login token is malformed, expired, revoked, out of date or meant for somewhere else."""

    def __init__(self, reason: TokenRefusal = TokenRefusal.INVALID) -> None:
        super().__init__(reason.value)
        self.reason = reason
