from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import validate_email
from rest_framework import serializers

from config.serializers import StrictSerializer
from emails.types import OfficeDetails
from tenancy.logo import (
    CHOOSE_LOGO_MESSAGE,
    LOGO_TOO_BIG_MESSAGE,
    MAX_LOGO_BYTES,
    CheckedLogo,
    LogoRefusedError,
    check_logo,
)
from tenancy.models import (
    CONTACT_EMAIL_MAX_LENGTH,
    OFFICE_ADDRESS_LINE_MAX_LENGTH,
    OFFICE_PHONE_MAX_LENGTH,
    OFFICE_WEBSITE_MAX_LENGTH,
)
from tenancy.types import DateDisplay

if TYPE_CHECKING:
    Upload = UploadedFile[bytes]
else:  # Not subscriptable at run time.
    Upload = UploadedFile

DATE_DISPLAY_MESSAGE = "Choose AD or BS."
CONTACT_EMAIL_MESSAGE = "Enter a real email address, like info@youroffice.com."
PHONE_MESSAGE = "A phone number can have digits, spaces, + ( ) and -."
WEBSITE_MESSAGE = "The website must start with https:// or http://."
ADDRESS_NOT_TEXT_MESSAGE = "Enter the address as text."
ADDRESS_ONE_LINE_MESSAGE = "Use one line, with no line breaks."
ADDRESS_TOO_LONG_MESSAGE = f"Use at most {OFFICE_ADDRESS_LINE_MAX_LENGTH} characters."
# Line breaks and other control characters: no Office detail needs them, and in an email header
# they could add lines of their own.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_SPACES = re.compile(r" {2,}")
_PHONE = re.compile(r"[0-9+() -]+")
_PHONE_MIN_DIGITS = 6
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# http(s), a host name with a dot, an optional port, then a path with nothing that could close an
# attribute or start a tag.
_WEBSITE = re.compile(
    rf"https?://{_LABEL}(?:\.{_LABEL})*\.[a-z]{{2,63}}(?::[0-9]{{1,5}})?(?:[/?#][^\s<>\"'`\\]*)?",
    re.IGNORECASE,
)


class DateDisplaySerializer(StrictSerializer[None]):
    """Configuration → Dates: exactly AD or BS, and nothing else."""

    date_display = serializers.ChoiceField(
        choices=[value.value for value in DateDisplay],
        error_messages={"invalid_choice": DATE_DISPLAY_MESSAGE, "null": DATE_DISPLAY_MESSAGE},
    )

    def to_date_display(self) -> DateDisplay:
        """Return the validated choice."""
        return DateDisplay(self.validated_data["date_display"])


class LogoFileField(serializers.Field[CheckedLogo, Upload, None, Any]):
    """The uploaded logo: a PNG, JPG or WebP (by its bytes) or a sanitised SVG, at most 1 MB."""

    def __init__(self) -> None:
        same: dict[str, Any] = dict.fromkeys(
            ("required", "null", "blank", "invalid"), CHOOSE_LOGO_MESSAGE
        )
        super().__init__(error_messages=same)

    def to_internal_value(self, data: object) -> CheckedLogo:
        """Return the logo to store once its size and bytes are checked."""
        if not isinstance(data, UploadedFile) or not data.size:
            self.fail("invalid")
        if data.size > MAX_LOGO_BYTES:
            raise serializers.ValidationError(LOGO_TOO_BIG_MESSAGE)
        data.seek(0)
        try:
            return check_logo(data.read())
        except LogoRefusedError as error:
            raise serializers.ValidationError(str(error)) from error

    def to_representation(self, value: CheckedLogo) -> None:
        """Never shown."""


class LogoUploadSerializer(StrictSerializer[None]):
    """Configuration → Office logo: a form with the one file and nothing else."""

    file = LogoFileField()

    def to_logo(self) -> CheckedLogo:
        """Return the checked logo."""
        logo: CheckedLogo = self.validated_data["file"]
        return logo


