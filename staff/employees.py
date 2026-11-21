"""Managing the office's Employees (spec 18-21). Call inside the Consultancy.

Admin checks are the views' job. The Admin never sees or sets a password: an Invite emails the
person a Code instead.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from auth_tokens.services import sign_out_everywhere
from central_login.directory import add_to_directory, remove_from_directory
from codes.emails import send_code_email
from codes.models import OneTimeCode
from codes.services import issue_code
from codes.types import CodePurpose
from modules.services import is_module_on
from modules.types import Module
from staff.exceptions import EmailTakenError, EmployeeChangeRefusedError, RoleNotAvailableError
from staff.models import Role, Staff, StaffStatus
from staff.services import build_staff, normalize_email
from staff.types import NewStaff, Office
from tenancy.models import Consultancy
from transfer_requests.closing import close_waiting_asked_by

NOT_YOURSELF_MESSAGE = "You can't change your own Role or account."
HAS_LEFT_MESSAGE = "This person has left the office."
NO_INVITE_MESSAGE = "Only someone who hasn't joined yet has an invite."
CANCEL_INSTEAD_MESSAGE = "Cancel the invite instead."

# Roles that can be an Assigned Counsellor (Admins counsel too, spec 22b).
COUNSELLING_ROLES = (Role.ADMIN, Role.COUNSELLOR)

logger = logging.getLogger(__name__)


def list_employees() -> QuerySet[Staff]:
    """Every Staff member of the Consultancy, invited and departed included, by name."""
    return Staff.objects.order_by("name", "pk")


def list_counsellors() -> QuerySet[Staff]:
    """Active Staff who can be picked as an Assigned Counsellor, by name."""
    return Staff.objects.filter(status=StaffStatus.ACTIVE, role__in=COUNSELLING_ROLES).order_by(
        "name", "pk"
    )


def available_roles(consultancy: Consultancy) -> list[Role]:
    """The Roles that can be given here; Teacher only while the Batches Module is on."""
    has_classes = is_module_on(consultancy, Module.BATCHES)
    return [role for role in Role if role != Role.TEACHER or has_classes]


def is_email_taken(email: str) -> bool:
    """Whether a Staff member here already uses `email`."""
    return Staff.objects.filter(email=normalize_email(email)).exists()


def invite_employee(
    details: NewStaff, consultancy: Consultancy, office: Office, by: Staff
) -> Staff:
    """Add someone as invited and email them a Code to join with; they choose their password.

    Raises:
        EmailTakenError: Someone here already uses the email.
        RoleNotAvailableError: The Role can't be given here.
    """
    _check_role(details.role, consultancy)
    staff = invite(details, office)
    logger.info("Employee invited: staff_id=%s, by_staff_id=%s", staff.pk, by.pk)
    return staff


def invite(details: NewStaff, office: Office) -> Staff:
    """Add someone as invited and email them a Code to join with. Call inside their office.

    Used by Employees and by a Platform Admin adding an Admin (issue 64); the caller logs who.

    Raises:
        EmailTakenError: Someone here already uses the email.
    """
    staff = build_staff(details, StaffStatus.INVITED)
    staff.set_unusable_password()
    try:
        with transaction.atomic():
            staff.save()
            add_to_directory(staff.email)
            _send_join_code(staff, office)
    except IntegrityError as error:
        raise EmailTakenError from error
    return staff


def resend_invite(staff: Staff, office: Office, by: Staff) -> None:
    """Email a new Code to someone who hasn't joined yet; the old Code stops working.

    Raises:
        EmployeeChangeRefusedError: They have no invite (they joined or have left).
        CodeLimitReachedError: Too many Codes were sent to them in the last hour.
    """
    _check_invited(staff)
    _send_join_code(staff, office)
    logger.info("Invite sent again: staff_id=%s, by_staff_id=%s", staff.pk, by.pk)


def cancel_invite(staff: Staff, by: Staff) -> None:
    """Remove someone who hasn't joined yet, with their Codes, so no Code of theirs works.

    Raises:
        EmployeeChangeRefusedError: They have no invite (they joined or have left).
    """
    _check_invited(staff)
    staff_id = staff.pk
    with transaction.atomic():
        OneTimeCode.objects.filter(staff=staff).delete()
        remove_from_directory(staff.email)
        staff.delete()
    logger.info("Invite cancelled: staff_id=%s, by_staff_id=%s", staff_id, by.pk)


def change_role(staff: Staff, role: Role, consultancy: Consultancy, by: Staff) -> Staff:
    """Give someone a new Role. It takes effect on their next request.

    Raises:
        EmployeeChangeRefusedError: It's the Admin themselves, or the person has left.
        RoleNotAvailableError: The Role can't be given here (keeping one's own Role always can).
    """
    _check_changeable(staff, by)
    if role != staff.role:
        _check_role(role, consultancy)
        staff.role = role
        staff.save(update_fields=["role"])
        logger.info(
            "Employee role changed: staff_id=%s, role=%s, by_staff_id=%s",
            staff.pk,
            role.value,
            by.pk,
        )
    return staff


def deactivate_employee(staff: Staff, by: Staff) -> Staff:
    """Mark someone as having left: they can't log in and every login of theirs ends at once.

    Their record stays, so their name stays on past History and Tasks.

    Raises:
        EmployeeChangeRefusedError: It's the Admin themselves, the person hasn't joined yet,
            or has already left.
    """
    _check_changeable(staff, by)
    if staff.status == StaffStatus.INVITED:
        raise EmployeeChangeRefusedError(CANCEL_INSTEAD_MESSAGE)
    with transaction.atomic():
        staff.status = StaffStatus.DEACTIVATED
        staff.save(update_fields=["status"])
        remove_from_directory(staff.email)
        sign_out_everywhere(staff)
        close_waiting_asked_by(staff)
    logger.info("Employee deactivated: staff_id=%s, by_staff_id=%s", staff.pk, by.pk)
    return staff


def send_join_code_to_invited(email: str, office: Office) -> Staff | None:
    """Email a new Code if `email` belongs to someone invited here; return them, or None."""
    staff = find_invited(email)
    if staff is not None:
        _send_join_code(staff, office)
    return staff


def find_invited(email: str) -> Staff | None:
    """The invited Staff member with `email`, if any."""
    return Staff.objects.filter(email=normalize_email(email), status=StaffStatus.INVITED).first()


def _send_join_code(staff: Staff, office: Office) -> None:
    code = issue_code(staff, CodePurpose.JOIN)
    send_code_email(staff, code, CodePurpose.JOIN, office.consultancy, link=office.join_link)


def _check_role(role: Role, consultancy: Consultancy) -> None:
    if role not in available_roles(consultancy):
        raise RoleNotAvailableError


def _check_invited(staff: Staff) -> None:
    if staff.status != StaffStatus.INVITED:
        raise EmployeeChangeRefusedError(NO_INVITE_MESSAGE)


def _check_changeable(staff: Staff, by: Staff) -> None:
    if staff.pk == by.pk:
        raise EmployeeChangeRefusedError(NOT_YOURSELF_MESSAGE)
    if staff.status == StaffStatus.DEACTIVATED:
        raise EmployeeChangeRefusedError(HAS_LEFT_MESSAGE)
