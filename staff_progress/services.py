"""Staff Progress: what one Staff member changed over a period (spec 125, 357; issues 84, 85).

The one place that counts, read-only. The Progress tab uses `progress_of`; Reports → Staff
Progress (issue 85) uses `count_progress` for many people at once, so both always match.
Call inside the Consultancy's schema.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from django.db.models import Count as Counted
from django.db.models import Max, Q, QuerySet, Sum
from django.utils import timezone

from applications.models import Application, Visa
from batches.models import Attendance, Batch
from history.models import HistoryEntry, HistoryKind
from payments.models import Payment
from staff.models import Role, Staff
from staff_progress.counting import (
    Count,
    StageMoveBy,
    counted_from,
    counts_for,
    tally_stage_moves,
)
from tasks.models import Task
from visits.models import Visit, VisitKind

logger = logging.getLogger(__name__)

# The day-by-day list shows at most this many changes, the newest; the cards count them all.
MAX_ENTRIES = 500
# Kinds the list adds to History's own.
VISIT_LOGGED = "visit_logged"
APPLICATION_MADE = "application_made"
VISA_APPLIED_FOR = "visa_applied_for"
ATTENDANCE_MARKED = "attendance_marked"


@dataclass
class ProgressCounts:
    """One Staff member's counts for a period, and the money in the Payments they took."""

    values: dict[Count, int] = field(default_factory=lambda: dict.fromkeys(Count, 0))
    payments_amount: int = 0


@dataclass(frozen=True)
class Card:
    """One number card: its count, the number, when counting began if later, and any amount."""

    count: Count
    value: int
    counted_from: date | None
    amount: int | None


@dataclass(frozen=True)
class Change:
    """One change they made, as the day-by-day list shows it."""

    kind: str
    day: date
    at: datetime
    before: str = ""
    after: str = ""
    detail: str = ""
    student_id: UUID | None = None  # the Student's public id (ADR 0027)
    student_name: str = ""


@dataclass(frozen=True)
class Day:
    """The changes made on one of the office's days, newest first."""

    day: date
    changes: list[Change]


@dataclass(frozen=True)
class StaffProgress:
    """A Staff member's Progress for a period: the cards and the changes, day by day."""

    staff: Staff
    start: date
    end: date
    cards: list[Card]
    days: list[Day]
    more: bool


def progress_of(staff: Staff, start: date, end: date, *, batches_on: bool) -> StaffProgress:
    """The Staff member's cards and changes between the days (both included).

    With test-prep classes off, nothing about classes is counted or listed.
    """
    teaches = batches_on and Batch.objects.filter(teacher=staff).exists()
    counts = count_progress([staff.pk], start, end).get(staff.pk, ProgressCounts())
    cards = [
        Card(
            count,
            counts.values[count],
            counted_from(count, start),
            counts.payments_amount if count == Count.PAYMENTS_TAKEN else None,
        )
        for count in counts_for(Role(staff.role), batches_on=batches_on, teaches=teaches)
    ]
    changes = _changes(staff, start, end, batches_on=batches_on)
    logger.debug("staff progress built: staff_id=%s days=%s", staff.pk, (end - start).days + 1)
    return StaffProgress(
        staff, start, end, cards, _by_day(changes[:MAX_ENTRIES]), len(changes) > MAX_ENTRIES
    )


def count_progress(staff_ids: Collection[int], start: date, end: date) -> dict[int, ProgressCounts]:
    """Every count for each of the Staff members between the days (both included).

    Only records linked to a Staff member count, so a Platform Admin's count for nobody.
    """
    found = {staff_id: ProgressCounts() for staff_id in staff_ids}
    moments = _between(start, end)
    moves = HistoryEntry.objects.filter(
        moments("at"), kind=HistoryKind.STAGE_CHANGED, by_staff_id__in=staff_ids
    ).values_list("by_staff_id", "before", "after")
    for staff_id, (forward, lost) in tally_stage_moves(
        StageMoveBy(int(by), before, after) for by, before, after in moves
    ).items():
        found[staff_id].values[Count.MOVED_FORWARD] = forward
        found[staff_id].values[Count.STUDENTS_LOST] = lost
    simple: list[tuple[Count, QuerySet[Any], str]] = [
        (Count.APPLICATIONS_MADE, Application.objects.filter(moments("created_at")), "created_by"),
        (Count.VISAS_APPLIED_FOR, Visa.objects.filter(moments("created_at")), "created_by"),
        (Count.TASKS_DONE, Task.objects.filter(moments("done_at"), is_done=True), "done_by"),
        (Count.VISITORS_REGISTERED, Visit.objects.filter(moments("at")), "recorded_by"),
        (
            Count.NEW_LEADS,
            Visit.objects.filter(moments("at"), kind=VisitKind.NEW),
            "recorded_by",
        ),
    ]
    for count, rows, by in simple:
        for staff_id, number in _per_person(rows, by, staff_ids):
            found[staff_id].values[count] = number
    _count_payments(found, moments("recorded_at"))
    _count_attendance(found, start, end)
    return found


