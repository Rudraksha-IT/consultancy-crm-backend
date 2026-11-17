"""Public services for Visits. Call them inside the Consultancy's schema.

Permission checks are the views' job, through `access.policy`.
"""

from __future__ import annotations

import logging
from datetime import date

from django.db.models import QuerySet

from staff.actors import actor_name, stored_staff
from staff.models import Staff
from students.models import Student
from visits.models import Visit, VisitKind, VisitReason

FIRST_VISIT_PURPOSE = "First visit"
# Today's timeline is one office's day; a cap keeps one answer small whatever happens.
MOST_VISITS_IN_A_DAY = 200

logger = logging.getLogger(__name__)


def log_first_visit(student: Student, by: Staff) -> Visit:
    """Log a new visitor's first Visit, made now (spec 23b)."""
    return _log(student, VisitKind.NEW, FIRST_VISIT_PURPOSE, by)


def log_follow_up(student: Student, reason: VisitReason, details: str, by: Staff) -> Visit:
    """Log a returning Student's Visit, made now, with why they came (spec 23a)."""
    purpose = f"{reason.label}: {details}" if details else str(reason.label)
    return _log(student, VisitKind.FOLLOW_UP, purpose, by)


def visits_of(student: Student) -> QuerySet[Visit]:
    """The Student's Visits, newest first."""
    return Visit.objects.filter(student=student).order_by("-at", "-pk")


def visits_on(day: date) -> QuerySet[Visit]:
    """The office's Visits on `day` (in the office's time zone), newest first."""
    return Visit.objects.filter(at__date=day).select_related("student").order_by("-at", "-pk")


def _log(student: Student, kind: VisitKind, purpose: str, by: Staff) -> Visit:
    visit = Visit.objects.create(
        student=student,
        kind=kind,
        purpose=purpose,
        recorded_by=stored_staff(by),
        recorded_by_name=actor_name(by),
    )
    logger.info(
        "Visit logged: visit_id=%s, student_id=%s, kind=%s, by_staff_id=%s",
        visit.pk,
        student.pk,
        kind.value,
        by.pk,
    )
    return visit
