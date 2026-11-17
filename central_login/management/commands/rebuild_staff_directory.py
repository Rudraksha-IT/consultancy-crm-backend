"""Build the Central login's index again from every office's Staff (issue 44).

Run it after restoring a backup or changing STAFF_DIRECTORY_KEY:
`python manage.py rebuild_staff_directory`.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django_tenants.utils import get_public_schema_name, schema_context

from central_login.directory import rebuild_directory
from config.commands import LoggedCommand
from staff.models import Staff, StaffStatus
from tenancy.context import office_context
from tenancy.models import Consultancy, SetupState

LISTED_STATUSES = (StaffStatus.ACTIVE, StaffStatus.INVITED)

logger = logging.getLogger(__name__)


class Command(LoggedCommand):
    """Replace the Staff directory with every active or invited Staff member of every office."""

    help = "Rebuild the Central login's index of Staff email hashes from every office."

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401  # Django's signature
        """Read each office's Staff in its own schema, then write the index in public."""
        public = get_public_schema_name()
        entries: list[tuple[str, Consultancy]] = []
        with schema_context(public):
            # An office still being set up has no Staff to list yet (issue 60); its setup job
            # adds its first Admin to the directory itself.
            offices = list(
                Consultancy.objects.exclude(schema_name=public).filter(setup_state=SetupState.READY)
            )
        for office in offices:
            with office_context(office):
                emails = Staff.objects.filter(status__in=LISTED_STATUSES).values_list(
                    "email", flat=True
                )
                found = [(str(email), office) for email in emails]
                logger.info("staff directory office read: staff=%s", len(found))
                entries.extend(found)
        with schema_context(public), transaction.atomic():
            count = rebuild_directory(entries)
        logger.info("staff directory rebuilt: entries=%s", count)
        self.stdout.write(f"Staff directory rebuilt: {count} entries.")
