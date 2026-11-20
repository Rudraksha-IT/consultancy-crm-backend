"""Search results as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from batches.models import Batch
from staff.models import Staff
from students.models import Student


def student_result(student: Student) -> dict[str, object]:
    """A Student for someone who may open their profile: enough to tell two Sitas apart."""
    counsellor: Staff | None = student.assigned_counsellor
    return {
        "kind": "student",
        "id": student.public_id,
        "name": student.name,
        "phone": student.phone,
        "stage": student.stage,
        "assigned_counsellor": (
            None if counsellor is None else {"id": counsellor.pk, "name": counsellor.name}
        ),
    }


def class_student_result(student: Student) -> dict[str, object]:
    """A Student for their Teacher: only their name and the class they're in (spec 22)."""
    return {
        "kind": "class_student",
        "id": student.public_id,
        "name": student.name,
        "batch": {
            "id": getattr(student, "class_id", None),
            "name": getattr(student, "class_name", ""),
        },
    }


def batch_result(batch: Batch) -> dict[str, object]:
    """A class by name with its time slot."""
    return {"id": batch.pk, "name": batch.name, "time_slot": batch.time_slot}
