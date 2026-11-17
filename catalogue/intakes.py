"""A Course's Intakes, tidied and checked before they're stored. Pure: no database."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from datetime import date

MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def tidy_intakes(months: Iterable[str]) -> list[str]:
    """Each "YYYY-MM" once, oldest first ("YYYY-MM" sorts as text in date order)."""
    return sorted(set(months))


def month_of(day: date) -> str:
    """The "YYYY-MM" a day falls in."""
    return f"{day.year:04d}-{day.month:02d}"


def intake_label(month: str) -> str:
    """The way the screens show "2030-04": "Apr 2030"."""
    year, number = month.split("-")
    return f"{MONTH_NAMES[int(number) - 1]} {year}"


def first_gone_by(wanted: Iterable[str], kept: Collection[str], today: date) -> str | None:
    """The earliest Intake in `wanted` that is new and in a month already gone, if any.

    Intakes the Course already has (`kept`) stay, even once their month has gone (issue 92).
    """
    this_month = month_of(today)
    gone = sorted(month for month in wanted if month not in kept and month < this_month)
    return gone[0] if gone else None


def gone_by_message(month: str) -> str:
    """Why a past month can't be added."""
    return f"{intake_label(month)} has gone by. Pick this month or a later one."
