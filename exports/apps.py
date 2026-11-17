from __future__ import annotations

from django.apps import AppConfig


class ExportsConfig(AppConfig):
    """CSV exports of the office's lists and their Export Records (spec 31b, 31c)."""

    name = "exports"
