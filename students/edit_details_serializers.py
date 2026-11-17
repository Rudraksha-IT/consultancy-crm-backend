"""What the Edit details page sends: every field and the three lists at once (issue 81).

The server never trusts the browser: each row is checked like the single-row endpoints check it,
and a row's id must be one of this Student's own rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from rest_framework import serializers

from students.edit_details import EditedDetails
from students.models import Student
from students.profile import MAX_ENTRIES_PER_STUDENT
from students.profile_serializers import (
    AcademicRecordSerializer,
    StrictIntegerField,
    StudentDetailsSerializer,
    _TestScoreFields,
    _WorkExperienceFields,
)

ROW_NOT_HERE_MESSAGE = "A row here isn't one of this Student's. Refresh the page."
LISTS = ("academic_records", "test_scores", "work_experience")


def _row_id() -> StrictIntegerField:
    """A stored row's id; left out for a new row."""
    return StrictIntegerField(required=False, min_value=1)


def _rows(row: serializers.Serializer[None]) -> serializers.ListSerializer[None]:
    """A whole list of rows, at most as many as a Student may have."""
    return serializers.ListSerializer(child=row, max_length=MAX_ENTRIES_PER_STUDENT)


class AcademicRecordRowSerializer(AcademicRecordSerializer):
    """One qualification on the page: a stored one (with its id) or a new one."""

    id = _row_id()


class TestScoreRowSerializer(_TestScoreFields):
    """One English test result on the page: a stored one (with its id) or a new one."""

    id = _row_id()


class WorkExperienceRowSerializer(_WorkExperienceFields):
    """One job on the page: a stored one (with its id) or a new one."""

    id = _row_id()


class EditDetailsSerializer(StudentDetailsSerializer):
    """Every detail the page shows, the version it was opened with, and each list whole.

    A stored row left out of its list is removed; a row without an id is added.
    """

    version = StrictIntegerField(min_value=1)
    academic_records = _rows(AcademicRecordRowSerializer())
    test_scores = _rows(TestScoreRowSerializer())
    work_experience = _rows(WorkExperienceRowSerializer())

    def __init__(self, student: Student, data: object) -> None:
        super().__init__(student, data)
        # Everything the page has is sent; only the details may be left out.
        self.partial = False

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """The details stay optional, as in a partial change; the rest is needed."""
        fields = super().get_fields()
        for name, field in fields.items():
            if name not in {"version", *LISTS}:
                field.required = False
        return fields

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Each row id is one of this Student's rows, sent once."""
        attrs = super().validate(attrs)
        stored = {
            "academic_records": self.student.academic_records.all(),
            "test_scores": self.student.test_scores.all(),
            "work_experience": self.student.work_experience.all(),
        }
        for name in LISTS:
            if not _ids_are_own(attrs[name], (row.pk for row in stored[name])):
                raise serializers.ValidationError({name: [ROW_NOT_HERE_MESSAGE]})
        return attrs

    def edited(self) -> EditedDetails:
        """The checked save, for `save_details`."""
        data = dict(self.validated_data)
        lists = {name: data.pop(name) for name in LISTS}
        return EditedDetails(
            version=data.pop("version"),
            details=data,
            academic_records=lists["academic_records"],
            test_scores=lists["test_scores"],
            work_experience=lists["work_experience"],
        )


def _ids_are_own(rows: Sequence[Mapping[str, object]], own: Iterable[int]) -> bool:
    sent = [row["id"] for row in rows if "id" in row]
    return len(set(sent)) == len(sent) and set(sent) <= set(own)
