"""Batches as the API returns them. Built by hand so nothing extra ever leaks out.

Teachers see a class's Students by name only: never their finances or Applications (spec 22).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from access.policy import BatchAction, why_cannot_on_batches, why_cannot_take_attendance
from batches.attendance import StudentClass, marks_on, percents_in
from batches.models import Batch, BatchMembership
from batches.services import timing
from staff.models import Staff
from students.models import Student


def batch_answer(batch: Batch, viewer: Staff, today: date) -> dict[str, object]:
    """One class as a list shows it; `batch` comes from `with_counts`."""
    return {
        "id": batch.pk,
        "name": batch.name,
        "test_type": batch.test_type,
        "teacher": {"id": batch.teacher.pk, "name": batch.teacher.name},
        "time_slot": batch.time_slot,
        "starts_on": batch.starts_on.isoformat(),
        "ends_on": batch.ends_on.isoformat(),
        "timing": timing(batch, today).value,
        "student_count": getattr(batch, "student_count", 0),
        "attendance_taken_today": getattr(batch, "attendance_taken_today", False),
        "can_change": why_cannot_on_batches(viewer, BatchAction.CHANGE) is None,
    }


def batch_with_students_answer(
    batch: Batch, students: Iterable[Student], viewer: Staff, today: date
) -> dict[str, object]:
    """One class with its Students by name, each with their share of class days attended."""
    shares = percents_in(batch)
    return {
        **batch_answer(batch, viewer, today),
        "students": [
            {"id": student.pk, "name": student.name, "attended_percent": shares.get(student.pk)}
            for student in students
        ],
    }


def enrolled_student_answer(student: Student, attended: int | None) -> dict[str, object]:
    """One Student with the classes they're in and their share attended across them (spec 86a)."""
    memberships: Iterable[BatchMembership] = student.memberships.all()
    return {
        "id": student.pk,
        "name": student.name,
        "batches": [{"id": m.batch.pk, "name": m.batch.name} for m in memberships],
        "attended_percent": attended,
    }


def attendance_day_answer(
    batch: Batch, day: date, students: Iterable[Student], viewer: Staff
) -> dict[str, object]:
    """One class day: each Student in the class, by name, with their mark or None (spec 85)."""
    marks = marks_on(batch, day)
    return {
        "day": day.isoformat(),
        "can_mark": why_cannot_take_attendance(viewer, batch.teacher_id) is None,
        "marks": [{"student": student.pk, "mark": marks.get(student.pk)} for student in students],
    }


def student_class_answer(entry: StudentClass) -> dict[str, object]:
    """One class a Student is in, with their Attendance in it (spec 86)."""
    return {
        "id": entry.batch.pk,
        "name": entry.batch.name,
        "time_slot": entry.batch.time_slot,
        "attended_percent": entry.attended_percent,
        "recent_days": [{"day": day.isoformat(), "mark": mark} for day, mark in entry.recent_days],
    }
