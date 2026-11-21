"""The Platform Admins tab (issue 65): list, invite by Code, resend, deactivate, and joining.

Call on the public schema (the Platform host). An invited Platform Admin has no usable password
and no `joined_at`; they join on the Platform's Join page with the emailed Code (Platform scope)
and choose their password. Nothing here logs a name, email, Code or password.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from auth_tokens.services import sign_out_everywhere
from codes.emails import send_platform_join_email
from codes.exceptions import CodeLimitReachedError, InvalidCodeError
from codes.services import issue_code, use_code
from codes.types import CodePurpose
from platform_admin.exceptions import PlatformAdminDetailsError
from platform_admin.models import PlatformAdmin, PlatformAdminChange
from platform_admin.services import (
    EMAIL_TAKEN_MESSAGE,
    platform_admin_problems,
    save_platform_admin,
)
from platform_admin.types import PlatformAdminDetails
from staff.exceptions import PasswordRulesNotMetError
from staff.passwords import unmet_password_rules
from staff.services import normalize_email
from two_factor.services import is_set_up

NOT_INVITED_MESSAGE = "They have already joined, or have been deactivated."
NOT_YOURSELF_MESSAGE = "You can't deactivate yourself."
ALREADY_DEACTIVATED_MESSAGE = "They are already deactivated."
LAST_ONE_MESSAGE = "At least one active Platform Admin must stay."
NOT_DEACTIVATED_MESSAGE = "Only a deactivated Platform Admin can be reactivated."

logger = logging.getLogger(__name__)


class PlatformAdminStatus(StrEnum):
    """Where a Platform Admin is: invited (not joined yet), active, or deactivated."""

    INVITED = "invited"
    ACTIVE = "active"
    DEACTIVATED = "deactivated"


class TeamChangeRefusedError(Exception):
    """A change on the Platform Admins tab is refused; `message` says why, in screen words."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PlatformAdminRow:
    """One Platform Admin on the tab; never a password or secret."""

    id: UUID
    name: str
    email: str
    status: PlatformAdminStatus
    two_factor_required: bool
    two_factor_set_up: bool
    is_me: bool


def status_of(admin: PlatformAdmin) -> PlatformAdminStatus:
    """Return whether `admin` is invited, active or deactivated."""
    if not admin.is_active:
        return PlatformAdminStatus.DEACTIVATED
    if admin.joined_at is None:
        return PlatformAdminStatus.INVITED
    return PlatformAdminStatus.ACTIVE


def row_of(admin: PlatformAdmin, me: PlatformAdmin) -> PlatformAdminRow:
    """`admin` as the tab shows it to `me`."""
    return PlatformAdminRow(
        id=admin.public_id,
        name=str(admin.name),
        email=str(admin.email),
        status=status_of(admin),
        two_factor_required=bool(admin.two_factor_required),
        two_factor_set_up=is_set_up(admin),
        is_me=admin.pk == me.pk,
    )


def list_platform_admins(me: PlatformAdmin) -> list[PlatformAdminRow]:
    """Every Platform Admin, oldest first."""
    return [row_of(admin, me) for admin in PlatformAdmin.objects.order_by("pk")]


def invite_platform_admin(
    details: PlatformAdminDetails, *, join_link: str, by: PlatformAdmin
) -> PlatformAdmin:
    """Add an invited Platform Admin and email them a join Code with the Join page.

    Two-step login starts from PLATFORM_ADMIN_TWO_FACTOR_DEFAULT. The change is recorded.

    Raises:
        PlatformAdminDetailsError: The name or email is refused, or the email is already used.
    """
    cleaned = PlatformAdminDetails(name=details.name.strip(), email=normalize_email(details.email))
    problems = platform_admin_problems(cleaned, None, creating=False)
    if "email" not in problems and PlatformAdmin.objects.filter(email=cleaned.email).exists():
        problems["email"] = EMAIL_TAKEN_MESSAGE
    if problems:
        raise PlatformAdminDetailsError(problems)
    with transaction.atomic():
        admin = PlatformAdmin(
            name=cleaned.name,
            email=cleaned.email,
            two_factor_required=settings.PLATFORM_ADMIN_TWO_FACTOR_DEFAULT,
        )
        admin.set_unusable_password()
        admin.save()
        PlatformAdminChange.objects.create(
            platform_admin=admin, by=by, created=True, fields=["name", "email"]
        )
        _send_join_code(admin, join_link)
    logger.info(
        "Platform Admin invited: platform_admin_id=%s, by_platform_admin_id=%s", admin.pk, by.pk
    )
    return admin


def resend_join_code(admin: PlatformAdmin, *, join_link: str, by: PlatformAdmin) -> None:
    """Email a new join Code to someone still invited; the old one stops working.

    Raises:
        TeamChangeRefusedError: They have joined or been deactivated.
        CodeLimitReachedError: Too many Codes were sent to them in the last hour.
    """
    if status_of(admin) != PlatformAdminStatus.INVITED:
        raise TeamChangeRefusedError(NOT_INVITED_MESSAGE)
    _send_join_code(admin, join_link)
    logger.info(
        "Platform Admin join code sent again: platform_admin_id=%s, by_platform_admin_id=%s",
        admin.pk,
        by.pk,
    )


