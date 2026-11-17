"""Communication as the API returns it. Built by hand so a secret can never leak out."""

from __future__ import annotations

from communication.models import DEFAULT_EMAIL_PORT, Message, ProviderSettings
from communication.services import email_is_ready, sms_is_ready
from communication.types import BulkResult, TestSmsResult


def settings_answer(saved: ProviderSettings | None) -> dict[str, object]:
    """The office's accounts, saying only whether each secret is saved (spec 88)."""
    return {
        "sms_provider": saved.sms_provider if saved else "",
        "sms_sender": saved.sms_sender if saved else "",
        "sms_token_saved": bool(saved and saved.sms_token_sealed),
        "email_host": saved.email_host if saved else "",
        "email_port": saved.email_port if saved else DEFAULT_EMAIL_PORT,
        "email_username": saved.email_username if saved else "",
        "email_password_saved": bool(saved and saved.email_password_sealed),
        "email_from": saved.email_from if saved else "",
        "sms_ready": sms_is_ready(saved),
        "email_ready": email_is_ready(saved),
    }


def message_answer(message: Message) -> dict[str, object]:
    """One message, sent or failed (spec 91)."""
    return {
        "id": message.pk,
        "student": {"id": message.student.pk, "name": message.student.name},
        "channel": message.channel,
        "body": message.body,
        "status": message.status,
        "error": message.error,
        "sent_to": message.sent_to,
        "sent_at": message.sent_at.isoformat(),
        "sent_by": {"id": message.sent_by_id, "name": message.sent_by_name},
    }


def test_sms_answer(result: TestSmsResult) -> dict[str, object]:
    """How the test SMS went."""
    return {"status": result.status, "error": result.error, "sent_to": result.sent_to}


def bulk_answer(result: BulkResult) -> dict[str, object]:
    """How one message to many Students went, with each failed message and its reason."""
    return {
        "sent": result.sent,
        "failed": len(result.failed),
        "failed_messages": [message_answer(message) for message in result.failed],
    }
