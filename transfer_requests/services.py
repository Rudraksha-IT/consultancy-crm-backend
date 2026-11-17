"""Public services for Transfer Requests (spec 44a-44e). Call inside the Consultancy's schema.

Who may ask, answer or cancel is the views' job, through `access.policy`. Each change locks the
request, checks it is still waiting, and runs in one transaction with what it causes: approving
makes the Transfer (with History) through the Student services.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum

from django.core.paginator import Page, Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from access.policy import TRANSFER_ASKING_ROLES, why_cannot_answer_transfer
from notifications.services import notify_transfer_answered, notify_transfer_asked
from staff.actors import actor_name, is_platform_visitor, stored_staff
from staff.models import Role, Staff, StaffStatus
from students.journey import assign_counsellor, transfer_student
from students.models import Student
from transfer_requests.exceptions import (
    AlreadyAskedError,
    NotWaitingError,
    RequesterCantCounselError,
)
from transfer_requests.models import TransferRequest, TransferRequestStatus

# Requests per page of the Transfer Requests list.
TRANSFER_REQUESTS_PER_PAGE = 20

logger = logging.getLogger(__name__)


class Box(StrEnum):
    """Which requests the list shows (spec 44c, 44d)."""

    WAITING_FOR_ME = "waiting_for_me"
    SENT = "sent"
    ALL = "all"  # Admins only.


def approval_detail(asked_by: str, approved_by: str) -> str:
    """What History says about an approved request, e.g. "Asked by X, approved by Y."."""
    return f"Asked by {asked_by}, approved by {approved_by}."


def ask_transfer(student: Student, by: Staff, reason: str) -> TransferRequest:
    """Send a request from `by` to take `student`, and notify everyone who may answer it.

    Raises:
        AlreadyAskedError: A request for the Student is already waiting.
    """
    try:
        with transaction.atomic():
            locked = (
                Student.objects.select_for_update(of=("self",))
                .select_related("assigned_counsellor")
                .get(pk=student.pk)
            )
            if waiting_request_for(locked) is not None:
                raise AlreadyAskedError
            counsellor = locked.assigned_counsellor
            request = TransferRequest.objects.create(
                student=locked,
                asked_by=by,
                reason=reason,
                taken_from_name="" if counsellor is None else counsellor.name,
            )
            notify_transfer_asked(request, _answerers(locked, by))
    except IntegrityError as error:  # another request was stored in the meantime
        raise AlreadyAskedError from error
    logger.info(
        "Transfer request sent: transfer_request_id=%s, student_id=%s, by_staff_id=%s",
        request.pk,
        student.pk,
        by.pk,
    )
    return request


def approve_transfer(request: TransferRequest, by: Staff) -> TransferRequest:
    """Approve: the Student becomes the asker's, History names who asked and who approved.

    Raises:
        NotWaitingError: It was answered, cancelled or closed already.
        RequesterCantCounselError: The asker can no longer be a Student's counsellor.
        students.exceptions.StudentChangedError: The Student changed while this was on its way.
    """
    with transaction.atomic():
        locked = _locked_waiting(request)
        asker = locked.asked_by
        if asker.status != StaffStatus.ACTIVE or asker.role not in TRANSFER_ASKING_ROLES:
            raise RequesterCantCounselError
        student = locked.student
        previous = student.assigned_counsellor
        _answer(locked, TransferRequestStatus.APPROVED, by)
        locked.taken_from_name = "" if previous is None else previous.name
        locked.save(update_fields=["taken_from_name"])
        detail = approval_detail(asker.name, actor_name(by))
        if previous is None:
            assign_counsellor(student, asker, by=by, detail=detail)
        else:
            transfer_student(student, asker, by=by, detail=detail)
        notify_transfer_answered(locked)
    _log_answer(locked, by)
    return locked


def decline_transfer(request: TransferRequest, by: Staff, reason: str) -> TransferRequest:
    """Decline, with an optional reason the asker sees.

    Raises:
        NotWaitingError: It was answered, cancelled or closed already.
    """
    with transaction.atomic():
        locked = _locked_waiting(request)
        locked.answer_reason = reason
        _answer(locked, TransferRequestStatus.DECLINED, by)
        notify_transfer_answered(locked)
    _log_answer(locked, by)
    return locked


def cancel_transfer(request: TransferRequest, by: Staff) -> TransferRequest:
    """Cancel a request its asker no longer wants.

    Raises:
        NotWaitingError: It was answered, cancelled or closed already.
    """
    with transaction.atomic():
        locked = _locked_waiting(request)
        locked.status = TransferRequestStatus.CANCELLED
        locked.answered_at = timezone.now()
        locked.save(update_fields=["status", "answered_at"])
    _log_answer(locked, by)
    return locked


def transfer_request(request_id: int) -> TransferRequest:
    """One request with its Student, asker and the Student's counsellor.

    Raises:
        TransferRequest.DoesNotExist: There is no such request.
    """
    return _with_people(TransferRequest.objects.all()).get(pk=request_id)


def waiting_request_for(student: Student) -> TransferRequest | None:
    """The Student's Waiting request, if any."""
    waiting = TransferRequest.objects.filter(student=student, status=TransferRequestStatus.WAITING)
    return _with_people(waiting).first()


