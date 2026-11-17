"""History entries as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from history.models import HistoryEntry


def history_entry_answer(entry: HistoryEntry) -> dict[str, object]:
    """One entry: what changed, when, who did it, and the value before and after."""
    return {
        "id": entry.pk,
        "kind": entry.kind,
        "at": entry.at.isoformat(),
        "by": {"name": entry.by_name, "is_platform_admin": entry.by_platform_admin},
        "before": entry.before,
        "after": entry.after,
        "detail": entry.detail,
    }
