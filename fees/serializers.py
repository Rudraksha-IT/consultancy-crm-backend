"""What the Fee endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from config.serializers import StrictSerializer
from fee_types.serializers import FeeTypeField, NoteField, check_fee_type_and_note
from fees.types import NewFee
from payments.serializers import AmountField


class NewFeeSerializer(StrictSerializer[None]):
    """A Fee to add, or a Fee's new details (spec 76a): Fee Type and amount; a note for Other.

    The Fee Type must be offered (not hidden), unless an edit keeps the one the Fee has.
    """

    fee_type = FeeTypeField()
    note = NoteField()
    amount = AmountField()

    def __init__(self, *, data: object, kept_type_id: int | None = None) -> None:
        super().__init__(data=data)
        self.kept_type_id = kept_type_id

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Refuse a hidden Fee Type, and Other without a note."""
        check_fee_type_and_note(attrs, kept_type_id=self.kept_type_id)
        return attrs

    def to_new_fee(self) -> NewFee:
        """Return the validated Fee."""
        data = self.validated_data
        return NewFee(fee_type=data["fee_type"], note=data.get("note", ""), amount=data["amount"])


class NoQuerySerializer(StrictSerializer[None]):
    """A query string that must be empty: any field in it is unknown."""


class StudentFeesListQuerySerializer(StrictSerializer[None]):
    """The Student fees list's page (spec 76b)."""

    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number
