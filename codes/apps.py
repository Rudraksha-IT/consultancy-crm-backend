from __future__ import annotations

from django.apps import AppConfig


class CodesConfig(AppConfig):
    """One-time Codes for joining and password resets, kept in each Consultancy's schema."""

    name = "codes"
