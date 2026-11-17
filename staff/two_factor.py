"""Two-step login for Staff (issue 62). Call inside the Consultancy's schema.

An Admin (or a Platform Admin in Support Access) switches it on or off for each person on
Employees, and resets it for someone who lost their phone. A Staff member switches their own on
in My settings, and off there when the policy lets them. Who may do what is asked of
`access.policy`; every change is recorded under whoever made it, and ends the person's logins.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from auth_tokens.services import issue_tokens, sign_out_everywhere
from auth_tokens.types import Device, TokenPair
from staff.employees import HAS_LEFT_MESSAGE
from staff.exceptions import EmployeeChangeRefusedError
from staff.models import Staff, StaffStatus
from two_factor.models import EventKind
from two_factor.services import check_available, forget, record

logger = logging.getLogger(__name__)


def set_two_factor_required(person: Staff, required: bool, by: Staff) -> Staff:
    """Switch `person`'s two-step login on or off, as their Admin; the caller asked the policy.

    Switching on asks them to set up their app at their next login; switching off removes
    their app. Either way every login of theirs ends. Asking for what is already so changes
    nothing, except that an Admin now requiring what the person chose stops them switching it
    off themselves.

    Raises:
        EmployeeChangeRefusedError: The person has left.
        TwoFactorUnavailableError: Switching on, on a server with no secrets key.
    """
    _check_not_left(person)
    if person.two_factor_required == required:
        if required and person.two_factor_chosen_by_self:
            with transaction.atomic():
                _store(person, required=True, by_self=False)
                record(person, EventKind.SWITCHED_ON, by)
        return person
    if required:
        check_available()
    with transaction.atomic():
        _store(person, required=required, by_self=False)
        if not required:
            forget(person)
        record(person, EventKind.SWITCHED_ON if required else EventKind.SWITCHED_OFF, by)
        sign_out_everywhere(person)
    logger.info(
        "two-step login %s by an Admin: staff_id=%s by_staff_id=%s",
        "switched on" if required else "switched off",
        person.pk,
        by.pk,
    )
    return person


def reset_two_factor(person: Staff, by: Staff) -> Staff:
    """Remove `person`'s app and recovery codes and end every login of theirs (a lost phone).

    With their switch on, they set up again at their next login. The caller asked the policy.

    Raises:
        EmployeeChangeRefusedError: The person has left.
    """
    _check_not_left(person)
    with transaction.atomic():
        forget(person)
        record(person, EventKind.RESET, by)
        sign_out_everywhere(person)
    logger.info("two-step login reset: staff_id=%s by_staff_id=%s", person.pk, by.pk)
    return person


def switch_on_myself(staff: Staff) -> None:
    """Require two-step login of myself, chosen by me; every login of mine ends.

    The caller starts setup at once and checked it was off.

    Raises:
        TwoFactorUnavailableError: The server has no secrets key.
    """
    check_available()
    with transaction.atomic():
        _store(staff, required=True, by_self=True)
        forget(staff)
        record(staff, EventKind.SWITCHED_ON)
        sign_out_everywhere(staff)
    logger.info("two-step login switched on by themselves: staff_id=%s", staff.pk)


def switch_off_myself(staff: Staff, device: str) -> TokenPair:
    """Stop requiring two-step login of myself; every other login ends, this device stays in.

    The caller checked the policy, the password and a number (or a recovery code).
    """
    with transaction.atomic():
        _store(staff, required=False, by_self=False)
        forget(staff)
        record(staff, EventKind.SWITCHED_OFF)
        sign_out_everywhere(staff)
        tokens = issue_tokens(staff, Device(id=device, signed_in_at=timezone.now()))
    logger.info("two-step login switched off by themselves: staff_id=%s", staff.pk)
    return tokens


def _store(staff: Staff, *, required: bool, by_self: bool) -> None:
    staff.two_factor_required = required
    staff.two_factor_chosen_by_self = by_self
    staff.save(update_fields=["two_factor_required", "two_factor_chosen_by_self"])


def _check_not_left(person: Staff) -> None:
    if person.status == StaffStatus.DEACTIVATED:
        raise EmployeeChangeRefusedError(HAS_LEFT_MESSAGE)
