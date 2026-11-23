"""What the browser may send to change my own email (ticket 04 of 2026-11-22)."""

from __future__ import annotations

from rest_framework import serializers

from config.serializers import StrictCharField, StrictSerializer
from staff.serializers import EMAIL_MAX_LENGTH, PASSWORD_MAX_LENGTH, ResetCodeSerializer
from two_factor.serializers import ONE_OF_MESSAGE, NumberField, RecoveryCodeField


class EmailChangeRequestSerializer(StrictSerializer[None]):
    """The new email and my password, plus a number (or recovery code) with two-step login."""

    email = serializers.EmailField(max_length=EMAIL_MAX_LENGTH)
    password = StrictCharField(max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False)
    code = NumberField(required=False)
    recovery_code = RecoveryCodeField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """A number or a recovery code, never both; whether one is needed is the view's call."""
        if "code" in attrs and "recovery_code" in attrs:
            raise serializers.ValidationError({"detail": [ONE_OF_MESSAGE]})
        return attrs


class EmailChangeConfirmSerializer(ResetCodeSerializer):
    """The new email and the 6-digit Code that was sent to it."""
