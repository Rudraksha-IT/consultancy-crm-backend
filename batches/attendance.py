"""Attendance (spec 85-86, 86a): each Student's mark for each day of a Batch, and its totals.

Call inside the Consultancy's schema. Who may read or take it is the views' job, through
`access.policy`. Attendance is not tracked in History (the spec tracks Stage, Note, Payment and
Transfer changes only); who last marked a day is kept on each mark.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from batches.exceptions import DayOutsideBatchError, NotInBatchError
from batches.models import ATTENDED_MARKS, Attendance, Batch, BatchMembership, Mark
from staff.actors import stored_staff
from staff.models import Staff
from students.models import Student

logger = logging.getLogger(__name__)

# How many of a class's latest marked days a Student's profile shows (the prototype's dots).
RECENT_DAYS = 12


def attended_percent(attended: int, marked: int) -> int | None:
    """The share of `marked` class days attended, as a whole percent rounded half up.

    None when no day is marked yet, so "not started" never reads as 0%.
    """
    if marked == 0:
        return None
    return (attended * 200 + marked) // (marked * 2)


def average_percent(percents: Iterable[int | None]) -> int | None:
    """The average of the shares that exist, rounded half up; None when there are none."""
    known = [percent for percent in percents if percent is not None]
    if not known:
        return None
    return (sum(known) * 2 + len(known)) // (len(known) * 2)


def check_class_day(batch: Batch, day: date) -> None:
    """Make sure `day` is one of the Batch's days.

    Raises:
        DayOutsideBatchError: It's before the Batch starts or after it ends.
    """
    if not batch.starts_on <= day <= batch.ends_on:
        raise DayOutsideBatchError


def marks_on(batch: Batch, day: date) -> dict[int, str]:
    """Each marked Student's mark on `day`, by Student id."""
    rows = Attendance.objects.filter(batch=batch, day=day).values_list("student_id", "mark")
    return dict(rows)


def take_attendance(batch: Batch, day: date, marks: Mapping[int, Mark], by: Staff) -> None:
    """Save the day's marks for the Batch's Students; any Student left out is unmarked that day.

    Raises:
        DayOutsideBatchError: `day` isn't one of the Batch's days.
        NotInBatchError: A Student marked isn't in the Batch.
    """
    check_class_day(batch, day)
    now = timezone.now()
    with transaction.atomic():
        # One save of a class at a time, so two Teachers' saves never mix.
        Batch.objects.select_for_update().filter(pk=batch.pk).first()
        members = set(
            BatchMembership.objects.filter(batch=batch).values_list("student_id", flat=True)
        )
        if not marks.keys() <= members:
            raise NotInBatchError
        Attendance.objects.filter(batch=batch, day=day, student_id__in=members).exclude(
            student_id__in=marks.keys()
        ).delete()
        Attendance.objects.bulk_create(
            [
                Attendance(
                    batch=batch,
                    student_id=student_id,
                    day=day,
                    mark=mark,
                    marked_by=stored_staff(by),
                    marked_at=now,
                )
                for student_id, mark in marks.items()
            ],
            update_conflicts=True,
            unique_fields=("batch", "student", "day"),
            update_fields=("mark", "marked_by", "marked_at"),
        )
    logger.info(
        "Attendance taken: batch_id=%s, day=%s, marked=%s, by_staff_id=%s",
        batch.pk,
        day.isoformat(),
        len(marks),
        by.pk,
    )


def percents(attendance: QuerySet[Attendance]) -> dict[tuple[int, int], int | None]:
    """Each (Batch id, Student id)'s share of class days attended, from `attendance`."""
    rows = (
        attendance.order_by()
        .values("batch_id", "student_id")
        .annotate(marked=Count("pk"), attended=Count("pk", filter=Q(mark__in=ATTENDED_MARKS)))
    )
    return {
        (row["batch_id"], row["student_id"]): attended_percent(row["attended"], row["marked"])
        for row in rows
    }


def percents_in(batch: Batch) -> dict[int, int | None]:
    """Each marked Student's share of this Batch's class days, by Student id."""
    shares = percents(Attendance.objects.filter(batch=batch))
    return {student_id: share for (_, student_id), share in shares.items()}


def percents_of(students: Iterable[Student]) -> dict[int, int | None]:
    """Each Student's share across the Batches they're in now, averaged (spec 86a)."""
    ids = [student.pk for student in students]
    current = set(
        BatchMembership.objects.filter(student_id__in=ids).values_list("batch_id", "student_id")
    )
    shares: defaultdict[int, list[int | None]] = defaultdict(list)
    for key, share in percents(Attendance.objects.filter(student_id__in=ids)).items():
        if key in current:
            shares[key[1]].append(share)
    return {pk: average_percent(shares[pk]) for pk in ids}


@dataclass(frozen=True)
class StudentClass:
    """One Batch a Student is in, with how their Attendance in it is going (spec 86)."""

    batch: Batch
    attended_percent: int | None
    # The Batch's latest marked days, oldest first, with the Student's mark (None if unmarked).
    recent_days: list[tuple[date, str | None]]


def classes_of(student: Student) -> list[StudentClass]:
    """The Batches the Student is in, by name, each with their Attendance."""
    batches = Batch.objects.filter(memberships__student=student).order_by("name", "pk")
    shares = percents(Attendance.objects.filter(student=student))
    return [
        StudentClass(
            batch=batch,
            attended_percent=shares.get((batch.pk, student.pk)),
            recent_days=_recent_days(batch, student),
        )
        for batch in batches
    ]


def _recent_days(batch: Batch, student: Student) -> list[tuple[date, str | None]]:
    days = list(
        Attendance.objects.filter(batch=batch)
        .order_by("-day")
        .values_list("day", flat=True)
        .distinct()[:RECENT_DAYS]
    )
    mine = dict(
        Attendance.objects.filter(batch=batch, student=student, day__in=days).values_list(
            "day", "mark"
        )
    )
    return [(day, mine.get(day)) for day in reversed(days)]
