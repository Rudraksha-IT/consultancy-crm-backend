"""What the Email endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from communication.models import SUBJECT_MAX_LENGTH
from communication.types import EmailText
from config.serializers import StrictCharField, StrictSerializer
from staff.serializers import CONTROL_CHARACTERS, CONTROL_CHARACTERS_MESSAGE
from students.serializers import (
    NOTE_CHARACTERS_MESSAGE,
    NOTE_CONTROL_CHARACTERS,
    SEARCH_MAX_LENGTH,
    StudentListQuerySerializer,
)

BODY_MAX_LENGTH = 10_000
LARGEST_ID = 2**63 - 1

SUBJECT_MESSAGE = "Write a subject line."
SUBJECT_TOO_LONG_MESSAGE = f"A subject line can be at most {SUBJECT_MAX_LENGTH} characters."
BODY_MESSAGE = "Write a message."
BODY_TOO_LONG_MESSAGE = f"A message can be at most {BODY_MAX_LENGTH} characters."
CHOOSE_SOMEONE_MESSAGE = "Choose at least one Student."
CHOOSE_FROM_LIST_MESSAGE = "Choose Students from the list."
BATCH_MESSAGE = "Choose a class."
_ALL_MESSAGES = ("required", "null", "blank", "invalid", "max_length")


def _same_message(message: str) -> dict[str, Any]:
    return dict.fromkeys(_ALL_MESSAGES, message)


class SubjectField(StrictCharField):
    """A subject line: one line of text, so it can never add a header."""

    def __init__(self) -> None:
        super().__init__(
            max_length=SUBJECT_MAX_LENGTH,
            error_messages={
                **_same_message(SUBJECT_MESSAGE),
                "max_length": SUBJECT_TOO_LONG_MESSAGE,
            },
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse line breaks and other control characters."""
        value = super().to_internal_value(data)
        if CONTROL_CHARACTERS.search(value):
            raise serializers.ValidationError(CONTROL_CHARACTERS_MESSAGE)
        return value


class MessageBodyField(StrictCharField):
    """A message's text: several lines, nothing else."""

    def __init__(self) -> None:
        super().__init__(
            max_length=BODY_MAX_LENGTH,
            error_messages={**_same_message(BODY_MESSAGE), "max_length": BODY_TOO_LONG_MESSAGE},
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse control characters other than line breaks and tabs."""
        value = super().to_internal_value(data)
        if NOTE_CONTROL_CHARACTERS.search(value):
            raise serializers.ValidationError(NOTE_CHARACTERS_MESSAGE)
        return value


class NewEmailSerializer(StrictSerializer[None]):
    """An email to one Student: the subject line and message Staff wrote."""

    subject = SubjectField()
    body = MessageBodyField()

    def email_text(self) -> EmailText:
        """The trimmed subject line and message."""
        return EmailText(self.validated_data["subject"], self.validated_data["body"])


class StudentIdsField(serializers.Field[list[int], list[int], list[int], Any]):
    """The ids of the Students chosen: whole numbers, each once, in the order given.

    Whether they exist, and how many may be chosen, is the view's to check.
    """

    def __init__(self) -> None:
        super().__init__(
            error_messages={
                "required": CHOOSE_SOMEONE_MESSAGE,
                "null": CHOOSE_FROM_LIST_MESSAGE,
                "invalid": CHOOSE_FROM_LIST_MESSAGE,
            }
        )

    def to_internal_value(self, data: object) -> list[int]:
        """Return the ids, each once; anything but a list of ids is refused."""
        if not isinstance(data, list):
            self.fail("invalid")
        for value in data:
            if isinstance(value, bool) or not isinstance(value, int):
                self.fail("invalid")
            if not 1 <= value <= LARGEST_ID:
                self.fail("invalid")
        return list(dict.fromkeys(int(value) for value in data))

    def to_representation(self, value: list[int]) -> list[int]:
        """The ids as they are."""
        return value


class BulkEmailSerializer(NewEmailSerializer):
    """One email to the Students chosen, each their own copy."""

    students = StudentIdsField()

    def student_ids(self) -> list[int]:
        """The ids chosen, each once."""
        ids: list[int] = self.validated_data["students"]
        return ids


class StudentListFiltersSerializer(StudentListQuerySerializer):
    """The Student list's search and filters (spec 31), without a page."""

    q = StrictCharField(max_length=SEARCH_MAX_LENGTH, required=False, allow_blank=True)

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """The list's fields but its page, which is refused as unknown."""
        fields = super().get_fields()
        del fields["page"]
        return fields


class PreTickQuerySerializer(StudentListFiltersSerializer):
    """From the query string: a class (`batch`) or a Student list's filters, to pre-tick."""

    batch = serializers.IntegerField(
        min_value=1,
        max_value=LARGEST_ID,
        required=False,
        error_messages=_same_message(BATCH_MESSAGE),
    )

    def batch_id(self) -> int | None:
        """The class chosen, if any."""
        batch: int | None = self.validated_data.get("batch")
        return batch

    def has_student_filters(self) -> bool:
        """Whether any of the Student list's filters were given."""
        return any(key != "batch" for key in self.validated_data)
