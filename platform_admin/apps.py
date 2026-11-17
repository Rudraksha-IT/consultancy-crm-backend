from __future__ import annotations

from django.apps import AppConfig


class PlatformAdminConfig(AppConfig):
    """Platform Admin accounts and the Platform API, stored in the shared public schema."""

    name = "platform_admin"

    def ready(self) -> None:
        """Register the background Job that sets up a new Consultancy (issue 60)."""
        from platform_admin.setup import register  # noqa: PLC0415  # needs the app registry

        register()
