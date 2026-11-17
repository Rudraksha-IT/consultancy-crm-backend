"""Logging a refused login token (issue 58): a WARNING with its reason code, never the token.

An expired token is part of normal use (the Next.js server then refreshes it), so it is INFO.
"""

from __future__ import annotations

import logging

from rest_framework.exceptions import AuthenticationFailed

from auth_tokens.exceptions import TokenRefusal
from auth_tokens.headers import REFUSED_MESSAGE

logger = logging.getLogger(__name__)


class TokenRefusedError(AuthenticationFailed):
    """DRF's 401 for a token that can't be used here, carrying why (for the log only)."""

    def __init__(self, reason: TokenRefusal) -> None:
        super().__init__(REFUSED_MESSAGE)
        self.reason = reason


def log_token_refusal(reason: TokenRefusal) -> None:
    """Write one line saying a token was refused and why."""
    level = logging.INFO if reason is TokenRefusal.EXPIRED else logging.WARNING
    logger.log(level, "token refused: reason=%s", reason.value)
