"""The lists that can be exported (spec 31b): their filters, columns, rows and filters in words.

Each list is found with that list's own filter code, so an export holds exactly the rows its
screen shows. The columns are fixed and never hold Documents, Notes or passport numbers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from itertools import batched
from typing import TYPE_CHECKING, Any

from django.db.models import QuerySet
from django.utils import timezone

from applications.appointments import Booked, next_appointment
from applications.office_list import find_applications
from applications.serializers import ApplicationListQuerySerializer, VisaListQuerySerializer
from applications.statuses import ApplicationStatus, VisaStatus
from applications.visa_list import checklists_for, find_visas
from clock_in.serializers import ClockInExportQuerySerializer
from clock_in.services import team_month_rows, team_staff
from config.serializers import StrictSerializer, parse_record_id
from exports.models import ExportList
from fee_types.models import FeeType
from payments.models import PaymentMethod
from payments.serializers import PaymentListQuerySerializer
from payments.services import find_payments
from staff.actors import actor_name
from staff.models import Staff
from students.models import Country, Source, Stage, Student, TargetLevel
from students.serializers import StudentListQuerySerializer
from students.services import find_students
from students.types import COUNSELLOR_ME, COUNSELLOR_NONE

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import TextChoices

# How many rows are read from the database at a time, so memory stays flat (micro EC2 budget).
CHUNK_SIZE = 500
NO_FILTERS_TEXT = "No filters, everyone"
UNKNOWN_COUNSELLOR_TEXT = "Unknown counsellor"
NO_COUNSELLOR_TEXT = "No counsellor yet"
UNKNOWN_FEE_TYPE_TEXT = "A Fee Type since deleted"
UNKNOWN_STUDENT_TEXT = "Unknown Student"
HAS_APPOINTMENT_TEXT = {"true": "Has a date", "false": "No date yet"}
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

Row = list[str]
Data = Mapping[str, Any]


@dataclass(frozen=True)
class ListExport:
    """How one list is exported."""

    # The list screen's own query serializer; its `page` field is never accepted here.
    query: type[StrictSerializer[None]]
    columns: tuple[str, ...]
    # The list's own search: the checked query serializer and who is asking ("me").
    find: Callable[[Any, Staff], QuerySet[Any]]
    # The rows of what `find` found; the checked query serializer too, for the month.
    rows: Callable[[QuerySet[Any], Any], Iterator[Row]]
    # (label, filter field, how its value reads), in the order the screen shows them.
    described: tuple[tuple[str, str, Callable[[Any, Staff], str]], ...]


def describe(export: ListExport, data: Data, viewer: Staff) -> str:
    """The filters in words, e.g. "Stage: Applying, Counsellor: Hari Thapa"."""
    parts = [
        f"{label}: {words(data[field], viewer)}"
        for label, field, words in export.described
        if data.get(field) not in (None, "", [])
    ]
    return ", ".join(parts) or NO_FILTERS_TEXT


def intake_text(intake: str) -> str:
    """An Intake month ("2027-02") as people write it ("Feb 2027"), always AD (spec 102)."""
    year, month = intake.split("-")
    return f"{_MONTHS[int(month) - 1]} {year}"


def _label(choices: type[TextChoices], value: str) -> str:
    """The shown word for a stored choice; blank stays blank, an old value shows as stored."""
    if not value:
        return ""
    try:
        return str(choices(value).label)
    except ValueError:
        # silent: not a number, so kept as written
        return value


def _labelled(choices: type[TextChoices]) -> Callable[[Any, Staff], str]:
    return lambda value, _viewer: _label(choices, str(value))


def _labels_joined(choices: type[TextChoices]) -> Callable[[Any, Staff], str]:
    return lambda values, _viewer: " or ".join(_label(choices, value) for value in values)


def _as_is(value: Any, _viewer: Staff) -> str:  # noqa: ANN401  # any validated value
    return str(value)


def _intake(value: Any, _viewer: Staff) -> str:  # noqa: ANN401  # a validated Intake
    return intake_text(str(value))


def _has_appointment(value: Any, _viewer: Staff) -> str:  # noqa: ANN401  # "true" or "false"
    return HAS_APPOINTMENT_TEXT[str(value)]


def _appointment_text(booked: Booked | None) -> str:
    """The appointment and when, as "Interview 2027-02-01 10:00"; empty when none is booked."""
    if booked is None:
        return ""
    return f"{booked.kind.label} {timezone.localtime(booked.at):%Y-%m-%d %H:%M}"


def _counsellor_filter(value: Any, viewer: Staff) -> str:  # noqa: ANN401  # "me", "none", a UUID
    """Whose Students, as the person exporting saw it."""
    if value == COUNSELLOR_ME:
        return f"{actor_name(viewer)}'s Students"
    if value == COUNSELLOR_NONE:
        return NO_COUNSELLOR_TEXT
    public_id = parse_record_id(value)
    name = (
        None
        if public_id is None
        else Staff.objects.filter(public_id=public_id).values_list("name", flat=True).first()
    )
    return UNKNOWN_COUNSELLOR_TEXT if name is None else str(name)


def month_text(month: str) -> str:
    """A month ("2026-10") as people write it ("October 2026")."""
    year, number = month.split("-")
    return f"{_MONTH_NAMES[int(number) - 1]} {year}"


def _month(value: Any, _viewer: Staff) -> str:  # noqa: ANN401  # a validated month
    return month_text(str(value))


def _counsellor(counsellor: Staff | None) -> str:
    return "" if counsellor is None else str(counsellor.name)


def _day(moment: datetime) -> str:
    """The Consultancy's calendar day of a stored moment."""
    return timezone.localdate(moment).isoformat()


