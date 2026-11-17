"""What the Catalogue endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from django.utils import timezone
from rest_framework import serializers

from catalogue.csv_import import EMPTY_FILE_MESSAGE
from catalogue.intakes import first_gone_by, gone_by_message
from catalogue.models import (
    COURSE_NAME_MAX_LENGTH,
    MAX_INTAKES_PER_COURSE,
    UNIVERSITY_NAME_MAX_LENGTH,
    Course,
    CourseLevel,
)
from catalogue.types import CourseChange, NewCourse, UniversityChange
from config.serializers import StrictCharField, StrictSerializer
from documents.serializers import DocumentTypeListField
from modules.serializers import StrictBooleanField
from staff.serializers import PlainTextField
from students.models import INTAKE_LENGTH, Country
from students.profile_serializers import INTAKE_MESSAGE, INTAKE_PATTERN
from students.serializers import SEARCH_MAX_LENGTH, StrictChoiceField

ENTER_UNIVERSITY_NAME_MESSAGE = "Enter the university's name."
ENTER_COURSE_NAME_MESSAGE = "Enter the course's name."
ADD_AN_INTAKE_MESSAGE = "Add at least one intake."
TOO_MANY_INTAKES_MESSAGE = f"A course can have at most {MAX_INTAKES_PER_COURSE} intakes."
TRUE_OR_FALSE = ("true", "false")
# 500 KB of text; the browser checks the file's size before sending it.
CSV_MAX_CHARACTERS = 500_000
CHOOSE_A_FILE_MESSAGE = "Choose a CSV file."
FILE_TOO_BIG_MESSAGE = "The file is too big. Import at most 500 KB at a time."


class IntakeField(StrictCharField):
    """One Intake as "YYYY-MM" (AD), exactly: no spaces or other characters around it."""

    def __init__(self) -> None:
        super().__init__(max_length=INTAKE_LENGTH, trim_whitespace=False)

    def to_internal_value(self, data: object) -> str:
        """Refuse anything that isn't a month and year from 2000 on."""
        value = super().to_internal_value(data)
        if not INTAKE_PATTERN.match(value):
            raise serializers.ValidationError(INTAKE_MESSAGE)
        return value


def refuse_gone_by(intakes: list[str], kept: Collection[str]) -> list[str]:
    """Refuse a new Intake in a month already gone; those the Course has stay (issue 92)."""
    gone = first_gone_by(intakes, kept, timezone.localdate())
    if gone is not None:
        raise serializers.ValidationError(gone_by_message(gone))
    return intakes


def university_name_field(*, required: bool) -> PlainTextField:
    """A University's name: plain text, at most 200 characters."""
    message = ENTER_UNIVERSITY_NAME_MESSAGE
    return PlainTextField(
        max_length=UNIVERSITY_NAME_MAX_LENGTH,
        required=required,
        error_messages={"blank": message, "required": message, "null": message},
    )


def course_name_field(*, required: bool) -> PlainTextField:
    """A Course's name: plain text, at most 200 characters."""
    message = ENTER_COURSE_NAME_MESSAGE
    return PlainTextField(
        max_length=COURSE_NAME_MAX_LENGTH,
        required=required,
        error_messages={"blank": message, "required": message, "null": message},
    )


def intakes_field(*, required: bool, allow_empty: bool = False) -> serializers.ListField:
    """A Course's Intakes: at most 24 "YYYY-MM"; empty only when `allow_empty`."""
    return serializers.ListField(
        child=IntakeField(),
        required=required,
        allow_empty=allow_empty,
        max_length=MAX_INTAKES_PER_COURSE,
        error_messages={"empty": ADD_AN_INTAKE_MESSAGE, "max_length": TOO_MANY_INTAKES_MESSAGE},
    )


class NewUniversitySerializer(StrictSerializer[None]):
    """Add a University (spec 46). It starts not archived, so `archived` can't be sent."""

    name = university_name_field(required=True)
    country = StrictChoiceField(choices=Country.choices)

    def to_values(self) -> tuple[str, Country]:
        """Return the validated name and country."""
        data = self.validated_data
        return data["name"], Country(data["country"])


