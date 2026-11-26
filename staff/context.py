"""The "current Staff and Consultancy context" the frontend builds its navigation from."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.http import HttpRequest
from rest_framework.request import Request

from emails.types import OfficeDetails
from modules.services import get_enabled_modules
from modules.types import Module
from staff.actors import is_platform_visitor
from staff.exceptions import StaffNotAuthenticatedError
from staff.models import Staff
from tenancy.models import Consultancy
from tenancy.services import (
    get_access,
    get_date_display,
    get_logo,
    get_office_details,
)
from tenancy.status import ConsultancyStatus
from tenancy.subdomains import get_platform_host
from tenancy.types import DateDisplay, OfficeLogo


@dataclass(frozen=True)
class StaffSummary:
    """Who is logged in."""

    id: str  # their UUID (ADR 0027)
    name: str
    email: str
    role: str
    colour_mode: str


@dataclass(frozen=True)
class ConsultancySummary:
    """Which Consultancy they are logged in to."""

    name: str
    # The office logo, or None when the initials show (spec 99c).
    logo: OfficeLogo | None
    # The Consultancy Status (issue 42): why Staff are limited (None when they aren't), and the
    # end of an Evaluation, so the shell can warn before it.
    status: ConsultancyStatus
    status_message: str | None
    evaluation_ends_at: datetime | None
    # Office details (Configuration → General), printed at the bottom of every email; emails to
    # Students can't be sent until the contact email is set (spec stories 11-13).
    office_details: OfficeDetails
    has_contact_email: bool


@dataclass(frozen=True)
class SupportAccessSummary:
    """Set while a Platform Admin is inside the office as themselves (issue 27)."""

    platform_admin_name: str
    # Where "Leave this office" goes back to: the Platform's address.
    platform_host: str


@dataclass(frozen=True)
class StaffContext:
    """Everything the frontend needs to know about the current session."""

    staff: StaffSummary
    consultancy: ConsultancySummary
    modules: list[Module]
    date_display: DateDisplay
    # Swipe between pages: the logged-in person's own choice in Settings (asked 2026-11-17).
    page_swipe: bool
    # None for Staff; set for a Platform Admin on a Support Access visit, who sees the banner.
    support_access: SupportAccessSummary | None


def get_request_staff(request: HttpRequest | Request) -> Staff:
    """Return the Staff member logged in on the request.

    Raises:
        StaffNotAuthenticatedError: Nobody is logged in.
    """
    user = request.user
    if not isinstance(user, Staff):
        raise StaffNotAuthenticatedError
    return user


def build_staff_context(staff: Staff, consultancy: Consultancy) -> StaffContext:
    """Return the session context for a Staff member of a Consultancy."""
    access = get_access(consultancy)
    office_details = get_office_details(consultancy)
    return StaffContext(
        staff=StaffSummary(
            id=str(staff.public_id),
            name=staff.name,
            email=staff.email,
            role=staff.role,
            colour_mode=staff.colour_mode,
        ),
        consultancy=ConsultancySummary(
            name=consultancy.name,
            logo=get_logo(consultancy),
            status=access.status,
            status_message=access.message,
            evaluation_ends_at=access.evaluation_ends_at,
            office_details=office_details,
            has_contact_email=bool(office_details.contact_email),
        ),
        modules=get_enabled_modules(consultancy),
        date_display=get_date_display(consultancy),
        page_swipe=staff.page_swipe,
        support_access=(
            SupportAccessSummary(platform_admin_name=staff.name, platform_host=get_platform_host())
            if is_platform_visitor(staff)
            else None
        ),
    )
