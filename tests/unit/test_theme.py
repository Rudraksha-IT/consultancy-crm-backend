"""The Theme config: three stored values plus dark-mode choices, with safe fallbacks."""

from __future__ import annotations

import pytest

from tenancy.theme import (
    DEFAULT_THEME,
    DarkTheme,
    ThemeConfig,
    is_hex_colour,
    theme_from_stored,
    theme_to_stored,
)

STORED_DEFAULT = {
    "page": "stone",
    "sidebar": "#ffffff",
    "accent": "#047857",
    "dark": {"base": "soft", "sidebar": "darker", "accent": "#047857"},
}


class TestIsHexColour:
    @pytest.mark.parametrize("value", ["#047857", "#FFFFFF", "#a1B2c3"])
    def test_accepts_six_digit_hex(self, value: str) -> None:
        assert is_hex_colour(value)

    @pytest.mark.parametrize(
        "value", ["047857", "#fff", "#04785", "#0478577", "#gggggg", "", "red"]
    )
    def test_rejects_anything_else(self, value: str) -> None:
        assert not is_hex_colour(value)


class TestDefaultTheme:
    def test_is_stone_and_emerald(self) -> None:
        assert theme_to_stored(DEFAULT_THEME) == STORED_DEFAULT


class TestThemeFromStored:
    def test_round_trips_a_stored_theme(self) -> None:
        theme = ThemeConfig(
            page="ice",
            sidebar="#1f2523",
            accent="#0b6e8e",
            dark=DarkTheme(base="pine", sidebar="brand", accent="auto"),
        )

        assert theme_from_stored(theme_to_stored(theme)) == theme

    def test_falls_back_to_the_default_for_something_that_is_not_a_theme(self) -> None:
        assert theme_from_stored(None) == DEFAULT_THEME

    def test_falls_back_field_by_field_for_bad_values(self) -> None:
        stored = {"page": "purple", "sidebar": "#123456", "accent": "nope", "dark": "x"}

        theme = theme_from_stored(stored)

        assert theme == ThemeConfig(
            page="stone",
            sidebar="#123456",
            accent="#047857",
            dark=DarkTheme(base="soft", sidebar="darker", accent="auto"),
        )

    def test_missing_dark_settings_use_the_documented_fallback(self) -> None:
        theme = theme_from_stored({"page": "sky", "sidebar": "#ffffff", "accent": "#2546c4"})

        assert theme.dark == DarkTheme(base="soft", sidebar="darker", accent="auto")

    def test_bad_dark_values_fall_back_one_by_one(self) -> None:
        stored = {**STORED_DEFAULT, "dark": {"base": "navy", "sidebar": "same", "accent": "blue"}}

        assert theme_from_stored(stored).dark == DarkTheme(
            base="soft", sidebar="same", accent="auto"
        )
