"""The passport warning: computed from the expiry date, never stored (issue 46, spec 35c)."""

from __future__ import annotations

from datetime import date

import pytest

from students.passport import PassportWarning, passport_warning

TODAY = date(2026, 11, 5)


@pytest.mark.parametrize(
    ("expiry", "warning"),
    [
        (None, None),
        (date(2026, 11, 4), PassportWarning.EXPIRED),
        (date(2020, 1, 1), PassportWarning.EXPIRED),
        (date(2026, 11, 5), PassportWarning.EXPIRES_SOON),
        (date(2027, 5, 5), PassportWarning.EXPIRES_SOON),
        (date(2027, 5, 6), None),
        (date(2035, 1, 1), None),
    ],
)
def test_the_warning_follows_the_expiry_date(expiry: date | None, warning: object) -> None:
    """Expired before today, expiring within six months, otherwise none."""
    assert passport_warning(expiry, today=TODAY) == warning


@pytest.mark.parametrize(
    ("today", "last_warned"),
    [
        (date(2026, 8, 31), date(2027, 2, 28)),  # No 31 February: the month's last day.
        (date(2027, 8, 31), date(2028, 2, 29)),  # A leap year.
        (date(2026, 7, 15), date(2027, 1, 15)),  # Across the new year.
    ],
)
def test_six_months_ahead_lands_on_a_real_day(today: date, last_warned: date) -> None:
    """A month too short for today's day ends on its last day."""
    assert passport_warning(last_warned, today=today) == PassportWarning.EXPIRES_SOON
    assert passport_warning(date.fromordinal(last_warned.toordinal() + 1), today=today) is None
