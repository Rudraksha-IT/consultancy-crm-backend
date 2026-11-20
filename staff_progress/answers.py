"""Staff Progress as the API returns it. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from staff_progress.services import Card, Change, StaffProgress


def progress_answer(progress: StaffProgress) -> dict[str, object]:
    """The person, the period, their cards and their changes day by day (newest first)."""
    staff = progress.staff
    return {
        "staff": {
            "id": staff.public_id,
            "name": staff.name,
            "job_title": staff.job_title,
            "mobile": staff.mobile,
            "role": staff.role,
            "status": staff.status,
        },
        "from": progress.start.isoformat(),
        "to": progress.end.isoformat(),
        "cards": [_card(card) for card in progress.cards],
        "days": [
            {"day": day.day.isoformat(), "entries": [_change(c) for c in day.changes]}
            for day in progress.days
        ],
        "more": progress.more,
    }


def _card(card: Card) -> dict[str, object]:
    return {
        "count": card.count.value,
        "value": card.value,
        "counted_from": None if card.counted_from is None else card.counted_from.isoformat(),
        "amount": card.amount,
    }


def _change(change: Change) -> dict[str, object]:
    student = (
        None
        if change.student_id is None
        else {"id": change.student_id, "name": change.student_name}
    )
    return {
        "kind": change.kind,
        "at": change.at.isoformat(),
        "before": change.before,
        "after": change.after,
        "detail": change.detail,
        "student": student,
    }
