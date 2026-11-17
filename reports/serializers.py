"""Checks for the Reports' queries; anything unknown or out of range is refused (issue 28)."""

from __future__ import annotations

from datetime import date
from typing import Any

from rest_framework import serializers

from config.serializers import StrictSerializer
from payments.serializers import DATE_ORDER_MESSAGE, FilterDateField
from students.serializers import StrictChoiceField

MONTH_CHOICES = (("6", "6"), ("12", "12"))
DEFAULT_MONTHS = 6
MONTHS_MESSAGE = "Choose 6 or 12 months."
# Far enough either way for any office, and keeps the day after `to` a real date.
EARLIEST_DAY = date(2000, 1, 1)
LATEST_DAY = date(2099, 12, 31)
RANGE_MESSAGE = "Choose dates between 2000 and 2099."
REQUIRED_MESSAGE = "Enter a date as YYYY-MM-DD."


class MonthsQuerySerializer(StrictSerializer[None]):
    """How many months the charts go back: 6 (the default) or 12."""

    months = StrictChoiceField(
        choices=MONTH_CHOICES,
        required=False,
        error_messages={"invalid_choice": MONTHS_MESSAGE},
    )

    def month_count(self) -> int:
        """How many months to show."""
        return int(self.validated_data.get("months", DEFAULT_MONTHS))


class DateRangeQuerySerializer(StrictSerializer[None]):
    """A range of days, both ends included, "YYYY-MM-DD" (AD)."""

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """`from` can't be a class attribute's name; both ends are required."""
        fields: dict[str, serializers.Field[Any, Any, Any, Any]] = {
            "from": FilterDateField(),
            "to": FilterDateField(),
        }
        for field in fields.values():
            field.required = True
            field.error_messages["required"] = REQUIRED_MESSAGE
        return fields

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Refuse days outside the years we accept, and an end before the start."""
        start, end = attrs["from"], attrs["to"]
        for name, day in (("from", start), ("to", end)):
            if not EARLIEST_DAY <= day <= LATEST_DAY:
                raise serializers.ValidationError({name: [RANGE_MESSAGE]})
        if end < start:
            raise serializers.ValidationError({"to": [DATE_ORDER_MESSAGE]})
        return attrs

    def days(self) -> tuple[date, date]:
        """The first and last day."""
        return self.validated_data["from"], self.validated_data["to"]
