"""What the Email endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from communication.attachments import MAX_ATTACHMENTS, AttachmentKind, Chosen
from communication.models import SUBJECT_MAX_LENGTH
from communication.ready_made import Kind
from communication.types import EmailText
from config.serializers import StrictCharField, StrictSerializer
from staff.serializers import CONTROL_CHARACTERS, CONTROL_CHARACTERS_MESSAGE
from students.serializers import (
    NOTE_CHARACTERS_MESSAGE,
    NOTE_CONTROL_CHARACTERS,
    SEARCH_MAX_LENGTH,
    StrictChoiceField,
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
KIND_MESSAGE = "Choose one of the ready-made emails."
CHOOSE_FILES_MESSAGE = "Choose files from the list."
TOO_MANY_FILES_MESSAGE = f"Attach at most {MAX_ATTACHMENTS} files."
_ATTACHMENT_KEYS = frozenset({"kind", "id"})
_ATTACHMENT_KINDS = frozenset(kind.value for kind in AttachmentKind)
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


def _is_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= LARGEST_ID


def _chosen(item: object) -> Chosen | None:
    """One file named `{"kind": …, "id": …}` and nothing else, or None when it isn't."""
    if not isinstance(item, dict) or set(item) != _ATTACHMENT_KEYS:
        return None
    kind, pk = item["kind"], item["id"]
    if not isinstance(kind, str) or kind not in _ATTACHMENT_KINDS or not _is_id(pk):
        return None
    return Chosen(AttachmentKind(kind), pk)


class AttachmentsField(serializers.Field[list[Chosen], list[Chosen], list[Chosen], Any]):
    """The files to attach, each named `{"kind": …, "id": …}`; nothing else is taken.

    Whether each file is in use, and may go with this email, is the service's to check.
    """

    def __init__(self) -> None:
        super().__init__(
            required=False,
            error_messages={"null": CHOOSE_FILES_MESSAGE, "invalid": CHOOSE_FILES_MESSAGE},
        )

    def get_default(self) -> list[Chosen]:
        """No files unless some are named."""
        return []

    def to_internal_value(self, data: object) -> list[Chosen]:
        """Return the files named, in order; anything but a short list of them is refused."""
        if not isinstance(data, list):
            self.fail("invalid")
        if len(data) > MAX_ATTACHMENTS:
            raise serializers.ValidationError(TOO_MANY_FILES_MESSAGE)
        chosen = [_chosen(item) for item in data]
        if None in chosen:
            self.fail("invalid")
        return [item for item in chosen if item is not None]

    def to_representation(self, value: list[Chosen]) -> list[Chosen]:
        """The files as they are."""
        return value


class NewEmailSerializer(StrictSerializer[None]):
    """An email to one Student: the subject line and message Staff wrote, and files to attach."""

    subject = SubjectField()
    body = MessageBodyField()
    attachments = AttachmentsField()

    def email_text(self) -> EmailText:
        """The trimmed subject line and message."""
        return EmailText(self.validated_data["subject"], self.validated_data["body"])

    def attachments_chosen(self) -> list[Chosen]:
        """The files named, in order (none when none were named)."""
        chosen: list[Chosen] = self.validated_data["attachments"]
        return chosen


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


class DraftQuerySerializer(StrictSerializer[None]):
    """A ready-made email to draft (`kind`), and the id of the record it is about, as typed.

    Whether the record is one of those to pick is the service's to check, with the message for
    that kind of record.
    """

    kind = StrictChoiceField(
        choices=[kind.value for kind in Kind],
        error_messages=_same_message(KIND_MESSAGE) | {"invalid_choice": KIND_MESSAGE},
    )
    record = StrictCharField(required=False, allow_blank=True, trim_whitespace=False)

    def kind_chosen(self) -> Kind:
        """The ready-made email asked for."""
        return Kind(self.validated_data["kind"])

    def record_typed(self) -> str | None:
        """The record's id as typed, or None when none was given."""
        record: str | None = self.validated_data.get("record")
        return record
