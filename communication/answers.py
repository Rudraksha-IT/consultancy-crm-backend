"""Communication as the API returns it."""

from __future__ import annotations

from communication.models import Message
from communication.ready_made import MenuEntry
from communication.types import BulkResult
from emails.types import Draft
from students.models import Student


def message_answer(message: Message) -> dict[str, object]:
    """One email (or a past SMS), sent or failed."""
    return {
        "id": message.pk,
        "student": {"id": message.student.pk, "name": message.student.name},
        "channel": message.channel,
        "subject": message.subject,
        "body": message.body,
        "status": message.status,
        "error": message.error,
        "sent_to": message.sent_to,
        "sent_at": message.sent_at.isoformat(),
        "sent_by": {"id": message.sent_by_id, "name": message.sent_by_name},
    }


def bulk_answer(result: BulkResult) -> dict[str, object]:
    """How one email to many Students went, with each failed email and its reason."""
    return {
        "sent": result.sent,
        "failed": len(result.failed),
        "failed_messages": [message_answer(message) for message in result.failed],
    }


def choosable_answer(students: list[Student], count: int, limit: int) -> dict[str, object]:
    """The Students to pre-tick, how many could be chosen in all, and the most at once."""
    return {
        "students": [
            {"id": s.pk, "name": s.name, "email": s.email, "stage": s.stage} for s in students
        ],
        "count": count,
        "max": limit,
    }


def menu_answer(entries: list[MenuEntry]) -> dict[str, object]:
    """The ready-made emails offered, each with the records to pick and why it can't be used."""
    return {
        "kinds": [
            {
                "kind": entry.kind.value,
                "record": None if entry.record is None else entry.record.value,
                "records": [{"id": record.id, "label": record.label} for record in entry.records],
                "unavailable": entry.unavailable,
            }
            for entry in entries
        ]
    }


def draft_answer(draft: Draft) -> dict[str, object]:
    """A ready-made email's starting subject line and message, as plain text."""
    return {"subject": draft.subject, "body": draft.text}
