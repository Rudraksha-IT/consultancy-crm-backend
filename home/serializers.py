"""Checks for the Homes' queries; anything unknown is refused (issue 33)."""

from __future__ import annotations

from rest_framework import serializers

from config.serializers import StrictSerializer
from students.serializers import StrictChoiceField

COUNSELLOR_ME = "me"
COUNSELLOR_ANY = "any"
# The Admin's Home shows the six furthest along (spec 22a).
DEFAULT_PROGRESS_LIMIT = 6
MAX_PROGRESS_LIMIT = 50
COUNSELLOR_MESSAGE = 'Choose "me" or "any".'


class ProgressQuerySerializer(StrictSerializer[None]):
    """Whose Students (the viewer's own, or anyone's) and how many to answer with."""

    counsellor = StrictChoiceField(
        choices=((COUNSELLOR_ME, COUNSELLOR_ME), (COUNSELLOR_ANY, COUNSELLOR_ANY)),
        required=False,
        error_messages={"invalid_choice": COUNSELLOR_MESSAGE},
    )
    limit = serializers.IntegerField(min_value=1, max_value=MAX_PROGRESS_LIMIT, required=False)

    def only_mine(self) -> bool:
        """Only the Students assigned to the viewer; anyone's by default."""
        counsellor: str = self.validated_data.get("counsellor", COUNSELLOR_ANY)
        return counsellor == COUNSELLOR_ME

    def limit_value(self) -> int:
        """How many Students to answer with."""
        return int(self.validated_data.get("limit", DEFAULT_PROGRESS_LIMIT))


class NoQuerySerializer(StrictSerializer[None]):
    """An endpoint that takes no query at all."""
