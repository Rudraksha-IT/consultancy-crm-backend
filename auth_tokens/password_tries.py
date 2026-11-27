"""A limit on wrong passwords per email, on the staff and Platform logins (security ticket 01).

Counted in the active schema: a Consultancy's for its Staff, public for Platform Admins. After
`WRONG_PASSWORDS_ALLOWED` wrong passwords for one email in `WRONG_PASSWORD_WINDOW`, every try for
that email is refused, right or wrong, until the window ends. An email that belongs to nobody is
counted the same, so the answers never say who works here. A right password below the limit
clears the count, and so does a password reset by Code.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from auth_tokens.models import WrongPasswordCount
from central_login.directory import email_hash
from tenancy.context import is_public_schema_active

WRONG_PASSWORDS_ALLOWED = 10
WRONG_PASSWORD_WINDOW = timedelta(minutes=15)

logger = logging.getLogger(__name__)


class PasswordTriesUsedUpError(Exception):
    """The email's wrong passwords are used up; nothing was checked."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("password tries used up")
        # Whole seconds until the window ends, for the Retry-After header.
        self.retry_after = retry_after


def check_password_try[T](email: str, check: Callable[[], T | None]) -> T | None:
    """Run the password `check` for `email` unless its tries are used up, and count the result.

    `check` returns the person for a right password, None for a wrong one (or no such person).
    Tries for one email are checked one at a time.

    Raises:
        PasswordTriesUsedUpError: Too many wrong passwords in the window; `check` was not run.
    """
    hashed = email_hash(email)
    retry_after: int | None = None
    with transaction.atomic():
        now = timezone.now()
        WrongPasswordCount.objects.filter(
            window_started_at__lte=now - WRONG_PASSWORD_WINDOW
        ).delete()
        row = WrongPasswordCount.objects.select_for_update().filter(email_hash=hashed).first()
        if row is not None and row.wrong_count >= WRONG_PASSWORDS_ALLOWED:
            ends = row.window_started_at + WRONG_PASSWORD_WINDOW
            retry_after = max(1, math.ceil((ends - now).total_seconds()))
            person = None
        else:
            person = check()
            if person is None:
                _count_wrong(hashed, now)
            elif row is not None:
                row.delete()
    if retry_after is not None:
        # Raised outside the transaction, which has nothing to undo.
        scope = "platform" if is_public_schema_active() else "staff"
        logger.info("Password tries used up: scope=%s", scope)
        raise PasswordTriesUsedUpError(retry_after)
    return person


def clear_wrong_passwords(email: str) -> None:
    """Forget the wrong passwords for `email` in the active schema (after a reset by Code)."""
    WrongPasswordCount.objects.filter(email_hash=email_hash(email)).delete()


def _count_wrong(hashed: str, now: datetime) -> None:
    row, _ = WrongPasswordCount.objects.get_or_create(
        email_hash=hashed, defaults={"window_started_at": now}
    )
    WrongPasswordCount.objects.filter(pk=row.pk).update(wrong_count=F("wrong_count") + 1)
