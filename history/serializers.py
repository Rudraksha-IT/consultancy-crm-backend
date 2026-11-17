"""What the History endpoint accepts in its query string."""

from __future__ import annotations

from rest_framework import serializers

from config.serializers import StrictSerializer


class HistoryQuerySerializer(StrictSerializer[None]):
    """Which page of a Student's History to show."""

    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number
