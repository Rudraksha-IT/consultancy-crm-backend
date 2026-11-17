"""Tasks: a piece of work with a due date for one Staff member (GLOSSARY: Task)."""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import Student

TITLE_MAX_LENGTH = 200


class TaskPriority(models.TextChoices):
    """How important a Task is (spec 77a). Normal unless someone says otherwise."""

    HIGH = "high", "High"
    NORMAL = "normal", "Normal"
    LOW = "low", "Low"


# The order "To do" shows them in: High first (spec 79a).
PRIORITY_ORDER: tuple[TaskPriority, ...] = (
    TaskPriority.HIGH,
    TaskPriority.NORMAL,
    TaskPriority.LOW,
)


class Task(models.Model):
    """One Task (spec 77): what needs doing, by when, for whom, and maybe about which Student.

    Staff are never deleted, so a Task keeps the names of whoever it was for and whoever gave it,
    even after they leave. An "Only me" Task (spec 77b) is one someone gave themselves, about no
    Student; nobody else ever sees it.
    """

    title = models.CharField(max_length=TITLE_MAX_LENGTH)
    due_on = models.DateField()
    assignee = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="tasks")
    # None when a Platform Admin did it during Support Access; the name says who (issue 27).
    created_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    # Who gave it, as they were named then.
    created_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, default="")
    student = models.ForeignKey(
        Student, on_delete=models.PROTECT, related_name="tasks", null=True, blank=True
    )
    is_done = models.BooleanField(default=False)
    # When it was marked done; empty while it's open.
    done_at = models.DateTimeField(null=True, blank=True)
    # Who marked it done (issue 83): None when open, when a Platform Admin did it during Support
    # Access, or for Tasks done before this was recorded; the name says who, as named then.
    done_by = models.ForeignKey(
        Staff, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    done_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    priority = models.CharField(
        max_length=10, choices=TaskPriority.choices, default=TaskPriority.NORMAL
    )
    only_me = models.BooleanField(default=False)

    class Meta:
        ordering = ("due_on", "pk")
        indexes = (
            models.Index(fields=("assignee", "is_done", "due_on"), name="tasks_assignee_open"),
            models.Index(fields=("student", "is_done", "due_on"), name="tasks_student_open"),
        )
        constraints = (
            models.CheckConstraint(
                condition=Q(is_done=True, done_at__isnull=False)
                | Q(is_done=False, done_at__isnull=True),
                name="tasks_done_at_follows_is_done",
            ),
            models.CheckConstraint(
                condition=Q(priority__in=TaskPriority.values), name="tasks_known_priority"
            ),
            # "Only me": given by the person it's for, and about no Student.
            models.CheckConstraint(
                condition=Q(only_me=False)
                | Q(student__isnull=True, created_by=models.F("assignee")),
                name="tasks_only_me_is_private",
            ),
        )

    def __str__(self) -> str:
        return self.title
