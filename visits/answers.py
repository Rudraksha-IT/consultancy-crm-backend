"""Visits as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from visits.models import Visit

# The first line of a Note on today's timeline, shortened past this many characters.
NOTE_LINE_MAX_LENGTH = 160
ELLIPSIS = "…"


def visit_answer(visit: Visit) -> dict[str, object]:
    """One Visit: when, New or Follow-up, why, and who logged it."""
    return {
        "id": visit.pk,
        "kind": visit.kind,
        "purpose": visit.purpose,
        "at": visit.at.isoformat(),
        "recorded_by": {"id": visit.recorded_by_id, "name": visit.recorded_by_name},
    }


def timeline_answer(visit: Visit) -> dict[str, object]:
    """One Visit on today's timeline, with the Student and the first line of their Note."""
    student = visit.student
    return {
        **visit_answer(visit),
        "student": {
            "id": student.pk,
            "name": student.name,
            "note_first_line": note_first_line(student.note),
        },
    }


def note_first_line(note: str) -> str:
    """The Note's first line with text, shortened to `NOTE_LINE_MAX_LENGTH` characters."""
    line = next((line.strip() for line in note.splitlines() if line.strip()), "")
    if len(line) <= NOTE_LINE_MAX_LENGTH:
        return line
    return line[: NOTE_LINE_MAX_LENGTH - 1] + ELLIPSIS