def deactivate_platform_admin(admin: PlatformAdmin, *, by: PlatformAdmin) -> PlatformAdmin:
    """Deactivate an invited or active Platform Admin and sign them out everywhere.

    Raises:
        TeamChangeRefusedError: It is yourself, they are already deactivated, or they are the
            last active Platform Admin.
    """
    if admin.pk == by.pk:
        raise TeamChangeRefusedError(NOT_YOURSELF_MESSAGE)
    with transaction.atomic():
        locked = PlatformAdmin.objects.select_for_update().get(pk=admin.pk)
        if not locked.is_active:
            raise TeamChangeRefusedError(ALREADY_DEACTIVATED_MESSAGE)
        others_active = PlatformAdmin.objects.filter(
            is_active=True, joined_at__isnull=False
        ).exclude(pk=locked.pk)
        if not others_active.exists():
            raise TeamChangeRefusedError(LAST_ONE_MESSAGE)
        locked.is_active = False
        locked.save(update_fields=["is_active"])
        sign_out_everywhere(locked)
        PlatformAdminChange.objects.create(platform_admin=locked, by=by, fields=["is_active"])
    logger.info(
        "Platform Admin deactivated: platform_admin_id=%s, by_platform_admin_id=%s",
        locked.pk,
        by.pk,
    )
    return locked


def reactivate_platform_admin(
    admin: PlatformAdmin, *, join_link: str, by: PlatformAdmin
) -> PlatformAdmin:
    """Bring a deactivated Platform Admin back; recorded as a change by `by`.

    Someone who had joined is active again (their password works again; Forgot password if they
    lost it). A cancelled invite is invited again and emailed a new join Code.

    Raises:
        TeamChangeRefusedError: They aren't deactivated.
    """
    with transaction.atomic():
        locked = PlatformAdmin.objects.select_for_update().get(pk=admin.pk)
        if locked.is_active:
            raise TeamChangeRefusedError(NOT_DEACTIVATED_MESSAGE)
        locked.is_active = True
        locked.save(update_fields=["is_active"])
        PlatformAdminChange.objects.create(platform_admin=locked, by=by, fields=["is_active"])
        if locked.joined_at is None:
            _send_join_code(locked, join_link)
    logger.info(
        "Platform Admin reactivated: platform_admin_id=%s, by_platform_admin_id=%s",
        locked.pk,
        by.pk,
    )
    return locked


def send_join_code_again(email: str, join_link: str) -> None:
    """The Join page's "Send me a new code": only for someone invited; never raises.

    Run after the answer has gone, so the answer and its timing never say who is invited.
    """
    try:
        admin = _invited(email)
        if admin is None:
            logger.info("Platform join code asked for no invited Platform Admin")
            return
        _send_join_code(admin, join_link)
        logger.info("Platform Admin join code sent again: platform_admin_id=%s", admin.pk)
    except CodeLimitReachedError:
        # issue_code has logged the limit at WARNING with the id
        return
    except Exception:  # the answer has gone; log it, never re-raise
        logger.warning("Platform join code not sent", exc_info=True)


def check_join_code(email: str, code: str) -> None:
    """Check a join Code without using it up (a wrong one still counts as a try).

    Raises:
        InvalidCodeError: Wrong, expired, used, out of tries, or no such invited person.
    """
    _use(email, code, consume=False)


def complete_join(email: str, code: str, new_password: str) -> PlatformAdmin:
    """Set the invited Platform Admin's password with the Code; they then log in as usual.

    Raises:
        PasswordRulesNotMetError: The password breaks a rule (checked first, keeping the Code).
        InvalidCodeError: Wrong, expired, used, out of tries, or no such invited person.
    """
    unmet = unmet_password_rules(new_password)
    if unmet:
        raise PasswordRulesNotMetError(unmet)
    admin = _use(email, code, consume=True)
    details = PlatformAdminDetails(name=str(admin.name), email=str(admin.email))
    with transaction.atomic():
        saved = save_platform_admin(details, password=new_password, admin=admin, by=admin)
        saved.joined_at = timezone.now()
        saved.save(update_fields=["joined_at"])
    logger.info("Platform Admin joined: platform_admin_id=%s", saved.pk)
    return saved


def _send_join_code(admin: PlatformAdmin, join_link: str) -> None:
    code = issue_code(admin, CodePurpose.JOIN)
    send_platform_join_email(admin, code, join_link)


def is_invited(email: str) -> bool:
    """Whether `email` is a Platform Admin invited but not joined yet (issue 66)."""
    return _invited(email) is not None


def _invited(email: str) -> PlatformAdmin | None:
    return PlatformAdmin.objects.filter(
        email=normalize_email(email), is_active=True, joined_at__isnull=True
    ).first()


def _use(email: str, code: str, *, consume: bool) -> PlatformAdmin:
    admin = _invited(email)
    try:
        if admin is None:
            raise InvalidCodeError
        use_code(admin, CodePurpose.JOIN, code, consume=consume)
    except InvalidCodeError:
        logger.warning("Platform join code refused")
        raise
    return admin
