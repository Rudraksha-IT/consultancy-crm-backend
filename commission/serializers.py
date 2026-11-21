"""What the Commission endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from catalogue.models import University
from commission.models import CommissionStatus
from commission.types import NewCommission, ReceivedPeriod
from config.serializers import StrictSerializer, parse_record_id
from payments.serializers import DATE_ORDER_MESSAGE, AmountField, FilterDateField
from students.models import Student
from students.profile_serializers import INTAKE_MESSAGE, INTAKE_PATTERN
from students.serializers import StrictChoiceField

STUDENT_MESSAGE = "Choose a Student."
UNIVERSITY_MESSAGE = "Choose a University from the Catalogue."
STATUS_MESSAGE = "Choose a commission status."
_ALL_MESSAGES = ("required", "null", "blank", "invalid", "invalid_choice")


def _same_message(message: str) -> dict[str, Any]:
    return dict.fromkeys(_ALL_MESSAGES, message)


class StudentChoiceField(serializers.Field[Student, str, str, Any]):
    """Any Student of this Consultancy, by UUID (ADR 0027)."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(STUDENT_MESSAGE))

    def to_internal_value(self, data: object) -> Student:
        """Return the Student; anything but a known Student's id is refused."""
        public_id = parse_record_id(data)
        student = None if public_id is None else Student.objects.filter(public_id=public_id).first()
        if student is None:
            self.fail("invalid")
        return student

    def to_representation(self, value: Student) -> str:
        """Return the UUID (unused: answers are built by hand)."""
        return str(value.public_id)


class UniversityChoiceField(serializers.Field[University, str, str, Any]):
    """A University from the active Catalogue (spec 51), by UUID (ADR 0027)."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(UNIVERSITY_MESSAGE))

    def to_internal_value(self, data: object) -> University:
        """Return the University; archived or unknown ones are refused."""
        public_id = parse_record_id(data)
        university = (
            None
            if public_id is None
            else University.objects.filter(public_id=public_id, archived=False).first()
        )
        if university is None:
            self.fail("invalid")
        return university

    def to_representation(self, value: University) -> str:
        """Return the id (unused: answers are built by hand)."""
        return str(value.public_id)


class IntakeField(serializers.Field[str, str, str, Any]):
    """An Intake's month, "YYYY-MM" (AD), from 2000 on."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(INTAKE_MESSAGE))

    def to_internal_value(self, data: object) -> str:
        """Return the month."""
        if not isinstance(data, str) or not INTAKE_PATTERN.match(data):
            self.fail("invalid")
        return data

    def to_representation(self, value: str) -> str:
        """Return the month (unused: answers are built by hand)."""
        return value


class NewCommissionSerializer(StrictSerializer[None]):
    """Commission to add (spec 76d). It always starts Waiting for enrolment."""

    student = StudentChoiceField()
    university = UniversityChoiceField()
    intake = IntakeField()
    amount = AmountField()

    def to_new_commission(self) -> NewCommission:
        """Return the validated Commission."""
        data = self.validated_data
        return NewCommission(
            student=data["student"],
            university=data["university"],
            intake=data["intake"],
            amount=data["amount"],
        )


class StatusSerializer(StrictSerializer[None]):
    """A Commission's new status, and nothing else."""

    status = StrictChoiceField(
        choices=CommissionStatus.choices, error_messages=_same_message(STATUS_MESSAGE)
    )

    def new_status(self) -> CommissionStatus:
        """Return the validated status."""
        return CommissionStatus(self.validated_data["status"])


class CommissionListQuerySerializer(StrictSerializer[None]):
    """The Commission list's page."""

    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number


class ReceivedQuerySerializer(StrictSerializer[None]):
    """The days to add up Received Commission over (Money in; spec 76e)."""

    to = FilterDateField()

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """Add `from` here: it can't be a class attribute's name."""
        return {"from": FilterDateField(), **super().get_fields()}

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Refuse an end date before the start date."""
        received_from, received_to = attrs.get("from"), attrs.get("to")
        if received_from is not None and received_to is not None and received_to < received_from:
            raise serializers.ValidationError({"to": [DATE_ORDER_MESSAGE]})
        return attrs

    def to_period(self) -> ReceivedPeriod:
        """Return the validated days."""
        data = self.validated_data
        return ReceivedPeriod(received_from=data.get("from"), received_to=data.get("to"))