def _fee_type_filter(value: Any, _viewer: Staff) -> str:  # noqa: ANN401  # a validated id
    """The Fee Type's name, from its UUID; one deleted since reads as unknown."""
    public_id = parse_record_id(str(value))
    found = (
        None
        if public_id is None
        else FeeType.objects.filter(public_id=public_id).values_list("name", flat=True).first()
    )
    return found or UNKNOWN_FEE_TYPE_TEXT


def _student_filter(value: Any, _viewer: Staff) -> str:  # noqa: ANN401  # a validated id
    """The Student's name, from their UUID (ADR 0027)."""
    public_id = parse_record_id(value)
    found = (
        None
        if public_id is None
        else Student.objects.filter(public_id=public_id).values_list("name", flat=True).first()
    )
    return found or UNKNOWN_STUDENT_TEXT


def _payment_rows(payments: QuerySet[Any]) -> Iterator[Row]:
    for payment in payments.iterator(chunk_size=CHUNK_SIZE):
        yield [
            payment.paid_on.isoformat(),
            payment.student.name,
            payment.fee_type.name,
            payment.note,
            str(payment.amount),
            PaymentMethod(payment.method).label,
            payment.recorded_by_name,
        ]


def _student_rows(students: QuerySet[Any]) -> Iterator[Row]:
    for student in students.iterator(chunk_size=CHUNK_SIZE):
        yield [
            student.name,
            student.phone,
            student.email,
            _label(Stage, student.stage),
            _counsellor(student.assigned_counsellor),
            _label(Country, student.country),
            _label(TargetLevel, student.target_level),
            _label(Source, student.source),
            _day(student.created_at),
        ]


def _application_rows(applications: QuerySet[Any]) -> Iterator[Row]:
    for application in applications.iterator(chunk_size=CHUNK_SIZE):
        student, course = application.student, application.course
        applied_on = application.applied_on
        yield [
            student.name,
            student.email,
            student.phone,
            course.university.name,
            _label(Country, course.university.country),
            course.name,
            intake_text(application.intake),
            _label(ApplicationStatus, application.status),
            _counsellor(student.assigned_counsellor),
            "" if applied_on is None else applied_on.isoformat(),
            _day(application.created_at),
        ]


def _visa_rows(visas: QuerySet[Any]) -> Iterator[Row]:
    """Each Visa with its next appointment and its Student's document count.

    The checklists are read a chunk at a time.
    """
    today = timezone.localdate()
    for chunk in batched(visas.iterator(chunk_size=CHUNK_SIZE), CHUNK_SIZE, strict=False):
        checklists = checklists_for(chunk)
        for visa in chunk:
            student, application = visa.student, visa.application
            course = application.course
            checklist = checklists[visa.student_id]
            soon = next_appointment(visa.dates, today)
            yield [
                student.name,
                student.email,
                student.phone,
                _label(Country, course.university.country),
                course.university.name,
                course.name,
                intake_text(application.intake),
                _label(VisaStatus, visa.status),
                _appointment_text(soon),
                f"{checklist.done}/{checklist.total}",
                _counsellor(student.assigned_counsellor),
            ]


