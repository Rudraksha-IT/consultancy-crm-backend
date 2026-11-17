"""What the Communication endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from rest_framework import serializers

from communication.models import (
    EMAIL_ADDRESS_MAX_LENGTH,
    EMAIL_HOST_MAX_LENGTH,
    EMAIL_PORTS,
    SMS_SENDER_MAX_LENGTH,
    Channel,
    ProviderSettings,
    SmsProvider,
)
from communication.types import ProviderDetails
from config.serializers import StrictCharField, StrictSerializer
from staff.serializers import CONTROL_CHARACTERS, CONTROL_CHARACTERS_MESSAGE
from students.serializers import (
    NOTE_CHARACTERS_MESSAGE,
    NOTE_CONTROL_CHARACTERS,
    SEARCH_MAX_LENGTH,
    StrictChoiceField,
    StudentListQuerySerializer,
    filters_from,
)
from students.types import StudentFilters

SMS_BODY_MAX_LENGTH = 800
EMAIL_BODY_MAX_LENGTH = 5000
SECRET_MAX_LENGTH = 200

SMS_PROVIDER_MESSAGE = "Choose Sparrow SMS or Aakash SMS."
SMS_SENDER_MESSAGE = f"Use up to {SMS_SENDER_MAX_LENGTH} letters and numbers."
SMS_TOKEN_MESSAGE = "Paste the API token exactly: 8 to 200 characters with no spaces."  # noqa: S105  # words, not a secret
EMAIL_HOST_MESSAGE = "Enter the email server's name, e.g. smtp.zoho.com."
EMAIL_PORT_MESSAGE = "Choose port 587, 465, 25 or 2525."
EMAIL_PASSWORD_MESSAGE = f"Use at most {SECRET_MAX_LENGTH} characters, on one line."
EMAIL_FROM_MESSAGE = "Enter an email address."
SENDER_NEEDED_MESSAGE = "Enter the sender name Sparrow SMS approved for you."
TOKEN_NEEDED_MESSAGE = "Enter the API token from your SMS company."  # noqa: S105  # words, not a secret
USERNAME_NEEDED_MESSAGE = "Enter the email account's username."
FROM_NEEDED_MESSAGE = "Enter the address emails are sent from."
PASSWORD_NEEDED_MESSAGE = "Enter the email account's password."  # noqa: S105  # words, not a secret
CHANNEL_MESSAGE = "Choose SMS or email."
BODY_MESSAGE = "Write a message."
SMS_TOO_LONG_MESSAGE = f"An SMS can be at most {SMS_BODY_MAX_LENGTH} characters."
EMAIL_TOO_LONG_MESSAGE = f"An email can be at most {EMAIL_BODY_MAX_LENGTH} characters."
BATCH_MESSAGE = "Choose a class."
SMS_NOT_SET_UP_MESSAGE = "SMS isn't set up yet. An Admin can set it up in Configuration."
EMAIL_NOT_SET_UP_MESSAGE = "Email isn't set up yet. An Admin can set it up in Configuration."

_SENDER = re.compile(rf"^[A-Za-z0-9]{{0,{SMS_SENDER_MAX_LENGTH}}}\Z")
_TOKEN = re.compile(rf"^[\x21-\x7e]{{8,{SECRET_MAX_LENGTH}}}\Z")
# A public host name: two or more dot-separated labels of letters, digits and hyphens.
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOST = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+\Z")
LARGEST_ID = 2**63 - 1
_ALL_MESSAGES = ("required", "null", "blank", "invalid", "invalid_choice", "max_length")


def _same_message(message: str) -> dict[str, Any]:
    return dict.fromkeys(_ALL_MESSAGES, message)


class PatternField(StrictCharField):
    """Text matching `pattern`, or blank; anything else gets one message."""

    def __init__(self, pattern: re.Pattern[str], message: str, *, required: bool = True) -> None:
        super().__init__(required=required, allow_blank=True, error_messages=_same_message(message))
        self.pattern = pattern

    def to_internal_value(self, data: object) -> str:
        """Return the text when it matches."""
        value = super().to_internal_value(data)
        if value and not self.pattern.match(value):
            self.fail("invalid")
        return value


class EmailHostField(StrictCharField):
    """A public email server's name, never an IP address or a name on our own network."""

    def __init__(self) -> None:
        super().__init__(
            allow_blank=True,
            max_length=EMAIL_HOST_MAX_LENGTH,
            error_messages=_same_message(EMAIL_HOST_MESSAGE),
        )

    def to_internal_value(self, data: object) -> str:
        """Return the lower-case name."""
        value = super().to_internal_value(data).lower()
        if value and (not _HOST.match(value) or _is_ip_address(value)):
            self.fail("invalid")
        return value


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        # silent: not a valid value; the field error follows
        return False
    return True


