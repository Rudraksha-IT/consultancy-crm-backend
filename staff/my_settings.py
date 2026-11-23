"""A Staff member's own Settings (spec 22c-22g; issue 38). Call inside the Consultancy's schema.

Each Staff member changes only their own name, mobile, light/dark, Swipe between pages and email
choices, and their own password. Job title, Role and joining date stay with the Admin; the
email changes only with a Code sent to the new one (`staff.email_change`, 2026-11-22).
"""

from __future__ import annotations

import logging
from dataclasses import fields

from django.db import transaction

from auth_tokens.services import sign_out_other_devices
from auth_tokens.types import TokenPair
from staff.exceptions import PasswordRulesNotMetError, WrongCurrentPasswordError
from staff.models import Staff
from staff.passwords import unmet_password_rules
from staff.types import MyDetailsChange, NotificationChoices

logger = logging.getLogger(__name__)

# Each changeable detail and the Staff field that stores it.
_STORED_AS = {
    "name": "name",
    "mobile": "mobile",
    "colour_mode": "colour_mode",
    "page_swipe": "page_swipe",
    "task_given": "email_on_task_given",
    "task_due_tomorrow": "email_on_task_due_tomorrow",
    "morning_summary": "email_morning_summary",
}


def capitalise_name(name: str) -> str:
    """Return the name with single spaces and a capital first letter on each word.

    The rest of each word is left alone, so "KC" and "McDonald" stay as typed.
    """
    return " ".join(word[0].upper() + word[1:] for word in name.split())


def notification_choices(staff: Staff) -> NotificationChoices:
    """Which emails the app sends this Staff member."""
    return NotificationChoices(
        task_given=staff.email_on_task_given,
        task_due_tomorrow=staff.email_on_task_due_tomorrow,
        morning_summary=staff.email_morning_summary,
    )


def update_my_details(staff: Staff, change: MyDetailsChange) -> Staff:
    """Save what the Staff member changed about themselves; the name is capitalised."""
    changed: list[str] = []
    for field in fields(change):
        value = getattr(change, field.name)
        if value is None:
            continue
        if field.name == "name":
            value = capitalise_name(value)
        setattr(staff, _STORED_AS[field.name], value)
        changed.append(_STORED_AS[field.name])
    if changed:
        staff.save(update_fields=changed)
    logger.info("My details saved: staff_id=%s", staff.pk)
    return staff


@transaction.atomic
def change_my_password(staff: Staff, current: str, new: str, device: str) -> TokenPair:
    """Set a new password, sign every other device out at once and log this one in again.

    Raises:
        PasswordRulesNotMetError: The new password breaks a rule (checked first).
        WrongCurrentPasswordError: `current` isn't the person's password.
    """
    unmet = unmet_password_rules(new)
    if unmet:
        raise PasswordRulesNotMetError(unmet)
    if not staff.check_password(current):
        logger.info("Password change refused: staff_id=%s", staff.pk)
        raise WrongCurrentPasswordError
    staff.set_password(new)
    staff.save(update_fields=["password"])
    tokens = sign_out_other_devices(staff, device)
    logger.info("Password changed: staff_id=%s", staff.pk)
    return tokens


def sign_out_my_other_devices(staff: Staff, device: str) -> TokenPair:
    """Sign every other device out at once; this one gets new tokens."""
    tokens = sign_out_other_devices(staff, device)
    logger.info("Signed out of other devices: staff_id=%s", staff.pk)
    return tokens
