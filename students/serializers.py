"""What the Student endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from rest_framework import serializers

from config.serializers import (
    RECORD_ID_PATTERN,
    StrictCharField,
    StrictSerializer,
    parse_record_id,
)
from staff.employees import list_counsellors
from staff.models import Staff
from staff.serializers import MobileField, PlainTextField
from students.models import (
    DETAIL_MAX_LENGTH,
    EMAIL_MAX_LENGTH,
    NOTE_MAX_LENGTH,
    STUDENT_NAME_MAX_LENGTH,
    Country,
    Priority,
    Qualification,
    Source,
    Stage,
    TargetLevel,
)
from students.types import COUNSELLOR_ME, COUNSELLOR_NONE, NewStudent, StudentFilters

SEARCH_MAX_LENGTH = 100
SUB_AGENT_NEEDED_MESSAGE = "Enter the Sub-agent's name."
SUB_AGENT_NOT_NEEDED_MESSAGE = "Only a Student who came through a Sub-agent has one."
CHOOSE_COUNSELLOR_MESSAGE = "Choose a counsellor from the list."
NOTE_CHARACTERS_MESSAGE = "Use letters, numbers, punctuation and line breaks only."
COUNSELLOR_FILTER_MESSAGE = 'Use "me", "none" or a Staff id.'
CHOOSE_FROM_LIST_MESSAGE = "Choose one from the list."
ENTER_NAME_MESSAGE = "Enter their full name."
ENTER_PHONE_MESSAGE = "Enter their mobile number."
# Line breaks and tabs are fine in a Note; no other control character is.
NOTE_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
COUNSELLOR_FILTER_PATTERN = re.compile(
    rf"^(?:{COUNSELLOR_ME}|{COUNSELLOR_NONE}|{RECORD_ID_PATTERN})\Z"
)


class StrictChoiceField(serializers.ChoiceField):
    """One value from a fixed list, sent as its exact text (DRF would match 1 to "1")."""

    def to_internal_value(self, data: object) -> Any:  # noqa: ANN401  # DRF's own signature
        """Refuse anything that isn't one of the choices' exact text."""
        if not isinstance(data, str):
            self.fail("invalid_choice", input=data)
        return super().to_internal_value(data)


class NoteField(StrictCharField):
    """The Student's Note: several lines of text, nothing else."""

    def __init__(self, *, required: bool = False, max_length: int = NOTE_MAX_LENGTH) -> None:
        super().__init__(max_length=max_length, required=required, allow_blank=True)

    def to_internal_value(self, data: object) -> str:
        """Refuse control characters other than line breaks and tabs."""
        value = super().to_internal_value(data)
        if NOTE_CONTROL_CHARACTERS.search(value):
            raise serializers.ValidationError(NOTE_CHARACTERS_MESSAGE)
        return value


class CounsellorChoiceField(serializers.Field[Staff | None, str | None, str | None, Any]):
    """An Assigned Counsellor picked by UUID from the active Counsellors and Admins (ADR 0027).

    Optional and nullable unless `required`.
    """

    def __init__(self, *, required: bool = False) -> None:
        super().__init__(required=required, allow_null=not required)

    def to_internal_value(self, data: object) -> Staff | None:
        """Return the Counsellor; anything but the UUID of someone in the list is refused."""
        public_id = parse_record_id(data)
        if public_id is None:
            raise serializers.ValidationError(CHOOSE_COUNSELLOR_MESSAGE)
        counsellor = list_counsellors().filter(public_id=public_id).first()
        if counsellor is None:
            raise serializers.ValidationError(CHOOSE_COUNSELLOR_MESSAGE)
        return counsellor

    def to_representation(self, value: Staff | None) -> str | None:
        """Return the UUID (unused: answers are built by the views)."""
        return None if value is None else str(value.public_id)


def _optional_text() -> PlainTextField:
    return PlainTextField(max_length=DETAIL_MAX_LENGTH, required=False, allow_blank=True)


