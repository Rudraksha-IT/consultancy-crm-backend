"""Public services for notifications (spec 80, 44b). Call inside the Consultancy's schema.

A Task given to someone else notifies them at once. A Task falling due is noticed when its
assignee next looks at their notifications (the bell asks every minute), so one server needs no
scheduled job: on its due day they get "due today", and if it's still open afterwards, "overdue".
"""

from __future__ import annotations

import logging
from datetime import date

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from clock_in.models import WorkFromHome, WorkFromHomeStatus
from notifications.models import WORK_FROM_HOME_KINDS, Notification, NotificationKind
from notifications.types import BinNotice, NotificationPage
from staff.actors import actor_name, stored_staff
from staff.models import Role, Staff, StaffStatus
from tasks.models import Task
from transfer_requests.models import TransferRequest, TransferRequestStatus

logger = logging.getLogger(__name__)

# Notifications per page of the list.
NOTIFICATIONS_PER_PAGE = 20


def notify_task_given(task: Task) -> None:
    """Tell the assignee they were given `task`, unless they gave it to themselves."""
    if task.assignee_id == task.created_by_id:
        return
    notification = Notification.objects.create(
        recipient_id=task.assignee_id, kind=NotificationKind.ASSIGNED, task=task
    )
    logger.info(
        "Notification created: notification_id=%s, kind=%s, task_id=%s, staff_id=%s",
        notification.pk,
        notification.kind,
        task.pk,
        task.assignee_id,
    )


def notify_transfer_asked(request: TransferRequest, recipients: list[Staff]) -> None:
    """Tell everyone who may answer `request` that it was sent."""
    Notification.objects.bulk_create(
        Notification(
            recipient=staff, kind=NotificationKind.TRANSFER_ASKED, transfer_request=request
        )
        for staff in recipients
    )
    logger.info(
        "Transfer request notifications created: transfer_request_id=%s, count=%s",
        request.pk,
        len(recipients),
    )


def notify_transfer_answered(request: TransferRequest) -> None:
    """Tell whoever asked that `request` was approved or declined."""
    approved = request.status == TransferRequestStatus.APPROVED
    kind = NotificationKind.TRANSFER_APPROVED if approved else NotificationKind.TRANSFER_DECLINED
    notification = Notification.objects.create(
        recipient_id=request.asked_by_id, kind=kind, transfer_request=request
    )
    logger.info(
        "Notification created: notification_id=%s, kind=%s, transfer_request_id=%s, staff_id=%s",
        notification.pk,
        kind,
        request.pk,
        request.asked_by_id,
    )


def notify_bin_added(notice: BinNotice) -> None:
    """Tell every active Admin that something was put in the Bin (issue 79)."""
    admins = Staff.objects.filter(role=Role.ADMIN, status=StaffStatus.ACTIVE)
    Notification.objects.bulk_create(
        _bin_notification(notice, admin.pk, NotificationKind.BIN_ADDED) for admin in admins
    )
    logger.info(
        "Bin notifications created: student_id=%s, count=%s", notice.student.pk, len(admins)
    )


def notify_bin_answered(notice: BinNotice, recipient_id: int | None, *, restored: bool) -> None:
    """Tell whoever put it in the Bin that an Admin restored it or deleted it for good.

    Nobody is told when a Platform Admin put it there (they are not Staff).
    """
    if recipient_id is None:
        return
    kind = NotificationKind.BIN_RESTORED if restored else NotificationKind.BIN_DELETED
    notification = _bin_notification(notice, recipient_id, kind)
    notification.save()
    logger.info(
        "Notification created: notification_id=%s, kind=%s, student_id=%s, staff_id=%s",
        notification.pk,
        kind,
        notice.student.pk,
        recipient_id,
    )


def _bin_notification(notice: BinNotice, recipient_id: int, kind: str) -> Notification:
    return Notification(
        recipient_id=recipient_id,
        kind=kind,
        student=notice.student,
        bin_item=notice.item,
        bin_reason=notice.reason,
        by_staff=stored_staff(notice.by),
        by_name=actor_name(notice.by),
    )


def notify_work_from_home_asked(dates: WorkFromHome, asker: Staff) -> None:
    """Tell every active Admin that `asker` asked for Work From Home dates (spec 132)."""
    admins = list(Staff.objects.filter(role=Role.ADMIN, status=StaffStatus.ACTIVE))
    Notification.objects.bulk_create(
        Notification(
            recipient=admin,
            kind=NotificationKind.WORK_FROM_HOME_ASKED,
            work_from_home=dates,
            by_staff=stored_staff(asker),
            by_name=actor_name(asker),
        )
        for admin in admins
    )
    logger.info(
        "Work from home notifications created: work_from_home_id=%s, count=%s",
        dates.pk,
        len(admins),
    )


