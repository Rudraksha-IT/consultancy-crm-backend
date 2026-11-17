"""What the browser may send for two-step login (issue 61); reused for Staff (62)."""

from __future__ import annotations

from rest_framework import serializers

from config.serializers import StrictCharField, StrictSerializer
from staff.serializers import PASSWORD_MAX_LENGTH
from two_factor.numbers import is_number_shaped, is_recovery_code_shaped

# Far longer than any challenge this server makes (about 150 characters).
CHALLENGE_MAX_LENGTH = 500
NUMBER_MESSAGE = "Enter the 6-digit number from your authenticator app."
RECOVERY_CODE_MESSAGE = "Enter one of your recovery codes, such as abcd-efgh-jkmn."
ONE_OF_MESSAGE = "Enter a number from your app or a recovery code, not both."
REQUIRED_MESSAGE = "This field is required."
NOT_A_CHOICE_MESSAGE = "Not a valid choice."


class NumberField(StrictCharField):
    """Exactly six digits, as text."""

    def to_internal_value(self, data: object) -> str:
        """Refuse anything but six ASCII digits; the message never repeats what was sent."""
        if not isinstance(data, str) or not is_number_shaped(data):
            raise serializers.ValidationError(NUMBER_MESSAGE)
        return data


class RecoveryCodeField(StrictCharField):
    """A recovery code: 12 letters and digits, with or without spaces and hyphens."""

    def to_internal_value(self, data: object) -> str:
        """Refuse anything that can't be a recovery code."""
        if not isinstance(data, str) or not is_recovery_code_shaped(data):
            raise serializers.ValidationError(RECOVERY_CODE_MESSAGE)
        return data


def _number_or_recovery_code(attrs: dict[str, object]) -> dict[str, object]:
    """Refuse both, or neither, of a number and a recovery code."""
    has_code, has_recovery = "code" in attrs, "recovery_code" in attrs
    if has_code and has_recovery:
        raise serializers.ValidationError({"detail": [ONE_OF_MESSAGE]})
    if not has_code and not has_recovery:
        raise serializers.ValidationError({"code": [REQUIRED_MESSAGE]})
    return attrs


class ChallengeSerializer(StrictSerializer[None]):
    """The challenge the Next.js server keeps between the two steps."""

    challenge = StrictCharField(max_length=CHALLENGE_MAX_LENGTH, trim_whitespace=False)


class ConfirmSerializer(ChallengeSerializer):
    """The first number from a newly set-up app."""

    code = NumberField()


class VerifySerializer(ChallengeSerializer):
    """The second step: a number from the app, or a recovery code."""

    code = NumberField(required=False)
    recovery_code = RecoveryCodeField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Exactly one of `code` and `recovery_code`."""
        return _number_or_recovery_code(attrs)


ACTIONS = ("on", "off", "new_recovery_codes")
# What each action needs besides itself; `off` also takes a recovery code in place of a number.
_NEEDS = {"on": (), "off": ("password",), "new_recovery_codes": ("code",)}
_ALLOWED = {
    "on": set(),
    "off": {"password", "code", "recovery_code"},
    "new_recovery_codes": {"code"},
}


class MyTwoFactorSerializer(StrictSerializer[None]):
    """A change on the Security page: switch on, switch off, or make new recovery codes.

    Switching off needs the password and a number (or a recovery code, for a lost phone).
    """

    action = serializers.ChoiceField(
        choices=ACTIONS, error_messages={"invalid_choice": NOT_A_CHOICE_MESSAGE}
    )
    password = StrictCharField(
        max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False, required=False
    )
    code = NumberField(required=False)
    recovery_code = RecoveryCodeField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Require what the action needs, and refuse what it doesn't use."""
        action = str(attrs["action"])
        extra = set(attrs) - {"action"} - _ALLOWED[action]
        if extra:
            raise serializers.ValidationError({"detail": ["Not used for this action."]})
        missing = [name for name in _NEEDS[action] if name not in attrs]
        if missing:
            raise serializers.ValidationError({name: [REQUIRED_MESSAGE] for name in missing})
        if action == "off":
            _number_or_recovery_code(attrs)
        return attrs
