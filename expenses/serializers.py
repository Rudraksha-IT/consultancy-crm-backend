"""What the Expense endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

import re
from typing import Any

from django.utils import timezone
from rest_framework import serializers

from config.serializers import StrictSerializer
from expenses.models import PAID_TO_MAX_LENGTH, ExpenseCategory, ExpenseMethod
from expenses.types import Month, NewExpense
from payments.serializers import PAID_ON_MESSAGE, AmountField, PaidOnField
from staff.serializers import PlainTextField
from students.serializers import StrictChoiceField

CATEGORY_MESSAGE = "Choose what kind of expense it is."
METHOD_MESSAGE = "Choose how it was paid."
PAID_TO_MESSAGE = "Say who it was paid to."
PAID_TO_LENGTH_MESSAGE = f"Use at most {PAID_TO_MAX_LENGTH} characters."
MONTH_MESSAGE = "Choose a month from Jan 2000 to this month, as YYYY-MM."
SPENT_ON_MESSAGE = PAID_ON_MESSAGE
EARLIEST_MONTH = Month(2000, 1)
ISO_MONTH = re.compile(r"^([0-9]{4})-(0[1-9]|1[0-2])\Z")
_ALL_MESSAGES = ("required", "null", "blank", "invalid", "invalid_choice")


def _same_message(message: str) -> dict[str, Any]:
    return dict.fromkeys(_ALL_MESSAGES, message)


class PaidToField(PlainTextField):
    """Who the office paid: one line of text, up to `PAID_TO_MAX_LENGTH` characters."""

    def __init__(self) -> None:
        super().__init__(
            max_length=PAID_TO_MAX_LENGTH,
            error_messages={**_same_message(PAID_TO_MESSAGE), "max_length": PAID_TO_LENGTH_MESSAGE},
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse anything that isn't text with the same message as a missing name."""
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class NewExpenseSerializer(StrictSerializer[None]):
    """An Expense to add, or an Expense's new details. Every field is always needed."""

    spent_on = PaidOnField()
    category = StrictChoiceField(
        choices=ExpenseCategory.choices, error_messages=_same_message(CATEGORY_MESSAGE)
    )
    paid_to = PaidToField()
    amount = AmountField()
    method = StrictChoiceField(
        choices=ExpenseMethod.choices, error_messages=_same_message(METHOD_MESSAGE)
    )

    def to_new_expense(self) -> NewExpense:
        """Return the validated Expense."""
        data = self.validated_data
        return NewExpense(
            spent_on=data["spent_on"],
            category=ExpenseCategory(data["category"]),
            paid_to=data["paid_to"],
            amount=data["amount"],
            method=ExpenseMethod(data["method"]),
        )


class MonthField(serializers.Field[Month, str, str, Any]):
    """A month, "YYYY-MM" (AD), from Jan 2000 up to this month in the office's time zone."""

    def __init__(self) -> None:
        super().__init__(required=False, error_messages=_same_message(MONTH_MESSAGE))

    def to_internal_value(self, data: object) -> Month:
        """Return the month."""
        found = ISO_MONTH.match(data) if isinstance(data, str) else None
        if found is None:
            self.fail("invalid")
        month = Month(int(found.group(1)), int(found.group(2)))
        if not str(EARLIEST_MONTH) <= str(month) <= str(this_month()):
            self.fail("invalid")
        return month

    def to_representation(self, value: Month) -> str:
        """Return the month as text (unused: answers are built by hand)."""
        return str(value)


def this_month() -> Month:
    """The month it is now in the office's time zone."""
    today = timezone.localdate()
    return Month(today.year, today.month)


class ExpenseListQuerySerializer(StrictSerializer[None]):
    """The Money out list's month and page, from the query string (spec 76c)."""

    month = MonthField()
    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def chosen_month(self) -> Month:
        """The month asked for; this month if none."""
        month: Month | None = self.validated_data.get("month")
        return month or this_month()

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number
