"""Reading the `Authorization: Bearer <access token>` header the Next.js server sends."""

from __future__ import annotations

from collections.abc import Mapping

from auth_tokens.exceptions import InvalidTokenError, TokenRefusal

AUTHORIZATION_HEADER = "Authorization"
BEARER_SCHEME = "Bearer"
# Far longer than any token we issue; anything longer is refused before it is decoded.
MAX_TOKEN_LENGTH = 2048
REFUSED_MESSAGE = "Invalid or expired token."
# Sent with every 401, so the caller knows a Bearer token is what's missing.
CHALLENGE = f'{BEARER_SCHEME} realm="api"'


def read_bearer_token(headers: Mapping[str, str]) -> str | None:
    """Return the Bearer token, or None when no `Authorization` header was sent.

    Raises:
        InvalidTokenError: The header is there but isn't `Bearer <one token>`.
    """
    value = headers.get(AUTHORIZATION_HEADER)
    if value is None:
        return None
    scheme, _, token = value.partition(" ")
    if scheme != BEARER_SCHEME or not token or " " in token or len(token) > MAX_TOKEN_LENGTH:
        raise InvalidTokenError(TokenRefusal.MALFORMED)
    return token
