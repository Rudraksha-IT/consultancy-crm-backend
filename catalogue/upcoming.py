"""The Catalogue's upcoming Intakes: a Student's preferred Intake is one of them (spec 145)."""

from __future__ import annotations

from datetime import date

from catalogue.intakes import month_of
from catalogue.models import Intake


def upcoming_intakes(today: date) -> list[str]:
    """Each "YYYY-MM" from this month on that an active Course of an active University offers."""
    months = (
        Intake.objects.filter(
            course__archived=False,
            course__university__archived=False,
            month__gte=month_of(today),
        )
        .order_by("month")
        .values_list("month", flat=True)
        .distinct()
    )
    return list(months)
