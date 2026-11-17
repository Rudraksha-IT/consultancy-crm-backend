"""Public services for Batches (spec 83-84, 86a). Call inside the Consultancy's schema.

Who may see or change a Batch is the views' job, through `access.policy`. Batches are not tracked
in History (the spec tracks Stage, Note, Payment and Transfer changes only).
"""

from __future__ import annotations

import logging
from datetime import date

from django.db import IntegrityError, transaction
from django.db.models import (
    Case,
    Count,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Prefetch,
    QuerySet,
    Value,
    When,
)

from batches.exceptions import AlreadyInBatchError, LostStudentError
from batches.models import Attendance, Batch, BatchMembership
from batches.types import BatchDetails, BatchTiming
from staff.models import Role, Staff, StaffStatus
from students.models import Stage, Student

logger = logging.getLogger(__name__)


def active_teachers() -> QuerySet[Staff]:
    """Everyone who can teach a Batch: active Teachers, by name."""
    return Staff.objects.filter(role=Role.TEACHER, status=StaffStatus.ACTIVE).order_by("name", "pk")


def create_batch(details: BatchDetails, by: Staff) -> Batch:
    """Create an empty Batch."""
    batch = Batch.objects.create(**_fields(details))
    logger.info(
        "Batch created: batch_id=%s, teacher_id=%s, by_staff_id=%s",
        batch.pk,
        details.teacher.pk,
        by.pk,
    )
    return batch


def change_batch(batch_id: int, details: BatchDetails, by: Staff) -> None:
    """Replace what the Batch is; who is in it stays.

    Raises:
        Batch.DoesNotExist: There is no such Batch.
    """
    with transaction.atomic():
        batch = Batch.objects.select_for_update().get(pk=batch_id)
        for field, value in _fields(details).items():
            setattr(batch, field, value)
        batch.save()
    logger.info("Batch changed: batch_id=%s, by_staff_id=%s", batch.pk, by.pk)


def add_student(batch: Batch, student: Student, by: Staff) -> None:
    """Put the Student in the Batch.

    Raises:
        LostStudentError: The Student is Lost.
        AlreadyInBatchError: The Student is already in it.
    """
    if student.stage == Stage.LOST:
        raise LostStudentError
    try:
        with transaction.atomic():
            BatchMembership.objects.create(batch=batch, student=student)
    except IntegrityError as error:
        raise AlreadyInBatchError from error
    logger.info(
        "Student added to Batch: batch_id=%s, student_id=%s, by_staff_id=%s",
        batch.pk,
        student.pk,
        by.pk,
    )


def remove_student(batch: Batch, student_id: int, by: Staff) -> None:
    """Take the Student out of the Batch; the Student themselves stays.

    Raises:
        BatchMembership.DoesNotExist: The Student isn't in it.
    """
    BatchMembership.objects.get(batch=batch, student_id=student_id).delete()
    logger.info(
        "Student removed from Batch: batch_id=%s, student_id=%s, by_staff_id=%s",
        batch.pk,
        student_id,
        by.pk,
    )


def batches_for(staff: Staff, today: date) -> QuerySet[Batch]:
    """The Batches `staff` sees: running and upcoming ones by start, then finished, latest first.

    Teachers see only the Batches they teach (spec 22); everyone else allowed sees all.
    """
    batches = with_counts(Batch.objects.all(), today)
    if staff.role == Role.TEACHER:
        batches = batches.filter(teacher=staff)
    is_finished = When(ends_on__lt=today, then=Value(1))
    return batches.annotate(
        is_finished=Case(is_finished, default=Value(0), output_field=IntegerField()),
        current_start=Case(When(ends_on__gte=today, then=F("starts_on")), default=None),
        finished_end=Case(When(ends_on__lt=today, then=F("ends_on")), default=None),
    ).order_by(
        "is_finished",
        F("current_start").asc(nulls_last=True),
        F("finished_end").desc(nulls_last=True),
        "name",
        "pk",
    )


def with_counts(batches: QuerySet[Batch], today: date) -> QuerySet[Batch]:
    """`batches` with their Teacher, Student count and whether today's Attendance is taken.

    The annotations are `student_count` and `attendance_taken_today` (anyone marked; spec 85).
    """
    taken_today = Attendance.objects.filter(batch=OuterRef("pk"), day=today)
    return batches.select_related("teacher").annotate(
        student_count=Count("memberships", distinct=True),
        attendance_taken_today=Exists(taken_today),
    )


def members_of(batch: Batch) -> QuerySet[Student]:
    """The Students in the Batch, by name."""
    return Student.objects.filter(memberships__batch=batch).order_by("name", "pk")


def enrolled_students() -> QuerySet[Student]:
    """Every Student in at least one Batch, by name, with their Batches by name (spec 86a)."""
    memberships = BatchMembership.objects.select_related("batch").order_by("batch__name", "batch")
    return (
        Student.objects.filter(memberships__isnull=False)
        .distinct()
        .order_by("name", "pk")
        .prefetch_related(Prefetch("memberships", queryset=memberships))
    )


def timing(batch: Batch, today: date) -> BatchTiming:
    """Whether the Batch hasn't started, is running or has finished on `today`."""
    if today < batch.starts_on:
        return BatchTiming.UPCOMING
    return BatchTiming.FINISHED if batch.ends_on < today else BatchTiming.RUNNING


def _fields(details: BatchDetails) -> dict[str, object]:
    return {
        "name": details.name,
        "test_type": details.test_type,
        "teacher": details.teacher,
        "time_slot": details.time_slot,
        "starts_on": details.starts_on,
        "ends_on": details.ends_on,
    }
