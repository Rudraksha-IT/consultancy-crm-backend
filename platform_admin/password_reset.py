"""A Platform Admin resets a forgotten password with an emailed Code (issue 63).

Call on the public schema (the Platform host). The same Code rules as Staff (issue 08), in the
Platform scope. Nothing here ever says whether an email belongs to a Platform Admin: unknown,
inactive and wrong all look the same to the caller.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from central_login.directory import email_hash
from codes.emails import send_platform_code_email
from codes.exceptions import CodeLimitReachedError, InvalidCodeError
from codes.services import issue_code, use_code
from codes.types import CodePurpose
from platform_admin.models import PlatformAdmin, PlatformResetRequest
from platform_admin.services import save_platform_admin
from platform_admin.types import PlatformAdminDetails
from staff.exceptions import PasswordRulesNotMetError
from staff.passwords import unmet_password_rules
from staff.services import normalize_email
from tenancy.subdomains import get_platform_host

# "Forgot password?" requests per email (by its hash) in one hour, known or not.
MAX_REQUESTS_PER_EMAIL_PER_HOUR = 5
_LIMIT_WINDOW = timedelta(hours=1)

logger = logging.getLogger(__name__)


def send_platform_reset_code(email: str) -> None:
    """Email a new reset Code if `email` is an active Platform Admin's; otherwise do nothing.

    Run after the answer has gone, so it never changes the answer or its timing. Never raises:
    a failure is logged without the address.
    """
    try:
        if not _within_limit(email):
            logger.warning("Platform password reset limit reached")
            return
        admin = _active_admin(email)
        if admin is None:
            logger.info("Platform password reset asked for no active Platform Admin")
            return
        try:
            code = issue_code(admin, CodePurpose.RESET)
        except CodeLimitReachedError:
            # issue_code has logged the limit at WARNING with the id
            return
        send_platform_code_email(admin, code, get_platform_host())
        logger.info("Platform password reset code sent: platform_admin_id=%s", admin.pk)
    except Exception:  # the answer has gone; log it, never re-raise
        # The redaction filter blanks the address an SMTP error may name.
        logger.warning("Platform password reset code not sent", exc_info=True)


def check_platform_reset_code(email: str, code: str) -> None:
    """Check the Code without using it up (a wrong one still counts as a try).

    Raises:
        InvalidCodeError: Wrong, expired, used, out of tries, or no such active Platform Admin.
    """
    _use(email, code, consume=False)


def reset_platform_password(email: str, code: str, new_password: str) -> PlatformAdmin:
    """Set a new password with the Code, through `save_platform_admin` (issue 59).

    The rules are checked first, so a weak password never uses up the Code. Saving signs the
    Platform Admin out everywhere and records the change; two-step login is untouched.

    Raises:
        PasswordRulesNotMetError: The password breaks a rule; it carries the unmet ones.
        InvalidCodeError: Wrong, expired, used, out of tries, or no such active Platform Admin.
    """
    unmet = unmet_password_rules(new_password)
    if unmet:
        raise PasswordRulesNotMetError(unmet)
    admin = _use(email, code, consume=True)
    details = PlatformAdminDetails(name=admin.name, email=admin.email)
    saved = save_platform_admin(details, password=new_password, admin=admin, by=admin)
    logger.info("Platform password reset: platform_admin_id=%s", saved.pk)
    return saved


def _use(email: str, code: str, *, consume: bool) -> PlatformAdmin:
    admin = _active_admin(email)
    try:
        if admin is None:
            raise InvalidCodeError
        use_code(admin, CodePurpose.RESET, code, consume=consume)
    except InvalidCodeError:
        logger.warning("Platform password reset code refused")
        raise
    return admin


def _active_admin(email: str) -> PlatformAdmin | None:
    # Only someone who has joined (issue 65): an invite is finished on the Join page.
    return PlatformAdmin.objects.filter(
        email=normalize_email(email), is_active=True, joined_at__isnull=False
    ).first()


def _within_limit(email: str) -> bool:
    """Count this request against the email's hourly limit; False once it's used up."""
    hashed = email_hash(email)
    now = timezone.now()
    with transaction.atomic():
        PlatformResetRequest.objects.filter(requested_at__lt=now - _LIMIT_WINDOW).delete()
        recent = PlatformResetRequest.objects.select_for_update().filter(email_hash=hashed)
        if len(recent) >= MAX_REQUESTS_PER_EMAIL_PER_HOUR:
            return False
        PlatformResetRequest.objects.create(email_hash=hashed, requested_at=now)
    return True
