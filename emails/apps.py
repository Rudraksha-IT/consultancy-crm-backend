from __future__ import annotations

from django.apps import AppConfig


class EmailsConfig(AppConfig):
    """The email builder, its templates and the demo command (ADR 0023). No models."""

    name = "emails"
