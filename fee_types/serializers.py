"""What the Fee Type endpoints accept, and the Fee Type and note every Fee and Payment needs.

The server never trusts the browser (issues README).
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from config.serializers import StrictSerializer
from fee_types.models import FEE_TYPE_NAME_MAX_LENGTH, NOTE_MAX_LENGTH, FeeType
from fee_types.services import MAX_FEE_TYPES, FeeTypeChange
from modules.serializers import StrictBooleanField
from staff.serializers import PlainTextField

ENTER_NAME_MESSAGE = "Enter the Fee Type's name."
NAME_LENGTH_MESSAGE = f"Use at most {FEE_TYPE_NAME_MAX_LENGTH} characters."
NOTHING_TO_CHANGE_MESSAGE = "Send a new name, or whether it is hidden."
ORDER_MESSAGE = "Send every Fee Type once, in the new order."
CHOOSE_FEE_TYPE_MESSAGE = "Choose a Fee Type."
HIDDEN_FEE_TYPE_MESSAGE = "This Fee Type is hidden. Choose another."
OTHER_NEEDS_NOTE_MESSAGE = "Add a short note for Other."
NOTE_LENGTH_MESSAGE = f"Use at most {NOTE_MAX_LENGTH} characters."
NOTE_MESSAGE = "Write the note as one line of text."
# Larger numbers can't be an id.
MAX_ID = 2**31 - 1
_ALL_MESSAGES = ("required", "null", "blank", "invalid")


class FeeTypeNameField(PlainTextField):
    """A Fee Type's name: one line, up to `FEE_TYPE_NAME_MAX_LENGTH` characters."""

    def __init__(self, *, required: bool = True) -> None:
        super().__init__(
            required=required,
            max_length=FEE_TYPE_NAME_MAX_LENGTH,
            error_messages={
                **dict.fromkeys(_ALL_MESSAGES, ENTER_NAME_MESSAGE),
                "max_length": NAME_LENGTH_MESSAGE,
            },
        )


class NewFeeTypeSerializer(StrictSerializer[None]):
    """A Fee Type to add: its name only. It goes at the end and is offered at once."""

    name = FeeTypeNameField()

    def name_value(self) -> str:
        """The name, trimmed."""
        return str(self.validated_data["name"])


class FeeTypeChangeSerializer(StrictSerializer[None]):
    """A new name, whether it is hidden, or both."""

    name = FeeTypeNameField(required=False)
    hidden = StrictBooleanField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Refuse a change that changes nothing."""
        if not attrs:
            raise serializers.ValidationError({"detail": [NOTHING_TO_CHANGE_MESSAGE]})
        return attrs

    def change(self) -> FeeTypeChange:
        """The change asked for."""
        data = self.validated_data
        return FeeTypeChange(name=data.get("name"), hidden=data.get("hidden"))


def _whole_id(data: object) -> int | None:
    """A positive whole-number id, or None for text, decimals, true/false or anything too big."""
    if isinstance(data, bool) or not isinstance(data, int) or not 1 <= data <= MAX_ID:
        return None
    return data


class _OrderIdField(serializers.Field[int, int, int, Any]):
    """One Fee Type's id in the new order."""

    def __init__(self) -> None:
        super().__init__(error_messages=dict.fromkeys(_ALL_MESSAGES, ORDER_MESSAGE))

    def to_internal_value(self, data: object) -> int:
        """Return the id."""
        found = _whole_id(data)
        if found is None:
            self.fail("invalid")
        return found

    def to_representation(self, value: int) -> int:
        """Return the id (unused: answers are built by hand)."""
        return value


_ORDER_MESSAGES = (*_ALL_MESSAGES, "not_a_list", "empty", "max_length")


class FeeTypeOrderSerializer(StrictSerializer[None]):
    """Every Fee Type's id once, in the new order."""

    order = serializers.ListField(
        child=_OrderIdField(),
        allow_empty=False,
        max_length=MAX_FEE_TYPES,
        error_messages=dict.fromkeys(_ORDER_MESSAGES, ORDER_MESSAGE),
    )

    def order_value(self) -> list[int]:
        """The ids in the new order."""
        return list(self.validated_data["order"])


class FeeTypeField(serializers.Field[FeeType, int, int, Any]):
    """The Fee Type a Fee or Payment is for, by its id, in this Consultancy."""

    def __init__(self) -> None:
        super().__init__(error_messages=dict.fromkeys(_ALL_MESSAGES, CHOOSE_FEE_TYPE_MESSAGE))

    def to_internal_value(self, data: object) -> FeeType:
        """Return the Fee Type; hidden ones too (`check_fee_type_and_note` decides)."""
        found = _whole_id(data)
        if found is None:
            self.fail("invalid")
        try:
            return FeeType.objects.get(pk=found)
        except FeeType.DoesNotExist:
            # self.fail raises the field's validation error
            self.fail("invalid")

    def to_representation(self, value: FeeType) -> int:
        """Return the id (unused: answers are built by hand)."""
        return value.pk


class NoteField(PlainTextField):
    """A short note on a Fee or Payment: optional, needed for Other; one line."""

    def __init__(self) -> None:
        super().__init__(
            required=False,
            allow_blank=True,
            max_length=NOTE_MAX_LENGTH,
            error_messages={
                **dict.fromkeys(("null", "invalid"), NOTE_MESSAGE),
                "max_length": NOTE_LENGTH_MESSAGE,
            },
        )


def check_fee_type_and_note(attrs: dict[str, Any], *, kept_type_id: int | None) -> None:
    """Refuse a hidden Fee Type (unless the record already has it) and Other without a note.

    Raises:
        serializers.ValidationError: Keyed by the field to fix.
    """
    fee_type: FeeType = attrs["fee_type"]
    if fee_type.hidden and fee_type.pk != kept_type_id:
        raise serializers.ValidationError({"fee_type": [HIDDEN_FEE_TYPE_MESSAGE]})
    if fee_type.is_other and not attrs.get("note"):
        raise serializers.ValidationError({"note": [OTHER_NEEDS_NOTE_MESSAGE]})
