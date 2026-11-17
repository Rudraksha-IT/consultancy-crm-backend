"""What the Stage, assign and Transfer endpoints accept. The server never trusts the browser."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from config.serializers import StrictSerializer
from staff.models import Staff
from students.models import LOST_REASON_MAX_LENGTH, Stage
from students.serializers import CounsellorChoiceField, NoteField, StrictChoiceField

LOST_REASON_NEEDED_MESSAGE = "Say why we lost this Student."
ONLY_LOST_HAS_A_REASON_MESSAGE = "Only a Lost Student has a reason."
SAME_COUNSELLOR_MESSAGE = "They're already this Student's counsellor."


class StageChangeSerializer(StrictSerializer[None]):
    """The Stage to move to by hand; Lost needs the reason (spec 38-40).

    Which moves are allowed from where the Student is now is `students.stages`' job.
    """

    stage = StrictChoiceField(choices=Stage.choices)
    lost_reason = NoteField(max_length=LOST_REASON_MAX_LENGTH)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """A reason is needed for, and only for, Lost."""
        reason = attrs.get("lost_reason", "")
        is_lost = attrs["stage"] == Stage.LOST
        if is_lost and not reason:
            raise serializers.ValidationError({"lost_reason": [LOST_REASON_NEEDED_MESSAGE]})
        if reason and not is_lost:
            raise serializers.ValidationError({"lost_reason": [ONLY_LOST_HAS_A_REASON_MESSAGE]})
        return attrs

    def stage_value(self) -> Stage:
        """The Stage asked for."""
        return Stage(self.validated_data["stage"])

    def reason(self) -> str:
        """Why the Student was lost; empty for any other Stage."""
        reason: str = self.validated_data.get("lost_reason", "")
        return reason


class CounsellorPickSerializer(StrictSerializer[None]):
    """The counsellor to assign or Transfer to: an active Counsellor or Admin (spec 22b)."""

    counsellor = CounsellorChoiceField(required=True)

    def counsellor_value(self) -> Staff:
        """The counsellor picked."""
        counsellor: Staff = self.validated_data["counsellor"]
        return counsellor
