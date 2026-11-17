"""Notifications as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from access.policy import why_cannot_link_student
from clock_in.models import WorkFromHome
from notifications.models import BIN_KINDS, Notification, NotificationKind
from notifications.types import NotificationPage
from staff.models import Staff
from transfer_requests.models import TransferRequest


def notification_answer(notification: Notification, viewer: Staff) -> dict[str, object]:
    """One notification; Teachers never see which Student its Task is about (spec 22)."""
    request = notification.transfer_request
    if request is not None:
        return _transfer_answer(notification, request)
    if notification.kind in BIN_KINDS:
        return _bin_answer(notification)
    if notification.work_from_home is not None:
        return _work_from_home_answer(notification, notification.work_from_home)
    task = notification.task
    assert task is not None  # noqa: S101  # the database allows only a Task or a request
    student = task.student
    shows_student = student is not None and why_cannot_link_student(viewer) is None
    given = notification.kind == NotificationKind.ASSIGNED
    return {
        "id": notification.pk,
        "kind": notification.kind,
        "created_at": notification.created_at.isoformat(),
        "is_read": notification.read_at is not None,
        "task": {"id": task.pk, "title": task.title, "due_on": task.due_on.isoformat()},
        "by": {"id": task.created_by_id, "name": task.created_by_name} if given else None,
        "student": {"id": student.pk, "name": student.name} if shows_student and student else None,
        "transfer_request": None,
        "bin": None,
        "work_from_home": None,
    }


def _bin_answer(notification: Notification) -> dict[str, object]:
    """Something put in the Bin (to Admins), or restored or deleted (to whoever put it there).

    Only Admins and those who change Students' Documents get these, so the Student is named.
    """
    student = notification.student
    assert student is not None  # noqa: S101  # the database requires it for these kinds
    return {
        "id": notification.pk,
        "kind": notification.kind,
        "created_at": notification.created_at.isoformat(),
        "is_read": notification.read_at is not None,
        "task": None,
        "by": {"id": notification.by_staff_id, "name": notification.by_name},
        "student": {"id": student.pk, "name": student.name},
        "transfer_request": None,
        "bin": {"item": notification.bin_item, "reason": notification.bin_reason},
        "work_from_home": None,
    }


def _transfer_answer(notification: Notification, request: TransferRequest) -> dict[str, object]:
    """A Transfer Request asked for (by who asked) or answered (by who answered) (spec 44b).

    Only Staff who see Students get these, so the Student is always named.
    """
    if notification.kind == NotificationKind.TRANSFER_ASKED:
        by: dict[str, object] = {"id": request.asked_by_id, "name": request.asked_by.name}
    else:
        by = {"id": request.answered_by_id, "name": request.answered_by_name}
    return {
        "id": notification.pk,
        "kind": notification.kind,
        "created_at": notification.created_at.isoformat(),
        "is_read": notification.read_at is not None,
        "task": None,
        "by": by,
        "student": {"id": request.student_id, "name": request.student.name},
        "transfer_request": {"id": request.pk, "status": request.status},
        "bin": None,
        "work_from_home": None,
    }


def _work_from_home_answer(notification: Notification, dates: WorkFromHome) -> dict[str, object]:
    """Work From Home dates asked for (to Admins), or answered or set (to the person).

    The reason stays on the person's page; the bell names who acted and which dates.
    """
    return {
        "id": notification.pk,
        "kind": notification.kind,
        "created_at": notification.created_at.isoformat(),
        "is_read": notification.read_at is not None,
        "task": None,
        "by": {"id": notification.by_staff_id, "name": notification.by_name},
        "student": None,
        "transfer_request": None,
        "bin": None,
        "work_from_home": {
            "id": dates.pk,
            "staff_id": dates.staff_id,
            "starts_on": dates.starts_on.isoformat(),
            "ends_on": None if dates.ends_on is None else dates.ends_on.isoformat(),
            "status": dates.status,
        },
    }


def page_answer(found: NotificationPage, page_size: int, viewer: Staff) -> dict[str, object]:
    """One page of notifications with the unread count for the bell."""
    return {
        "count": found.count,
        "page": found.page,
        "page_size": page_size,
        "unread_count": found.unread_count,
        "results": [notification_answer(item, viewer) for item in found.notifications],
    }
