"""What the Visit endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

from rest_framework import serializers

from config.serializers import StrictSerializer
from staff.serializers import PlainTextField
from students.serializers import StrictChoiceField
from visits.models import VisitReason

DETAILS_MAX_LENGTH = 200
CHOOSE_REASON_MESSAGE = "Choose why they are here."


class NewVisitSerializer(StrictSerializer[None]):
    """A returning Student's Visit: why they came, and optional details."""

    reason = StrictChoiceField(
        choices=VisitReason.choices,
        error_messages={
            "required": CHOOSE_REASON_MESSAGE,
            "null": CHOOSE_REASON_MESSAGE,
            "invalid_choice": CHOOSE_REASON_MESSAGE,
        },
    )
    details = PlainTextField(max_length=DETAILS_MAX_LENGTH, required=False, allow_blank=True)

    def reason_value(self) -> VisitReason:
        """Why they came."""
        return VisitReason(self.validated_data["reason"])

    def details_value(self) -> str:
        """The details, trimmed; empty when none were given."""
        details: str = self.validated_data.get("details", "")
        return details.strip()


class VisitPageQuerySerializer(StrictSerializer[None]):
    """Which page of a Student's Visits to show."""

    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number
