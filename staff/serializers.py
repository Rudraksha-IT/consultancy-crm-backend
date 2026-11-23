from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from rest_framework import serializers

from config.serializers import StrictCharField, StrictSerializer
from modules.serializers import StrictBooleanField
from staff.employees import is_email_taken
from staff.models import JOB_TITLE_MAX_LENGTH, STAFF_NAME_MAX_LENGTH, ColourMode, Role
from staff.types import MyDetailsChange, NewStaff

# The longest address email allows, and far more than any password needs.
EMAIL_MAX_LENGTH = 254
PASSWORD_MAX_LENGTH = 128


class LoginSerializer(serializers.Serializer[None]):
    """Email and password submitted on the login screen."""

    email = serializers.EmailField(max_length=EMAIL_MAX_LENGTH)
    password = serializers.CharField(max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False)


# Exactly six ASCII digits; the screen removes the space it shows in the middle ("123 456").
CODE_PATTERN = r"^[0-9]{6}\Z"
CODE_FORMAT_MESSAGE = "Enter the 6-digit code."


class _EmailSerializer(StrictSerializer[None]):
    email = serializers.EmailField(max_length=EMAIL_MAX_LENGTH)


class ResetRequestSerializer(_EmailSerializer):
    """Step 1 of a password reset: the work email."""


class ResetCodeSerializer(_EmailSerializer):
    """Step 2 of a password reset: the email and the Code it received."""

    code = StrictCharField(trim_whitespace=False)

    def validate_code(self, value: str) -> str:
        """Allow exactly six digits."""
        if not re.match(CODE_PATTERN, value):
            raise serializers.ValidationError(CODE_FORMAT_MESSAGE)
        return value


class ResetCompleteSerializer(ResetCodeSerializer):
    """Step 3 of a password reset: the email, the Code and the new password."""

    password = StrictCharField(max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False)


class JoinRequestSerializer(_EmailSerializer):
    """Send me a new code, on the Join screen: the work email."""


class JoinCodeSerializer(ResetCodeSerializer):
    """Join step 1: the work email and the Code from the invite email."""


class JoinCompleteSerializer(ResetCompleteSerializer):
    """Join step 2: the email, the Code and the person's own password."""


# Typed with spaces or dashes ("+977 981-234-5670"); stored as "+" and 7 to 15 digits.
MOBILE_INPUT_MAX_LENGTH = 20
MOBILE_PATTERN = r"^\+?[0-9]{7,15}\Z"
MOBILE_FORMAT_MESSAGE = "Enter a mobile number of 7 to 15 digits, optionally starting with +."
MOBILE_SEPARATORS = re.compile(r"[ -]")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
CONTROL_CHARACTERS_MESSAGE = "Use letters, numbers and punctuation only."
EMAIL_TAKEN_MESSAGE = "Someone here already uses this email."
TEACHER_NEEDS_CLASSES_MESSAGE = "Teachers need the test-prep classes feature."


class PlainTextField(StrictCharField):
    """One line of text: no line breaks or other control characters."""

    def to_internal_value(self, data: object) -> str:
        """Refuse control characters, which no name or title needs."""
        value = super().to_internal_value(data)
        if CONTROL_CHARACTERS.search(value):
            raise serializers.ValidationError(CONTROL_CHARACTERS_MESSAGE)
        return value


class MobileField(StrictCharField):
    """A mobile number; spaces and dashes are dropped before it's checked."""

    def __init__(self, error_messages: Mapping[str, str] | None = None) -> None:
        super().__init__(
            max_length=MOBILE_INPUT_MAX_LENGTH, error_messages=dict(error_messages or {})
        )

    def to_internal_value(self, data: object) -> str:
        """Return "+" (optional) and 7 to 15 digits."""
        value = MOBILE_SEPARATORS.sub("", super().to_internal_value(data))
        if not re.match(MOBILE_PATTERN, value):
            raise serializers.ValidationError(MOBILE_FORMAT_MESSAGE)
        return value


