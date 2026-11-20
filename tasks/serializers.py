"""What the Task endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from django.utils import timezone
from rest_framework import serializers

from config.serializers import StrictSerializer, parse_record_id
from staff.models import Staff
from staff.serializers import PlainTextField
from students.models import Student
from students.serializers import StrictChoiceField
from tasks.models import TITLE_MAX_LENGTH, TaskPriority
from tasks.services import assignable_staff
from tasks.types import NewTask, TaskView

TITLE_MESSAGE = "Say what needs doing."
TITLE_LENGTH_MESSAGE = f"Use at most {TITLE_MAX_LENGTH} characters."
# How far ahead a Task can be due; anything later is surely a typing slip.
YEARS_AHEAD = 5
DUE_ON_MESSAGE = f"Enter a due date from today to {YEARS_AHEAD} years ahead."
ASSIGNEE_MESSAGE = "Choose who it's for from the list."
STUDENT_MESSAGE = "Choose a Student from the list."
DONE_MESSAGE = "Say whether it's done."
VIEW_MESSAGE = "Choose mine, given or done."
PRIORITY_MESSAGE = "Choose High, Normal or Low."
DUE_BY_MESSAGE = "Enter a date as YYYY-MM-DD."
ONLY_ME_MESSAGE = "Say whether only you should see it."
ONLY_ME_ASSIGNEE_MESSAGE = "An “Only me” task can only be for you."
ONLY_ME_STUDENT_MESSAGE = "An “Only me” task can't be about a Student."
ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
LARGEST_ID = 2**63 - 1
_ALL_MESSAGES = ("required", "null", "blank", "invalid", "invalid_choice")


def _same_message(message: str) -> dict[str, Any]:
    return dict.fromkeys(_ALL_MESSAGES, message)


def _an_id(data: object) -> int | None:
    """`data` as a database id, or None when it can't be one (text, decimals, booleans...)."""
    if isinstance(data, bool) or not isinstance(data, int) or not 1 <= data <= LARGEST_ID:
        return None
    return data


