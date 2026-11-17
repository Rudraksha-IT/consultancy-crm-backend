"""Public services for a Student's Stage and counsellor (spec 38-44). Call inside the schema.

Who may do these is the views' job, through `access.policy`. Each change locks the Student, checks
again what it depends on, and writes History in the same transaction.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import QuerySet

from applications.statuses import VisaStatus
from history.models import HistoryKind
from history.services import Change, record
from staff.models import Staff
from students.exceptions import SameCounsellorError, StageChangeRefusedError, StudentChangedError
from students.models import Stage, Student
from students.stages import manual_move_refusal
from transfer_requests.closing import close_waiting_for_student

logger = logging.getLogger(__name__)


def set_stage_by_hand(student: Student, stage: Stage, by: Staff, lost_reason: str = "") -> None:
    """Move the Student to `stage`; Lost keeps `lost_reason`, leaving Lost clears it.

    Raises:
        StageChangeRefusedError: The move can't be made by hand from where they are now.
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        before = Stage(locked.stage)
        refusal = manual_move_refusal(before, stage)
        if refusal is not None:
            raise StageChangeRefusedError(refusal)
        reason = lost_reason if stage == Stage.LOST else ""
        locked.stage = stage
        locked.lost_reason = reason
        locked.save(update_fields=["stage", "lost_reason"])
        change = Change(before=before.value, after=stage.value, detail=reason)
        record(locked, HistoryKind.STAGE_CHANGED, by, change)
    student.stage, student.lost_reason = stage, reason
    logger.info(
        "Stage changed: student_id=%s, from=%s, to=%s, by_staff_id=%s",
        student.pk,
        before.value,
        stage.value,
        by.pk,
    )


def assign_counsellor(student: Student, counsellor: Staff, by: Staff, detail: str = "") -> None:
    """Give a Student who has no counsellor their first one.

    A Transfer Request still waiting for them closes (spec 44e). `detail` goes into History, e.g.
    who asked and who approved.

    Raises:
        StudentChangedError: Someone gave them a counsellor in the meantime.
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        if locked.assigned_counsellor_id is not None:
            raise StudentChangedError
        _set_counsellor(locked, counsellor)
        change = Change(after=counsellor.name, detail=detail)
        record(locked, HistoryKind.COUNSELLOR_ASSIGNED, by, change)
    student.assigned_counsellor = counsellor
    logger.info(
        "Counsellor assigned: student_id=%s, counsellor_id=%s, by_staff_id=%s",
        student.pk,
        counsellor.pk,
        by.pk,
    )


def transfer_student(student: Student, counsellor: Staff, by: Staff, detail: str = "") -> None:
    """Hand the Student from their counsellor to `counsellor`, who alone may change them now.

    A Transfer Request still waiting for them closes (spec 44e). `detail` goes into History, e.g.
    who asked and who approved.

    Raises:
        SameCounsellorError: `counsellor` is already theirs.
        StudentChangedError: Their counsellor changed since the request was checked.
    """
    with transaction.atomic():
        locked = (
            Student.objects.select_for_update(of=("self",))
            .select_related("assigned_counsellor")
            .get(pk=student.pk)
        )
        previous = locked.assigned_counsellor
        if previous is None or previous.pk != student.assigned_counsellor_id:
            raise StudentChangedError
        if previous.pk == counsellor.pk:
            raise SameCounsellorError
        _set_counsellor(locked, counsellor)
        change = Change(before=previous.name, after=counsellor.name, detail=detail)
        record(locked, HistoryKind.TRANSFERRED, by, change)
    student.assigned_counsellor = counsellor
    logger.info(
        "Student transferred: student_id=%s, from_staff_id=%s, to_staff_id=%s, by_staff_id=%s",
        student.pk,
        previous.pk,
        counsellor.pk,
        by.pk,
    )


def students_still_assigned_to(staff: Staff) -> QuerySet[Student]:
    """The Students `staff` still counsels, by name.

    A Lost Student, or one at Visa with a granted Visa (the end of the journey), needs no
    counsellor any more (issue 74).
    """
    return (
        Student.objects.filter(assigned_counsellor=staff)
        .exclude(stage=Stage.LOST)
        .exclude(stage=Stage.VISA, visas__status=VisaStatus.GRANTED)
        .order_by("name", "pk")
        .distinct()
    )


def _set_counsellor(student: Student, counsellor: Staff) -> None:
    student.assigned_counsellor = counsellor
    student.save(update_fields=["assigned_counsellor"])
    close_waiting_for_student(student.pk)
