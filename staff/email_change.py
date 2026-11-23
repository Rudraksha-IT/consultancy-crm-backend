"""A Staff member changes their own email (spec 2026-11-22 stories 34-52; ticket 04).

Call inside the Consultancy's schema. Asking needs the current password (and the two-step
number, checked by the caller) and emails a Code, bound to the new email, to that new email.
The Code changes it: the Central login moves, every other device is signed out, this one gets
new tokens and the old email gets a notice. Only the person
themselves ever does this; there is no way for an Admin to change someone's email.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction

from auth_tokens.services import sign_out_other_devices
from codes.emails import send_email_change_code
from codes.services import issue_code, use_code, withdraw_code
from codes.types import CodePurpose
from emails.builder import build_email
from emails.exceptions import SendFailedError
from emails.office import office_look
from emails.sender import send_email
from emails.types import StaffEmailChanged
from staff.employees import is_email_taken
from staff.exceptions import EmailTakenError, SameEmailError, WrongCurrentPasswordError
from staff.models import Staff
from staff.services import change_email, normalize_email

if TYPE_CHECKING:
    from auth_tokens.types import TokenPair
    from tenancy.models import Consultancy

logger = logging.getLogger(__name__)


def check_new_email(staff: Staff, email: str, password: str) -> str:
    """Return the new email as stored, once the password is right and the email is free here.

    Raises:
        SameEmailError: It is already the person's email (checked first).
        WrongCurrentPasswordError: `password` isn't the person's.
        EmailTakenError: Someone here already uses it.
    """
    new_email = normalize_email(email)
    if new_email == staff.email:
        raise SameEmailError
    if not staff.check_password(password):
        logger.info("Email change refused, wrong password: staff_id=%s", staff.pk)
        raise WrongCurrentPasswordError
    if is_email_taken(new_email):
        raise EmailTakenError
    return new_email


def send_email_change_code_to(staff: Staff, new_email: str, office: Consultancy) -> None:
    """Email a new Code, bound to `new_email`, to `new_email`. A Code not sent is withdrawn.

    Raises:
        CodeLimitReachedError: Too many Codes were sent in the last hour.
        SendFailedError: The mail server didn't take it; nothing changed.
    """
    code = issue_code(staff, CodePurpose.EMAIL_CHANGE, bound_to=new_email)
    try:
        send_email_change_code(staff, code, new_email, office)
    except SendFailedError:
        withdraw_code(staff, CodePurpose.EMAIL_CHANGE)
        raise
    logger.info("Email change code sent: staff_id=%s", staff.pk)


def confirm_email_change(
    staff: Staff, email: str, code: str, device: str, office: Consultancy
) -> TokenPair:
    """Make `email` the person's email with the Code sent to it; return this device's tokens.

    Raises:
        InvalidCodeError: Wrong, expired, used, out of tries, or for another email or person.
        EmailTakenError: Someone here took the email since the Code was sent.
    """
    new_email = normalize_email(email)
    # Outside the transaction, so a wrong Code still counts as a try.
    use_code(staff, CodePurpose.EMAIL_CHANGE, code, bound_to=new_email)
    old_email = str(staff.email)
    with transaction.atomic():
        if is_email_taken(new_email):
            raise EmailTakenError
        change_email(staff, new_email)
        tokens = sign_out_other_devices(staff, device)
    logger.info("Email changed: staff_id=%s", staff.pk)
    _send_notice(staff, old_email, office)
    return tokens


def _send_notice(staff: Staff, old_email: str, office: Consultancy) -> None:
    """Tell the old email about the change; the change stands even if this can't be sent."""
    notice = StaffEmailChanged(name=str(staff.name), new_email=str(staff.email))
    try:
        send_email(build_email(office_look(office), notice), old_email, "send_email_changed")
    except SendFailedError:
        # silent: the outside call logged the failure; the change is already made
        return
