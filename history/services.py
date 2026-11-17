"""The only way History is written. Call inside the Consultancy's schema."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db.models import QuerySet

from history.models import HistoryEntry, HistoryKind
from staff.actors import is_platform_visitor, stored_staff
from staff.models import Staff
from students.models import Student

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Change:
    """What changed: the value before and after, and anything more to say (e.g. a reason)."""

    before: str = ""
    after: str = ""
    detail: str = ""


def record(student: Student, kind: HistoryKind, by: Staff, change: Change) -> HistoryEntry:
    """Add one entry to the Student's History, done now by `by`.

    A Platform Admin on a Support Access visit is recorded under their own name, marked as a
    Platform Admin, and never linked as Staff (spec 11).
    """
    entry = HistoryEntry.objects.create(
        student=student,
        kind=kind,
        by_staff=stored_staff(by),
        by_name=by.name,
        by_platform_admin=is_platform_visitor(by),
        before=change.before,
        after=change.after,
        detail=change.detail,
    )
    logger.debug("history recorded: kind=%s student_id=%s entry_id=%s", kind, student.pk, entry.pk)
    return entry


def history_of(student: Student) -> QuerySet[HistoryEntry]:
    """The Student's History, newest first."""
    return HistoryEntry.objects.filter(student=student).order_by("-at", "-pk")


def stage_changes_of(student: Student) -> QuerySet[HistoryEntry]:
    """Every Stage change of the Student, oldest first, for the Activity timeline (issue 82)."""
    return HistoryEntry.objects.filter(student=student, kind=HistoryKind.STAGE_CHANGED).order_by(
        "at", "pk"
    )
