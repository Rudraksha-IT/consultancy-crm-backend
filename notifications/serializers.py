"""What the notification endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from config.serializers import StrictSerializer

READ_MESSAGE = "Say whether it's read."
_ALL_MESSAGES = ("required", "null", "blank", "invalid")


class ReadField(serializers.Field[bool, bool, bool, Any]):
    """True or false, as JSON (never "true", 1 or null)."""

    def __init__(self) -> None:
        super().__init__(error_messages=dict.fromkeys(_ALL_MESSAGES, READ_MESSAGE))

    def to_internal_value(self, data: object) -> bool:
        """Return the flag."""
        if not isinstance(data, bool):
            self.fail("invalid")
        return data

    def to_representation(self, value: bool) -> bool:
        """Return the flag (unused: answers are built by hand)."""
        return value


class ReadSerializer(StrictSerializer[None]):
    """Mark one notification read or unread. Nothing else about it can change."""

    read = ReadField()

    def is_read(self) -> bool:
        """Whether it should now be read."""
        read: bool = self.validated_data["read"]
        return read


class ReadAllSerializer(StrictSerializer[None]):
    """Mark every notification read: the body is an empty object."""