class TitleField(PlainTextField):
    """What needs doing: one line of text, up to `TITLE_MAX_LENGTH` characters."""

    def __init__(self) -> None:
        super().__init__(
            max_length=TITLE_MAX_LENGTH,
            error_messages={**_same_message(TITLE_MESSAGE), "max_length": TITLE_LENGTH_MESSAGE},
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse anything that isn't text with the same message as a missing title."""
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class DueOnField(serializers.Field[date, str, str, Any]):
    """The day it's due, "YYYY-MM-DD" (AD), from today up to `YEARS_AHEAD` years ahead."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(DUE_ON_MESSAGE))

    @staticmethod
    def latest(today: date) -> date:
        """The last day a Task created `today` can be due."""
        try:
            return today.replace(year=today.year + YEARS_AHEAD)
        except ValueError:  # 29 February, in a year that has none.
            return today.replace(year=today.year + YEARS_AHEAD, day=28)

    def to_internal_value(self, data: object) -> date:
        """Return the date."""
        if not isinstance(data, str) or not ISO_DATE.match(data):
            self.fail("invalid")
        try:
            due_on = date.fromisoformat(data)
        except ValueError:
            # self.fail raises the field's validation error
            self.fail("invalid")
        today = timezone.localdate()
        if not today <= due_on <= self.latest(today):
            self.fail("invalid")
        return due_on

    def to_representation(self, value: date) -> str:
        """Return the date as text (unused: answers are built by hand)."""
        return value.isoformat()


class AssigneeField(serializers.Field[Staff, int, int, Any]):
    """Who the Task is for: any active Staff member, by id."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(ASSIGNEE_MESSAGE))

    def to_internal_value(self, data: object) -> Staff:
        """Return the Staff member; anyone invited, gone or unknown is refused."""
        pk = _an_id(data)
        assignee = None if pk is None else assignable_staff().filter(pk=pk).first()
        if assignee is None:
            self.fail("invalid")
        return assignee

    def to_representation(self, value: Staff) -> int:
        """Return the id (unused: answers are built by hand)."""
        return value.pk


class StudentLinkField(serializers.Field[Student | None, str | None, str | None, Any]):
    """The Student the Task is about, by UUID (ADR 0027); optional and nullable."""

    def __init__(self) -> None:
        super().__init__(
            required=False, allow_null=True, error_messages=_same_message(STUDENT_MESSAGE)
        )

    def to_internal_value(self, data: object) -> Student:
        """Return the Student; anything but a known Student's id is refused."""
        public_id = parse_record_id(data)
        student = None if public_id is None else Student.objects.filter(public_id=public_id).first()
        if student is None:
            self.fail("invalid")
        return student

    def to_representation(self, value: Student | None) -> str | None:
        """Return the UUID (unused: answers are built by hand)."""
        return None if value is None else str(value.public_id)


class DoneField(serializers.Field[bool, bool, bool, Any]):
    """True or false, as JSON (never "true", 1 or null)."""

    def __init__(self, *, message: str = DONE_MESSAGE, required: bool = True) -> None:
        super().__init__(required=required, error_messages=_same_message(message))

    def to_internal_value(self, data: object) -> bool:
        """Return the flag."""
        if not isinstance(data, bool):
            self.fail("invalid")
        return data

    def to_representation(self, value: bool) -> bool:
        """Return the flag (unused: answers are built by hand)."""
        return value


def priority_field() -> StrictChoiceField:
    """High, Normal or Low, as their exact lower-case text (spec 77a); optional."""
    return StrictChoiceField(
        choices=TaskPriority.choices,
        required=False,
        error_messages=_same_message(PRIORITY_MESSAGE),
    )


class NewTaskSerializer(StrictSerializer[None]):
    """A Task to create (spec 77, 77a, 77b).

    Who gave it, and whether it's done, never come from the body: the giver is the logged-in
    person, passed as `context["by"]`. An "Only me" Task must be for them and about no Student.
    """

    title = TitleField()
    due_on = DueOnField()
    assignee = AssigneeField()
    student = StudentLinkField()
    priority = priority_field()
    only_me = DoneField(message=ONLY_ME_MESSAGE, required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Refuse an "Only me" Task given to someone else or about a Student."""
        if not attrs.get("only_me", False):
            return attrs
        errors: dict[str, list[str]] = {}
        by: Staff = self.context["by"]
        if attrs["assignee"].pk != by.pk:
            errors["assignee"] = [ONLY_ME_ASSIGNEE_MESSAGE]
        if attrs.get("student") is not None:
            errors["student"] = [ONLY_ME_STUDENT_MESSAGE]
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def to_new_task(self) -> NewTask:
        """Return the validated Task."""
        data = self.validated_data
        return NewTask(
            title=data["title"],
            due_on=data["due_on"],
            assignee=data["assignee"],
            student=data.get("student"),
            priority=TaskPriority(data.get("priority", TaskPriority.NORMAL)),
            only_me=data.get("only_me", False),
        )


class DoneSerializer(StrictSerializer[None]):
    """Mark a Task done, or open again (spec 81). Nothing else about a Task can change."""

    done = DoneField()

    def is_done(self) -> bool:
        """Whether it should now be done."""
        done: bool = self.validated_data["done"]
        return done


class DueByField(serializers.Field[date, str, str, Any]):
    """A day, "YYYY-MM-DD" (AD), with no limits: only Tasks due by it are listed."""

    def __init__(self) -> None:
        super().__init__(required=False, error_messages=_same_message(DUE_BY_MESSAGE))

    def to_internal_value(self, data: object) -> date:
        """Return the date."""
        if not isinstance(data, str) or not ISO_DATE.match(data):
            self.fail("invalid")
        try:
            return date.fromisoformat(data)
        except ValueError:
            # self.fail raises the field's validation error
            self.fail("invalid")

    def to_representation(self, value: date) -> str:
        """Return the date as text (unused)."""
        return value.isoformat()


class PageSerializer(StrictSerializer[None]):
    """A page number from the query string; the first if none."""

    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def page_number(self) -> int:
        """The page asked for."""
        number: int = self.validated_data.get("page", 1)
        return number


class TaskListQuerySerializer(PageSerializer):
    """Which view of My Tasks, and which page (spec 79)."""

    view = StrictChoiceField(
        choices=[(view.value, view.value) for view in TaskView],
        required=False,
        error_messages=_same_message(VIEW_MESSAGE),
    )

    priority = priority_field()
    due_by = DueByField()

    def task_view(self) -> TaskView:
        """The view asked for; "mine" if none."""
        return TaskView(self.validated_data.get("view", TaskView.MINE))

    def task_priority(self) -> TaskPriority | None:
        """Only Tasks of this priority, or None for every priority."""
        priority = self.validated_data.get("priority")
        return None if priority is None else TaskPriority(priority)

    def due_by_day(self) -> date | None:
        """Only Tasks due on or before this day, or None for any day."""
        due_by: date | None = self.validated_data.get("due_by")
        return due_by


class NoQuerySerializer(StrictSerializer[None]):
    """An endpoint that takes nothing in its query string; anything sent is refused."""
