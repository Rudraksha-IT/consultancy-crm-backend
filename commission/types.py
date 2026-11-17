from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from catalogue.models import University
from students.models import Student


@dataclass(frozen=True)
class NewCommission:
    """Commission to add: whose, from which University, for which Intake, and how much."""

    student: Student
    university: University
    intake: str
    amount: int


@dataclass(frozen=True)
class ReceivedPeriod:
    """The days to add up Received Commission over; None leaves that end open (AD)."""

    received_from: date | None
    received_to: date | None
