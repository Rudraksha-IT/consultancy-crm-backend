"""Public services for Tasks (spec 77-82). Call inside the Consultancy's schema.

Who may link a Student or tick a Task is the views' job, through `access.policy`. Marking a Task
done keeps who did it and when on the Task; one about a Student is also added to their History,
so it shows in their Activity (issue 83).

An "Only me" Task (spec 77b) is seen by its owner alone: every read here goes through
`visible_tasks`, so no list, count or lookup shows it to anyone else, Admins and Platform Admins
included. (The Platform Admin's full data download still holds it.)
"""

from __future__ import annotations

import logging
from datetime import date

from django.db import transaction
from django.db.models import Case, IntegerField, Q, QuerySet, Value, When
from django.utils import timezone

from history.models import HistoryKind
from history.services import Change, record
from notifications.services import notify_task_given
from staff.actors import actor_name, stored_staff
from staff.models import Staff, StaffStatus
from students.models import Student
from tasks.models import PRIORITY_ORDER, Task, TaskPriority
from tasks.types import NewTask, TaskView, Timing

logger = logging.getLogger(__name__)

_VIEW_ORDER: dict[TaskView, tuple[str, ...]] = {
    # Overdue first, then High to Low, then soonest (spec 79a).
    TaskView.MINE: ("is_on_time", "priority_rank", "due_on", "pk"),
    TaskView.GIVEN: ("is_done", "due_on", "pk"),
    TaskView.DONE: ("-done_at", "-pk"),
}


def assignable_staff() -> QuerySet[Staff]:
    """Everyone a Task can be given to: active Staff of any Role, by name."""
    return Staff.objects.filter(status=StaffStatus.ACTIVE).order_by("name", "pk")


def create_task(details: NewTask, by: Staff) -> Task:
    """Create an open Task given by `by`, telling the assignee if it isn't `by` (spec 80)."""
    with transaction.atomic():
        task = Task.objects.create(
            title=details.title,
            due_on=details.due_on,
            assignee=details.assignee,
            created_by=stored_staff(by),
            created_by_name=actor_name(by),
            student=details.student,
            priority=details.priority,
            only_me=details.only_me,
        )
        notify_task_given(task)
    logger.info(
        "Task created: task_id=%s, assignee_id=%s, by_staff_id=%s",
        task.pk,
        details.assignee.pk,
        by.pk,
    )
    return task


def visible_tasks(viewer: Staff) -> QuerySet[Task]:
    """Every Task `viewer` may know of: all but other people's "Only me" Tasks (spec 77b)."""
    return Task.objects.exclude(Q(only_me=True) & ~Q(assignee_id=viewer.pk))


def task_with_people(task_id: int, viewer: Staff) -> Task:
    """One Task `viewer` may see, with whoever it's for, whoever gave it and its Student.

    Raises:
        Task.DoesNotExist: There is no such Task, or it is someone else's "Only me" Task.
    """
    return _with_people(visible_tasks(viewer)).get(pk=task_id)


def set_task_done(task_id: int, *, done: bool, by: Staff) -> Task:
    """Mark the Task done (keeping who did it and when it first was) or open it again.

    Done, a Task about a Student adds a Task done entry to their History; opening it again
    clears who and when on the Task, and the History entry stays (History is append-only).

    Raises:
        Task.DoesNotExist: There is no such Task, or it is someone else's "Only me" Task.
    """
    with transaction.atomic():
        task = visible_tasks(by).select_for_update().get(pk=task_id)
        if task.is_done != done:
            task.is_done = done
            task.done_at = timezone.now() if done else None
            task.done_by = stored_staff(by) if done else None
            task.done_by_name = actor_name(by) if done else ""
            task.save(update_fields=["is_done", "done_at", "done_by", "done_by_name"])
            if done and task.student is not None:
                record(task.student, HistoryKind.TASK_DONE, by, Change(after=task.title))
    what = "Task marked done" if done else "Task reopened"
    logger.info("%s: task_id=%s, by_staff_id=%s", what, task.pk, by.pk)
    return task_with_people(task.pk, by)


def my_tasks(
    staff: Staff,
    view: TaskView,
    today: date,
    *,
    priority: TaskPriority | None = None,
    due_by: date | None = None,
) -> QuerySet[Task]:
    """One view of `staff`'s Tasks (spec 79, 79a), narrowed to `priority` and `due_by` if given.

    "To do" is overdue first, then by priority, then soonest; given ones open first, soonest
    first; done ones latest first.
    """
    tasks = visible_tasks(staff)
    if view is TaskView.MINE:
        tasks = tasks.filter(assignee=staff, is_done=False)
    elif view is TaskView.GIVEN:
        tasks = tasks.filter(created_by=staff).exclude(assignee=staff)
    else:
        tasks = tasks.filter(assignee=staff, is_done=True)
    if priority is not None:
        tasks = tasks.filter(priority=priority)
    if due_by is not None:
        tasks = tasks.filter(due_on__lte=due_by)
    ordered = tasks.annotate(
        is_on_time=Case(
            When(due_on__lt=today, then=Value(0)), default=Value(1), output_field=IntegerField()
        ),
        priority_rank=Case(
            *(When(priority=level, then=Value(rank)) for rank, level in enumerate(PRIORITY_ORDER)),
            output_field=IntegerField(),
        ),
    )
    return _with_people(ordered).order_by(*_VIEW_ORDER[view])


def due_now_count(staff: Staff, today: date) -> int:
    """How many of `staff`'s open Tasks are due `today` or overdue, for the menu (spec 79a)."""
    return visible_tasks(staff).filter(assignee=staff, is_done=False, due_on__lte=today).count()


def open_tasks_of(student: Student) -> QuerySet[Task]:
    """The Student's open Tasks, soonest first (spec 82). "Only me" Tasks have no Student."""
    tasks = Task.objects.filter(student=student, is_done=False, only_me=False)
    return _with_people(tasks).order_by("due_on", "pk")


def timing(task: Task, today: date) -> Timing:
    """Whether the Task is done, overdue, due `today` or due later."""
    if task.is_done:
        return Timing.DONE
    if task.due_on < today:
        return Timing.OVERDUE
    return Timing.TODAY if task.due_on == today else Timing.UPCOMING


def _with_people(tasks: QuerySet[Task]) -> QuerySet[Task]:
    return tasks.select_related("assignee", "student")
