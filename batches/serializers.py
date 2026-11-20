"""What the Batch endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from uuid import UUID

from django.utils import timezone
from rest_framework import serializers

from batches.models import NAME_MAX_LENGTH, TIME_SLOT_MAX_LENGTH, Mark, TestType
from batches.services import active_teachers
from batches.types import BatchDetails
from config.serializers import StrictSerializer, parse_record_id
from staff.models import Staff
from staff.serializers import PlainTextField
from students.models import Student
from students.serializers import StrictChoiceField

NAME_MESSAGE = "Give the class a name."
TEST_TYPE_MESSAGE = "Choose a test type from the list."
TEACHER_MESSAGE = "Choose a teacher from the list."
TIME_SLOT_MESSAGE = "Say when the class meets, e.g. 7:00-8:30 AM."
# How far from today a class can start or end; anything further is surely a typing slip.
YEARS_AWAY = 5
STARTS_ON_MESSAGE = f"Enter a start date within {YEARS_AWAY} years of today."
ENDS_ON_MESSAGE = f"Enter an end date within {YEARS_AWAY} years of today."
ENDS_BEFORE_STARTS_MESSAGE = "It can't end before it starts."
STUDENT_MESSAGE = "Choose a Student from the list."
ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
LARGEST_ID = 2**63 - 1
_ALL_MESSAGES = ("required", "null", "blank", "invalid", "invalid_choice")


def _same_message(message: str) -> dict[str, Any]:
    return dict.fromkeys(_ALL_MESSAGES, message)


def _length_message(limit: int) -> str:
    return f"Use at most {limit} characters."


def _an_id(data: object) -> int | None:
    """`data` as a database id, or None when it can't be one (text, decimals, booleans...)."""
    if isinstance(data, bool) or not isinstance(data, int) or not 1 <= data <= LARGEST_ID:
        return None
    return data


def years_from(today: date, years: int) -> date:
    """The same day `years` years from `today` (28 February for a leap day with no match)."""
    try:
        return today.replace(year=today.year + years)
    except ValueError:
        # silent: 29 February in a year without one becomes the 28th
        return today.replace(year=today.year + years, day=28)


class OneLineField(PlainTextField):
    """One line of text up to `max_length`; anything but text gets the same message as none."""

    def __init__(self, message: str, max_length: int) -> None:
        super().__init__(
            max_length=max_length,
            error_messages={**_same_message(message), "max_length": _length_message(max_length)},
        )

    def to_internal_value(self, data: object) -> str:
        """Return the trimmed text."""
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class BatchDateField(serializers.Field[date, str, str, Any]):
    """A day, "YYYY-MM-DD" (AD), within `YEARS_AWAY` years of today either way."""

    def __init__(self, message: str) -> None:
        super().__init__(error_messages=_same_message(message))

    def to_internal_value(self, data: object) -> date:
        """Return the date."""
        if not isinstance(data, str) or not ISO_DATE.match(data):
            self.fail("invalid")
        try:
            value = date.fromisoformat(data)
        except ValueError:
            # self.fail raises the field's validation error
            self.fail("invalid")
        today = timezone.localdate()
        if not years_from(today, -YEARS_AWAY) <= value <= years_from(today, YEARS_AWAY):
            self.fail("invalid")
        return value

    def to_representation(self, value: date) -> str:
        """Return the date as text (unused: answers are built by hand)."""
        return value.isoformat()


class TeacherField(serializers.Field[Staff, int, int, Any]):
    """Who teaches the class: an active Teacher, by id."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(TEACHER_MESSAGE))

    def to_internal_value(self, data: object) -> Staff:
        """Return the Teacher; anyone else, invited, gone or unknown is refused."""
        pk = _an_id(data)
        teacher = None if pk is None else active_teachers().filter(pk=pk).first()
        if teacher is None:
            self.fail("invalid")
        return teacher

    def to_representation(self, value: Staff) -> int:
        """Return the id (unused: answers are built by hand)."""
        return value.pk


class BatchSerializer(StrictSerializer[None]):
    """A whole class, to create or to replace (spec 83). Its Students never come from here."""

    name = OneLineField(NAME_MESSAGE, NAME_MAX_LENGTH)
    test_type = StrictChoiceField(
        choices=TestType.choices, error_messages=_same_message(TEST_TYPE_MESSAGE)
    )
    teacher = TeacherField()
    time_slot = OneLineField(TIME_SLOT_MESSAGE, TIME_SLOT_MAX_LENGTH)
    starts_on = BatchDateField(STARTS_ON_MESSAGE)
    ends_on = BatchDateField(ENDS_ON_MESSAGE)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Refuse a class that ends before it starts."""
        if attrs["ends_on"] < attrs["starts_on"]:
            raise serializers.ValidationError({"ends_on": [ENDS_BEFORE_STARTS_MESSAGE]})
        return attrs

    def to_details(self) -> BatchDetails:
        """Return the validated class."""
        data = self.validated_data
        return BatchDetails(
            name=data["name"],
            test_type=data["test_type"],
            teacher=data["teacher"],
            time_slot=data["time_slot"],
            starts_on=data["starts_on"],
            ends_on=data["ends_on"],
        )


class StudentChoiceField(serializers.Field[Student, str, str, Any]):
    """A Student, by UUID (ADR 0027)."""

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


class NewMemberSerializer(StrictSerializer[None]):
    """The Student to add to a class (spec 84)."""

    student = StudentChoiceField()

    def student_to_add(self) -> Student:
        """The validated Student."""
        student: Student = self.validated_data["student"]
        return student


MARKS_MESSAGE = "Mark each Student present, absent, late or leave."
MARKED_TWICE_MESSAGE = "Mark only Students in this class, once each."
# More than any class holds; a longer list is refused before anything is read from it.
MAX_MARKS = 500
TOO_MANY_MARKS_MESSAGE = f"Send at most {MAX_MARKS} marks at once."
_MARK_KEYS = frozenset({"student", "mark"})


class MarksField(serializers.Field[dict[UUID, Mark], list[object], list[object], Any]):
    """A class day's marks: a list of {"student": UUID, "mark": a `Mark`}, each Student once."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(MARKS_MESSAGE))

    def to_internal_value(self, data: object) -> dict[UUID, Mark]:
        """Return each Student UUID's mark; anything else, or a Student twice, is refused."""
        if not isinstance(data, list):
            self.fail("invalid")
        if len(data) > MAX_MARKS:
            raise serializers.ValidationError(TOO_MANY_MARKS_MESSAGE)
        marks = dict(self._one(item) for item in data)
        if len(marks) != len(data):
            raise serializers.ValidationError(MARKED_TWICE_MESSAGE)
        return marks

    def _one(self, item: object) -> tuple[UUID, Mark]:
        if not isinstance(item, dict) or item.keys() != _MARK_KEYS:
            self.fail("invalid")
        student, mark = parse_record_id(item["student"]), item["mark"]
        if student is None or not isinstance(mark, str) or mark not in Mark.values:
            self.fail("invalid")
        return student, Mark(mark)

    def to_representation(self, value: dict[UUID, Mark]) -> list[object]:
        """Return the marks as sent (unused: answers are built by hand)."""
        return [{"student": str(one), "mark": mark.value} for one, mark in value.items()]


class AttendanceSerializer(StrictSerializer[None]):
    """A class day's Attendance, to replace that day's marks (spec 85)."""

    marks = MarksField()

    def to_marks(self) -> dict[UUID, Mark]:
        """The validated marks, by Student UUID."""
        marks: dict[UUID, Mark] = self.validated_data["marks"]
        return marks
