"""The Reports' queries (issues 28, 29, 30, 53). Read-only; call inside the Consultancy's schema.

Days and months are the office's local ones (settings.TIME_ZONE).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from django.db.models import Count, Q, QuerySet, Sum
from django.utils import timezone

from applications.models import Visa
from applications.statuses import VisaStatus
from batches.models import ATTENDED_MARKS, Attendance, Batch
from commission.models import Commission, CommissionStatus
from expenses.models import Expense, ExpenseCategory
from history.models import HistoryEntry, HistoryKind
from payments.models import Payment
from reports.calculations import (
    Amount,
    ClassMark,
    GrantedVisa,
    MoneyMonth,
    MonthCounts,
    NewVisitor,
    StageMove,
    by_category,
    class_attendance,
    countries_granted,
    countries_wanted,
    money_months,
    months_back,
    sorted_sources,
    stage_from_history,
    student_months,
)
from staff.employees import COUNSELLING_ROLES
from staff.models import Role, Staff, StaffStatus
from staff_progress.counting import (
    COUNSELLOR_COUNTS,
    FRONT_DESK_COUNTS,
    TEACHER_COUNTS,
    counted_from,
)
from staff_progress.counting import Count as ProgressCount
from staff_progress.services import ProgressCounts, count_progress
from students.models import Country, Source
from visits.models import Visit, VisitKind

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProgressRow:
    """One person on Reports → Staff Progress: their counts, and for counsellors their Students."""

    staff: Staff
    counts: ProgressCounts
    students: int = 0
    visas_granted: int = 0

    def shows_something(self, columns: tuple[ProgressCount, ...]) -> bool:
        """Whether they have Students now, or any of the columns' counts above zero."""
        return self.students > 0 or any(self.counts.values[count] for count in columns)


@dataclass(frozen=True)
class StaffProgressReport:
    """The Counsellors, Front Desk and Teachers tables, and counts whose recording began later."""

    counsellors: list[ProgressRow]
    front_desk: list[ProgressRow]
    teachers: list[ProgressRow]
    counted_from: dict[ProgressCount, date]


@dataclass(frozen=True)
class MoneyReport:
    """Money in and out each month, and what the money out went on over all of them."""

    months: list[MoneyMonth]
    money_out_by_category: list[tuple[ExpenseCategory, int]]


@dataclass(frozen=True)
class CountryReport:
    """Visas granted per country and the countries new visitors want, most first."""

    visas_granted: list[tuple[Country, int]]
    wanted: list[tuple[Country, int]]


@dataclass(frozen=True)
class ClassRow:
    """One class on the Classes report."""

    batch: Batch
    students: int
    attended_percent: int | None


