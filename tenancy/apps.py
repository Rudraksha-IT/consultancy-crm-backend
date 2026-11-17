from __future__ import annotations

from django.apps import AppConfig


class TenancyConfig(AppConfig):
    """Consultancies and their domains, stored in the shared public schema."""

    name = "tenancy"
