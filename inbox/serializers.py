"""What the Inbox accepts. The server never trusts the browser."""

from __future__ import annotations

from rest_framework import serializers

from config.serializers import StrictSerializer
from inbox.models import INBOX_MESSAGE_MAX_LENGTH
from students.serializers import NoteField

TEXT_NEEDED_MESSAGE = "Write a message."


class SendSerializer(StrictSerializer[None]):
    """A message: plain text of several lines, 1 to 2,000 characters after trimming."""

    text = NoteField(max_length=INBOX_MESSAGE_MAX_LENGTH)

    def validate_text(self, value: str) -> str:
        """Refuse an empty message, spaces only included."""
        if not value:
            raise serializers.ValidationError(TEXT_NEEDED_MESSAGE)
        return value

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """A missing message is refused like an empty one."""
        if "text" not in attrs:
            raise serializers.ValidationError({"text": [TEXT_NEEDED_MESSAGE]})
        return attrs

    def text_value(self) -> str:
        """The message as written."""
        text: str = self.validated_data["text"]
        return text
