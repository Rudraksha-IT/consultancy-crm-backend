"""A Visa's Biometrics, Medical and Interview dates (spec 62a; issue 75). Pure: no database.

Each is an optional moment, kept in UTC and compared by the Consultancy's own day, so an
appointment earlier today is still "coming up" until the day ends.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from django.db import models
from django.utils import timezone


class Appointment(models.TextChoices):
    """The three appointments a Visa can have, in the order they usually happen."""

    BIOMETRICS = "biometrics", "Biometrics"
    MEDICAL = "medical", "Medical"
    INTERVIEW = "interview", "Interview"


@dataclass(frozen=True)
class Booked:
    """One appointment and when it is."""

    kind: Appointment
    at: datetime


@dataclass(frozen=True)
class VisaDates:
    """The appointments booked on one Visa; None when not booked."""

    biometrics: datetime | None = None
    medical: datetime | None = None
    interview: datetime | None = None

    def booked(self) -> list[Booked]:
        """The appointments that are set, soonest first."""
        found = [
            Booked(kind, moment)
            for kind in Appointment
            if (moment := getattr(self, kind.value)) is not None
        ]
        return sorted(found, key=lambda one: one.at)


def upcoming(dates: VisaDates, today: date) -> Booked | None:
    """The soonest appointment on `today` or later, or None."""
    return next((one for one in dates.booked() if _day(one.at) >= today), None)


def next_appointment(dates: VisaDates, today: date) -> Booked | None:
    """The soonest appointment from `today` on; with none, the latest past one; else None."""
    booked = dates.booked()
    soon = upcoming(dates, today)
    if soon is not None or not booked:
        return soon
    return booked[-1]


def _day(moment: datetime) -> date:
    return timezone.localtime(moment).date()
