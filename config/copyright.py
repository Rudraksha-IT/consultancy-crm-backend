"""The copyright line shown on the product (owner, 2026-11-27).

The first year alone while it is still that year; after it, the first year to this one, so the
line never needs a change by hand.
"""

from __future__ import annotations

from django.utils import timezone

COPYRIGHT_HOLDER = "Rudraksha Labs"
FIRST_YEAR = 2026


def this_year() -> int:
    """The year now in the product's time zone (Nepal)."""
    return timezone.localdate().year


def copyright_line(year: int) -> str:
    """'© 2026 Rudraksha Labs. All rights reserved.', or '© 2026-2028 …' from 2028."""
    years = str(FIRST_YEAR) if year <= FIRST_YEAR else f"{FIRST_YEAR}-{year}"
    return f"© {years} {COPYRIGHT_HOLDER}. All rights reserved."
