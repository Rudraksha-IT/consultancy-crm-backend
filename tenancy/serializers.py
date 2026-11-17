from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers

from config.serializers import StrictSerializer
from modules.serializers import StrictBooleanField
from tenancy.logo import (
    CHOOSE_LOGO_MESSAGE,
    LOGO_TOO_BIG_MESSAGE,
    MAX_LOGO_BYTES,
    CheckedLogo,
    LogoRefusedError,
    check_logo,
)
from tenancy.theme import (
    AUTO_ACCENT,
    DARK_BASE_NAMES,
    DARK_SIDEBAR_MODES,
    PAGE_NAMES,
    ThemeConfig,
    is_dark_accent,
    is_hex_colour,
    theme_from_stored,
)
from tenancy.types import DateDisplay

if TYPE_CHECKING:
    Upload = UploadedFile[bytes]
else:  # Not subscriptable at run time.
    Upload = UploadedFile

HEX_COLOUR_MESSAGE = "Enter a colour as # followed by six hex digits, for example #047857."
DARK_ACCENT_MESSAGE = "Enter auto, or a colour as # followed by six hex digits."
COLOUR_MAX_INPUT_LENGTH = 20
DATE_DISPLAY_MESSAGE = "Choose AD or BS."


class DarkThemeSerializer(serializers.Serializer[None]):
    """Dark-mode choices: a named near-black background, a sidebar mode, and an accent or auto."""

    base = serializers.ChoiceField(choices=DARK_BASE_NAMES)
    sidebar = serializers.ChoiceField(choices=DARK_SIDEBAR_MODES)
    accent = serializers.CharField(max_length=COLOUR_MAX_INPUT_LENGTH, trim_whitespace=False)

    def validate_accent(self, value: str) -> str:
        """Accept `auto` or a hex colour, stored in lowercase."""
        if not is_dark_accent(value.lower()):
            raise serializers.ValidationError(DARK_ACCENT_MESSAGE)
        return AUTO_ACCENT if value.lower() == AUTO_ACCENT else value.lower()


class ThemeSerializer(serializers.Serializer[None]):
    """A Consultancy's Theme: a named page, hex sidebar and accent, and optional dark settings."""

    page = serializers.ChoiceField(choices=PAGE_NAMES)
    sidebar = serializers.CharField(max_length=COLOUR_MAX_INPUT_LENGTH, trim_whitespace=False)
    accent = serializers.CharField(max_length=COLOUR_MAX_INPUT_LENGTH, trim_whitespace=False)
    dark = DarkThemeSerializer(required=False)

    def validate_sidebar(self, value: str) -> str:
        """Accept a hex colour, stored in lowercase."""
        return _hex_colour(value)

    def validate_accent(self, value: str) -> str:
        """Accept a hex colour, stored in lowercase."""
        return _hex_colour(value)

    def to_theme(self) -> ThemeConfig:
        """Return the validated Theme; without dark settings it gets the documented fallback."""
        return theme_from_stored(self.validated_data)


def _hex_colour(value: str) -> str:
    if not is_hex_colour(value):
        raise serializers.ValidationError(HEX_COLOUR_MESSAGE)
    return value.lower()


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


class ShowNameSerializer(StrictSerializer[None]):
    """Whether the office name shows beside its logo: exactly true or false."""

    show_name = StrictBooleanField()

    def to_show_name(self) -> bool:
        """Return the choice."""
        return bool(self.validated_data["show_name"])