def notify_work_from_home_answered(dates: WorkFromHome, by: Staff) -> None:
    """Tell the person that an Admin approved, declined or set their dates (spec 132-133)."""
    if dates.set_directly:
        kind = NotificationKind.WORK_FROM_HOME_SET
    elif dates.status == WorkFromHomeStatus.APPROVED:
        kind = NotificationKind.WORK_FROM_HOME_APPROVED
    else:
        kind = NotificationKind.WORK_FROM_HOME_DECLINED
    notification = Notification.objects.create(
        recipient_id=dates.staff_id,
        kind=kind,
        work_from_home=dates,
        by_staff=stored_staff(by),
        by_name=actor_name(by),
    )
    logger.info(
        "Notification created: notification_id=%s, kind=%s, work_from_home_id=%s, staff_id=%s",
        notification.pk,
        kind,
        dates.pk,
        dates.staff_id,
    )


def notify_tasks_due(staff: Staff, today: date) -> None:
    """Notify `staff` once about each open Task of theirs due `today`, and once if overdue."""
    open_tasks = Task.objects.filter(assignee=staff, is_done=False).only("pk")
    due = {
        NotificationKind.DUE_TODAY: open_tasks.filter(due_on=today),
        NotificationKind.OVERDUE: open_tasks.filter(due_on__lt=today),
    }
    wanted = [
        Notification(recipient=staff, kind=kind, task=task)
        for kind, tasks in due.items()
        for task in tasks.exclude(notifications__kind=kind)
    ]
    if wanted:
        # Two requests at once may both add one; the database keeps only the first.
        Notification.objects.bulk_create(wanted, ignore_conflicts=True)
        logger.info("Due notifications added: staff_id=%s, count=%s", staff.pk, len(wanted))


def notifications_for(
    staff: Staff, page: int, today: date, *, clock_in_on: bool = True
) -> NotificationPage:
    """One page of `staff`'s notifications, newest first, after noticing Tasks that fell due.

    While the Clock-in Module is off, its Work From Home notifications are hidden, not deleted.

    Raises:
        django.core.paginator.EmptyPage: There is no such page.
    """
    notify_tasks_due(staff, today)
    mine = _with_task(_shown(Notification.objects.filter(recipient=staff), clock_in_on))
    found = Paginator(mine, NOTIFICATIONS_PER_PAGE).page(page)
    return NotificationPage(
        notifications=list(found),
        count=found.paginator.count,
        page=found.number,
        unread_count=_shown(_unread(staff), clock_in_on).count(),
    )


def _shown(notifications: QuerySet[Notification], clock_in_on: bool) -> QuerySet[Notification]:
    if clock_in_on:
        return notifications
    return notifications.exclude(kind__in=WORK_FROM_HOME_KINDS)


def set_read(staff: Staff, notification_id: int, *, read: bool) -> Notification:
    """Mark one of `staff`'s notifications read (keeping when it first was), or unread again.

    Raises:
        Notification.DoesNotExist: `staff` has no such notification.
    """
    with transaction.atomic():
        notification = Notification.objects.select_for_update().get(
            pk=notification_id, recipient=staff
        )
        if (notification.read_at is not None) != read:
            notification.read_at = timezone.now() if read else None
            notification.save(update_fields=["read_at"])
    logger.info(
        "Notification marked %s: notification_id=%s, staff_id=%s",
        "read" if read else "unread",
        notification.pk,
        staff.pk,
    )
    return _with_task(Notification.objects.all()).get(pk=notification.pk)


def read_all(staff: Staff) -> int:
    """Mark every unread notification of `staff` read; return how many were."""
    marked = _unread(staff).update(read_at=timezone.now())
    logger.info("Notifications all marked read: staff_id=%s, count=%s", staff.pk, marked)
    return marked


def _unread(staff: Staff) -> QuerySet[Notification]:
    return Notification.objects.filter(recipient=staff, read_at__isnull=True)


def _with_task(notifications: QuerySet[Notification]) -> QuerySet[Notification]:
    return notifications.select_related(
        "task",
        "task__student",
        "task__created_by",
        "transfer_request__student",
        "transfer_request__asked_by",
        "transfer_request__answered_by",
        "student",
        "work_from_home__staff",
        "by_staff",
    )
