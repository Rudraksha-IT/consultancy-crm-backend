"""The office-wide Visas list (spec 62b): every Visa, filtered and ordered by its appointments.

Call inside the Consultancy's schema. Who may read it is the views' job, through `access.policy`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from django.core.paginator import Page, Paginator
from django.db.models import Case, DateTimeField, F, IntegerField, Q, QuerySet, Value, When
from django.db.models.functions import Coalesce, Greatest, Least
from django.utils import timezone

from applications.models import Visa
from applications.types import VisaFilterChoices, VisaFilters, VisaSort
from catalogue.models import University
from documents.services import checklists_of
from students.services import assigned_to

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from django.db.models.expressions import OrderBy

    from documents.checklist import Checklist
    from staff.models import Staff

VISAS_PER_PAGE = 25
_STUDENT = "student__"
_NEWEST_ADDED = (F("created_at").desc(), F("pk").desc())
_UPCOMING, _NO_DATE, _PAST = 0, 1, 2
# Where a Visa's three appointments are kept.
_DATE_FIELDS = ("biometrics_at", "medical_at", "interview_at")


def find_visas(filters: VisaFilters, viewer: Staff) -> QuerySet[Visa]:
    """Every Visa matching the filters, in the order asked for.

    Each comes with its Student, their counsellor, and its Application's Course and University, so
    a page costs the same few queries however many rows it has. `viewer` is who "my Students" is.
    """
    today = _start_of(timezone.localdate())
    visas = (
        Visa.objects.select_related(
            "student__assigned_counsellor", "application__course__university"
        )
        .filter(_matching(filters, viewer))
        .annotate(upcoming_at=_soonest_from(today), past_at=_latest_before(today))
        .annotate(next_at=Coalesce(F("upcoming_at"), F("past_at")))
    )
    return visas.order_by(*_order(filters.sort))


def page_of_visas(visas: QuerySet[Visa], number: int) -> Page[Visa]:
    """One page of `visas`; the first page exists even when there are none.

    Raises:
        django.core.paginator.EmptyPage: There is no such page.
    """
    return Paginator(visas, VISAS_PER_PAGE).page(number)


def checklists_for(visas: Iterable[Visa]) -> dict[int, Checklist]:
    """Each listed Student's document checklist by their id, in a fixed few queries (spec 64)."""
    return checklists_of({visa.student_id for visa in visas})


def visa_filter_choices() -> VisaFilterChoices:
    """The Intakes and countries the office's Visas use, for the list's filters."""
    intakes = (
        Visa.objects.order_by("application__intake")
        .values_list("application__intake", flat=True)
        .distinct()
    )
    countries = (
        University.objects.filter(courses__applications__visas__isnull=False)
        .order_by("country")
        .values_list("country", flat=True)
        .distinct()
    )
    return VisaFilterChoices(intakes=list(intakes), countries=list(countries))


def _start_of(day: date) -> datetime:
    """Midnight at the start of `day` in the Consultancy's time zone."""
    return timezone.make_aware(datetime.combine(day, time.min))


def _matching(filters: VisaFilters, viewer: Staff) -> Q:
    """Every filter that is set, joined; an empty `Q` matches every Visa."""
    wanted: list[tuple[object, Callable[[], Q]]] = [
        (filters.statuses, lambda: Q(status__in=filters.statuses)),
        (filters.country, lambda: Q(application__course__university__country=filters.country)),
        (filters.intake, lambda: Q(application__intake=filters.intake)),
        (filters.counsellor, lambda: assigned_to(filters.counsellor, viewer, _STUDENT)),
        (
            filters.appointment_from or filters.appointment_to,
            lambda: _appointment_days(filters.appointment_from, filters.appointment_to),
        ),
        (
            filters.has_appointment is not None,
            lambda: _any_booked() if filters.has_appointment else ~_any_booked(),
        ),
    ]
    match = Q()
    for value, condition in wanted:
        if value:
            match &= condition()
    return match


def _any_booked() -> Q:
    """The Visa has at least one appointment."""
    match = Q()
    for field in _DATE_FIELDS:
        match |= Q(**{f"{field}__isnull": False})
    return match


def _appointment_days(first: date | None, last: date | None) -> Q:
    """Any appointment on a Kathmandu day from `first` to `last`, both included; None is open."""
    match = Q()
    for field in _DATE_FIELDS:
        one = Q(**{f"{field}__isnull": False})
        if first is not None:
            one &= Q(**{f"{field}__gte": _start_of(first)})
        if last is not None:
            one &= Q(**{f"{field}__lt": _start_of(last + timedelta(days=1))})
        match |= one
    return match


def _soonest_from(start: datetime) -> Least:
    """The soonest appointment at or after `start`; NULL if none (PostgreSQL LEAST skips NULL)."""
    return Least(
        *(Case(When(**{f"{field}__gte": start}, then=F(field))) for field in _DATE_FIELDS),
        output_field=DateTimeField(),
    )


def _latest_before(start: datetime) -> Greatest:
    """The latest appointment before `start`; NULL when none."""
    return Greatest(
        *(Case(When(**{f"{field}__lt": start}, then=F(field))) for field in _DATE_FIELDS),
        output_field=DateTimeField(),
    )


def _order(sort: VisaSort) -> tuple[OrderBy | F, ...]:
    """The ORDER BY for `sort`, by the annotated next appointment; ties go to the newest Visa."""
    if sort == VisaSort.APPOINTMENT:
        return (F("next_at").asc(nulls_last=True), *_NEWEST_ADDED)
    if sort == VisaSort.APPOINTMENT_DESC:
        return (F("next_at").desc(nulls_last=True), *_NEWEST_ADDED)
    group = Case(
        When(upcoming_at__isnull=False, then=Value(_UPCOMING)),
        When(past_at__isnull=True, then=Value(_NO_DATE)),
        default=Value(_PAST),
        output_field=IntegerField(),
    )
    return (
        group.asc(),
        F("upcoming_at").asc(nulls_last=True),
        F("past_at").desc(nulls_last=True),
        *_NEWEST_ADDED,
    )