class OfficeTextField(serializers.Field[str, str, str, Any]):
    """One Office detail: text on one line (trimmed), or empty; anything else gets `message`.

    Control characters are refused before trimming, so even a trailing line break is refused.
    """

    def __init__(self, message: str, max_length: int) -> None:
        super().__init__(error_messages=dict.fromkeys(("required", "null", "invalid"), message))
        self.max_length = max_length

    def to_internal_value(self, data: object) -> str:
        """Return the trimmed text once it passes `check`."""
        if not isinstance(data, str) or _CONTROL_CHARACTERS.search(data):
            self.fail("invalid")
        value = data.strip()
        if value and (len(value) > self.max_length or not self.check(value)):
            self.fail("invalid")
        return value

    def check(self, value: str) -> bool:
        """Return whether the trimmed, non-empty text is acceptable."""
        return True

    def to_representation(self, value: str) -> str:
        """Return the text as stored."""
        return value


class ContactEmailField(OfficeTextField):
    """One plain email address (no name, no list), where Students' and staff's replies go.

    Never empty (ticket 20): it is asked when the office is opened and can't be cleared, so
    every office email has Reply-To set to the office.
    """

    def __init__(self) -> None:
        super().__init__(CONTACT_EMAIL_MESSAGE, CONTACT_EMAIL_MAX_LENGTH)

    def to_internal_value(self, data: object) -> str:
        """Return the trimmed address; empty (or only spaces) gets the field's message."""
        value = super().to_internal_value(data)
        if not value:
            self.fail("invalid")
        return value

    def check(self, value: str) -> bool:
        """Return whether it is a single real address."""
        try:
            validate_email(value)
        except DjangoValidationError:
            # silent: not a valid value; the field error follows
            return False
        return True


class PhoneField(OfficeTextField):
    """A phone number as people write it: digits, spaces, +, - and brackets."""

    def __init__(self) -> None:
        super().__init__(PHONE_MESSAGE, OFFICE_PHONE_MAX_LENGTH)

    def check(self, value: str) -> bool:
        """Return whether it is made of the allowed characters with enough digits."""
        digits = sum(character.isdigit() for character in value)
        return bool(_PHONE.fullmatch(value)) and digits >= _PHONE_MIN_DIGITS


class WebsiteField(OfficeTextField):
    """A web address starting with https:// or http://."""

    def __init__(self) -> None:
        super().__init__(WEBSITE_MESSAGE, OFFICE_WEBSITE_MAX_LENGTH)

    def check(self, value: str) -> bool:
        """Return whether it is an http(s) address with a real host name."""
        return bool(_WEBSITE.fullmatch(value))


class AddressLineField(serializers.Field[str, str, str, Any]):
    """One line of the office's address: free text, trimmed, with runs of spaces made one."""

    def __init__(self) -> None:
        super().__init__(
            error_messages={
                **dict.fromkeys(("required", "null", "invalid"), ADDRESS_NOT_TEXT_MESSAGE),
                "one_line": ADDRESS_ONE_LINE_MESSAGE,
                "too_long": ADDRESS_TOO_LONG_MESSAGE,
            }
        )

    def to_internal_value(self, data: object) -> str:
        """Return the tidied line."""
        if not isinstance(data, str):
            self.fail("invalid")
        if _CONTROL_CHARACTERS.search(data):
            self.fail("one_line")
        value = _SPACES.sub(" ", data.strip())
        if len(value) > OFFICE_ADDRESS_LINE_MAX_LENGTH:
            self.fail("too_long")
        return value

    def to_representation(self, value: str) -> str:
        """Return the line as stored."""
        return value


class OfficeDetailsSerializer(StrictSerializer[None]):
    """Configuration → General → Office details: every field sent, each checked.

    The contact email can't be empty; the other fields may be.
    """

    contact_email = ContactEmailField()
    phone = PhoneField()
    address_line1 = AddressLineField()
    address_line2 = AddressLineField()
    website = WebsiteField()

    def to_office_details(self) -> OfficeDetails:
        """Return the checked details."""
        return OfficeDetails(**self.validated_data)
