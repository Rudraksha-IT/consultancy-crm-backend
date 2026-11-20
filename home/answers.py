"""How the Homes' endpoints answer (issue 33)."""

from __future__ import annotations

from applications.answers import moment_text
from home.services import StudentProgress
from home.waiting import Waiting
from staff.models import Staff
from students.models import Stage


def _person(staff: Staff | None) -> dict[str, object] | None:
    return None if staff is None else {"id": staff.pk, "name": staff.name}


def progress_row(row: StudentProgress) -> dict[str, object]:
    """One Student's progress, next step and document counts."""
    student = row.student
    counts = row.checklist.counts
    return {
        "student": {
            "id": student.public_id,
            "name": student.name,
            "stage": student.stage,
            "country": student.country,
            "assigned_counsellor": _person(student.assigned_counsellor),
        },
        "percent": row.progress.percent,
        "next_step": row.progress.next_step,
        "is_done": row.progress.is_done,
        "documents": {
            "done": row.checklist.done,
            "total": row.checklist.total,
            "rejected": counts.rejected,
            "waiting": counts.waiting,
            "missing": counts.missing,
        },
    }


def pipeline_answer(counts: dict[Stage, int]) -> dict[str, object]:
    """How many Students are at each Stage, by the Stage's API value."""
    return {"counts": {stage.value: count for stage, count in counts.items()}}


def waiting_answer(waiting: Waiting) -> dict[str, object]:
    """What waits on the viewer: counts, and the first few Visa appointments coming up."""
    return {
        "documents_waiting": waiting.documents,
        "transfer_requests_waiting": waiting.transfer_requests,
        "documents_in_bin": waiting.documents_in_bin,
        "work_from_home_requests": {
            "count": waiting.work_from_home_requests,
            "first_staff_id": waiting.work_from_home_first_staff_id,
        },
        "visa_appointments": {
            "count": waiting.appointment_count,
            "results": [
                {
                    "visa_id": one.visa.pk,
                    "student": {"id": one.visa.student.public_id, "name": one.visa.student.name},
                    "kind": one.booked.kind.value,
                    "at": moment_text(one.booked.at),
                }
                for one in waiting.appointments
            ],
        },
    }
