"""Serializer pieces every endpoint uses so the server never trusts what the browser sends."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping

from rest_framework import serializers

UNKNOWN_FIELD_MESSAGE = "Unknown field."
# Where the refusal goes, in place of the unknown field's own name.
UNKNOWN_FIELD_KEY = "detail"
NOT_TEXT_MESSAGE = "Not a valid string."
NOT_A_RECORD_ID_MESSAGE = "Not a valid id."
# A UUID as the server writes it (ADR 0027): lower-case hex in 8-4-4-4-12.
RECORD_ID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_RECORD_ID = re.compile(RECORD_ID_PATTERN)


def parse_record_id(data: object) -> uuid.UUID | None:
    """The UUID `data` names, in the server's spelling (ADR 0027); None for anything else."""
    if not isinstance(data, str) or not _RECORD_ID.fullmatch(data):
        return None
    return uuid.UUID(data)


class StrictCharField(serializers.CharField):
    """Text only: DRF's CharField would turn a number or a boolean into text."""

    def to_internal_value(self, data: object) -> str:
        """Refuse anything that isn't already a string."""
        if not isinstance(data, str):
            raise serializers.ValidationError(NOT_TEXT_MESSAGE)
        return super().to_internal_value(data)


class RecordIdField(serializers.Field[uuid.UUID, str, str, object]):
    """A record named by its UUID, as the server writes it (ADR 0027).

    A number, a UUID in another spelling, or anything else is refused: DRF's UUIDField would
    turn a number into a UUID.
    """

    default_error_messages = {"invalid": NOT_A_RECORD_ID_MESSAGE}  # noqa: RUF012  # DRF's own

    def __init__(self, message: str | None = None, **kwargs: object) -> None:
        """`message` replaces "Not a valid id." (and "required") where a field says it better."""
        if message is not None:
            kwargs["error_messages"] = {"invalid": message, "required": message, "null": message}
        super().__init__(**kwargs)  # type: ignore[arg-type]  # DRF's untyped keyword arguments

    def to_internal_value(self, data: object) -> uuid.UUID:
        """Return the UUID, or refuse anything that isn't one in the server's spelling."""
        found = parse_record_id(data)
        if found is None:
            self.fail("invalid")
        return found

    def to_representation(self, value: uuid.UUID) -> str:
        """The UUID as text."""
        return str(value)


class StrictSerializer[T](serializers.Serializer[T]):
    """Refuses fields it doesn't know (DRF would silently drop them), so they're never stored.

    The refusal never repeats the unknown names: they are whatever the browser sent.
    """

    def to_internal_value(self, data: object) -> dict[str, object]:
        """Refuse any unknown field before checking the known ones."""
        if isinstance(data, Mapping) and any(key not in self.fields for key in data):
            raise serializers.ValidationError({UNKNOWN_FIELD_KEY: [UNKNOWN_FIELD_MESSAGE]})
        validated: dict[str, object] = super().to_internal_value(data)
        return validated
