"""Who did something: a Staff member, or a Platform Admin during Support Access (issue 27).

Services record the person who acted through these, never by storing `by` directly, so a
Platform Admin is never linked as if they were Staff and is always named as themselves,
e.g. "Ramesh (Platform Admin)".
"""

from __future__ import annotations

from typing import TypeGuard

from staff.models import STAFF_NAME_MAX_LENGTH, PlatformVisitor, Staff

PLATFORM_ADMIN_SUFFIX = " (Platform Admin)"


def is_platform_visitor(by: Staff) -> TypeGuard[PlatformVisitor]:
    """Return whether `by` is a Platform Admin inside the Consultancy, not a Staff member."""
    return isinstance(by, PlatformVisitor)


def stored_staff(by: Staff) -> Staff | None:
    """The Staff member to link a record to: `by`, or None for a Platform Admin."""
    return None if is_platform_visitor(by) else by


def actor_name(by: Staff) -> str:
    """The name to show for who did it, fitting the stored name columns.

    A Platform Admin's is marked, e.g. "Ramesh (Platform Admin)".
    """
    if not is_platform_visitor(by):
        return str(by.name)
    room = STAFF_NAME_MAX_LENGTH - len(PLATFORM_ADMIN_SUFFIX)
    return f"{str(by.name)[:room]}{PLATFORM_ADMIN_SUFFIX}"
