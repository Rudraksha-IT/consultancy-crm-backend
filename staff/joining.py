"""Joining with an Invite Code (spec 18b): work email and Code, then one's own password.

Call inside the Consultancy. Like the password reset, nothing here says whether an email has an
invite: unknown, joined, departed and wrong all look the same to the caller.
"""

from __future__ import annotations

import logging

from django.db import transaction

from codes.exceptions import CodeLimitReachedError, InvalidCodeError
from codes.services import use_code
from codes.types import CodePurpose
from staff.employees import find_invited, send_join_code_to_invited
from staff.exceptions import PasswordRulesNotMetError
from staff.models import Staff, StaffStatus
from staff.passwords import unmet_password_rules
from staff.types import Office

logger = logging.getLogger(__name__)


def send_new_join_code(email: str, office: Office) -> None:
    """Email a new join Code if `email` has an invite here; otherwise do nothing."""
    try:
        staff = send_join_code_to_invited(email, office)
    except CodeLimitReachedError:
        logger.info("Join code limit reached")
        return
    if staff is None:
        logger.info("Join code asked for no invited Staff member")
    else:
        logger.info("Join code sent: staff_id=%s", staff.pk)


def check_join_code(email: str, code: str) -> None:
    """Check the Code without using it up (a wrong one still counts as a try).

    Raises:
        InvalidCodeError: Wrong, expired, used, out of tries, or no such invite.
    """
    use_code(_invited_or_refuse(email), CodePurpose.JOIN, code, consume=False)


def join(email: str, code: str, password: str) -> Staff:
    """Set the person's first password with the Code and make their account active.

    The rules are checked first, so a weak password never uses up the Code.

    Raises:
        PasswordRulesNotMetError: The password breaks a rule; it carries the unmet ones.
        InvalidCodeError: Wrong, expired, used, out of tries, or no such invite.
    """
    unmet = unmet_password_rules(password)
    if unmet:
        raise PasswordRulesNotMetError(unmet)
    staff = _invited_or_refuse(email)
    use_code(staff, CodePurpose.JOIN, code)
    with transaction.atomic():
        staff.set_password(password)
        staff.status = StaffStatus.ACTIVE
        staff.save(update_fields=["password", "status"])
    logger.info("Employee joined: staff_id=%s", staff.pk)
    return Staff.objects.get(pk=staff.pk)


def _invited_or_refuse(email: str) -> Staff:
    staff = find_invited(email)
    if staff is None:
        raise InvalidCodeError
    return staff
