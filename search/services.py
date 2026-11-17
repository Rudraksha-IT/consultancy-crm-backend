"""The global search (spec 29a): Students and classes a Staff member may see, a few at a time.

Call these inside the Consultancy's schema. Who may see what comes from `access.policy`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Case, IntegerField, OuterRef, Q, QuerySet, Subquery, Value, When
from django.utils import timezone

from access.policy import BatchAction, StudentAction, can_act_on_student, why_cannot_on_batches
from batches.models import Batch, BatchMembership
from search.models import SearchAllowance
from staff.actors import is_platform_visitor
from staff.models import Role, Staff
from students.models import Student

# At most this many of each kind are returned (spec Implementation Decisions → Search).
RESULTS_PER_GROUP = 5
SEARCHES_PER_MINUTE = 30
SEARCH_WINDOW = timedelta(minutes=1)
# Fewer digits than this match too many phone numbers to be useful, so they're not tried.
PHONE_DIGITS_MIN = 3
_NOT_DIGITS = re.compile(r"\D")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StudentMatches:
    """The first Students found, and how many match in all (for "See all ... Students")."""

    first: list[Student]
    count: int


def use_a_search(staff: Staff) -> bool:
    """Count one search for `staff`; False once they made `SEARCHES_PER_MINUTE` this minute."""
    searcher = _searcher(staff)
    now = timezone.now()
    with transaction.atomic():
        allowance, _ = SearchAllowance.objects.select_for_update().get_or_create(
            searcher=searcher, defaults={"window_started_at": now}
        )
        if allowance.window_started_at <= now - SEARCH_WINDOW:
            allowance.window_started_at = now
            allowance.used = 0
        if allowance.used >= SEARCHES_PER_MINUTE:
            logger.warning("Search limit reached: searcher=%s", searcher)
            return False
        allowance.used += 1
        allowance.save(update_fields=("window_started_at", "used"))
    return True


def find_students(text: str, viewer: Staff, batches_on: bool) -> StudentMatches:
    """Students whose name or email holds `text`, or whose phone holds its digits.

    Admins, counsellors and Front Desk find anyone, as on the Student list. A Teacher finds
    only Students in their own classes (spec 22), and only while classes are on; their rows
    are annotated with `class_id` and `class_name`, the first of those classes by name.
    """
    students = _students_for(viewer, batches_on)
    if students is None:
        return StudentMatches(first=[], count=0)
    matching = students.filter(_student_match(text))
    ordered = matching.annotate(starts=_starts_with("name", text)).order_by("starts", "name", "pk")
    return StudentMatches(first=list(ordered[:RESULTS_PER_GROUP]), count=matching.count())


def find_batches(text: str, viewer: Staff, batches_on: bool) -> list[Batch]:
    """Classes named with `text` that `viewer` may open; none when classes are off."""
    if not batches_on or why_cannot_on_batches(viewer, BatchAction.SEE) is not None:
        return []
    batches = Batch.objects.filter(name__icontains=text)
    if viewer.role == Role.TEACHER:
        batches = batches.filter(teacher=viewer)
    ordered = batches.annotate(starts=_starts_with("name", text)).order_by("starts", "name", "pk")
    return list(ordered[:RESULTS_PER_GROUP])


def _searcher(staff: Staff) -> str:
    if is_platform_visitor(staff):
        return f"platform:{staff.platform_admin_id}"
    return f"staff:{staff.pk}"


def _students_for(viewer: Staff, batches_on: bool) -> QuerySet[Student] | None:
    """Every Student `viewer` may find, or None when they may find none."""
    if can_act_on_student(viewer, StudentAction.SEE):
        return Student.objects.select_related("assigned_counsellor")
    if not batches_on or why_cannot_on_batches(viewer, BatchAction.SEE) is not None:
        return None
    mine = BatchMembership.objects.filter(batch__teacher=viewer)
    first_class = mine.filter(student=OuterRef("pk")).order_by("batch__name", "batch_id")
    return Student.objects.filter(pk__in=mine.values("student_id")).annotate(
        class_id=Subquery(first_class.values("batch_id")[:1]),
        class_name=Subquery(first_class.values("batch__name")[:1]),
    )


def _student_match(text: str) -> Q:
    match = Q(name__icontains=text) | Q(email__icontains=text)
    digits = _NOT_DIGITS.sub("", text)
    return match | Q(phone__contains=digits) if len(digits) >= PHONE_DIGITS_MIN else match


def _starts_with(field: str, text: str) -> Case:
    """0 when `field` starts with `text`, so those come first; 1 otherwise."""
    return Case(
        When(**{f"{field}__istartswith": text}, then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )
