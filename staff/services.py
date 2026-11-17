"""Public services for Staff accounts. Call them inside the Consultancy's schema."""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from central_login.directory import add_to_directory, move_in_directory
from staff.models import Staff, StaffStatus
from staff.types import NewStaff


def normalize_email(email: str) -> str:
    """Return the email in the form it is stored and looked up in (trimmed, lowercase)."""
    return email.strip().lower()


def create_staff(details: NewStaff, password: str) -> Staff:
    """Add an active Staff member who can log in with their email and password.

    Args:
        details: Name, email, Role and contact details of the new Staff member.
        password: Raw password; only its hash is stored.

    Returns:
        The saved Staff member.
    """
    staff = build_staff(details, StaffStatus.ACTIVE)
    staff.set_password(password)
    with transaction.atomic():
        staff.save()
        add_to_directory(staff.email)
    return staff


def change_email(staff: Staff, email: str) -> Staff:
    """Give a Staff member a new work email, moving them in the Central login's index too.

    The caller checks who may do it and that no one here uses the email yet.
    """
    old_email = str(staff.email)
    staff.email = normalize_email(email)
    with transaction.atomic():
        staff.save(update_fields=["email"])
        move_in_directory(old_email, staff.email)
    return staff


def build_staff(details: NewStaff, status: StaffStatus) -> Staff:
    """Return an unsaved Staff member with these details and status, and no password set.

    Two-step login starts as STAFF_TWO_FACTOR_DEFAULT says (issue 62), for the first Admin of a
    new office too.
    """
    return Staff(
        two_factor_required=settings.STAFF_TWO_FACTOR_DEFAULT,
        name=details.name,
        email=normalize_email(details.email),
        role=details.role,
        mobile=details.mobile,
        job_title=details.job_title,
        joined_on=details.joined_on or timezone.localdate(),
        status=status,
    )
