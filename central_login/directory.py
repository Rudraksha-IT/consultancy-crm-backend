"""The index of which offices an email belongs to, kept by the Staff services (issue 44).

It holds an HMAC-SHA256 of each normalised Staff email, keyed with `STAFF_DIRECTORY_KEY` (never
DJANGO_SECRET_KEY), so the public schema never holds a raw email. Call the write functions
inside the office's own schema: they find the office from the active tenant.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

from central_login.models import StaffDirectoryEntry
from tenancy.models import Consultancy

KEY_MIN_LENGTH = 32

logger = logging.getLogger(__name__)


def normalise_email(email: str) -> str:
    """The form an email is hashed in: trimmed and lowercase, as Staff emails are stored."""
    return email.strip().lower()


def email_hash(email: str) -> str:
    """Return the keyed hash of a normalised email, as 64 hex characters.

    Raises:
        ImproperlyConfigured: STAFF_DIRECTORY_KEY is missing or too short to be a real secret.
    """
    key: str = getattr(settings, "STAFF_DIRECTORY_KEY", "")
    if len(key) < KEY_MIN_LENGTH:
        message = f"STAFF_DIRECTORY_KEY must be at least {KEY_MIN_LENGTH} characters."
        raise ImproperlyConfigured(message)
    digest = hmac.new(key.encode(), normalise_email(email).encode(), hashlib.sha256)
    return digest.hexdigest()


def add_to_directory(email: str) -> None:
    """Record that `email` belongs to the active office (no change if it already is)."""
    StaffDirectoryEntry.objects.get_or_create(
        email_hash=email_hash(email), consultancy=_active_office()
    )


def remove_from_directory(email: str) -> None:
    """Record that `email` no longer belongs to the active office."""
    StaffDirectoryEntry.objects.filter(
        email_hash=email_hash(email), consultancy=_active_office()
    ).delete()


def move_in_directory(old_email: str, new_email: str) -> None:
    """Record that someone of the active office now uses `new_email` instead of `old_email`."""
    remove_from_directory(old_email)
    add_to_directory(new_email)


def offices_for(email: str) -> list[Consultancy]:
    """The offices `email` belongs to, by name. Call in the public schema."""
    found = StaffDirectoryEntry.objects.filter(email_hash=email_hash(email)).values("consultancy")
    return list(Consultancy.objects.filter(pk__in=found).order_by("name", "pk"))


def rebuild_directory(entries: list[tuple[str, Consultancy]]) -> int:
    """Replace the whole index with `(email, office)` pairs; return how many entries it holds."""
    StaffDirectoryEntry.objects.all().delete()
    rows = {(email_hash(email), office.pk): office for email, office in entries}
    StaffDirectoryEntry.objects.bulk_create(
        StaffDirectoryEntry(email_hash=hashed, consultancy=office)
        for (hashed, _), office in rows.items()
    )
    return len(rows)


def _active_office() -> Consultancy:
    office = getattr(connection, "tenant", None)
    if not isinstance(office, Consultancy):
        message = "The Staff directory is changed only inside an office's schema."
        raise RuntimeError(message)
    return office
