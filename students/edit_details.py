"""Edit details: save every field and the three lists together, or nothing (issue 81).

Call inside the Consultancy's schema. Permission checks are the views' job, through
`access.policy`. Details and list rows aren't tracked in History (spec 45), exactly as the
single-section edits weren't.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from django.db import models, transaction

from staff.models import Staff
from students.exceptions import StudentChangedError
from students.models import AcademicRecord, Student, TestScore, WorkExperience
from students.profile import (
    ACADEMIC_RECORD_FIELDS,
    TEST_SCORE_FIELDS,
    WORK_EXPERIENCE_FIELDS,
    apply_changes,
    move_details_version_on,
    store_details,
)

logger = logging.getLogger(__name__)

Row = Mapping[str, object]


@dataclass(frozen=True)
class EditedDetails:
    """One checked save from the Edit details page.

    `version` is the Student's `details_version` when the page was opened. Each list is whole:
    a row with an `id` changes that stored row, one without is added, and a stored row left out
    is removed.
    """

    version: int
    details: Mapping[str, object]
    academic_records: Sequence[Row]
    test_scores: Sequence[Row]
    work_experience: Sequence[Row]


def save_details(student: Student, edited: EditedDetails, by: Staff) -> None:
    """Save everything in one transaction.

    Raises:
        StudentChangedError: Someone changed these details since the page was opened.
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        if locked.details_version != edited.version:
            raise StudentChangedError
        if edited.details:
            store_details(locked, edited.details)
        _sync_records(locked, edited.academic_records)
        _sync_scores(locked, edited.test_scores)
        _sync_jobs(locked, edited.work_experience)
        move_details_version_on(locked)
    # Only ids and counts: the details are personal data and never logged.
    logger.info(
        "Student details saved: student_id=%s, by_staff_id=%s, academic_records=%s, "
        "test_scores=%s, work_experience=%s",
        student.pk,
        by.pk,
        len(edited.academic_records),
        len(edited.test_scores),
        len(edited.work_experience),
    )


def _sync_records(student: Student, rows: Sequence[Row]) -> None:
    stored = AcademicRecord.objects.filter(student=student)
    _sync(stored, lambda: AcademicRecord(student=student), rows, ACADEMIC_RECORD_FIELDS)


def _sync_scores(student: Student, rows: Sequence[Row]) -> None:
    stored = TestScore.objects.filter(student=student)
    _sync(stored, lambda: TestScore(student=student), rows, TEST_SCORE_FIELDS)


def _sync_jobs(student: Student, rows: Sequence[Row]) -> None:
    stored = WorkExperience.objects.filter(student=student)
    _sync(stored, lambda: WorkExperience(student=student), rows, WORK_EXPERIENCE_FIELDS)


def _sync[Entry: models.Model](
    stored: models.QuerySet[Entry],
    new: Callable[[], Entry],
    rows: Sequence[Row],
    fields: frozenset[str],
) -> None:
    """Make the Student's `stored` rows exactly `rows`: change, add and remove."""
    kept = [row["id"] for row in rows if "id" in row]
    stored.exclude(pk__in=kept).delete()
    by_id = {entry.pk: entry for entry in stored.filter(pk__in=kept)}
    for row in rows:
        entry = by_id[row["id"]] if "id" in row else new()
        apply_changes(entry, {key: value for key, value in row.items() if key != "id"}, fields)
        entry.save()
