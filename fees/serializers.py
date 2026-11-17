"""What the Fee endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

from rest_framework import serializers

from config.serializers import StrictSerializer
from fees.models import PURPOSE_MAX_LENGTH
from fees.types import NewFee
from payments.serializers import AmountField
from staff.serializers import PlainTextField

PURPOSE_MESSAGE = "Say what the fee is for."
PURPOSE_LENGTH_MESSAGE = f"Use at most {PURPOSE_MAX_LENGTH} characters."
_PURPOSE_MESSAGES = ("required", "null", "blank", "invalid")


class FeePurposeField(PlainTextField):
    """What the Fee is for: one line of text, up to `PURPOSE_MAX_LENGTH` characters."""

    def __init__(self) -> None:
        super().__init__(
            max_length=PURPOSE_MAX_LENGTH,
            error_messages={
                **dict.fromkeys(_PURPOSE_MESSAGES, PURPOSE_MESSAGE),
                "max_length": PURPOSE_LENGTH_MESSAGE,
            },
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse anything that isn't text with the same message as a missing purpose."""
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class NewFeeSerializer(StrictSerializer[None]):
    """A Fee to add, or a Fee's new details (spec 76a). Both fields are always needed."""

    purpose = FeePurposeField()
    amount = AmountField()

    def to_new_fee(self) -> NewFee:
        """Return the validated Fee."""
        data = self.validated_data
        return NewFee(purpose=data["purpose"], amount=data["amount"])


class NoQuerySerializer(StrictSerializer[None]):
    """A query string that must be empty: any field in it is unknown."""


class StudentFeesListQuerySerializer(StrictSerializer[None]):
    """The Student fees list's page (spec 76b)."""

    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number
