"""What the Application endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.utils import timezone
from rest_framework import serializers

from applications.appointments import Appointment
from applications.statuses import ApplicationStatus, VisaStatus
from applications.types import (
    ApplicationFilters,
    ApplicationSort,
    DatesChange,
    NewApplication,
    VisaFilters,
    VisaSort,
)
from catalogue.models import Course
from config.serializers import (
    UNKNOWN_FIELD_KEY,
    RecordIdField,
    StrictSerializer,
    parse_record_id,
)
from students.models import Country
from students.profile_serializers import INTAKE_MESSAGE, INTAKE_PATTERN
from students.serializers import (
    CHOOSE_FROM_LIST_MESSAGE,
    COUNSELLOR_FILTER_MESSAGE,
    COUNSELLOR_FILTER_PATTERN,
    SEARCH_MAX_LENGTH,
    StrictChoiceField,
)

CHOOSE_COURSE_MESSAGE = "Choose a course from the Catalogue."
CHOOSE_INTAKE_MESSAGE = "Choose one of this course's intakes."
CHOOSE_APPLICATION_MESSAGE = "Choose an application that has an offer."
YEAR_PATTERN = re.compile(r"^20[0-9]{2}\Z")
YEAR_MESSAGE = "Choose a year from 2000 on."
_ALL_MESSAGES = ("required", "null", "blank", "invalid", "max_length")
# A Visa appointment, as the Consultancy's clock shows it: "YYYY-MM-DDTHH:MM" (ASCII digits only).
APPOINTMENT_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}\Z")
APPOINTMENT_FORMAT = "%Y-%m-%dT%H:%M"
APPOINTMENT_MESSAGE = "Enter the date and time."
APPOINTMENT_PAST_MESSAGE = "Pick today or a later day."
APPOINTMENT_TOO_FAR_MESSAGE = "Pick a day within the next two years."
NO_DATE_MESSAGE = "Change at least one date."
# How far ahead an appointment can be booked.
APPOINTMENT_MAX_DAYS_AHEAD = 730
DAY_MESSAGE = "Enter a date like 2027-02-01."
DAY_ORDER_MESSAGE = "The end can't be before the start."


def _same_message(message: str) -> dict[str, Any]:
    return dict.fromkeys(_ALL_MESSAGES, message)


class CatalogueCourseField(serializers.Field[Course, str, str, Any]):
    """A Course picked by UUID from the active Catalogue: neither it nor its University archived."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(CHOOSE_COURSE_MESSAGE))

    def to_internal_value(self, data: object) -> Course:
        """Return the Course; anything but the UUID of one that can be picked is refused."""
        public_id = parse_record_id(data)
        if public_id is None:
            self.fail("invalid")
        course = (
            Course.objects.select_related("university")
            .prefetch_related("intakes")
            .filter(public_id=public_id, archived=False, university__archived=False)
            .first()
        )
        if course is None:
            self.fail("invalid")
        return course

    def to_representation(self, value: Course) -> str:
        """Return the UUID (unused: answers are built by hand)."""
        return str(value.public_id)


