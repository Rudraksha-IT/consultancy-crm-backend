"""The office-wide Applications list (spec 57a): every Application, searched, filtered and sorted.

Call inside the Consultancy's schema. Who may read it is the views' job, through `access.policy`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.paginator import Page, Paginator
from django.db.models import F, Q, QuerySet

from applications.models import Application
from applications.types import ApplicationFilters, ApplicationSort, FilterChoices
from catalogue.models import University
from students.services import assigned_to, student_search

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.db.models.expressions import OrderBy

    from staff.models import Staff

APPLICATIONS_PER_PAGE = 25
_STUDENT = "student__"
_NEWEST_ADDED = (F("created_at").desc(), F("pk").desc())
_ORDER: dict[ApplicationSort, tuple[OrderBy, ...]] = {
    ApplicationSort.APPLIED_ON_DESC: (F("applied_on").desc(nulls_last=True), *_NEWEST_ADDED),
    ApplicationSort.APPLIED_ON: (F("applied_on").asc(nulls_last=True), *_NEWEST_ADDED),
    ApplicationSort.ADDED_ON_DESC: _NEWEST_ADDED,
    ApplicationSort.ADDED_ON: (F("created_at").asc(), F("pk").asc()),
    ApplicationSort.INTAKE: (F("intake").asc(), *_NEWEST_ADDED),
    ApplicationSort.INTAKE_DESC: (F("intake").desc(), *_NEWEST_ADDED),
}


def find_applications(filters: ApplicationFilters, viewer: Staff) -> QuerySet[Application]:
    """Every Application matching the search and filters, in the order asked for.

    Each comes with its Student, their counsellor, its Course and University, so a page of them
    costs the same few queries however many rows it has. `viewer` is who "my Students" means.
    """
    return (
        Application.objects.select_related("student__assigned_counsellor", "course__university")
        .filter(_matching(filters, viewer))
        .order_by(*_ORDER[filters.sort])
    )


def page_of_applications(applications: QuerySet[Application], number: int) -> Page[Application]:
    """One page of `applications`; the first page exists even when there are none.

    Raises:
        django.core.paginator.EmptyPage: There is no such page.
    """
    return Paginator(applications, APPLICATIONS_PER_PAGE).page(number)


def filter_choices() -> FilterChoices:
    """The Intakes, years and countries the office's Applications use, for the list's filters."""
    intakes = list(
        Application.objects.order_by("intake").values_list("intake", flat=True).distinct()
    )
    countries = (
        University.objects.filter(courses__applications__isnull=False)
        .order_by("country")
        .values_list("country", flat=True)
        .distinct()
    )
    years = sorted({intake[:4] for intake in intakes})
    return FilterChoices(intakes=intakes, years=years, countries=list(countries))


def _matching(filters: ApplicationFilters, viewer: Staff) -> Q:
    """Every filter that is set, joined; an empty `Q` matches every Application."""
    wanted: list[tuple[object, Callable[[], Q]]] = [
        (filters.query, lambda: _search(filters.query)),
        (filters.statuses, lambda: Q(status__in=filters.statuses)),
        (filters.intake, lambda: Q(intake=filters.intake)),
        (filters.year, lambda: Q(intake__startswith=f"{filters.year}-")),
        (filters.country, lambda: Q(course__university__country=filters.country)),
        (filters.counsellor, lambda: assigned_to(filters.counsellor, viewer, _STUDENT)),
    ]
    match = Q()
    for value, condition in wanted:
        if value:
            match &= condition()
    return match


def _search(query: str) -> Q:
    """The Student's name, email or phone, or the University's name."""
    return student_search(query, _STUDENT) | Q(course__university__name__icontains=query)
