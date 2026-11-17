"""Public services for Commission (spec 76d, 76e). Call inside the Consultancy's schema.

Who may see or change Commission is the views' job, through `access.policy`. Commission belongs
to the Accounts records, not a Student's History (which tracks Stage, Note, Payment and
Transfer changes).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count, QuerySet, Sum
from django.utils import timezone

from commission.models import Commission, CommissionStatus
from commission.types import NewCommission, ReceivedPeriod
from staff.actors import actor_name, stored_staff
from staff.models import Staff

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommissionTotals:
    """What all Commission comes to by status, and what is still to come from Universities."""

    waiting_for_enrolment: int
    claim_sent: int
    received: int

    @property
    def to_come(self) -> int:
        """Everything not Received yet."""
        return self.waiting_for_enrolment + self.claim_sent


@dataclass(frozen=True)
class ReceivedTotal:
    """Received Commission over some days: how much, and how many."""

    total: int
    count: int


def every_commission() -> QuerySet[Commission]:
    """Every Commission with its Student and University, newest first."""
    return Commission.objects.select_related("student", "university").order_by("-pk")


def commission_totals() -> CommissionTotals:
    """What all Commission comes to by status."""
    sums = {
        row["status"]: row["amount"]
        for row in Commission.objects.order_by().values("status").annotate(amount=Sum("amount"))
    }
    return CommissionTotals(
        waiting_for_enrolment=sums.get(CommissionStatus.WAITING_FOR_ENROLMENT, 0),
        claim_sent=sums.get(CommissionStatus.CLAIM_SENT, 0),
        received=sums.get(CommissionStatus.RECEIVED, 0),
    )


def received_between(period: ReceivedPeriod) -> ReceivedTotal:
    """The Commission Received between two days, both included (money in; spec 76e)."""
    found = Commission.objects.filter(status=CommissionStatus.RECEIVED)
    if period.received_from is not None:
        found = found.filter(received_on__gte=period.received_from)
    if period.received_to is not None:
        found = found.filter(received_on__lte=period.received_to)
    sums = found.aggregate(total=Sum("amount"), count=Count("pk"))
    return ReceivedTotal(total=sums["total"] or 0, count=sums["count"])


def add_commission(details: NewCommission, by: Staff) -> Commission:
    """Record Commission, Waiting for enrolment."""
    commission = Commission.objects.create(
        student=details.student,
        university=details.university,
        intake=details.intake,
        amount=details.amount,
        recorded_by=stored_staff(by),
        recorded_by_name=actor_name(by),
    )
    logger.info(
        "Commission added: commission_id=%s, student_id=%s, by_staff_id=%s",
        commission.pk,
        details.student.pk,
        by.pk,
    )
    return commission


def change_status(commission_id: int, status: CommissionStatus, by: Staff) -> Commission:
    """Move Commission to `status`. Received keeps the day it first arrived; leaving clears it.

    Raises:
        Commission.DoesNotExist: There is no such Commission.
    """
    with transaction.atomic():
        commission = (
            Commission.objects.select_for_update()
            .select_related("student", "university")
            .get(pk=commission_id)
        )
        if status != CommissionStatus.RECEIVED:
            commission.received_on = None
        elif commission.received_on is None:
            commission.received_on = timezone.localdate()
        commission.status = status
        commission.save(update_fields=["status", "received_on"])
    logger.info(
        "Commission status changed: commission_id=%s, status=%s, by_staff_id=%s",
        commission.pk,
        status.value,
        by.pk,
    )
    return commission
