from __future__ import annotations

from django.apps import AppConfig


class StaffConfig(AppConfig):
    """Staff accounts and their Roles, stored in each Consultancy's own schema."""

    name = "staff"
