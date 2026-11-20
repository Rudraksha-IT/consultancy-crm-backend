"""A Transfer Request: a counsellor asks to take over a Student (spec 44a-44e; GLOSSARY)."""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils import timezone

from config.public_ids import public_id_field
from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import Student

# The longest reason for asking, and for an answer.
TRANSFER_REASON_MAX_LENGTH = 500
STATUS_MAX_LENGTH = 20


class TransferRequestStatus(models.TextChoices):
    """Where a request is. Only a Waiting one can be answered or cancelled."""

    WAITING = "waiting", "Waiting"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Cancelled"
    # Closed by itself: the Student got a counsellor another way, or the asker left.
    CLOSED = "closed", "Closed"


class TransferRequest(models.Model):
    """One request by a Staff member to become a Student's Assigned Counsellor."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="transfer_requests")
    asked_by = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=TRANSFER_REASON_MAX_LENGTH)
    asked_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(
        max_length=STATUS_MAX_LENGTH,
        choices=TransferRequestStatus.choices,
        default=TransferRequestStatus.WAITING,
    )
    # Who approved or declined it: a Staff member, or a Platform Admin (no Staff link, own name).
    answered_by = models.ForeignKey(
        Staff, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    answered_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, blank=True, default="")
    answered_by_platform_admin = models.BooleanField(default=False)
    # The answer's optional reason, or why the request closed by itself.
    answer_reason = models.CharField(max_length=TRANSFER_REASON_MAX_LENGTH, blank=True, default="")
    # When it stopped waiting, whichever way.
    answered_at = models.DateTimeField(null=True, blank=True)
    # Who the Student was with: their counsellor when asked, then, on approval, the one they were
    # taken from. Empty for no counsellor. Kept as it was, so later Transfers change nothing.
    taken_from_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, blank=True, default="")

    class Meta:
        ordering = ("-asked_at", "-pk")
        indexes = (
            models.Index(fields=("status", "-asked_at"), name="transfer_requests_status"),
            models.Index(fields=("asked_by", "-asked_at"), name="transfer_requests_asked_by"),
        )
        constraints = (
            models.UniqueConstraint(
                fields=("student",),
                condition=Q(status=TransferRequestStatus.WAITING),
                name="transfer_requests_one_waiting_per_student",
            ),
            models.CheckConstraint(
                condition=Q(status__in=TransferRequestStatus.values),
                name="transfer_requests_known_status",
            ),
            models.CheckConstraint(condition=~Q(reason=""), name="transfer_requests_reason_given"),
        )

    def __str__(self) -> str:
        return f"Transfer request {self.pk} ({self.status})"