class RoleField(serializers.ChoiceField):
    """One of the fixed Roles, by its API value."""

    def __init__(self) -> None:
        super().__init__(choices=Role.choices)

    def to_internal_value(self, data: object) -> Role:
        """Return the Role; anything but a Role's exact value is refused."""
        if not isinstance(data, str):
            self.fail("invalid_choice", input=data)
        return Role(super().to_internal_value(data))


class InviteSerializer(StrictSerializer[None]):
    """Add an employee: who they are, how to reach them and what they can use the app as."""

    name = PlainTextField(max_length=STAFF_NAME_MAX_LENGTH)
    mobile = MobileField()
    email = serializers.EmailField(max_length=EMAIL_MAX_LENGTH)
    job_title = PlainTextField(max_length=JOB_TITLE_MAX_LENGTH)
    role = RoleField()
    joined_on = serializers.DateField(input_formats=["iso-8601"])

    def validate_email(self, value: str) -> str:
        """Refuse an email someone here already uses."""
        if is_email_taken(value):
            raise serializers.ValidationError(EMAIL_TAKEN_MESSAGE)
        return value

    def to_new_staff(self) -> NewStaff:
        """Return the validated details."""
        data = self.validated_data
        return NewStaff(
            name=data["name"],
            email=data["email"],
            role=data["role"],
            mobile=data["mobile"],
            job_title=data["job_title"],
            joined_on=data["joined_on"],
        )


class RoleChangeSerializer(StrictSerializer[None]):
    """Manage an employee: their new Role."""

    role = RoleField()


class OptionalMobileField(MobileField):
    """A mobile number, or empty to remove it."""

    def __init__(self) -> None:
        super().__init__()
        self.allow_blank = True
        self.required = False


class ColourModeField(serializers.ChoiceField):
    """Light, dark, or the same as the device, by its API value."""

    def __init__(self) -> None:
        super().__init__(choices=ColourMode.choices, required=False)

    def to_internal_value(self, data: object) -> ColourMode:
        """Return the choice; anything but a choice's exact value is refused."""
        if not isinstance(data, str):
            self.fail("invalid_choice", input=data)
        return ColourMode(super().to_internal_value(data))


class NotificationChoicesSerializer(StrictSerializer[None]):
    """Which emails the app sends me; any left out stay as they are."""

    task_given = StrictBooleanField(required=False)
    task_due_tomorrow = StrictBooleanField(required=False)
    morning_summary = StrictBooleanField(required=False)


class MyDetailsSerializer(StrictSerializer[None]):
    """What a Staff member may change about themselves (spec 22d, 22f, 22g).

    Job title, Role and joining date are set by an Admin, and the email changes only with a
    Code (`/api/me/email/`), so they are unknown here and refused.
    """

    name = PlainTextField(max_length=STAFF_NAME_MAX_LENGTH, required=False)
    mobile = OptionalMobileField()
    colour_mode = ColourModeField()
    page_swipe = StrictBooleanField(required=False)
    notifications = NotificationChoicesSerializer(required=False)

    def to_change(self) -> MyDetailsChange:
        """Return the validated change; anything not sent stays as it is."""
        data = self.validated_data
        notifications = data.get("notifications", {})
        return MyDetailsChange(
            name=data.get("name"),
            mobile=data.get("mobile"),
            colour_mode=data.get("colour_mode"),
            page_swipe=data.get("page_swipe"),
            task_given=notifications.get("task_given"),
            task_due_tomorrow=notifications.get("task_due_tomorrow"),
            morning_summary=notifications.get("morning_summary"),
        )


class PasswordChangeSerializer(StrictSerializer[None]):
    """Change my password: the current one, then the new one (spec 22e)."""

    current_password = StrictCharField(max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False)
    new_password = StrictCharField(max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False)


class NoBodySerializer(StrictSerializer[None]):
    """A call that takes nothing: anything sent is refused."""


class TwoFactorSwitchSerializer(StrictSerializer[None]):
    """An Admin switching someone's two-step login on or off (issue 62): `{required}` only.

    The field is named in `get_fields`, since a class attribute `required` would hide the
    serializer's own `required` flag.
    """

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """Only `required`, true or false."""
        return {"required": StrictBooleanField()}
