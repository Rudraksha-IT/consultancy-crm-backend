"""Communication as the API returns it."""

from __future__ import annotations

from communication.models import Message
from communication.types import BulkResult
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
