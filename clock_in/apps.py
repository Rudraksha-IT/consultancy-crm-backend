from __future__ import annotations

from django.apps import AppConfig


class ClockInConfig(AppConfig):
    """Clock-in: Work Sessions, the Working Day and Admin corrections (spec 103-111, issue 56)."""

    name = "clock_in"