class NewApplicationSerializer(StrictSerializer[None]):
    """A new Application: Course and Intake from the Catalogue only (spec 51). Always Applied."""

    course = CatalogueCourseField()
    intake = serializers.CharField(
        trim_whitespace=False, error_messages=_same_message(CHOOSE_INTAKE_MESSAGE)
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """The Intake must be one the Course runs."""
        course: Course = attrs["course"]
        if attrs["intake"] not in {intake.month for intake in course.intakes.all()}:
            raise serializers.ValidationError({"intake": [CHOOSE_INTAKE_MESSAGE]})
        return attrs

    def to_new_application(self) -> NewApplication:
        """Return the validated Course and Intake."""
        return NewApplication(
            course=self.validated_data["course"], intake=self.validated_data["intake"]
        )


class StatusChangeSerializer(StrictSerializer[None]):
    """The Status to set by hand (spec 53): Applied or Rejected; the service refuses Offer."""

    status = StrictChoiceField(choices=ApplicationStatus.choices)

    def status_value(self) -> ApplicationStatus:
        """The Status asked for."""
        return ApplicationStatus(self.validated_data["status"])


class NewVisaSerializer(StrictSerializer[None]):
    """A new Visa: which of the Student's Applications it is for. Always starts as Applied."""

    application = RecordIdField(CHOOSE_APPLICATION_MESSAGE)

    def application_id(self) -> UUID:
        """The Application asked for; the service checks it is the Student's, at Offer."""
        application: UUID = self.validated_data["application"]
        return application


class ChosenOfferSerializer(StrictSerializer[None]):
    """The offer the Student goes ahead with: one of their Applications at Offer (spec 115)."""

    application = RecordIdField(CHOOSE_APPLICATION_MESSAGE)

    def application_id(self) -> UUID:
        """The Application asked for; the service checks it is the Student's, at Offer."""
        application: UUID = self.validated_data["application"]
        return application


class VisaStatusChangeSerializer(StrictSerializer[None]):
    """The Status to move one Visa to (spec 59). Its Application never changes."""

    status = StrictChoiceField(choices=VisaStatus.choices)

    def status_value(self) -> VisaStatus:
        """The Status asked for."""
        return VisaStatus(self.validated_data["status"])


class AppointmentAtField(serializers.Field[datetime, str, str, Any]):
    """A moment in the Consultancy's time zone, sent as "YYYY-MM-DDTHH:MM"; null clears it.

    It must fall on today or a later day, at most two years ahead.
    """

    def __init__(self) -> None:
        super().__init__(
            required=False, allow_null=True, error_messages=_same_message(APPOINTMENT_MESSAGE)
        )

    def to_internal_value(self, data: object) -> datetime:
        """Return the aware moment; anything else, or a day that doesn't exist, is refused."""
        if not isinstance(data, str) or APPOINTMENT_PATTERN.match(data) is None:
            self.fail("invalid")
        try:
            naive = datetime.strptime(data, APPOINTMENT_FORMAT)  # made aware below
        except ValueError:
            # self.fail raises the field's validation error
            self.fail("invalid")
        moment = timezone.make_aware(naive)
        today = timezone.localdate()
        if naive.date() < today:
            raise serializers.ValidationError(APPOINTMENT_PAST_MESSAGE)
        if naive.date() > today + timedelta(days=APPOINTMENT_MAX_DAYS_AHEAD):
            raise serializers.ValidationError(APPOINTMENT_TOO_FAR_MESSAGE)
        return moment

    def to_representation(self, value: datetime) -> str:
        """Return it as the Consultancy's clock shows it (unused: answers are built by hand)."""
        return timezone.localtime(value).strftime(APPOINTMENT_FORMAT)


class VisaDatesSerializer(StrictSerializer[None]):
    """The Visa appointments to book, move or clear (spec 62a); those not sent stay as they are."""

    biometrics_at = AppointmentAtField()
    medical_at = AppointmentAtField()
    interview_at = AppointmentAtField()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """At least one appointment must be sent."""
        if not attrs:
            raise serializers.ValidationError({UNKNOWN_FIELD_KEY: [NO_DATE_MESSAGE]})
        return attrs

    def change(self) -> DatesChange:
        """The appointments sent, each with its new moment or None."""
        return {
            kind: self.validated_data[f"{kind.value}_at"]
            for kind in Appointment
            if f"{kind.value}_at" in self.validated_data
        }


def _choice(choices: Iterable[tuple[str, str]] | Iterable[str]) -> StrictChoiceField:
    """One value from the list, refused without repeating what was sent."""
    return StrictChoiceField(
        choices=list(choices),
        required=False,
        error_messages={"invalid_choice": CHOOSE_FROM_LIST_MESSAGE},
    )


class ApplicationListQuerySerializer(StrictSerializer[None]):
    """The office-wide Applications list's search, filters, order and page (spec 57a)."""

    q = serializers.CharField(max_length=SEARCH_MAX_LENGTH, required=False, allow_blank=True)
    status = serializers.ListField(
        child=StrictChoiceField(
            choices=ApplicationStatus.choices,
            error_messages={"invalid_choice": CHOOSE_FROM_LIST_MESSAGE},
        ),
        required=False,
        max_length=len(ApplicationStatus),
    )
    intake = serializers.RegexField(
        INTAKE_PATTERN, required=False, error_messages={"invalid": INTAKE_MESSAGE}
    )
    year = serializers.RegexField(
        YEAR_PATTERN, required=False, error_messages={"invalid": YEAR_MESSAGE}
    )
    country = _choice(Country.choices)
    counsellor = serializers.RegexField(
        COUNSELLOR_FILTER_PATTERN,
        required=False,
        error_messages={"invalid": COUNSELLOR_FILTER_MESSAGE},
    )
    sort = _choice(ApplicationSort)
    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def to_filters(self) -> ApplicationFilters:
        """Return the validated search, filters and order."""
        data = self.validated_data
        country = data.get("country")
        return ApplicationFilters(
            query=data.get("q", ""),
            statuses=frozenset(ApplicationStatus(value) for value in data.get("status", [])),
            intake=data.get("intake", ""),
            year=data.get("year", ""),
            country=None if country is None else Country(country),
            counsellor=data.get("counsellor", ""),
            sort=ApplicationSort(data.get("sort", ApplicationSort.APPLIED_ON_DESC)),
        )

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number


class VisaListQuerySerializer(StrictSerializer[None]):
    """The office-wide Visas list's filters, order and page (spec 62b)."""

    status = serializers.ListField(
        child=StrictChoiceField(
            choices=VisaStatus.choices,
            error_messages={"invalid_choice": CHOOSE_FROM_LIST_MESSAGE},
        ),
        required=False,
        max_length=len(VisaStatus),
    )
    country = _choice(Country.choices)
    intake = serializers.RegexField(
        INTAKE_PATTERN, required=False, error_messages={"invalid": INTAKE_MESSAGE}
    )
    counsellor = serializers.RegexField(
        COUNSELLOR_FILTER_PATTERN,
        required=False,
        error_messages={"invalid": COUNSELLOR_FILTER_MESSAGE},
    )
    appointment_from = serializers.DateField(
        input_formats=["%Y-%m-%d"], required=False, error_messages={"invalid": DAY_MESSAGE}
    )
    appointment_to = serializers.DateField(
        input_formats=["%Y-%m-%d"], required=False, error_messages={"invalid": DAY_MESSAGE}
    )
    has_appointment = _choice(["true", "false"])
    sort = _choice(VisaSort)
    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """The appointment range can't end before it starts."""
        start, end = attrs.get("appointment_from"), attrs.get("appointment_to")
        if start is not None and end is not None and end < start:
            raise serializers.ValidationError({"appointment_to": [DAY_ORDER_MESSAGE]})
        return attrs

    def to_filters(self) -> VisaFilters:
        """Return the validated filters and order."""
        data = self.validated_data
        country = data.get("country")
        has_appointment = data.get("has_appointment")
        return VisaFilters(
            statuses=frozenset(VisaStatus(value) for value in data.get("status", [])),
            country=None if country is None else Country(country),
            intake=data.get("intake", ""),
            counsellor=data.get("counsellor", ""),
            appointment_from=data.get("appointment_from"),
            appointment_to=data.get("appointment_to"),
            has_appointment=None if has_appointment is None else has_appointment == "true",
            sort=VisaSort(data.get("sort", VisaSort.UPCOMING)),
        )

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number
