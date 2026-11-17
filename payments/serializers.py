"""What the Payment endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from django.utils import timezone
from rest_framework import serializers

from config.serializers import StrictSerializer
from payments.models import MAX_AMOUNT, PURPOSE_MAX_LENGTH, PaymentMethod
from payments.money import format_npr
from payments.types import NewPayment, PaymentFilters
from staff.serializers import PlainTextField
from students.serializers import StrictChoiceField

AMOUNT_MESSAGE = f"Enter a whole amount from NPR 1 to {format_npr(MAX_AMOUNT)}."
EARLIEST_PAID_ON = date(2000, 1, 1)
PAID_ON_MESSAGE = "Enter a date from 1 Jan 2000 to today."
METHOD_MESSAGE = "Choose how they paid."
PURPOSE_MESSAGE = "Say what the payment is for."
PURPOSE_LENGTH_MESSAGE = f"Use at most {PURPOSE_MAX_LENGTH} characters."
FILTER_DATE_MESSAGE = "Enter a date as YYYY-MM-DD."
DATE_ORDER_MESSAGE = "Choose an end date on or after the start date."
# The longest search the Payments list takes (a name, a phone or a receipt number).
SEARCH_MAX_LENGTH = 100
ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_ALL_MESSAGES = ("required", "null", "blank", "invalid", "invalid_choice")


def _same_message(message: str) -> dict[str, Any]:
    return dict.fromkeys(_ALL_MESSAGES, message)


class AmountField(serializers.Field[int, int, int, Any]):
    """Whole NPR, 1 up to `MAX_AMOUNT`, sent as a JSON number (never text, decimals or true)."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(AMOUNT_MESSAGE))

    def to_internal_value(self, data: object) -> int:
        """Return the amount."""
        if isinstance(data, bool) or not isinstance(data, int) or not 1 <= data <= MAX_AMOUNT:
            self.fail("invalid")
        return data

    def to_representation(self, value: int) -> int:
        """Return the amount (unused: answers are built by hand)."""
        return value


class PaidOnField(serializers.Field[date, str, str, Any]):
    """The day it was paid, "YYYY-MM-DD" (AD), from 2000 up to today in the office's time zone."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(PAID_ON_MESSAGE))

    def to_internal_value(self, data: object) -> date:
        """Return the date."""
        if not isinstance(data, str) or not ISO_DATE.match(data):
            self.fail("invalid")
        try:
            paid_on = date.fromisoformat(data)
        except ValueError:
            # self.fail raises the field's validation error
            self.fail("invalid")
        if not EARLIEST_PAID_ON <= paid_on <= timezone.localdate():
            self.fail("invalid")
        return paid_on

    def to_representation(self, value: date) -> str:
        """Return the date as text (unused: answers are built by hand)."""
        return value.isoformat()


class PurposeField(PlainTextField):
    """What the Payment was for: one line of text, up to `PURPOSE_MAX_LENGTH` characters."""

    def __init__(self) -> None:
        super().__init__(
            max_length=PURPOSE_MAX_LENGTH,
            error_messages={**_same_message(PURPOSE_MESSAGE), "max_length": PURPOSE_LENGTH_MESSAGE},
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse anything that isn't text with the same message as a missing purpose."""
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class NewPaymentSerializer(StrictSerializer[None]):
    """A Payment to record (spec 71). Who received it and when are never taken from the body."""

    amount = AmountField()
    paid_on = PaidOnField()
    method = StrictChoiceField(
        choices=PaymentMethod.choices, error_messages=_same_message(METHOD_MESSAGE)
    )
    purpose = PurposeField()

    def to_new_payment(self) -> NewPayment:
        """Return the validated Payment."""
        data = self.validated_data
        return NewPayment(
            amount=data["amount"],
            paid_on=data["paid_on"],
            method=PaymentMethod(data["method"]),
            purpose=data["purpose"],
        )


class FilterDateField(serializers.Field[date, str, str, Any]):
    """A day to filter by, "YYYY-MM-DD" (AD)."""

    def __init__(self) -> None:
        super().__init__(required=False, error_messages=_same_message(FILTER_DATE_MESSAGE))

    def to_internal_value(self, data: object) -> date:
        """Return the date."""
        if not isinstance(data, str) or not ISO_DATE.match(data):
            self.fail("invalid")
        try:
            return date.fromisoformat(data)
        except ValueError:
            # self.fail raises the field's validation error
            self.fail("invalid")

    def to_representation(self, value: date) -> str:
        """Return the date as text (unused: answers are built by hand)."""
        return value.isoformat()


class PaymentListQuerySerializer(StrictSerializer[None]):
    """The Payments list's filters and page, from the query string (spec 74)."""

    to = FilterDateField()
    q = serializers.CharField(max_length=SEARCH_MAX_LENGTH, required=False, allow_blank=True)
    student = serializers.IntegerField(min_value=1, max_value=2**31 - 1, required=False)
    purpose = serializers.CharField(max_length=PURPOSE_MAX_LENGTH, required=False, allow_blank=True)
    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """Add `from` here: it can't be a class attribute's name."""
        return {"from": FilterDateField(), **super().get_fields()}

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Refuse an end date before the start date."""
        paid_from, paid_to = attrs.get("from"), attrs.get("to")
        if paid_from is not None and paid_to is not None and paid_to < paid_from:
            raise serializers.ValidationError({"to": [DATE_ORDER_MESSAGE]})
        return attrs

    def to_filters(self) -> PaymentFilters:
        """Return the validated filters."""
        data = self.validated_data
        return PaymentFilters(
            paid_from=data.get("from"),
            paid_to=data.get("to"),
            student_id=data.get("student"),
            purpose=data.get("purpose", ""),
            query=data.get("q", ""),
        )

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number
