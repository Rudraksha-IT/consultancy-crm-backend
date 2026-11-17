"""What a University's edit page sends: the University and its Courses at once (issue 92).

The server never trusts the browser: each Course is checked like the single-Course endpoints
check it, a Course's id must be one of this University's, and a Course is deleted only once it
was saved as archived and no Application uses it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from django.db.models import Count
from rest_framework import serializers

from catalogue.models import Course, CourseLevel, University
from catalogue.serializers import (
    course_name_field,
    intakes_field,
    refuse_gone_by,
    university_name_field,
)
from catalogue.types import CourseEdit, UniversityEdit
from config.serializers import StrictSerializer
from documents.serializers import DocumentTypeListField
from modules.serializers import StrictBooleanField
from students.models import Country
from students.profile_serializers import StrictIntegerField
from students.serializers import StrictChoiceField

# More Courses than any University offers; keeps one save small.
MAX_COURSES_PER_SAVE = 200
NOT_THIS_UNIVERSITYS_MESSAGE = "A course here isn't one of this university's. Refresh the page."
NEEDS_AN_INTAKE_MESSAGE = "Add at least one intake, or archive the course."
COURSE_NAME_TAKEN_MESSAGE = "This university already has a course with this name."
UNIVERSITY_NAME_TAKEN_MESSAGE = "There's already a university with this name."
ARCHIVE_FIRST_MESSAGE = "Archive the course and save before deleting it."


def used_by_message(count: int, what: str) -> str:
    """Why something stays archived: "Used by 2 applications, so it stays archived."."""
    return f"Used by {count} {what}{'' if count == 1 else 's'}, so it stays archived."


class CourseEditRowSerializer(StrictSerializer[None]):
    """One Course on the edit page: a stored one (with its id) or a new one."""

    id = StrictIntegerField(required=False, min_value=1)
    name = course_name_field(required=True)
    level = StrictChoiceField(choices=CourseLevel.choices)
    # An archived Course may have none; an active one is checked by the page as a whole.
    intakes = intakes_field(required=True, allow_empty=True)
    archived = StrictBooleanField()
    documents = DocumentTypeListField()


class UniversityEditSerializer(StrictSerializer[None]):
    """The University's fields, every Course on the page, and the Courses to delete."""

    name = university_name_field(required=True)
    country = StrictChoiceField(choices=Country.choices)
    archived = StrictBooleanField()
    courses: serializers.ListSerializer[None] = serializers.ListSerializer(
        child=CourseEditRowSerializer(), max_length=MAX_COURSES_PER_SAVE
    )
    deleted_courses = serializers.ListField(
        child=StrictIntegerField(min_value=1), max_length=MAX_COURSES_PER_SAVE
    )

    def __init__(self, university: University, data: object) -> None:
        super().__init__(data=data)
        self.university = university
        self.stored = {
            course.pk: course
            for course in Course.objects.filter(university=university)
            .prefetch_related("intakes")
            .annotate(application_count=Count("applications"))
        }

    def validate_name(self, value: str) -> str:
        """No other University has this name, whatever its case."""
        others = University.objects.exclude(pk=self.university.pk)
        if others.filter(name__iexact=value).exists():
            raise serializers.ValidationError(UNIVERSITY_NAME_TAKEN_MESSAGE)
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Ids are this University's, each once; deletions are allowed; every row is sound."""
        rows: list[dict[str, Any]] = attrs["courses"]
        deleted: list[int] = attrs["deleted_courses"]
        sent = [row["id"] for row in rows if "id" in row]
        if len(set(sent)) != len(sent) or not set(sent) <= self.stored.keys():
            raise serializers.ValidationError({"courses": [NOT_THIS_UNIVERSITYS_MESSAGE]})
        self._check_deleted(deleted, set(sent))
        # Keyed by the row's place on the page, as DRF keys a list's own field errors.
        errors = {
            str(index): found
            for index, row in enumerate(rows)
            if (found := self._row_errors(index, row, rows, deleted))
        }
        if errors:
            raise serializers.ValidationError({"courses": errors})
        return attrs

    def edit(self) -> UniversityEdit:
        """The checked save, for `save_university_edit`."""
        data = self.validated_data
        return UniversityEdit(
            name=data["name"],
            country=Country(data["country"]),
            archived=data["archived"],
            courses=tuple(_course_edit(row) for row in data["courses"]),
            deleted_courses=tuple(data["deleted_courses"]),
        )

    def _check_deleted(self, deleted: Sequence[int], kept: set[int]) -> None:
        """Each deleted Course is this University's, once, saved as archived and unused."""
        own = set(deleted) <= self.stored.keys() and not set(deleted) & kept
        if len(set(deleted)) != len(deleted) or not own:
            raise serializers.ValidationError({"deleted_courses": [NOT_THIS_UNIVERSITYS_MESSAGE]})
        for course_id in deleted:
            course = self.stored[course_id]
            if not course.archived:
                raise serializers.ValidationError({"deleted_courses": [ARCHIVE_FIRST_MESSAGE]})
            uses: int = getattr(course, "application_count", 0)
            if uses:
                message = used_by_message(uses, "application")
                raise serializers.ValidationError({"deleted_courses": [message]})

    def _row_errors(
        self,
        index: int,
        row: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
        deleted: Sequence[int],
    ) -> dict[str, list[str]]:
        """What's wrong with one Course on the page, by field; empty when it's fine."""
        errors: dict[str, list[str]] = {}
        if self._name_taken(index, row, rows, deleted):
            errors["name"] = [COURSE_NAME_TAKEN_MESSAGE]
        intakes: list[str] = row["intakes"]
        if not intakes and not row["archived"]:
            errors["intakes"] = [NEEDS_AN_INTAKE_MESSAGE]
        stored = self.stored.get(row.get("id", 0))
        kept = set() if stored is None else {intake.month for intake in stored.intakes.all()}
        try:
            refuse_gone_by(intakes, kept)
        except serializers.ValidationError as error:
            errors["intakes"] = [str(detail) for detail in _details(error)]
        return errors

    def _name_taken(
        self,
        index: int,
        row: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
        deleted: Sequence[int],
    ) -> bool:
        """An earlier row, or a Course left out of the page, already has this name."""
        name = str(row["name"]).lower()
        earlier = {str(other["name"]).lower() for other in rows[:index]}
        on_page = {other["id"] for other in rows if "id" in other} | set(deleted)
        left_out = {c.name.lower() for pk, c in self.stored.items() if pk not in on_page}
        return name in earlier or name in left_out


def _details(error: serializers.ValidationError) -> list[object]:
    detail = error.detail
    return list(detail) if isinstance(detail, list) else [detail]


def _course_edit(row: Mapping[str, Any]) -> CourseEdit:
    return CourseEdit(
        id=row.get("id"),
        name=row["name"],
        level=CourseLevel(row["level"]),
        intakes=tuple(row["intakes"]),
        archived=row["archived"],
        documents=tuple(row["documents"]),
    )
