"""Tasks as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from datetime import date

from access.policy import why_cannot_link_student, why_cannot_mark_task_done
from staff.models import Staff
from tasks.models import Task
from tasks.services import timing


def task_answer(task: Task, viewer: Staff, today: date) -> dict[str, object]:
    """One Task as `viewer` sees it; Teachers never see which Student it's about (spec 22)."""
    student = task.student
    shows_student = student is not None and why_cannot_link_student(viewer) is None
    return {
        "id": task.pk,
        "title": task.title,
        "due_on": task.due_on.isoformat(),
        "priority": task.priority,
        "only_me": task.only_me,
        "timing": timing(task, today).value,
        "is_done": task.is_done,
        "done_at": None if task.done_at is None else task.done_at.isoformat(),
        "assignee": _person(task.assignee),
        # No id when a Platform Admin gave it (Support Access); the name says who.
        "created_by": {"id": task.created_by_id, "name": task.created_by_name},
        "student": {"id": student.public_id, "name": student.name}
        if shows_student and student
        else None,
        "can_mark_done": why_cannot_mark_task_done(viewer, task.assignee_id, task.created_by_id)
        is None,
    }


def _person(staff: Staff) -> dict[str, object]:
    return {"id": staff.pk, "name": staff.name}
