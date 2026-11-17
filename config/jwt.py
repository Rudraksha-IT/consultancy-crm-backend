"""Login token settings read from the environment (ADR 0003)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured

MAX_ACCESS_TOKEN_MINUTES = 15


def read_access_token_lifetime(environ: Mapping[str, str]) -> timedelta:
    """Return how long an access token lasts: `JWT_ACCESS_TOKEN_MINUTES`, 15 when unset.

    It can only be shortened (1 to 15 minutes), e.g. to watch the Next.js server refresh.

    Raises:
        ImproperlyConfigured: The value isn't a whole number from 1 to 15.
    """
    value = environ.get("JWT_ACCESS_TOKEN_MINUTES", str(MAX_ACCESS_TOKEN_MINUTES))
    if not value.isdigit() or not 1 <= int(value) <= MAX_ACCESS_TOKEN_MINUTES:
        message = (
            f"JWT_ACCESS_TOKEN_MINUTES must be a whole number from 1 to {MAX_ACCESS_TOKEN_MINUTES}."
        )
        raise ImproperlyConfigured(message)
    return timedelta(minutes=int(value))
