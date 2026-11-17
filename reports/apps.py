from __future__ import annotations

from django.apps import AppConfig


class ReportsConfig(AppConfig):
    """Admin-only monthly charts and reports, read from History and Visits (issue 28)."""

    name = "reports"
