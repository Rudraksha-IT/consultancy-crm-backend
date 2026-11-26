from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from applications.appointments import Appointment
from applications.statuses import ApplicationStatus, VisaStatus
from catalogue.models import Course
from students.models import Country

# The appointments to change on a Visa: a new moment, or None to clear it. Others stay.
DatesChange = dict[Appointment, datetime | None]


@dataclass(frozen=True)
class NewApplication:
    """An Application to add: an active Catalogue Course and one of its Intakes ("YYYY-MM")."""

    course: Course
    intake: str


class ApplicationSort(StrEnum):
    """How the office-wide Applications list can be ordered; "-" is newest or latest first."""

    APPLIED_ON = "applied_on"
    APPLIED_ON_DESC = "-applied_on"
    ADDED_ON = "added_on"
    ADDED_ON_DESC = "-added_on"
    INTAKE = "intake"
    INTAKE_DESC = "-intake"


@dataclass(frozen=True)
class ApplicationFilters:
    """Search and filters for the office-wide Applications list (spec 57a). Empty means "any"."""

    query: str = ""
    statuses: frozenset[ApplicationStatus] = frozenset()
    # An Intake month ("YYYY-MM") and an intake year ("YYYY").
    intake: str = ""
    year: str = ""
    # The country of the Application's University.
    country: Country | None = None
    # "", "me", "none" or a Staff id: whose Students.
    counsellor: str = ""
    sort: ApplicationSort = ApplicationSort.APPLIED_ON_DESC


@dataclass(frozen=True)
class FilterChoices:
    """The Intakes ("YYYY-MM"), years and University countries the office's Applications use."""

    intakes: list[str]
    years: list[str]
    countries: list[str]


class VisaSort(StrEnum):
    """How the office-wide Visas list can be ordered, by each Visa's next appointment.

    UPCOMING (the default) puts Visas with an appointment from today on first, soonest first, then
    Visas with no appointment (newest first), then those whose appointments are all past, latest
    first.
    """

    UPCOMING = "upcoming"
    APPOINTMENT = "appointment"
    APPOINTMENT_DESC = "-appointment"


@dataclass(frozen=True)
class VisaFilters:
    """Search and filters for the office-wide Visas list (spec 62b). Empty means "any"."""

    # The Student's name, email or phone, or the University's name.
    query: str = ""
    statuses: frozenset[VisaStatus] = frozenset()
    # The country of the Visa's University.
    country: Country | None = None
    # The Intake month ("YYYY-MM") of the Visa's Application.
    intake: str = ""
    # "", "me", "none" or a Staff id: whose Students.
    counsellor: str = ""
    # Days (Kathmandu), both included, on which any of the Visa's appointments falls.
    appointment_from: date | None = None
    appointment_to: date | None = None
    # True: only Visas with an appointment booked; False: only those without.
    has_appointment: bool | None = None
    sort: VisaSort = VisaSort.UPCOMING


@dataclass(frozen=True)
class VisaFilterChoices:
    """The Intakes ("YYYY-MM") and University countries the office's Visas use."""

    intakes: list[str]
    countries: list[str]
