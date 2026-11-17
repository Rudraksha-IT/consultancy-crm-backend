"""Creating a Consultancy in a background job (issue 60).

The Platform's Create answers at once: `start_consultancy_setup` reserves the name and
subdomain and queues a `create_consultancy` Job. The `worker` then builds the schema, runs its
migrations and records the starting Status, and only then switches the host on. A failure drops
the half-made schema and keeps the host off; Try again (`retry_consultancy_setup`) queues a new
Job.

It invites nobody: a Platform Admin adds Admins once the office is set up (issue 64,
`platform_admin.office_admins`).
"""

from __future__ import annotations

import io
import logging
from datetime import date

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django_tenants.utils import get_public_schema_name, schema_exists

from jobs.models import Job
from jobs.registry import register_job_kind
from jobs.services import queue_job
from platform_admin.models import PlatformAdmin
from platform_admin.types import ConsultancyOnboarding
from tenancy.context import activate_public_schema
from tenancy.exceptions import InvalidStatusChangeError
from tenancy.models import Consultancy, ConsultancyStatusChange, Domain, SetupState
from tenancy.services import (
    check_paid_until,
    is_ready,
    reserve_consultancy,
    set_theme,
    start_active,
    start_evaluation,
)
from tenancy.status import ConsultancyStatus
from tenancy.subdomains import build_host, schema_name_for

CREATE_CONSULTANCY = "create_consultancy"
PAID_UNTIL_NEEDED_MESSAGE = "Choose the date the office has paid up to."
SETUP_FAILED_MESSAGE = "The office could not be set up. Try again."
NOT_FAILED_MESSAGE = "Only an office whose setup failed can be set up again."

logger = logging.getLogger(__name__)


class SubdomainTakenError(Exception):
    """Another Consultancy took the subdomain between the check and the save."""


