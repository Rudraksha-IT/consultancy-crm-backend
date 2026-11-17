"""Checks for the Staff Progress period (issue 84); anything unknown or out of range is refused."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from reports.serializers import DateRangeQuerySerializer
from staff_progress.counting import is_too_long

TOO_LONG_MESSAGE = "Choose a period of one year or less."


class ProgressQuerySerializer(DateRangeQuerySerializer):
    """A period of at most one year, "YYYY-MM-DD" (AD), both ends included."""

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Refuse what any range refuses, and a period longer than one year."""
        attrs = super().validate(attrs)
        if is_too_long(attrs["from"], attrs["to"]):
            raise serializers.ValidationError({"to": [TOO_LONG_MESSAGE]})
        return attrs