class EmailPortField(serializers.Field[int, int, int, Any]):
    """One of the SMTP ports we allow, as a whole number."""

    default_error_messages = {"invalid": EMAIL_PORT_MESSAGE}  # noqa: RUF012  # DRF's own

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(EMAIL_PORT_MESSAGE))

    def to_internal_value(self, data: object) -> int:
        """Return the port when it's one we allow."""
        if isinstance(data, bool) or not isinstance(data, int) or data not in EMAIL_PORTS:
            self.fail("invalid")
        return int(data)

    def to_representation(self, value: int) -> int:
        """The port as it is."""
        return value


class OneLineSecretField(StrictCharField):
    """A password on one line, kept exactly as typed (spaces too)."""

    def __init__(self) -> None:
        super().__init__(
            required=False,
            allow_blank=True,
            trim_whitespace=False,
            max_length=SECRET_MAX_LENGTH,
            error_messages=_same_message(EMAIL_PASSWORD_MESSAGE),
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse control characters."""
        value = super().to_internal_value(data)
        if CONTROL_CHARACTERS.search(value):
            self.fail("invalid")
        return value


class UsernameField(StrictCharField):
    """The email account's username: one line of text."""

    def __init__(self) -> None:
        super().__init__(
            allow_blank=True,
            max_length=EMAIL_ADDRESS_MAX_LENGTH,
            error_messages={"max_length": f"Use at most {EMAIL_ADDRESS_MAX_LENGTH} characters."},
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse control characters."""
        value = super().to_internal_value(data)
        if CONTROL_CHARACTERS.search(value):
            raise serializers.ValidationError(CONTROL_CHARACTERS_MESSAGE)
        return value


class ProviderSettingsSerializer(StrictSerializer[None]):
    """The office's SMS and email accounts (spec 88). Blank secrets keep the saved ones.

    Give it the saved settings as `context["saved"]`, so it knows which secrets are saved.
    """

    sms_provider = StrictChoiceField(
        choices=[("", ""), *SmsProvider.choices],
        allow_blank=True,
        error_messages=_same_message(SMS_PROVIDER_MESSAGE),
    )
    sms_sender = PatternField(_SENDER, SMS_SENDER_MESSAGE)
    sms_token = PatternField(_TOKEN, SMS_TOKEN_MESSAGE, required=False)
    email_host = EmailHostField()
    email_port = EmailPortField()
    email_username = UsernameField()
    email_password = OneLineSecretField()
    email_from = serializers.EmailField(
        allow_blank=True,
        max_length=EMAIL_ADDRESS_MAX_LENGTH,
        error_messages=_same_message(EMAIL_FROM_MESSAGE),
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Each channel that is set up needs all of its account."""
        saved: ProviderSettings | None = self.context.get("saved")
        errors: dict[str, str] = {}
        if attrs["sms_provider"] == SmsProvider.SPARROW and not attrs["sms_sender"]:
            errors["sms_sender"] = SENDER_NEEDED_MESSAGE
        if attrs["sms_provider"] and not attrs.get("sms_token") and not _has_token(saved):
            errors["sms_token"] = TOKEN_NEEDED_MESSAGE
        if attrs["email_host"]:
            errors.update(_missing_email_account(attrs, saved))
        if errors:
            raise serializers.ValidationError({key: [value] for key, value in errors.items()})
        return attrs

    def to_details(self) -> ProviderDetails:
        """The settings to store; a blank or missing secret becomes None (keep the saved one)."""
        data = self.validated_data
        return ProviderDetails(
            sms_provider=data["sms_provider"],
            sms_sender=data["sms_sender"],
            sms_token=data.get("sms_token") or None,
            email_host=data["email_host"],
            email_port=data["email_port"],
            email_username=data["email_username"],
            email_password=data.get("email_password") or None,
            email_from=data["email_from"],
        )


def _has_token(saved: ProviderSettings | None) -> bool:
    return saved is not None and saved.sms_token_sealed != ""


def _missing_email_account(attrs: dict[str, Any], saved: ProviderSettings | None) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not attrs["email_username"]:
        errors["email_username"] = USERNAME_NEEDED_MESSAGE
    if not attrs["email_from"]:
        errors["email_from"] = FROM_NEEDED_MESSAGE
    if not attrs.get("email_password") and (saved is None or saved.email_password_sealed == ""):
        errors["email_password"] = PASSWORD_NEEDED_MESSAGE
    return errors


class MessageBodyField(StrictCharField):
    """A message's text: several lines, nothing else."""

    def __init__(self) -> None:
        super().__init__(
            max_length=EMAIL_BODY_MAX_LENGTH,
            error_messages={
                **_same_message(BODY_MESSAGE),
                "max_length": EMAIL_TOO_LONG_MESSAGE,
            },
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse control characters other than line breaks and tabs."""
        if not isinstance(data, str):
            self.fail("invalid")
        value = super().to_internal_value(data)
        if NOTE_CONTROL_CHARACTERS.search(value):
            raise serializers.ValidationError(NOTE_CHARACTERS_MESSAGE)
        return value


class NewMessageSerializer(StrictSerializer[None]):
    """An SMS or email to one Student (spec 89)."""

    channel = StrictChoiceField(
        choices=Channel.choices, error_messages=_same_message(CHANNEL_MESSAGE)
    )
    body = MessageBodyField()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """An SMS is shorter than an email."""
        if attrs["channel"] == Channel.SMS and len(attrs["body"]) > SMS_BODY_MAX_LENGTH:
            raise serializers.ValidationError({"body": [SMS_TOO_LONG_MESSAGE]})
        return attrs

    def channel_value(self) -> Channel:
        """The channel chosen."""
        return Channel(self.validated_data["channel"])

    def body_value(self) -> str:
        """The trimmed text."""
        body: str = self.validated_data["body"]
        return body


class NoFieldsSerializer(StrictSerializer[None]):
    """A request that takes nothing: any field sent is refused."""


class BatchIdField(serializers.Field[int, int, int, Any]):
    """A Batch's id, as a whole number; whether it exists is the view's to check."""

    def __init__(self) -> None:
        super().__init__(required=False, error_messages=_same_message(BATCH_MESSAGE))

    def to_internal_value(self, data: object) -> int:
        """Return the id; text, decimals and booleans are refused."""
        if isinstance(data, bool) or not isinstance(data, int) or not 1 <= data <= LARGEST_ID:
            self.fail("invalid")
        return int(data)

    def to_representation(self, value: int) -> int:
        """The id as it is."""
        return value


class StudentListFiltersSerializer(StudentListQuerySerializer):
    """The Student list's search and filters (spec 31), without a page: who a message goes to."""

    q = StrictCharField(max_length=SEARCH_MAX_LENGTH, required=False, allow_blank=True)

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """The list's fields but its page, which is refused as unknown."""
        fields = super().get_fields()
        del fields["page"]
        return fields


class BulkMessageSerializer(NewMessageSerializer):
    """One SMS or email to many Students (spec 90): a class, or a Student list's filters.

    `batch` names the class, `students` the filters. That exactly one was given is the view's to
    check, so it can say so plainly.
    """

    batch = BatchIdField()
    students = StudentListFiltersSerializer(required=False)

    def batch_id(self) -> int | None:
        """The class chosen, if any."""
        batch: int | None = self.validated_data.get("batch")
        return batch

    def student_filters(self) -> StudentFilters | None:
        """The Student list's filters chosen, if any."""
        students = self.validated_data.get("students")
        return None if students is None else filters_from(students)


class BulkCountQuerySerializer(StudentListFiltersSerializer):
    """From the query string: a class (`batch`) or a Student list's filters, to count."""

    batch = serializers.IntegerField(
        min_value=1,
        max_value=LARGEST_ID,
        required=False,
        error_messages=_same_message(BATCH_MESSAGE),
    )

    def batch_id(self) -> int | None:
        """The class chosen, if any."""
        batch: int | None = self.validated_data.get("batch")
        return batch

    def has_student_filters(self) -> bool:
        """Whether any of the Student list's filters were given."""
        return any(key != "batch" for key in self.validated_data)