class SetupRefusedError(Exception):
    """Try again is refused; `message` says why, in screen words."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def start_consultancy_setup(onboarding: ConsultancyOnboarding, *, by: PlatformAdmin) -> Consultancy:
    """Reserve a new Consultancy and queue the Job that sets it up. Call on the Platform.

    Args:
        onboarding: The Consultancy's name, subdomain (the front part of its address), Theme
            and starting Status.
        by: The Platform Admin creating it, recorded with its starting Status.

    Returns:
        The saved Consultancy, `setting_up`, without its schema; its host answers 404 until
        the Job has finished.

    Raises:
        InvalidStatusChangeError: It starts Active without a paid-until date, or the date is
            refused; nothing is saved.
        SubdomainTakenError: Another Consultancy has the subdomain; nothing is saved.
    """
    if onboarding.status == ConsultancyStatus.ACTIVE:
        if onboarding.paid_until is None:
            raise InvalidStatusChangeError(PAID_UNTIL_NEEDED_MESSAGE)
        check_paid_until(onboarding.paid_until)
    subdomain = onboarding.subdomain
    try:
        with transaction.atomic():
            consultancy = reserve_consultancy(
                name=onboarding.name,
                schema_name=schema_name_for(subdomain),
                host=build_host(subdomain),
            )
            set_theme(consultancy, onboarding.theme)
            job = queue_job(
                CREATE_CONSULTANCY,
                {
                    "consultancy_id": consultancy.pk,
                    "status": onboarding.status.value,
                    "paid_until": _iso(onboarding.paid_until),
                },
                created_by=by,
            )
    except (IntegrityError, ValidationError) as error:
        # django-tenants refuses a taken schema name with ValidationError; the database
        # refuses a taken host with IntegrityError.
        raise SubdomainTakenError from error
    logger.info(
        "Consultancy setup queued: consultancy_id=%s, job_id=%s, platform_admin_id=%s",
        consultancy.pk,
        job.pk,
        by.pk,
    )
    return consultancy


def retry_consultancy_setup(consultancy: Consultancy, *, by: PlatformAdmin) -> Job:
    """Try again: queue a new setup Job for a Consultancy whose setup failed.

    Raises:
        SetupRefusedError: Its setup didn't fail (it is ready, or still being set up).
    """
    with transaction.atomic():
        locked = Consultancy.objects.select_for_update().get(pk=consultancy.pk)
        last = (
            Job.objects.filter(kind=CREATE_CONSULTANCY, payload__consultancy_id=locked.pk)
            .order_by("-pk")
            .first()
        )
        if str(locked.setup_state) != SetupState.FAILED.value or last is None:
            raise SetupRefusedError(NOT_FAILED_MESSAGE)
        locked.setup_state = SetupState.SETTING_UP
        locked.save(update_fields=["setup_state"])
        job = queue_job(CREATE_CONSULTANCY, last.payload, created_by=by)
    consultancy.setup_state = locked.setup_state
    logger.info(
        "Consultancy setup queued again: consultancy_id=%s, job_id=%s, platform_admin_id=%s",
        consultancy.pk,
        job.pk,
        by.pk,
    )
    return job


def _iso(day: date | None) -> str | None:
    return None if day is None else day.isoformat()


class ConsultancySetup:
    """The `create_consultancy` Job. Each step checks what is already done, so it can rerun."""

    failure_message = SETUP_FAILED_MESSAGE

    def run(self, job: Job) -> None:
        """Build the schema, record the Status, then open the host. Nobody is invited."""
        consultancy = _consultancy_of(job)
        if consultancy is None or str(consultancy.setup_state) != SetupState.SETTING_UP.value:
            # Ready already, or given up on: a stale Job never touches it again.
            logger.info("Consultancy setup not needed: %s", self.log_context(job))
            return
        _build_schema(consultancy)
        by = job.created_by
        if by is None:  # pragma: no cover - every setup Job is queued by a Platform Admin
            raise TypeError
        with transaction.atomic():
            locked = Consultancy.objects.select_for_update().get(pk=consultancy.pk)
            _record_starting_status(locked, job, by)
            locked.setup_state = SetupState.READY
            locked.save(update_fields=["setup_state"])
            Domain.objects.filter(tenant=locked).update(is_active=True)
            _forget_personal_values(job)
        logger.info("Consultancy set up: %s", self.log_context(job))

    def gave_up(self, job: Job) -> None:
        """Drop the half-made schema, keep the host off and mark the setup failed."""
        consultancy = _consultancy_of(job)
        if consultancy is None or is_ready(consultancy):
            return
        _drop_schema(consultancy)
        with transaction.atomic():
            Consultancy.objects.filter(pk=consultancy.pk).update(setup_state=SetupState.FAILED)
            Domain.objects.filter(tenant=consultancy).update(is_active=False)
        logger.warning("Consultancy setup failed: %s", self.log_context(job))

    def log_context(self, job: Job) -> str:
        """Name the Consultancy by its id (never its name)."""
        return f"consultancy_id={job.payload.get('consultancy_id')}"

    def tenant_host(self, job: Job) -> str | None:
        """The host the new Consultancy will be reached at."""
        domain = Domain.objects.filter(
            tenant_id=job.payload.get("consultancy_id"), is_primary=True
        ).first()
        return None if domain is None else str(domain.domain)


def _consultancy_of(job: Job) -> Consultancy | None:
    found: Consultancy | None = Consultancy.objects.filter(
        pk=job.payload.get("consultancy_id")
    ).first()
    return found


def _build_schema(consultancy: Consultancy) -> None:
    """Create the schema if it isn't there, then apply every migration it doesn't have yet."""
    schema = str(consultancy.schema_name)
    if not schema_exists(schema):
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA {connection.ops.quote_name(schema)}")
    call_command(
        "migrate_schemas",
        tenant=True,
        schema_name=schema,
        interactive=False,
        verbosity=0,
        stdout=io.StringIO(),
    )
    activate_public_schema()


def _drop_schema(consultancy: Consultancy) -> None:
    schema = str(consultancy.schema_name)
    if schema == get_public_schema_name():  # pragma: no cover - a Consultancy never has it
        raise ValueError(schema)
    activate_public_schema()
    with connection.cursor() as cursor:
        # Inside an open transaction (as in tests), checks the failed work left pending would
        # block the drop; run them first. On its own (the worker) this does nothing.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(f"DROP SCHEMA IF EXISTS {connection.ops.quote_name(schema)} CASCADE")


# A Job queued before issue 64 also carried an Admin to invite. It is ignored, and forgotten
# once the office is set up: no name or email stays in public (as the Central login's index
# keeps no raw email, issue 44).
_PERSONAL_KEYS = ("admin_name", "admin_email", "join_link")


def _forget_personal_values(job: Job) -> None:
    job.payload = {k: v for k, v in job.payload.items() if k not in _PERSONAL_KEYS}
    Job.objects.filter(pk=job.pk).update(payload=job.payload)


def _record_starting_status(locked: Consultancy, job: Job, by: PlatformAdmin) -> None:
    if ConsultancyStatusChange.objects.filter(consultancy=locked).exists():
        return
    paid_until = job.payload.get("paid_until")
    if job.payload.get("status") == ConsultancyStatus.ACTIVE.value and isinstance(paid_until, str):
        start_active(locked, paid_until=date.fromisoformat(paid_until), by=by)
    else:
        start_evaluation(locked, by=by)


def register() -> None:
    """Make the `worker` run `create_consultancy` Jobs; called from the app's ready()."""
    register_job_kind(CREATE_CONSULTANCY, ConsultancySetup())