class UniversityChangeSerializer(StrictSerializer[None]):
    """Rename, move or archive a University (spec 50); only the fields sent change."""

    name = university_name_field(required=False)
    country = StrictChoiceField(choices=Country.choices, required=False)
    archived = StrictBooleanField(required=False)

    def to_change(self) -> UniversityChange:
        """Return the validated change."""
        data = self.validated_data
        country = data.get("country")
        return UniversityChange(
            name=data.get("name"),
            country=None if country is None else Country(country),
            archived=data.get("archived"),
        )


class NewCourseSerializer(StrictSerializer[None]):
    """Add a Course with its Intakes to a University (spec 47)."""

    name = course_name_field(required=True)
    level = StrictChoiceField(choices=CourseLevel.choices)
    intakes = intakes_field(required=True)
    # The Required documents ticked in the same dialog (issue 92); none when left out.
    documents = DocumentTypeListField(required=False)

    def validate_intakes(self, value: list[str]) -> list[str]:
        """No month already gone."""
        return refuse_gone_by(value, ())

    def to_new_course(self) -> NewCourse:
        """Return the validated Course."""
        data = self.validated_data
        return NewCourse(
            name=data["name"],
            level=CourseLevel(data["level"]),
            intakes=tuple(data["intakes"]),
            documents=tuple(data.get("documents", ())),
        )


class CourseChangeSerializer(StrictSerializer[None]):
    """Change or archive a Course (spec 50); only the fields sent change."""

    name = course_name_field(required=False)
    level = StrictChoiceField(choices=CourseLevel.choices, required=False)
    intakes = intakes_field(required=False)
    archived = StrictBooleanField(required=False)

    def __init__(self, course: Course, data: object) -> None:
        super().__init__(data=data)
        self.course = course

    def validate_intakes(self, value: list[str]) -> list[str]:
        """No new month already gone; Intakes the Course has may stay."""
        kept = {intake.month for intake in self.course.intakes.all()}
        return refuse_gone_by(value, kept)

    def to_change(self) -> CourseChange:
        """Return the validated change."""
        data = self.validated_data
        level = data.get("level")
        intakes = data.get("intakes")
        return CourseChange(
            name=data.get("name"),
            level=None if level is None else CourseLevel(level),
            intakes=None if intakes is None else tuple(intakes),
            archived=data.get("archived"),
        )


class CatalogueQuerySerializer(StrictSerializer[None]):
    """The Catalogue list's search, archived switch and page, from the query string."""

    q = serializers.CharField(max_length=SEARCH_MAX_LENGTH, required=False, allow_blank=True)
    include_archived = StrictChoiceField(choices=TRUE_OR_FALSE, required=False)
    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def query(self) -> str:
        """The search text; empty for none."""
        text: str = self.validated_data.get("q", "")
        return text

    def includes_archived(self) -> bool:
        """Whether archived Universities and Courses are shown too."""
        value: Any = self.validated_data.get("include_archived", "false")
        return bool(value == "true")

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number


class CatalogueIntakesQuerySerializer(StrictSerializer[None]):
    """The upcoming Intakes take no query: anything sent is refused."""


class CatalogueImportSerializer(StrictSerializer[None]):
    """A CSV file's text, read by the browser (spec 48). Each row is checked by the import."""

    csv = StrictCharField(
        max_length=CSV_MAX_CHARACTERS,
        trim_whitespace=False,
        error_messages={
            "blank": EMPTY_FILE_MESSAGE,
            "required": CHOOSE_A_FILE_MESSAGE,
            "null": CHOOSE_A_FILE_MESSAGE,
            "max_length": FILE_TOO_BIG_MESSAGE,
        },
    )

    def text(self) -> str:
        """The file's text."""
        text: str = self.validated_data["csv"]
        return text
