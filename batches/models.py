"""Batches: a test-prep class with one Teacher (GLOSSARY: Batch, shown on screen as "class")."""

from __future__ import annotations

from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from staff.models import Staff
from students.models import Student

NAME_MAX_LENGTH = 80
TIME_SLOT_MAX_LENGTH = 40
TEST_TYPE_MAX_LENGTH = 20


class TestType(models.TextChoices):
    """The test a Batch prepares for (the prototype's list)."""

    # Not a pytest test class, despite its name.
    __test__ = False

    IELTS = "ielts", "IELTS"
    PTE = "pte", "PTE"
    TOEFL = "toefl", "TOEFL"
    DUOLINGO = "duolingo", "Duolingo"
    JAPANESE = "japanese", "Japanese (NAT/JLPT)"
    SAT = "sat", "SAT"


class Batch(models.Model):
    """One class (spec 83): a test type, a Teacher, a daily time slot, start and end dates.

    Batches are never deleted, and Staff never are, so a Batch keeps its Teacher's name after they
    leave.
    """

    name = models.CharField(max_length=NAME_MAX_LENGTH)
    test_type = models.CharField(max_length=TEST_TYPE_MAX_LENGTH, choices=TestType.choices)
    teacher = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="batches")
    # Free text as the office writes it, e.g. "7:00-8:30 AM".
    time_slot = models.CharField(max_length=TIME_SLOT_MAX_LENGTH)
    starts_on = models.DateField()
    ends_on = models.DateField()
    students = models.ManyToManyField(Student, through="BatchMembership", related_name="batches")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("starts_on", "name", "pk")
        indexes = (models.Index(fields=("teacher", "ends_on"), name="batches_teacher_ends"),)
        constraints = (
            models.CheckConstraint(
                condition=Q(ends_on__gte=F("starts_on")), name="batches_ends_after_start"
            ),
            models.CheckConstraint(
                condition=Q(test_type__in=TestType.values), name="batches_known_test_type"
            ),
        )

    def __str__(self) -> str:
        return self.name


class BatchMembership(models.Model):
    """One Student in one Batch (spec 84)."""

    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="memberships")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="memberships")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("batch", "student"), name="batches_one_membership"),
        )

    def __str__(self) -> str:
        return f"{self.student.name} in {self.batch.name}"


class Mark(models.TextChoices):
    """How a Student was on one class day (GLOSSARY: Attendance)."""

    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"
    LATE = "late", "Late"
    LEAVE = "leave", "Leave"


# The marks that count as attending (the prototype's rule): Present and Late.
ATTENDED_MARKS = (Mark.PRESENT, Mark.LATE)
MARK_MAX_LENGTH = 10


class Attendance(models.Model):
    """One Student's mark for one day of one Batch (spec 85), taken by its Teacher or an Admin.

    Kept when the Student leaves the Batch, so their record comes back if they rejoin.
    """

    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="attendance")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="attendance")
    day = models.DateField()
    mark = models.CharField(max_length=MARK_MAX_LENGTH, choices=Mark.choices)
    # None when a Platform Admin did it during Support Access; the name says who (issue 27).
    marked_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    marked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=("batch", "student", "day"), name="batches_one_mark_per_day"
            ),
            models.CheckConstraint(condition=Q(mark__in=Mark.values), name="batches_known_mark"),
        )
        indexes = (models.Index(fields=("batch", "day"), name="batches_attendance_day"),)

    def __str__(self) -> str:
        return f"{self.student.name} on {self.day.isoformat()}: {self.get_mark_display()}"
