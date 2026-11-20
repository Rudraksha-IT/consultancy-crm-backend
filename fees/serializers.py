"""What the Fee endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from config.serializers import StrictSerializer
from fee_types.serializers import FeeTypeField, NoteField, check_fee_type_and_note
from fees.types import NewFee
from payments.serializers import AmountField
from students.serializers import StrictChoiceField

PAGE_FIELD_LIMIT = 1_000_000
# What the Student fees list may leave out: Lost Students, as Fees by type does (spec 87, 91).
LEAVE_OUT_LOST = "lost"
# The two parts of one Fee Type's Students (spec 82, 89).
COUNTED, LOST = "counted", "lost"


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

    page = serializers.IntegerField(min_value=1, max_value=PAGE_FIELD_LIMIT, required=False)
    leave_out = StrictChoiceField(
        choices=(LEAVE_OUT_LOST,),
        required=False,
        error_messages={"invalid_choice": "Only Lost Students can be left out."},
    )

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number

    def leaves_out_lost(self) -> bool:
        """Whether Lost Students are left out (the Admin Home's figure and list)."""
        leave_out: str | None = self.validated_data.get("leave_out")
        return leave_out == LEAVE_OUT_LOST


class FeeTypeStudentsQuerySerializer(StrictSerializer[None]):
    """One Fee Type's Students: which part (counted or Lost) and which page."""

    part = StrictChoiceField(
        choices=(COUNTED, LOST),
        required=False,
        error_messages={"invalid_choice": "Choose the counted or the Lost Students."},
    )
    page = serializers.IntegerField(min_value=1, max_value=PAGE_FIELD_LIMIT, required=False)

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number

    def part_asked(self) -> str:
        """The part asked for; the counted Students if none."""
        part: str = self.validated_data.get("part", COUNTED)
        return part
