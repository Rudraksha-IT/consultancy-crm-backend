"""Staff Progress counting rules (spec 125, 357; issue 84). Pure: no database, no request.

Only changes are counted, never pages someone opened. Each count, by who did it:

- Students moved forward: their Stage changes further along the journey (Lost is outside it).
- Applications made, Visas applied for: the Applications and Visas they added.
- Students lost: their Stage changes to Lost.
- Tasks done: Tasks they marked done (recorded since issue 83 shipped).
- Visitors registered: the Visits they logged; New Leads: those of kind New (a new Student).
- Payments taken: the Payments they recorded, with the amount.
- Classes taken: the class days they marked attendance for; Attendance marked: the marks.

Actions by a Platform Admin are never linked to a Staff member, so they count for nobody.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from reports.calculations import JOURNEY, stage_from_history
from staff.models import Role
from students.models import Stage

_RANK = {stage: rank for rank, stage in enumerate(JOURNEY)}


class Count(StrEnum):
    """One number card of Staff Progress."""

    MOVED_FORWARD = "moved_forward"
    APPLICATIONS_MADE = "applications_made"
    VISAS_APPLIED_FOR = "visas_applied_for"
    STUDENTS_LOST = "students_lost"
    TASKS_DONE = "tasks_done"
    VISITORS_REGISTERED = "visitors_registered"
    NEW_LEADS = "new_leads"
    PAYMENTS_TAKEN = "payments_taken"
    CLASSES_TAKEN = "classes_taken"
    ATTENDANCE_MARKED = "attendance_marked"


COUNSELLOR_COUNTS = (
    Count.MOVED_FORWARD,
    Count.APPLICATIONS_MADE,
    Count.VISAS_APPLIED_FOR,
    Count.STUDENTS_LOST,
    Count.TASKS_DONE,
)
FRONT_DESK_COUNTS = (Count.VISITORS_REGISTERED, Count.NEW_LEADS, Count.PAYMENTS_TAKEN)
TEACHER_COUNTS = (Count.CLASSES_TAKEN, Count.ATTENDANCE_MARKED)

# Counts whose recording began after the product started: who marked a Task done (issue 83).
RECORDED_SINCE: dict[Count, date] = {Count.TASKS_DONE: date(2026, 11, 15)}


@dataclass(frozen=True)
class StageMoveBy:
    """One Stage change from History: who made it, and the Stages before and after."""

    staff_id: int
    before: str
    after: str


def counts_for(role: Role, *, batches_on: bool, teaches: bool) -> tuple[Count, ...]:
    """The cards a Staff member of `role` gets, in order.

    An Admin gets every card that applies: the Counsellor and Front Desk cards, and the Teacher
    cards when they teach a class. Teacher cards need test-prep classes switched on.
    """
    teacher = TEACHER_COUNTS if batches_on else ()
    if role == Role.COUNSELLOR:
        return COUNSELLOR_COUNTS
    if role == Role.FRONT_DESK:
        return FRONT_DESK_COUNTS
    if role == Role.TEACHER:
        return teacher
    return (*COUNSELLOR_COUNTS, *FRONT_DESK_COUNTS, *(teacher if teaches else ()))


def counted_from(count: Count, start: date) -> date | None:
    """The day recording began, when it began after the period's first day; otherwise None."""
    since = RECORDED_SINCE.get(count)
    return since if since is not None and start < since else None


def is_too_long(start: date, end: date) -> bool:
    """Whether the period is longer than one year (to the same date a year on)."""
    try:
        a_year_on = start.replace(year=start.year + 1)
    except ValueError:  # the 29th of February: a year on is the 1st of March
        a_year_on = date(start.year + 1, 3, 1)
    return end > a_year_on


def tally_stage_moves(moves: Iterable[StageMoveBy]) -> dict[int, tuple[int, int]]:
    """Per Staff member: how many Stage changes moved a Student forward, and how many to Lost."""
    tally: dict[int, tuple[int, int]] = {}
    for move in moves:
        forward, lost = tally.get(move.staff_id, (0, 0))
        before, after = stage_from_history(move.before), stage_from_history(move.after)
        if after == Stage.LOST:
            lost += 1
        elif _is_forward(before, after):
            forward += 1
        tally[move.staff_id] = (forward, lost)
    return tally


def _is_forward(before: Stage | None, after: Stage | None) -> bool:
    was = _RANK.get(before) if before is not None else None
    now = _RANK.get(after) if after is not None else None
    return was is not None and now is not None and now > was
