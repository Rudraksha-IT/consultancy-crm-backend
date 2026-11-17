"""Create the Platform Admin from the deploy's PLATFORM_ADMIN_* values (issue 59).

Runs on every deploy, right after the public schema's migrations. It only ever creates: a
Platform Admin whose email already exists is left exactly as it is. A new one starts with
two-step login on or off as PLATFORM_ADMIN_TWO_FACTOR_DEFAULT says (issue 61). Every run logs
one outcome and exits 0, so a missing or bad value never stops a deploy. Values are never logged
or printed.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from django.conf import settings
from django_tenants.utils import schema_context

from config.commands import LoggedCommand
from platform_admin.exceptions import PlatformAdminDetailsError
from platform_admin.models import PlatformAdmin
from platform_admin.services import (
    find_platform_admin,
    platform_admin_problems,
    save_platform_admin,
)
from platform_admin.types import PlatformAdminDetails
from staff.services import normalize_email

PUBLIC_SCHEMA = "public"
NAME_VAR = "PLATFORM_ADMIN_NAME"
EMAIL_VAR = "PLATFORM_ADMIN_EMAIL"
PASSWORD_VAR = "PLATFORM_ADMIN_PASSWORD"  # noqa: S105  # the variable's name, not a secret
# Each detail and the variable it comes from, in the order they are named in a refusal.
VARIABLES = {"name": NAME_VAR, "email": EMAIL_VAR, "password": PASSWORD_VAR}

logger = logging.getLogger(__name__)


class Command(LoggedCommand):
    """`manage.py ensure_platform_admin`: no options, no prompt; reads the environment only."""

    help = "Create the Platform Admin from PLATFORM_ADMIN_NAME, _EMAIL and _PASSWORD if new."

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401  # Django's signature
        """Log exactly one outcome; never raise for a missing, bad or existing value."""
        values = {field: os.environ.get(var, "") for field, var in VARIABLES.items()}
        given = {field for field, value in values.items() if value.strip()}
        if not given:
            logger.info("platform admin values not set; skipped")
            return
        details = PlatformAdminDetails(name=values["name"], email=values["email"])
        problems = platform_admin_problems(details, values["password"], creating=True)
        bad = [var for field, var in VARIABLES.items() if field not in given or field in problems]
        if bad:
            logger.error("platform admin values incomplete or invalid; skipped: %s", ", ".join(bad))
            return
        with schema_context(PUBLIC_SCHEMA):
            self._create_unless_there(details, values["password"])

    def _create_unless_there(self, details: PlatformAdminDetails, password: str) -> None:
        existing = find_platform_admin(details.email)
        if existing is None:
            try:
                admin = save_platform_admin(
                    details,
                    password=password,
                    # Read at start-up from PLATFORM_ADMIN_TWO_FACTOR_DEFAULT; only used now,
                    # at creation (issue 61).
                    two_factor_required=settings.PLATFORM_ADMIN_TWO_FACTOR_DEFAULT,
                )
            except PlatformAdminDetailsError:
                # The values passed every check above, so only the email can be refused here:
                # another deploy created it between the lookup and the save.
                existing = PlatformAdmin.objects.get(email=normalize_email(details.email))
            else:
                logger.info("platform admin created: id=%s", admin.pk)
                return
        logger.info("platform admin already exists; skipped: id=%s", existing.pk)
