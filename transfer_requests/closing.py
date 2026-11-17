"""Closing Waiting Transfer Requests that can no longer be answered (spec 44e).

Kept apart from the services so the Student and Staff services can call it without importing
the services that call them.
"""

from __future__ import annotations

import logging

from django.db.models import QuerySet
from django.utils import timezone

from staff.models import Staff
from transfer_requests.models import TransferRequest, TransferRequestStatus

# Why a request closed by itself, shown with it.
GIVEN_ANOTHER_WAY_MESSAGE = "The Student was given to a counsellor another way."
REQUESTER_LEFT_MESSAGE = "The person who asked has left the office."

logger = logging.getLogger(__name__)


def close_waiting_for_student(student_id: int) -> None:
    """Close the Student's Waiting request: they were assigned or Transferred another way."""
    _close(TransferRequest.objects.filter(student_id=student_id), GIVEN_ANOTHER_WAY_MESSAGE)


def close_waiting_asked_by(staff: Staff) -> None:
    """Close every Waiting request `staff` sent: they have left the office."""
    _close(TransferRequest.objects.filter(asked_by=staff), REQUESTER_LEFT_MESSAGE)


def _close(requests: QuerySet[TransferRequest], why: str) -> None:
    closed = requests.filter(status=TransferRequestStatus.WAITING).update(
        status=TransferRequestStatus.CLOSED, answer_reason=why, answered_at=timezone.now()
    )
    if closed:
        logger.info("Transfer requests closed: count=%s, why=%s", closed, why)
