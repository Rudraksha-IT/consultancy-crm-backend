from __future__ import annotations

from rest_framework import serializers

from config.serializers import StrictSerializer

EMAIL_MAX_LENGTH = 254


class CentralLoginSerializer(StrictSerializer[dict[str, str]]):
    """The one field of the Central login: an email."""

    email = serializers.EmailField(max_length=EMAIL_MAX_LENGTH)
