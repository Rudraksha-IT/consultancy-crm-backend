from __future__ import annotations

from django.apps import AppConfig


class CommunicationConfig(AppConfig):
    """Email to Students through the product's own account (ADR 0023), per Consultancy schema."""

    name = "communication"