def _between(start: date, end: date) -> _Moments:
    return _Moments(
        timezone.make_aware(datetime.combine(start, time.min)),
        timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min)),
    )


@dataclass(frozen=True)
class _Moments:
    """From the start of the first day to the start of the day after the last, office time."""

    since: datetime
    until: datetime

    def __call__(self, name: str) -> Q:
        return Q(**{f"{name}__gte": self.since, f"{name}__lt": self.until})


def _per_person(
    rows: QuerySet[Any],
    by: str,
    staff_ids: Collection[int],
) -> Iterable[tuple[int, int]]:
    counted = (
        rows.filter(**{f"{by}_id__in": staff_ids})
        .order_by()
        .values_list(f"{by}_id")
        .annotate(number=Counted("pk"))
        .values_list(f"{by}_id", "number")
    )
    return ((int(staff_id), int(number)) for staff_id, number in counted)


def _count_payments(found: dict[int, ProgressCounts], moments: Q) -> None:
    rows = (
        Payment.objects.filter(moments, recorded_by_id__in=found)
        .order_by()
        .values_list("recorded_by_id")
        .annotate(number=Counted("pk"), amount=Sum("amount"))
        .values_list("recorded_by_id", "number", "amount")
    )
    for staff_id, number, amount in rows:
        found[int(staff_id)].values[Count.PAYMENTS_TAKEN] = int(number)
        found[int(staff_id)].payments_amount = int(amount or 0)


def _count_attendance(found: dict[int, ProgressCounts], start: date, end: date) -> None:
    rows = Attendance.objects.filter(
        day__gte=start, day__lte=end, marked_by_id__in=found
    ).values_list("marked_by_id", "batch_id", "day")
    marks: Counter[int] = Counter()
    class_days: set[tuple[int, int, date]] = set()
    for staff_id, batch_id, day in rows:
        marks[int(staff_id)] += 1
        class_days.add((int(staff_id), batch_id, day))
    for staff_id, number in marks.items():
        found[staff_id].values[Count.ATTENDANCE_MARKED] = number
    for staff_id, _, _ in class_days:
        found[staff_id].values[Count.CLASSES_TAKEN] += 1


def _changes(staff: Staff, start: date, end: date, *, batches_on: bool) -> list[Change]:
    """Their changes, newest first, at most one more than the list shows (to know if more)."""
    moments, limit = _between(start, end), MAX_ENTRIES + 1
    local = timezone.localdate
    found = [
        Change(e.kind, local(e.at), e.at, e.before, e.after, e.detail, e.student.public_id, sn)
        for e, sn in _with_name(
            HistoryEntry.objects.filter(moments("at"), by_staff=staff), limit, "at"
        )
    ]
    found += [
        Change(VISIT_LOGGED, local(v.at), v.at, v.kind, v.purpose, "", v.student.public_id, sn)
        for v, sn in _with_name(Visit.objects.filter(moments("at"), recorded_by=staff), limit, "at")
    ]
    applications = Application.objects.filter(moments("created_at"), created_by=staff)
    found += [
        Change(
            APPLICATION_MADE,
            local(a.created_at),
            a.created_at,
            after=a.course.university.name,
            detail=a.course.name,
            student_id=a.student.public_id,
            student_name=sn,
        )
        for a, sn in _with_name(applications.select_related("course__university"), limit)
    ]
    visas = Visa.objects.filter(moments("created_at"), created_by=staff)
    found += [
        Change(
            VISA_APPLIED_FOR,
            local(v.created_at),
            v.created_at,
            after=v.application.course.university.name,
            student_id=v.student.public_id,
            student_name=sn,
        )
        for v, sn in _with_name(visas.select_related("application__course__university"), limit)
    ]
    if batches_on:
        found += _class_days(staff, start, end, limit)
    found.sort(key=lambda change: (change.day, change.at), reverse=True)
    return found[:limit]


def _with_name[T: HistoryEntry | Visit | Application | Visa](
    rows: QuerySet[T], limit: int, moment: str = "created_at"
) -> list[tuple[T, str]]:
    """The newest rows, each with its Student's name, in one query."""
    newest = rows.select_related("student").order_by(f"-{moment}", "-pk")[:limit]
    return [(row, str(row.student.name)) for row in newest]


def _class_days(staff: Staff, start: date, end: date, limit: int) -> list[Change]:
    rows = (
        Attendance.objects.filter(day__gte=start, day__lte=end, marked_by=staff)
        .order_by()
        .values_list("batch_id", "batch__name", "day")
        .annotate(marks=Counted("pk"), last=Max("marked_at"))
        .order_by("-day", "-last")[:limit]
    )
    return [
        Change(ATTENDANCE_MARKED, day, last, after=str(name), detail=str(marks))
        for _, name, day, marks, last in rows
    ]


def _by_day(changes: list[Change]) -> list[Day]:
    days: list[Day] = []
    for change in changes:
        if not days or days[-1].day != change.day:
            days.append(Day(change.day, []))
        days[-1].changes.append(change)
    return days
