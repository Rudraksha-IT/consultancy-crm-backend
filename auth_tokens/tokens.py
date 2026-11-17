"""simplejwt tokens signed with `JWT_SIGNING_KEY`, read from the settings each time they're used."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.tokens import Token

ALGORITHM = "HS256"


class _SignedWithSettingsKey(Token):
    """Sign and verify with the settings' key; simplejwt freezes its own at import.

    Lifetimes are set from the settings when a token is issued (auth_tokens.services).
    """

    def get_token_backend(self) -> TokenBackend:
        """Return a backend that only accepts HS256 tokens signed with `JWT_SIGNING_KEY`."""
        return TokenBackend(ALGORITHM, settings.JWT_SIGNING_KEY)


class AccessToken(_SignedWithSettingsKey):
    """The short-lived token sent as `Authorization: Bearer` on every call."""

    token_type = "access"  # noqa: S105  # the type claim, not a secret
    lifetime = timedelta(minutes=15)


class RefreshToken(_SignedWithSettingsKey):
    """The long-lived token that renews the access token, once (rotation)."""

    token_type = "refresh"  # noqa: S105  # the type claim, not a secret
    lifetime = timedelta(days=7)
