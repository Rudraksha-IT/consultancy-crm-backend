"""The migrate step of every deploy, in its one right order (issue 59).

1. `migrate_schemas --shared`: the public schema, which holds Platform Admins.
2. `ensure_platform_admin`: the Platform Admin from the PLATFORM_ADMIN_* values; it always
   finishes without failing, so a missing or bad value never stops a deploy.
3. `migrate_schemas --tenant`: every Consultancy's schema.
4. `rebuild_staff_directory`: the Central login's index of Staff emails.

A failing step stops the run (and the deploy keeps the running version). Every step runs with a
lock timeout (ticket 14). One Python process runs
every step, which keeps the start-up cost low on the micro instance.
"""

from __future__ import annotations

from typing import Any

from django.core.management import call_command

from config.commands import LoggedCommand
from operations.migration_lock import migration_lock_timeout

STEPS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("migrate_schemas", {"shared": True, "interactive": False}),
    ("ensure_platform_admin", {}),
    ("migrate_schemas", {"tenant": True, "interactive": False}),
    ("rebuild_staff_directory", {}),
)


class Command(LoggedCommand):
    """`manage.py migrate_on_deploy`: no options; run by the `migrate` service."""

    help = "Migrate the public schema, ensure the Platform Admin, then migrate every office."

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401  # Django's signature
        """Run each step in order; the first failure stops the rest.

        A step that would wait on a lock longer than `MIGRATION_LOCK_TIMEOUT_MS` fails instead
        (ticket 14), so a busy table stops the deploy before the switch.
        """
        with migration_lock_timeout():
            for name, step_options in STEPS:
                call_command(name, **step_options)
