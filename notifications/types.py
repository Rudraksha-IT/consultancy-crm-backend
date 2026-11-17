from __future__ import annotations

from dataclasses import dataclass

from notifications.models import Notification
from staff.models import Staff
from students.models import Student


@dataclass(frozen=True)
class NotificationPage:
    """One page of someone's notifications, newest first, and how many they haven't read."""

    notifications: list[Notification]
    count: int
    page: int
    unread_count: int


@dataclass(frozen=True)
class BinNotice:
    """Something put in, restored from or deleted from the Bin (issue 79): what, why, who."""

    student: Student
    # "Passport", or "page-2.pdf of Passport".
    item: str
    reason: str
    by: Staff