def _start_of(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


def _first_day(month: str) -> date:
    year, number = month.split("-")
    return date(int(year), int(number), 1)


def _moves(entries: Q) -> list[StageMove]:
    rows = (
        HistoryEntry.objects.filter(entries, kind=HistoryKind.STAGE_CHANGED)
        .order_by("at", "pk")
        .values_list("student_id", "at", "before", "after")
    )
    moves = []
    for student_id, moment, before, after in rows:
        was, now = stage_from_history(before), stage_from_history(after)
        if was is not None and now is not None:
            moves.append(StageMove(student_id, timezone.localdate(moment), was, now))
    return moves


def _granted(start: date | None = None, end: date | None = None) -> QuerySet[Visa]:
    """Visas granted (and still Granted), on their granted day between the days if given."""
    visas = Visa.objects.filter(status=VisaStatus.GRANTED, granted_on__isnull=False)
    if start is not None:
        visas = visas.filter(granted_on__gte=start)
    if end is not None:
        visas = visas.filter(granted_on__lte=end)
    return visas.order_by()


def student_report(month_count: int) -> list[MonthCounts]:
    """What happened to Students in each of the last `month_count` months, this one last."""
    months = months_back(timezone.localdate(), month_count)
    since = _start_of(_first_day(months[0]))
    visit_days = [
        timezone.localdate(moment)
        for moment in Visit.objects.filter(kind=VisitKind.NEW, at__gte=since).values_list(
            "at", flat=True
        )
    ]
    granted_days = [
        day
        for day in _granted(_first_day(months[0])).values_list("granted_on", flat=True)
        if day is not None
    ]
    logger.debug("report built: name=students months=%s", month_count)
    # Every earlier Stage change too, so nobody who signed up before is counted again.
    return student_months(months, visit_days, _moves(Q()), granted_days)


def _between(start: date, end: date, field: str) -> Q:
    return Q(**{f"{field}__gte": _start_of(start), f"{field}__lt": _start_of(end + timedelta(1))})


def country_report(month_count: int) -> CountryReport:
    """Visas granted and new visitors' wanted countries, the last `month_count` months to today.

    A Visa counts on the day it was granted, by its University's country, while it is Granted.
    A new visitor is a Visit of kind New, as on the Students report; each Student counts once.
    """
    today = timezone.localdate()
    start = _first_day(months_back(today, month_count)[0])
    granted = (
        Visa.objects.filter(status=VisaStatus.GRANTED, granted_on__gte=start, granted_on__lte=today)
        .order_by()
        .values_list("granted_on", "application__course__university__country")
    )
    visitors = (
        Visit.objects.filter(_between(start, today, "at"), kind=VisitKind.NEW)
        .order_by()
        .values_list("student_id", "at", "student__country")
    )
    return CountryReport(
        visas_granted=countries_granted(
            (GrantedVisa(on, Country(country)) for on, country in granted if on), start, today
        ),
        wanted=countries_wanted(
            (
                NewVisitor(student_id, timezone.localdate(moment), Country(country))
                for student_id, moment, country in visitors
            ),
            start,
            today,
        ),
    )


def source_report(start: date, end: date) -> list[tuple[Source, int]]:
    """How many new visitors came between the days (both included), per Source.

    A new visitor is a Visit of kind New, as on the Students report, so both count the same people.
    """
    counted = dict(
        Visit.objects.filter(_between(start, end, "at"), kind=VisitKind.NEW)
        .order_by()
        .values_list("student__source")
        .annotate(count=Count("pk"))
        .values_list("student__source", "count")
    )
    return sorted_sources({source: int(counted.get(source.value, 0)) for source in Source})


def staff_progress_report(start: date, end: date, *, batches_on: bool) -> StaffProgressReport:
    """Each person's Staff Progress counts between the days (both included), one table per Role.

    The counts are those of the Progress tab (`count_progress`), so the two always match.
    Counsellors (Admins and Counsellors, and anyone who still has Students assigned) also show
    their Students now and those of them granted a Visa between the days. Someone who has left is
    listed only while they have Students or something counted. Teachers only with classes on.
    """
    people = list(
        Staff.objects.exclude(status=StaffStatus.INVITED)
        .annotate(student_count=Count("assigned_students"))
        .order_by("name", "pk")
    )
    counts = count_progress([person.pk for person in people], start, end)
    granted = _granted_per_counsellor(start, end)
    tables: dict[str, list[ProgressRow]] = {"counsellors": [], "front_desk": [], "teachers": []}
    for person in people:
        for table, columns in _tables_of(person.role, person.student_count, batches_on=batches_on):
            row = ProgressRow(
                person,
                counts[person.pk],
                person.student_count if table == "counsellors" else 0,
                granted.get(person.pk, 0) if table == "counsellors" else 0,
            )
            if person.status == StaffStatus.ACTIVE or row.shows_something(columns):
                tables[table].append(row)
    since = {
        count: day for count in ProgressCount if (day := counted_from(count, start)) is not None
    }
    return StaffProgressReport(**tables, counted_from=since)


def _tables_of(
    role: str, students: int, *, batches_on: bool
) -> list[tuple[str, tuple[ProgressCount, ...]]]:
    """The tables a person is in, by their Role (and Students still assigned), with the columns."""
    tables: list[tuple[str, tuple[ProgressCount, ...]]] = []
    if role in COUNSELLING_ROLES or students > 0:
        tables.append(("counsellors", COUNSELLOR_COUNTS))
    if role == Role.FRONT_DESK:
        tables.append(("front_desk", FRONT_DESK_COUNTS))
    if role == Role.TEACHER and batches_on:
        tables.append(("teachers", TEACHER_COUNTS))
    return tables


def _granted_per_counsellor(start: date, end: date) -> dict[int, int]:
    """Per Assigned Counsellor now: how many of their Students were granted a Visa in the days."""
    rows = (
        _granted(start, end)
        .filter(student__assigned_counsellor__isnull=False)
        .values_list("student__assigned_counsellor_id")
        .annotate(students=Count("student_id", distinct=True))
        .values_list("student__assigned_counsellor_id", "students")
    )
    return {int(counsellor): int(students) for counsellor, students in rows}


def class_report(start: date, end: date) -> list[ClassRow]:
    """Each class running between the days (both included), by name, with its share attended.

    Students is who is in the class now; the share counts the class days between the days. Every
    Student marked between the days counts, also one who has since left the class.
    """
    batches = (
        Batch.objects.filter(starts_on__lte=end, ends_on__gte=start)
        .select_related("teacher")
        .annotate(student_count=Count("memberships"))
        .order_by("name", "pk")
    )
    marks = Attendance.objects.filter(
        batch__in=batches.values("pk"), day__gte=start, day__lte=end
    ).values_list("batch_id", "student_id", "mark")
    attended = {mark.value for mark in ATTENDED_MARKS}
    shares = class_attendance(
        ClassMark(batch_id, student_id, mark in attended) for batch_id, student_id, mark in marks
    )
    return [ClassRow(batch, batch.student_count, shares.get(batch.pk)) for batch in batches]


def _per_day(rows: QuerySet[Any], day: str) -> list[Amount]:
    """The rows' amounts added up per day, in the database."""
    sums = rows.order_by().values_list(day).annotate(amount=Sum("amount"))
    return [Amount(on, int(amount)) for on, amount in sums.values_list(day, "amount")]


def _days_of(months: list[str], field: str) -> Q:
    """Every day of the months (consecutive, oldest first)."""
    last = _first_day(months[-1])
    after = date(last.year + last.month // 12, last.month % 12 + 1, 1)
    return Q(**{f"{field}__gte": _first_day(months[0]), f"{field}__lt": after})


def money_report(month_count: int) -> MoneyReport:
    """Money in and out in each of the last `month_count` months, this one last.

    Money in is Payments (on the day paid) and Commission Received (on the day received); money
    out is Expenses (on the day spent), also added up by category over all the months.
    """
    months = months_back(timezone.localdate(), month_count)
    received = Commission.objects.filter(status=CommissionStatus.RECEIVED)
    expenses = Expense.objects.filter(_days_of(months, "spent_on")).order_by()
    per_category = expenses.values_list("category").annotate(amount=Sum("amount"))
    return MoneyReport(
        months=money_months(
            months,
            payments=_per_day(Payment.objects.filter(_days_of(months, "paid_on")), "paid_on"),
            commission=_per_day(received.filter(_days_of(months, "received_on")), "received_on"),
            expenses=_per_day(expenses, "spent_on"),
        ),
        money_out_by_category=by_category(
            (category, int(amount))
            for category, amount in per_category.values_list("category", "amount")
        ),
    )
