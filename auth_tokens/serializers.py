from __future__ import annotations

from rest_framework import serializers

from auth_tokens.headers import MAX_TOKEN_LENGTH

NOT_TEXT_MESSAGE = "Not a valid string."


class TokenField(serializers.CharField):
    """A token as sent: text only (DRF would turn a number into text), never trimmed."""

    def __init__(self) -> None:
        super().__init__(max_length=MAX_TOKEN_LENGTH, trim_whitespace=False)

    def to_internal_value(self, data: object) -> str:
        """Refuse anything that isn't already a string."""
        if not isinstance(data, str):
            raise serializers.ValidationError(NOT_TEXT_MESSAGE)
        return super().to_internal_value(data)


class RefreshTokenSerializer(serializers.Serializer[None]):
    """The refresh token sent to renew the access token, or to log out."""

    refresh = TokenField()
