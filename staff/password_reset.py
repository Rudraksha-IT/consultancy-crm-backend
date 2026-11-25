"""Resetting a forgotten password with an emailed Code (spec 16). Call inside the Consultancy.

Nothing here ever says whether an email belongs to anyone: unknown, inactive and wrong all look
the same to the caller.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction

from auth_tokens.password_tries import clear_wrong_passwords
from auth_tokens.services import sign_out_everywhere
from codes.emails import send_code_email
from codes.exceptions import CodeLimitReachedError, InvalidCodeError
from codes.services import issue_code, use_code
from codes.types import CodePurpose
from staff.exceptions import PasswordRulesNotMetError
from staff.models import Staff
from staff.passwords import unmet_password_rules
from staff.services import normalize_email

if TYPE_CHECKING:
    from tenancy.models import Consultancy

logger = logging.getLogger(__name__)


def send_reset_code(email: str, office: Consultancy) -> None:
    """Email a new reset Code if `email` is an active Staff member's; otherwise do nothing."""
    staff = _active_staff(email)
    if staff is None:
        logger.info("Password reset asked for no active Staff member")
        return
    try:
        code = issue_code(staff, CodePurpose.RESET)
    except CodeLimitReachedError:
        logger.info("Password reset code limit reached: staff_id=%s", staff.pk)
        return
    send_code_email(staff, code, CodePurpose.RESET, office)
    logger.info("Password reset code sent: staff_id=%s", staff.pk)


def check_reset_code(email: str, code: str) -> None:
    """Check the Code without using it up (a wrong one still counts as a try).

    Raises:
        InvalidCodeError: Wrong, expired, used, out of tries, or no such active Staff member.
    """
    use_code(_staff_or_refuse(email), CodePurpose.RESET, code, consume=False)


def reset_password(email: str, code: str, password: str) -> Staff:
    """Set a new password with the Code and end every existing login of the person.

    The rules are checked first, so a weak password never uses up the Code.

    Raises:
        PasswordRulesNotMetError: The password breaks a rule; it carries the unmet ones.
        InvalidCodeError: Wrong, expired, used, out of tries, or no such active Staff member.
    """
    unmet = unmet_password_rules(password)
    if unmet:
        raise PasswordRulesNotMetError(unmet)
    staff = _staff_or_refuse(email)
    use_code(staff, CodePurpose.RESET, code)
    with transaction.atomic():
        staff.set_password(password)
        staff.save(update_fields=["password"])
        sign_out_everywhere(staff)
        clear_wrong_passwords(staff.email)
    logger.info("Password reset: staff_id=%s", staff.pk)
    return staff


def _active_staff(email: str) -> Staff | None:
    return Staff.objects.filter(email=normalize_email(email), is_active=True).first()


def _staff_or_refuse(email: str) -> Staff:
    staff = _active_staff(email)
    if staff is None:
        raise InvalidCodeError
    return staff
