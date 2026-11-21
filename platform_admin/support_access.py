"""Support Access (spec 9-11b, issue 27): a Platform Admin opens a Consultancy as themselves.

A visit starts on the Platform with a reason and is recorded there (public schema). Starting it
gives a one-time entry code; on the office's own address the code is swapped, once and briefly,
for login tokens of kind `support_access` (ADR 0003). While the visit is open, those tokens make
the Platform Admin a `PlatformVisitor`: never a Staff row, acting with every Admin permission
plus the Platform-only actions. Leaving the office, or `VISIT_LENGTH` passing, ends the visit.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from platform_admin.models import PlatformAdmin, SupportVisit
from staff.models import (
    PLATFORM_VISITOR_ID,
    PLATFORM_VISITOR_PUBLIC_ID,
    PlatformVisitor,
    Role,
    StaffStatus,
)
from tenancy.models import Consultancy
from tenancy.services import NOT_READY_MESSAGE, get_status, is_ready
from tenancy.status import ConsultancyStatus

# How long a visit may stay open before it ends by itself.
VISIT_LENGTH = timedelta(hours=8)
# How long the entry code works: just long enough for the browser to reach the office's address.
ENTRY_CODE_LIFETIME = timedelta(minutes=2)
ENTRY_CODE_BYTES = 32
# How many visits the Platform page lists.
RECENT_VISITS = 50

TERMINATED_MESSAGE = "This office's account has been closed, so it can't be opened."
ENTRY_REFUSED_MESSAGE = "This link has expired. Open the office again from the Platform."

logger = logging.getLogger(__name__)


class SupportAccessRefusedError(Exception):
    """The visit can't start or be entered; `message` says why, in screen words."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class StartedVisit:
    """A visit just started, and the one-time code that enters it on the office's address."""

    visit: SupportVisit
    entry_code: str


def start_visit(admin: PlatformAdmin, consultancy: Consultancy, reason: str) -> StartedVisit:
    """Record a new visit by `admin` to `consultancy`, with why, and return its entry code.

    Any Consultancy may be opened to help it, Past due and Suspended included, except a
    Terminated one. The Status at the start is kept on the visit. Call on the Platform.

    Raises:
        SupportAccessRefusedError: The Consultancy is Terminated, or isn't set up yet.
    """
    if not is_ready(consultancy):
        raise SupportAccessRefusedError(NOT_READY_MESSAGE)
    status = get_status(consultancy)
    if status == ConsultancyStatus.TERMINATED:
        raise SupportAccessRefusedError(TERMINATED_MESSAGE)
    code = secrets.token_urlsafe(ENTRY_CODE_BYTES)
    now = timezone.now()
    visit = SupportVisit.objects.create(
        platform_admin=admin,
        consultancy=consultancy,
        reason=reason,
        consultancy_status=status.value,
        started_at=now,
        ends_by=now + VISIT_LENGTH,
        entry_code_hash=_hash(code),
        entry_code_expires_at=now + ENTRY_CODE_LIFETIME,
    )
    logger.info(
        "Support Access started: visit_id=%s, platform_admin_id=%s, consultancy_id=%s",
        visit.pk,
        admin.pk,
        consultancy.pk,
    )
    return StartedVisit(visit=visit, entry_code=code)


@transaction.atomic
def enter_visit(consultancy: Consultancy, entry_code: str) -> PlatformVisitor:
    """Use a visit's entry code on `consultancy`'s address and return who is entering.

    The code works once, only on the Consultancy the visit is for, only briefly, and only while
    the visit and its Platform Admin are still active.

    Raises:
        SupportAccessRefusedError: The code is unknown, used, expired or for another office.
    """
    now = timezone.now()
    visit = (
        SupportVisit.objects.select_for_update()
        .select_related("platform_admin")
        .filter(
            entry_code_hash=_hash(entry_code),
            consultancy=consultancy,
            entered_at__isnull=True,
            entry_code_expires_at__gt=now,
            ended_at__isnull=True,
            ends_by__gt=now,
            platform_admin__is_active=True,
        )
        .first()
    )
    if visit is None:
        logger.info("Support Access entry refused: consultancy_id=%s", consultancy.pk)
        raise SupportAccessRefusedError(ENTRY_REFUSED_MESSAGE)
    visit.entered_at = now
    visit.save(update_fields=["entered_at"])
    logger.info("Support Access entered: visit_id=%s", visit.pk)
    return visitor_for(visit)


def find_visitor(
    visit: uuid.UUID, platform_admin: uuid.UUID, auth_version: int, consultancy_schema: str
) -> PlatformVisitor | None:
    """Return who a `support_access` token is for while its visit is open, or None.

    The visit must be this Platform Admin's, on this Consultancy, entered, not left and not
    past its end; the Platform Admin must still be active with an unchanged `auth_version`.
    """
    now = timezone.now()
    found = (
        SupportVisit.objects.select_related("platform_admin")
        .filter(
            public_id=visit,
            platform_admin__public_id=platform_admin,
            platform_admin__is_active=True,
            platform_admin__auth_version=auth_version,
            consultancy__schema_name=consultancy_schema,
            entered_at__isnull=False,
            ended_at__isnull=True,
            ends_by__gt=now,
        )
        .first()
    )
    return None if found is None else visitor_for(found)


def end_visit(visit_id: int) -> None:
    """Leave the office: the visit ends now and its tokens stop working at once."""
    ended = SupportVisit.objects.filter(pk=visit_id, ended_at__isnull=True).update(
        ended_at=timezone.now()
    )
    if ended:
        logger.info("Support Access ended: visit_id=%s", visit_id)


def recent_visits() -> list[SupportVisit]:
    """The latest visits by every Platform Admin, newest first, for the Platform page."""
    return list(
        SupportVisit.objects.select_related("platform_admin", "consultancy").order_by(
            "-started_at", "-pk"
        )[:RECENT_VISITS]
    )


def visitor_for(visit: SupportVisit) -> PlatformVisitor:
    """The Platform Admin of `visit`, as the office sees them: an active Admin, not Staff."""
    admin = visit.platform_admin
    visitor = PlatformVisitor(
        pk=PLATFORM_VISITOR_ID,
        public_id=PLATFORM_VISITOR_PUBLIC_ID,
        name=admin.name,
        email=admin.email,
        role=Role.ADMIN,
        status=StaffStatus.ACTIVE,
        auth_version=admin.auth_version,
    )
    visitor.platform_admin_id = admin.pk
    visitor.support_visit_id = visit.pk
    visitor.platform_admin_public_id = admin.public_id
    visitor.support_visit_public_id = visit.public_id
    return visitor


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()
