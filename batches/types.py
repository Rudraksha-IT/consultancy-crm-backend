from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from staff.models import Staff


@dataclass(frozen=True)
class BatchDetails:
    """What a Batch is (spec 83): everything but who is in it."""

    name: str
    test_type: str
    teacher: Staff
    time_slot: str
    starts_on: date
    ends_on: date


class BatchTiming(StrEnum):
    """Where a Batch stands against today, in the office's time zone."""

    UPCOMING = "upcoming"
    RUNNING = "running"
    FINISHED = "finished"
