"""Communication as the API returns it."""

from __future__ import annotations

from communication.attachments import MAX_EMAIL_BYTES, Attachable, AttachableFiles
from communication.models import Message
from communication.ready_made import DraftWithFiles, MenuEntry
from communication.types import BulkResult
from staff.refs import person_ref
from students.models import Student


def message_answer(message: Message) -> dict[str, object]:
    """One email (or a past SMS), sent or failed."""
    return {
        "id": message.public_id,
        "student": {"id": message.student.public_id, "name": message.student.name},
        "channel": message.channel,
        "subject": message.subject,
        "body": message.body,
        "status": message.status,
        "error": message.error,
        "sent_to": message.sent_to,
        "attachments": list(message.attachments),
        "sent_at": message.sent_at.isoformat(),
        "sent_by": person_ref(message.sent_by, message.sent_by_name),
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
            {"id": s.public_id, "name": s.name, "email": s.email, "stage": s.stage}
            for s in students
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


def attachable_row(file: Attachable) -> dict[str, object]:
    """One file that can be attached, as the picker shows it."""
    return {
        "kind": file.kind.value,
        "id": file.id,
        "name": file.name,
        "file_name": file.file_name,
        "size": file.size,
        "group": file.group,
    }


def attachable_answer(files: AttachableFiles) -> dict[str, object]:
    """Everything that can be attached, by where it comes from, and the most one email carries."""
    return {
        "course_files": [attachable_row(file) for file in files.course_files],
        "document_examples": [attachable_row(file) for file in files.document_examples],
        "documents": [attachable_row(file) for file in files.documents],
        "max_bytes": MAX_EMAIL_BYTES,
    }


def draft_answer(draft: DraftWithFiles) -> dict[str, object]:
    """A ready-made email's starting subject line and message, and the files it suggests."""
    return {
        "subject": draft.draft.subject,
        "body": draft.draft.text,
        "attachments": [attachable_row(file) for file in draft.files],
    }