def requests_in(box: Box, staff: Staff, page: int) -> Page[TransferRequest]:
    """One page of the requests in `box` for `staff`, newest first.

    Raises:
        django.core.paginator.EmptyPage: There is no such page.
    """
    if box is Box.WAITING_FOR_ME:
        found = waiting_for(staff)
    elif box is Box.SENT:
        found = TransferRequest.objects.filter(asked_by=stored_staff(staff))
    else:
        found = TransferRequest.objects.all()
    ordered = _with_people(found).order_by("-asked_at", "-pk")
    return Paginator(ordered, TRANSFER_REQUESTS_PER_PAGE).page(page)


def waiting_for(staff: Staff) -> QuerySet[TransferRequest]:
    """The Waiting requests `staff` may answer, as `access.policy` decides (spec 44b, 44d).

    Admins: all but their own. A counsellor: those for their Students. Front Desk: those for
    Students with no counsellor. Anyone else: none.
    """
    waiting = TransferRequest.objects.filter(status=TransferRequestStatus.WAITING)
    if staff.status != StaffStatus.ACTIVE or staff.role not in _ANSWERABLE:
        return waiting.none()
    if not is_platform_visitor(staff):
        waiting = waiting.exclude(asked_by_id=staff.pk)
    return waiting.filter(_ANSWERABLE[Role(staff.role)](staff))


# For each Role that answers, which Waiting requests it may answer (see `waiting_for`).
_ANSWERABLE: dict[Role, Callable[[Staff], Q]] = {
    Role.ADMIN: lambda _staff: Q(),
    Role.COUNSELLOR: lambda staff: Q(student__assigned_counsellor_id=staff.pk),
    Role.FRONT_DESK: lambda _staff: Q(student__assigned_counsellor__isnull=True),
}


def _answerers(student: Student, asker: Staff) -> list[Staff]:
    """The active Staff who may answer a request from `asker` for `student`."""
    candidates = Staff.objects.filter(
        status=StaffStatus.ACTIVE, role__in=(Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)
    ).order_by("pk")
    return [
        staff
        for staff in candidates
        if why_cannot_answer_transfer(staff, student, asked_by_id=asker.pk) is None
    ]


def _locked_waiting(request: TransferRequest) -> TransferRequest:
    locked = (
        TransferRequest.objects.select_for_update(of=("self",))
        .select_related("asked_by", "student", "student__assigned_counsellor")
        .get(pk=request.pk)
    )
    if locked.status != TransferRequestStatus.WAITING:
        raise NotWaitingError
    return locked


def _answer(request: TransferRequest, outcome: TransferRequestStatus, by: Staff) -> None:
    request.status = outcome
    request.answered_by = stored_staff(by)
    request.answered_by_name = actor_name(by)
    request.answered_by_platform_admin = is_platform_visitor(by)
    request.answered_at = timezone.now()
    request.save(
        update_fields=[
            "status",
            "answered_by",
            "answered_by_name",
            "answered_by_platform_admin",
            "answer_reason",
            "answered_at",
        ]
    )


def _log_answer(request: TransferRequest, by: Staff) -> None:
    logger.info(
        "Transfer request %s: transfer_request_id=%s, student_id=%s, by_staff_id=%s",
        request.status,
        request.pk,
        request.student_id,
        by.pk,
    )


def _with_people(requests: QuerySet[TransferRequest]) -> QuerySet[TransferRequest]:
    return requests.select_related("student", "student__assigned_counsellor", "asked_by")
