"""What the Transfer Request endpoints accept. The server never trusts the browser."""

from __future__ import annotations

from rest_framework import serializers

from config.serializers import StrictSerializer
from students.serializers import NoteField, StrictChoiceField
from transfer_requests.models import TRANSFER_REASON_MAX_LENGTH
from transfer_requests.services import Box

REASON_NEEDED_MESSAGE = "Say why you should take this Student."
BOX_MESSAGE = "Choose waiting_for_me, sent or all."


class AskSerializer(StrictSerializer[None]):
    """Why the asker should take the Student: required, several lines at most 500 characters."""

    reason = NoteField(max_length=TRANSFER_REASON_MAX_LENGTH)

    def validate_reason(self, value: str) -> str:
        """Refuse an empty reason, spaces only included."""
        if not value:
            raise serializers.ValidationError(REASON_NEEDED_MESSAGE)
        return value

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """A missing reason is refused like an empty one."""
        if "reason" not in attrs:
            raise serializers.ValidationError({"reason": [REASON_NEEDED_MESSAGE]})
        return attrs

    def reason_value(self) -> str:
        """The reason given."""
        reason: str = self.validated_data["reason"]
        return reason


class DeclineSerializer(StrictSerializer[None]):
    """An optional reason the asker will see."""

    reason = NoteField(max_length=TRANSFER_REASON_MAX_LENGTH)

    def reason_value(self) -> str:
        """The reason given, or empty."""
        reason: str = self.validated_data.get("reason", "")
        return reason


class EmptySerializer(StrictSerializer[None]):
    """Approve and cancel take nothing: who did it and when come from the server."""


class TransferRequestListQuerySerializer(StrictSerializer[None]):
    """Which box, and which page."""

    box = StrictChoiceField(
        choices=[(box.value, box.value) for box in Box],
        required=False,
        error_messages={"invalid_choice": BOX_MESSAGE},
    )
    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def box_value(self) -> Box:
        """The box asked for; "waiting_for_me" if none."""
        return Box(self.validated_data.get("box", Box.WAITING_FOR_ME))

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number
