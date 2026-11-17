from __future__ import annotations

from django.apps import AppConfig


class HistoryConfig(AppConfig):
    """The append-only trail of important changes to each Student, in each Consultancy's schema."""

    name = "history"
