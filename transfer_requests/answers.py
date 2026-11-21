"""Transfer Requests as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from django.core.paginator import Page

from access.policy import why_cannot_answer_transfer, why_cannot_cancel_transfer
from staff.models import Staff
from transfer_requests.models import TransferRequest, TransferRequestStatus


def transfer_request_answer(request: TransferRequest, viewer: Staff) -> dict[str, object]:
    """One request, the Student's counsellor now, and what `viewer` may do with it."""
    student = request.student
    counsellor = student.assigned_counsellor
    is_waiting = request.status == TransferRequestStatus.WAITING
    answered = request.status in (TransferRequestStatus.APPROVED, TransferRequestStatus.DECLINED)
    return {
        "id": str(request.public_id),
        "student": {"id": student.public_id, "name": student.name},
        "asked_by": {"id": str(request.asked_by.public_id), "name": request.asked_by.name},
        "reason": request.reason,
        "asked_at": request.asked_at.isoformat(),
        "status": request.status,
        "counsellor": None
        if counsellor is None
        else {"id": str(counsellor.public_id), "name": counsellor.name},
        "answered_by": (
            {
                "name": request.answered_by_name,
                "is_platform_admin": request.answered_by_platform_admin,
            }
            if answered
            else None
        ),
        "answer_reason": request.answer_reason,
        "answered_at": None if request.answered_at is None else request.answered_at.isoformat(),
        "taken_from": request.taken_from_name,
        "can_answer": is_waiting
        and why_cannot_answer_transfer(viewer, student, request.asked_by_id) is None,
        "can_cancel": is_waiting
        and why_cannot_cancel_transfer(viewer, request.asked_by_id) is None,
    }


def page_answer(
    found: Page[TransferRequest], viewer: Staff, waiting_for_me_count: int
) -> dict[str, object]:
    """One page of requests, and how many wait for the viewer's answer (the menu count)."""
    return {
        "count": found.paginator.count,
        "page": found.number,
        "page_size": found.paginator.per_page,
        "waiting_for_me_count": waiting_for_me_count,
        "results": [transfer_request_answer(request, viewer) for request in found],
    }
