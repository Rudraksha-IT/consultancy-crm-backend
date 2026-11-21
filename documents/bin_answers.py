"""How something in the Bin says who put it there, when and why.

Documents, Course Files and Document Examples alike. Kept apart so every kind's answers can use it
without importing each other.
"""

from __future__ import annotations

from documents.models import InTheBin
from staff.refs import person_ref


def bin_answer(held: InTheBin) -> dict[str, object]:
    """Who put it in the Bin (as named then; no id for a Platform Admin), when and why."""
    at = held.binned_at
    assert at is not None  # noqa: S101  # asked only of what is in the Bin
    return {
        "by": person_ref(held.binned_by, held.binned_by_name),
        "at": at.isoformat(),
        "reason": held.bin_reason,
    }
