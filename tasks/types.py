from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from staff.models import Staff
from students.models import Student
from tasks.models import TaskPriority


@dataclass(frozen=True)
class NewTask:
    """A Task to create: what needs doing, by when (AD), for whom, and maybe about whom."""

    title: str
    due_on: date
    assignee: Staff
    student: Student | None = None
    priority: TaskPriority = TaskPriority.NORMAL
    # Seen only by the person who gave it to themselves (spec 77b).
    only_me: bool = False


class TaskView(StrEnum):
    """The views of My Tasks (the prototype's "To do", "Given to others" and "Done")."""

    MINE = "mine"  # Open Tasks given to me.
    GIVEN = "given"  # Tasks I gave to someone else, open or done.
    DONE = "done"  # Tasks given to me that are done.


class Timing(StrEnum):
    """Where a Task stands against its due date, in the office's time zone."""

    OVERDUE = "overdue"
    TODAY = "today"
    UPCOMING = "upcoming"
    DONE = "done"
