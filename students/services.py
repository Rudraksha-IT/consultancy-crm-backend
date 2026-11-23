"""Public services for Students. Call them inside the Consultancy's schema.

Permission checks are the views' job, through `access.policy`.
"""

from __future__ import annotations

import logging
import re

from django.core.paginator import Page, Paginator
from django.db import transaction
from django.db.models import Q, QuerySet

from staff.actors import stored_staff
from staff.models import Staff
from students.models import Stage, Student
from students.types import COUNSELLOR_ME, COUNSELLOR_NONE, NewStudent, StudentFilters
from visits.services import log_first_visit

STUDENTS_PER_PAGE = 25
# Phone numbers are stored as digits (and "+"), so a typed "98410-22 33" is searched without them.
PHONE_SEPARATORS = re.compile(r"[ -]")

logger = logging.getLogger(__name__)


@transaction.atomic
def add_student(details: NewStudent, by: Staff) -> Student:
    """Add a new visitor as a Student at the Lead Stage, with their first Visit (spec 23b)."""
    student = Student.objects.create(
        name=details.name,
        phone=details.phone,
        email=details.email.strip().lower(),
        country=details.country,
        source=details.source,
        highest_qualification=details.highest_qualification,
        past_degree=details.past_degree,
        previous_job=details.previous_job,
        sub_agent_name=details.sub_agent_name,
        note=details.note,
        stage=Stage.LEAD,
        assigned_counsellor=details.assigned_counsellor,
        created_by=stored_staff(by),
    )
    logger.info("Student added: student_id=%s, by_staff_id=%s", student.pk, by.pk)
    log_first_visit(student, by)
    return student


def find_students(filters: StudentFilters, viewer: Staff) -> QuerySet[Student]:
    """Every Student matching the search and filters, newest first.

    `viewer` is who "assigned to me" means.
    """
    students = Student.objects.select_related("assigned_counsellor").filter(
        _matching(filters, viewer)
    )
    return students.order_by("-created_at", "-pk")


def _matching(filters: StudentFilters, viewer: Staff) -> Q:
    """Every filter that is set, joined; an empty `Q` matches everyone."""
    exact = {
        "stage": filters.stage,
        "source": filters.source,
        "country": filters.country,
        "target_level": filters.target_level,
        "priority": filters.priority,
    }
    match = Q(**{field: value for field, value in exact.items() if value is not None})
    if filters.query:
        match &= student_search(filters.query)
    if filters.counsellor:
        match &= assigned_to(filters.counsellor, viewer)
    return match


def page_of(students: QuerySet[Student], number: int) -> Page[Student]:
    """One page of `students`; the first page exists even when there are none.

    Raises:
        django.core.paginator.EmptyPage: There is no such page.
    """
    return Paginator(students, STUDENTS_PER_PAGE).page(number)


def student_search(query: str, prefix: str = "") -> Q:
    """Name or email containing the text, or a phone containing its digits.

    `prefix` reaches the Student through a relation, e.g. "student__" from an Application.
    """
    match = Q(**{f"{prefix}name__icontains": query}) | Q(**{f"{prefix}email__icontains": query})
    phone = PHONE_SEPARATORS.sub("", query)
    return match | Q(**{f"{prefix}phone__contains": phone}) if phone else match


def assigned_to(counsellor: str, viewer: Staff, prefix: str = "") -> Q:
    """Students of one counsellor: "me" (the viewer), "none" (no one yet) or a Staff UUID.

    `prefix` reaches the Student through a relation, e.g. "student__" from an Application.
    """
    field = f"{prefix}assigned_counsellor"
    if counsellor == COUNSELLOR_ME:
        return Q(**{field: viewer})
    if counsellor == COUNSELLOR_NONE:
        return Q(**{f"{field}__isnull": True})
    return Q(**{f"{field}__public_id": counsellor})
