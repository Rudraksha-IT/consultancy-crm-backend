"""The sender looks' colours: an office's Theme main colour, made readable, or emerald."""

from __future__ import annotations

from tenancy.theme import is_hex_colour

PRODUCT_NAME = "Rudraksha CRM"
PLATFORM_ACCENT = "#047857"
# WCAG AA for white text on the colour (buttons, the Code box's frame, the top bar).
_MIN_CONTRAST = 4.5
_DARKEN_STEP = 0.05
_WHITE_LUMINANCE = 1.0
_SRGB_KNEE = 0.03928
_CHANNEL_MAX = 255
_FLARE = 0.05


def _channel(value: int) -> float:
    c = value / _CHANNEL_MAX
    return c / 12.92 if c <= _SRGB_KNEE else ((c + 0.055) / 1.055) ** 2.4


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _contrast_with_white(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_channel(v) for v in rgb)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (_WHITE_LUMINANCE + _FLARE) / (luminance + _FLARE)


def readable_accent(colour: str) -> str:
    """Return `colour` darkened until white text on it reaches 4.5:1; emerald if it isn't hex.

    The colour goes into an inline style, so only a strict `#rrggbb` value is ever used.
    """
    if not is_hex_colour(colour):
        return PLATFORM_ACCENT
    rgb = _rgb(colour)
    while _contrast_with_white(rgb) < _MIN_CONTRAST:
        rgb = (
            int(rgb[0] * (1 - _DARKEN_STEP)),
            int(rgb[1] * (1 - _DARKEN_STEP)),
            int(rgb[2] * (1 - _DARKEN_STEP)),
        )
    return "#{:02x}{:02x}{:02x}".format(*rgb)