def _clock_in_rows(people: QuerySet[Any], query: ClockInExportQuerySerializer) -> Iterator[Row]:
    """Each person's month in the team view: days, hours, short days, no clock-out, corrected."""
    year, month = query.year_month()
    for chunk in batched(people.iterator(chunk_size=CHUNK_SIZE), CHUNK_SIZE, strict=False):
        for person, summary in team_month_rows(chunk, year, month):
            yield [
                person.name,
                person.job_title,
                str(summary.days_worked),
                f"{summary.minutes / 60:.1f}",
                str(summary.short_days),
                str(summary.missing_clock_outs),
                str(summary.corrected),
            ]


LIST_EXPORTS: dict[ExportList, ListExport] = {
    ExportList.STUDENTS: ListExport(
        query=StudentListQuerySerializer,
        columns=(
            "Name",
            "Mobile",
            "Email",
            "Stage",
            "Counsellor",
            "Wants to study in",
            "Target level",
            "How they found us",
            "First visit",
        ),
        find=lambda query, viewer: find_students(query.to_filters(), viewer),
        rows=lambda found, _query: _student_rows(found),
        described=(
            ("Search", "q", _as_is),
            ("Stage", "stage", _labelled(Stage)),
            ("Counsellor", "counsellor", _counsellor_filter),
            ("How they found us", "source", _labelled(Source)),
            ("Wants to study in", "country", _labelled(Country)),
            ("Target level", "target_level", _labelled(TargetLevel)),
        ),
    ),
    ExportList.APPLICATIONS: ListExport(
        query=ApplicationListQuerySerializer,
        columns=(
            "Student",
            "Email",
            "Mobile",
            "University",
            "Country",
            "Course",
            "Intake",
            "Status",
            "Counsellor",
            "Applied",
            "Added",
        ),
        find=lambda query, viewer: find_applications(query.to_filters(), viewer),
        rows=lambda found, _query: _application_rows(found),
        described=(
            ("Search", "q", _as_is),
            ("Status", "status", _labels_joined(ApplicationStatus)),
            ("Intake", "intake", _intake),
            ("Year", "year", _as_is),
            ("Country", "country", _labelled(Country)),
            ("Counsellor", "counsellor", _counsellor_filter),
        ),
    ),
    ExportList.VISAS: ListExport(
        query=VisaListQuerySerializer,
        columns=(
            "Student",
            "Email",
            "Mobile",
            "Country",
            "University",
            "Course",
            "Intake",
            "Visa status",
            "Next appointment",
            "Documents",
            "Counsellor",
        ),
        find=lambda query, viewer: find_visas(query.to_filters(), viewer),
        rows=lambda found, _query: _visa_rows(found),
        described=(
            ("Status", "status", _labels_joined(VisaStatus)),
            ("Country", "country", _labelled(Country)),
            ("Intake", "intake", _intake),
            ("Counsellor", "counsellor", _counsellor_filter),
            ("Appointments from", "appointment_from", _as_is),
            ("Appointments to", "appointment_to", _as_is),
            ("Appointment", "has_appointment", _has_appointment),
        ),
    ),
    ExportList.CLOCK_IN: ListExport(
        query=ClockInExportQuerySerializer,
        columns=(
            "Employee",
            "Job title",
            "Days worked",
            "Hours",
            "Short days",
            "No clock-out",
            "Corrected",
        ),
        find=lambda query, _viewer: team_staff(*query.year_month()),
        rows=_clock_in_rows,
        described=(("Month", "month", _month),),
    ),
    ExportList.PAYMENTS: ListExport(
        query=PaymentListQuerySerializer,
        columns=(
            "Date",
            "Student",
            "Fee Type",
            "Note",
            "Amount (NPR)",
            "Method",
            "Taken by",
        ),
        find=lambda query, _viewer: find_payments(query.to_filters()).payments,
        rows=lambda found, _query: _payment_rows(found),
        described=(
            ("From", "from", _as_is),
            ("To", "to", _as_is),
            ("Student", "student", _student_filter),
            ("Fee Type", "fee_type", _fee_type_filter),
            ("Search", "q", _as_is),
        ),
    ),
}
