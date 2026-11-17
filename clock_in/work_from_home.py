"""Work From Home dates: asking, answering, setting directly, ending (spec 131-133, issue 87).

Call inside the Consultancy's schema. Who may do it is the views' job, through `access.policy`.
Only Approved dates give permission to clock in from any network, and only on the days they
cover. An empty end lasts until an Admin ends it. Nothing here ever reads the client address.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from clock_in.models import WorkFromHome, WorkFromHomeStatus
from notifications.services import notify_work_from_home_answered, notify_work_from_home_asked
from staff.actors import actor_name, is_platform_visitor, stored_staff
from staff.models import Staff

NOT_WAITING_MESSAGE = "These dates are no longer waiting for an answer."
CANT_END_MESSAGE = "These dates have already ended."
# The newest dates shown on a person's list.
DATES_SHOWN = 100

logger = logging.getLogger(__name__)


class WorkFromHomeRefusedError(Exception):
    """The dates can't be changed that way now; the message says why in plain words."""


@dataclass(frozen=True)
class NewDates:
    """Dates asked for or set: start, optional end, and why (already checked)."""

    starts_on: date
    ends_on: date | None
    reason: str


def dates_of(staff_id: int) -> QuerySet[WorkFromHome]:
    """One person's dates, newest start first."""
    return WorkFromHome.objects.filter(staff_id=staff_id).order_by("-starts_on", "-pk")[
        :DATES_SHOWN
    ]


def approved_on(staff_id: int, day: date) -> bool:
    """Whether Approved dates of `staff_id` cover `day`."""
    return (
        WorkFromHome.objects.filter(
            staff_id=staff_id, status=WorkFromHomeStatus.APPROVED, starts_on__lte=day
        )
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=day))
        .exists()
    )


def waiting() -> QuerySet[WorkFromHome]:
    """Every request waiting for an Admin, oldest first."""
    return WorkFromHome.objects.filter(status=WorkFromHomeStatus.WAITING).order_by("asked_at", "pk")


def ask(staff: Staff, dates: NewDates) -> WorkFromHome:
    """Ask for dates; every active Admin is told (spec 132)."""
    with transaction.atomic():
        asked = WorkFromHome.objects.create(
            staff=staff,
            starts_on=dates.starts_on,
            ends_on=dates.ends_on,
            reason=dates.reason,
            status=WorkFromHomeStatus.WAITING,
            asked_by_staff=staff,
            asked_by_name=actor_name(staff),
        )
        notify_work_from_home_asked(asked, staff)
    logger.info("Work from home asked: work_from_home_id=%s", asked.pk)
    return asked


def set_directly(staff: Staff, dates: NewDates, by: Staff) -> WorkFromHome:
    """An Admin sets dates for `staff`: Approved at once, and the person is told (spec 133)."""
    now = timezone.now()
    with transaction.atomic():
        made = WorkFromHome.objects.create(
            staff=staff,
            starts_on=dates.starts_on,
            ends_on=dates.ends_on,
            reason=dates.reason,
            status=WorkFromHomeStatus.APPROVED,
            asked_at=now,
            asked_by_staff=stored_staff(by),
            asked_by_name=actor_name(by),
            asked_by_platform_admin=is_platform_visitor(by),
            answered_at=now,
            answered_by_staff=stored_staff(by),
            answered_by_name=actor_name(by),
            answered_by_platform_admin=is_platform_visitor(by),
        )
        notify_work_from_home_answered(made, by)
    logger.info("Work from home set: work_from_home_id=%s", made.pk)
    return made


def answer(dates_id: int, by: Staff, *, approve: bool) -> WorkFromHome:
    """Approve or decline a waiting request; the person is told (spec 132-133).

    Raises:
        WorkFromHome.DoesNotExist: No such dates here.
        WorkFromHomeRefusedError: They are no longer waiting.
    """
    with transaction.atomic():
        dates = WorkFromHome.objects.select_for_update().get(pk=dates_id)
        if dates.status != WorkFromHomeStatus.WAITING:
            raise WorkFromHomeRefusedError(NOT_WAITING_MESSAGE)
        dates.status = WorkFromHomeStatus.APPROVED if approve else WorkFromHomeStatus.DECLINED
        dates.answered_at = timezone.now()
        dates.answered_by_staff = stored_staff(by)
        dates.answered_by_name = actor_name(by)
        dates.answered_by_platform_admin = is_platform_visitor(by)
        dates.save()
        notify_work_from_home_answered(dates, by)
    logger.info("Work from home %s: work_from_home_id=%s", dates.status, dates.pk)
    return dates


def cancel(dates_id: int, staff: Staff) -> WorkFromHome:
    """The person who asked takes back a waiting request.

    Raises:
        WorkFromHome.DoesNotExist: No such request of theirs.
        WorkFromHomeRefusedError: It is no longer waiting.
    """
    with transaction.atomic():
        dates = WorkFromHome.objects.select_for_update().get(pk=dates_id, staff_id=staff.pk)
        if dates.status != WorkFromHomeStatus.WAITING:
            raise WorkFromHomeRefusedError(NOT_WAITING_MESSAGE)
        dates.status = WorkFromHomeStatus.CANCELLED
        dates.save(update_fields=["status"])
    logger.info("Work from home cancelled: work_from_home_id=%s", dates.pk)
    return dates


def end(dates_id: int) -> WorkFromHome:
    """An Admin ends Approved dates: started ones end today; ones not started give nothing.

    Days already worked keep their "Home" label, so started dates stay Approved up to today.

    Raises:
        WorkFromHome.DoesNotExist: No such dates here.
        WorkFromHomeRefusedError: They aren't Approved, or have already ended.
    """
    today = timezone.localdate()
    with transaction.atomic():
        dates = WorkFromHome.objects.select_for_update().get(pk=dates_id)
        ended = dates.ends_on is not None and dates.ends_on < today
        if dates.status != WorkFromHomeStatus.APPROVED or ended:
            raise WorkFromHomeRefusedError(CANT_END_MESSAGE)
        if dates.starts_on > today:
            dates.status = WorkFromHomeStatus.ENDED
        else:
            dates.ends_on = today
        dates.save(update_fields=["status", "ends_on"])
    logger.info("Work from home ended: work_from_home_id=%s", dates.pk)
    return dates
