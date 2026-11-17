"""Export Records as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from exports.models import ExportRecord


def export_record_answer(record: ExportRecord) -> dict[str, object]:
    """One export: which list, its filters in words, how many rows, who and when."""
    return {
        "id": record.pk,
        "list": record.list,
        "filters": record.filters_text,
        "row_count": record.row_count,
        "by": record.by_name,
        "at": record.at.isoformat(),
    }
