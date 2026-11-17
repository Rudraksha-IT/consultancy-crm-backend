"""The passport warning (spec 35c): computed from the expiry date on every answer, never stored."""

from __future__ import annotations

import calendar
from datetime import date
from enum import StrEnum

# A passport running out within this many months gets a warning.
WARNING_MONTHS = 6


class PassportWarning(StrEnum):
    """Why a Student's passport needs attention."""

    EXPIRED = "expired"
    EXPIRES_SOON = "expires_soon"


def passport_warning(expiry: date | None, today: date) -> PassportWarning | None:
    """Expired before `today`, expiring within six months of it, or None."""
    if expiry is None:
        return None
    if expiry < today:
        return PassportWarning.EXPIRED
    return PassportWarning.EXPIRES_SOON if expiry <= months_after(today, WARNING_MONTHS) else None


def months_after(day: date, months: int) -> date:
    """The same day `months` later, or that month's last day when it is shorter."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))
