"""What the global search accepts. The server never trusts the browser (issues README)."""

from __future__ import annotations

from config.serializers import StrictSerializer
from staff.serializers import PlainTextField

SEARCH_MIN_LENGTH = 2
SEARCH_MAX_LENGTH = 100
TOO_SHORT_MESSAGE = "Type at least 2 characters."


class SearchQuerySerializer(StrictSerializer[None]):
    """`q`: 2 to 100 characters of one line, spaces around it ignored."""

    q = PlainTextField(
        min_length=SEARCH_MIN_LENGTH,
        max_length=SEARCH_MAX_LENGTH,
        error_messages={
            "required": TOO_SHORT_MESSAGE,
            "blank": TOO_SHORT_MESSAGE,
            "min_length": TOO_SHORT_MESSAGE,
        },
    )

    def text(self) -> str:
        """Return the validated search."""
        value: str = self.validated_data["q"]
        return value
