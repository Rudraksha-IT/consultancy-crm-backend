"""The copyright line (owner, 2026-11-27): the first year alone, or the first year to this one."""

from __future__ import annotations

import pytest
from freezegun import freeze_time

from config.copyright import COPYRIGHT_HOLDER, FIRST_YEAR, copyright_line, this_year


def test_names_rudraksha_labs_from_2026() -> None:
    """The holder and the first year."""
    assert COPYRIGHT_HOLDER == "Rudraksha Labs"
    assert FIRST_YEAR == 2026


def test_shows_one_year_while_it_is_still_the_first() -> None:
    """2026 alone in 2026."""
    assert copyright_line(2026) == "© 2026 Rudraksha Labs. All rights reserved."


@pytest.mark.parametrize(("year", "years"), [(2027, "2026-2027"), (2031, "2026-2031")])
def test_shows_the_first_year_to_this_one_after_that(year: int, years: str) -> None:
    """A range once the year has moved on."""
    assert copyright_line(year) == f"© {years} Rudraksha Labs. All rights reserved."


def test_never_shows_a_year_before_the_first() -> None:
    """A clock set wrong can't show a year before 2026."""
    assert copyright_line(2025) == "© 2026 Rudraksha Labs. All rights reserved."


@freeze_time("2027-12-31T18:30:00Z")
def test_takes_this_year_in_nepal_time() -> None:
    """The year turns at midnight in Kathmandu, not UTC."""
    # 00:15 on 1 January 2028 in Kathmandu.
    assert this_year() == 2028
