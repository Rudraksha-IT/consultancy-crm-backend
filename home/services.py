"""The Homes' Students: their progress, and how many are at each Stage (issue 33)."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Count, F, Q, QuerySet
from django.db.models.functions import Lower

from applications.models import Application, Visa
from applications.statuses import ApplicationStatus, VisaStatus
from documents.checklist import Checklist
from documents.services import checklists_of
from home.progress import ApplicationAt, Progress, VisaAt, progress_of
from students.models import Stage, Student

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StudentProgress:
    """One Student on a Home: how far along they are and their checklist."""

    student: Student
    progress: Progress
    checklist: Checklist


def progress_of_students(students: QuerySet[Student]) -> list[StudentProgress]:
    """The Students (not Lost), furthest along first, then by name.

    A fixed number of queries however many Students there are: the Students, their Applications,
    their Visas, and their checklists.
    """
    found = list(
        students.exclude(stage=Stage.LOST)
        .select_related("assigned_counsellor")
        .order_by(Lower("name"), "pk")
    )
    ids = [student.pk for student in found]
    applications = _applications_by_student(ids)
    visas = _visas_by_student(ids)
    checklists = checklists_of(ids)
    rows = [
        StudentProgress(
            student,
            progress_of(
                Stage(student.stage),
                applications.get(student.pk, []),
                visas.get(student.pk, []),
                student.lost_reason,
            ),
            checklists[student.pk],
        )
        for student in found
    ]
    logger.debug("progress built: students=%s", len(rows))
    # sorted() keeps the name order among Students equally far along.
    return sorted(rows, key=lambda row: -row.progress.percent)


def progress_of_student(student: Student) -> Progress:
    """How far along one Student is, Lost included, and their next step."""
    return progress_of(
        Stage(student.stage),
        _applications_by_student([student.pk]).get(student.pk, []),
        _visas_by_student([student.pk]).get(student.pk, []),
        student.lost_reason,
    )


def stage_counts() -> dict[Stage, int]:
    """How many Students are at each Stage, Lost included, in one query."""
    counted = dict(
        Student.objects.order_by()
        .values_list("stage")
        .annotate(count=Count("pk"))
        .values_list("stage", "count")
    )
    return {stage: int(counted.get(stage.value, 0)) for stage in Stage}


def _applications_by_student(ids: list[int]) -> dict[int, list[ApplicationAt]]:
    rows = (
        Application.objects.filter(student_id__in=ids)
        .order_by("pk")
        .annotate(is_chosen=Q(student__chosen_offer=F("pk")))
        .values_list("student_id", "status", "course__university__name", "is_chosen")
    )
    found: dict[int, list[ApplicationAt]] = defaultdict(list)
    for student_id, status, university, is_chosen in rows:
        found[student_id].append(
            ApplicationAt(ApplicationStatus(status), university, is_chosen=bool(is_chosen))
        )
    return found


def _visas_by_student(ids: list[int]) -> dict[int, list[VisaAt]]:
    """Each Student's Visas, oldest first, with their Status and appointments."""
    visas = (
        Visa.objects.filter(student_id__in=ids)
        .order_by("pk")
        .only("student_id", "status", "biometrics_at", "medical_at", "interview_at")
    )
    found: dict[int, list[VisaAt]] = defaultdict(list)
    for visa in visas:
        found[visa.student_id].append(VisaAt(VisaStatus(visa.status), visa.dates))
    return found
