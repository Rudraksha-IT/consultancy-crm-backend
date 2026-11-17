from __future__ import annotations

from django.apps import AppConfig


class CommunicationConfig(AppConfig):
    """SMS and email to Students from the office's own accounts, per Consultancy schema."""

    name = "communication"
