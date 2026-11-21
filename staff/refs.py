"""A Staff member named in an answer: by UUID (ADR 0027), never their database number."""

from __future__ import annotations

from staff.models import Staff


def public_id_of(staff: Staff | None) -> str | None:
    """Their UUID as text; None when nobody (or a Platform Admin, who is never Staff)."""
    return None if staff is None else str(staff.public_id)


def person_ref(staff: Staff | None, name: str) -> dict[str, object]:
    """Who did something: their UUID (None for a Platform Admin) and the name kept."""
    return {"id": public_id_of(staff), "name": name}
