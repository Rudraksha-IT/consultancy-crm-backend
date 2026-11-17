from __future__ import annotations

from django.apps import AppConfig


class AuthTokensConfig(AppConfig):
    """Login tokens (ADR 0003): issued, checked and refused in the schema they belong to."""

    name = "auth_tokens"
