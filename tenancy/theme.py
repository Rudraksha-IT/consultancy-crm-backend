"""A Consultancy's Theme: the three stored colours plus dark-mode choices (docs/design-system.md).

Every screen colour is derived from these values by the frontend's theme function; the backend only
stores and validates them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass

PAGE_NAMES = ("ice", "stone", "white", "fog", "mist", "sage", "lilac", "sky")
DARK_BASE_NAMES = ("pure", "black", "charcoal", "graphite", "soft", "warm", "pine", "teal")
DARK_SIDEBAR_MODES = ("darker", "same", "lighter", "black", "brand")
AUTO_ACCENT = "auto"

_HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")

type _Check = Callable[[str], bool]


@dataclass(frozen=True)
class DarkTheme:
    """Dark-mode choices: a named near-black background, a sidebar mode, and an accent or auto."""

    base: str
    sidebar: str
    accent: str


@dataclass(frozen=True)
class ThemeConfig:
    """A named page background, hex sidebar and accent colours, and the dark-mode choices."""

    page: str
    sidebar: str
    accent: str
    dark: DarkTheme


# "Stone and emerald", chosen on 2026-10-28 for new Consultancies.
DEFAULT_THEME = ThemeConfig(
    page="stone",
    sidebar="#ffffff",
    accent="#047857",
    dark=DarkTheme(base="soft", sidebar="darker", accent="#047857"),
)
# Used when a theme has no dark settings at all.
DEFAULT_DARK = DarkTheme(base="soft", sidebar="darker", accent=AUTO_ACCENT)


def is_hex_colour(value: object) -> bool:
    """Return whether the value is a six-digit hex colour such as `#047857`."""
    return isinstance(value, str) and _HEX_COLOUR.fullmatch(value) is not None


def is_dark_accent(value: object) -> bool:
    """Return whether the value is a valid dark-mode accent: `auto` or a hex colour."""
    return value == AUTO_ACCENT or is_hex_colour(value)


def theme_to_stored(theme: ThemeConfig) -> dict[str, object]:
    """Return the Theme as the JSON object it is stored and sent as."""
    return asdict(theme)


def theme_from_stored(stored: object) -> ThemeConfig:
    """Return the Theme in a stored JSON object, replacing any missing or bad value by its default.

    A bad stored value must never break the product, so each field falls back on its own.
    """
    if not isinstance(stored, dict):
        return DEFAULT_THEME
    data: dict[str, object] = stored
    return ThemeConfig(
        page=_pick(data.get("page"), DEFAULT_THEME.page, lambda value: value in PAGE_NAMES),
        sidebar=_pick(data.get("sidebar"), DEFAULT_THEME.sidebar, is_hex_colour),
        accent=_pick(data.get("accent"), DEFAULT_THEME.accent, is_hex_colour),
        dark=_dark_from_stored(data.get("dark")),
    )


def _dark_from_stored(stored: object) -> DarkTheme:
    data = stored if isinstance(stored, dict) else {}
    return DarkTheme(
        base=_pick(data.get("base"), DEFAULT_DARK.base, lambda value: value in DARK_BASE_NAMES),
        sidebar=_pick(
            data.get("sidebar"), DEFAULT_DARK.sidebar, lambda value: value in DARK_SIDEBAR_MODES
        ),
        accent=_pick(data.get("accent"), DEFAULT_DARK.accent, is_dark_accent),
    )


def _pick(value: object, default: str, is_valid: _Check) -> str:
    return value if isinstance(value, str) and is_valid(value) else default
