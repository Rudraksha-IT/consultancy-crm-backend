"""Serializer pieces every endpoint uses so the server never trusts what the browser sends."""

from __future__ import annotations

from collections.abc import Mapping

from rest_framework import serializers

UNKNOWN_FIELD_MESSAGE = "Unknown field."
# Where the refusal goes, in place of the unknown field's own name.
UNKNOWN_FIELD_KEY = "detail"
NOT_TEXT_MESSAGE = "Not a valid string."


class StrictCharField(serializers.CharField):
    """Text only: DRF's CharField would turn a number or a boolean into text."""

    def to_internal_value(self, data: object) -> str:
        """Refuse anything that isn't already a string."""
        if not isinstance(data, str):
            raise serializers.ValidationError(NOT_TEXT_MESSAGE)
        return super().to_internal_value(data)


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
