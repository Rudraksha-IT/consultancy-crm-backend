"""Is the product's email working? (spec 2026-11-18 stories 3-4, ADR 0023).

A Platform Admin sends themselves a test email from Platform → Settings. The newest test is the
status every Platform Admin sees: working, failed (with the reference to quote) or untested. Each
Platform Admin may send `MAX_TESTS_PER_HOUR` an hour. Call in the public schema.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from django.db import transaction
from django.utils import timezone

from config.correlation import get_correlation_id
from emails.builder import build_email
from emails.exceptions import SendFailedError
from emails.platform import platform_look
from emails.sender import send_email
from emails.types import TestEmail
from platform_admin.exceptions import TestEmailLimitError
from platform_admin.models import EmailTest, PlatformAdmin

MAX_TESTS_PER_HOUR = 5
_LIMIT_WINDOW = timedelta(hours=1)

logger = logging.getLogger(__name__)


class EmailState(StrEnum):
    """What the last test email showed."""

    WORKING = "working"
    FAILED = "failed"
    UNTESTED = "untested"


@dataclass(frozen=True)
class EmailStatus:
    """The Platform's email status: the last test's outcome, when, and a failure's reference."""

    state: EmailState
    tested_at: datetime | None
    reference: str | None


def _status_of(test: EmailTest | None) -> EmailStatus:
    if test is None:
        return EmailStatus(EmailState.UNTESTED, None, None)
    if test.sent:
        return EmailStatus(EmailState.WORKING, test.tested_at, None)
    return EmailStatus(EmailState.FAILED, test.tested_at, test.reference or None)


def _newest() -> EmailTest | None:
    return EmailTest.objects.order_by("-tested_at", "-pk").first()


def email_status() -> EmailStatus:
    """The newest test's outcome, or untested."""
    return _status_of(_newest())


def _count_against_limit(admin: PlatformAdmin, now: datetime) -> None:
    """Clear tests older than the window (keeping the newest, for the status) and check the limit.

    Raises:
        TestEmailLimitError: `admin` has sent `MAX_TESTS_PER_HOUR` in the last hour.
    """
    since = now - _LIMIT_WINDOW
    newest = _newest()
    old = EmailTest.objects.filter(tested_at__lt=since)
    if newest is not None:
        old = old.exclude(pk=newest.pk)
    old.delete()
    PlatformAdmin.objects.select_for_update().filter(pk=admin.pk).first()
    if EmailTest.objects.filter(platform_admin=admin, tested_at__gte=since).count() >= (
        MAX_TESTS_PER_HOUR
    ):
        raise TestEmailLimitError


def send_test_email(admin: PlatformAdmin) -> EmailStatus:
    """Email `admin` the test email in the Platform's look and record how it went.

    A mail server that doesn't take it is recorded as failed with this request's reference; its
    words are never kept.

    Raises:
        TestEmailLimitError: `admin` has sent too many in the last hour; nothing is sent.
    """
    now = timezone.now()
    with transaction.atomic():
        _count_against_limit(admin, now)
        test = EmailTest.objects.create(platform_admin=admin, tested_at=now, sent=False)
    try:
        send_email(
            build_email(platform_look(), TestEmail(name=str(admin.name))),
            str(admin.email),
            "send_test_email",
        )
    except SendFailedError as error:
        test.reference = get_correlation_id() or ""
        test.save(update_fields=["reference"])
        # The reason holds only the kind of failure, never the server's words or the address.
        logger.warning("Platform test email failed: platform_admin_id=%s, %s", admin.pk, error)
        return _status_of(test)
    test.sent = True
    test.save(update_fields=["sent"])
    logger.info("Platform test email sent: platform_admin_id=%s", admin.pk)
    return _status_of(test)