class NewStudentSerializer(StrictSerializer[None]):
    """Add a new visitor (spec 23-26). The Stage is always Lead, so it can't be sent."""

    name = PlainTextField(
        max_length=STUDENT_NAME_MAX_LENGTH,
        error_messages={"blank": ENTER_NAME_MESSAGE, "required": ENTER_NAME_MESSAGE},
    )
    phone = MobileField(
        error_messages={"blank": ENTER_PHONE_MESSAGE, "required": ENTER_PHONE_MESSAGE}
    )
    email = serializers.EmailField(max_length=EMAIL_MAX_LENGTH, required=False, allow_blank=True)
    country = StrictChoiceField(choices=Country.choices)
    sub_agent_name = _optional_text()
    highest_qualification = StrictChoiceField(choices=Qualification.choices)
    past_degree = _optional_text()
    previous_job = _optional_text()
    note = NoteField()
    assigned_counsellor = CounsellorChoiceField()

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """Add `source` here: as a class attribute it would hide DRF's own `Field.source`."""
        return {**super().get_fields(), "source": StrictChoiceField(choices=Source.choices)}

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """A Sub-agent name is needed for, and only for, a Sub-agent Source."""
        has_name = bool(attrs.get("sub_agent_name"))
        is_sub_agent = attrs["source"] == Source.SUB_AGENT
        if is_sub_agent and not has_name:
            raise serializers.ValidationError({"sub_agent_name": [SUB_AGENT_NEEDED_MESSAGE]})
        if has_name and not is_sub_agent:
            raise serializers.ValidationError({"sub_agent_name": [SUB_AGENT_NOT_NEEDED_MESSAGE]})
        return attrs

    def to_new_student(self) -> NewStudent:
        """Return the validated details."""
        data = self.validated_data
        return NewStudent(
            name=data["name"],
            phone=data["phone"],
            email=data.get("email", ""),
            country=Country(data["country"]),
            source=Source(data["source"]),
            sub_agent_name=data.get("sub_agent_name", ""),
            highest_qualification=Qualification(data["highest_qualification"]),
            past_degree=data.get("past_degree", ""),
            previous_job=data.get("previous_job", ""),
            note=data.get("note", ""),
            assigned_counsellor=data.get("assigned_counsellor"),
        )


CHOOSE_PRIORITY_MESSAGE = "Choose High, Normal or Low."


def priority_field(*, required: bool = True) -> StrictChoiceField:
    """A Student's Priority (ticket 10): one of the three, exactly as written."""
    return StrictChoiceField(
        choices=Priority.choices,
        required=required,
        error_messages=dict.fromkeys(
            ("required", "null", "blank", "invalid", "invalid_choice"), CHOOSE_PRIORITY_MESSAGE
        ),
    )


class StudentListQuerySerializer(StrictSerializer[None]):
    """The Student list's search, filters and page, from the query string."""

    q = serializers.CharField(max_length=SEARCH_MAX_LENGTH, required=False, allow_blank=True)
    stage = StrictChoiceField(choices=Stage.choices, required=False)
    # Only Students with this Priority (ticket 11).
    priority = priority_field(required=False)
    # Leave out Lost Students, as Home's "High priority Students" does (ticket 12).
    # Only "true" is taken; a missing value means Lost Students are listed too.
    hide_lost = StrictChoiceField(choices=[("true", "true")], required=False)
    # "name": A to Z by name (the email picker, list tidy-ups 34); otherwise newest first.
    order = StrictChoiceField(choices=[("name", "name")], required=False)
    counsellor = serializers.RegexField(
        COUNSELLOR_FILTER_PATTERN,
        required=False,
        error_messages={"invalid": COUNSELLOR_FILTER_MESSAGE},
    )
    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)
    country = StrictChoiceField(
        choices=Country.choices,
        required=False,
        error_messages={"invalid_choice": CHOOSE_FROM_LIST_MESSAGE},
    )
    target_level = StrictChoiceField(
        choices=TargetLevel.choices,
        required=False,
        error_messages={"invalid_choice": CHOOSE_FROM_LIST_MESSAGE},
    )

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """Add `source` here: as a class attribute it would hide DRF's own `Field.source`."""
        source = StrictChoiceField(choices=Source.choices, required=False)
        return {**super().get_fields(), "source": source}

    def to_filters(self) -> StudentFilters:
        """Return the validated search and filters."""
        return filters_from(self.validated_data)

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number


def filters_from(data: Mapping[str, Any]) -> StudentFilters:
    """The Student list's search and filters, from their validated values."""
    stage = data.get("stage")
    source = data.get("source")
    country = data.get("country")
    target_level = data.get("target_level")
    priority = data.get("priority")
    return StudentFilters(
        query=data.get("q", ""),
        stage=None if stage is None else Stage(stage),
        source=None if source is None else Source(source),
        counsellor=data.get("counsellor", ""),
        country=None if country is None else Country(country),
        target_level=None if target_level is None else TargetLevel(target_level),
        priority=None if priority is None else Priority(priority),
        hide_lost=data.get("hide_lost") == "true",
        by_name=data.get("order") == "name",
    )
