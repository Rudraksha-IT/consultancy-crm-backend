"""Commission: money a University pays the Consultancy for a Student (GLOSSARY; spec 76d)."""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from catalogue.models import University
from config.public_ids import public_id_field
from payments.models import MAX_AMOUNT
from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import Student

# An Intake's month, "YYYY-MM" (AD), as Applications keep it.
INTAKE_LENGTH = len("YYYY-MM")
# Room for the longest status value, "waiting_for_enrolment".
STATUS_MAX_LENGTH = 32


class CommissionStatus(models.TextChoices):
    """Where a Commission is: to be confirmed with the Consultancy (spec Further Notes)."""

    WAITING_FOR_ENROLMENT = "waiting_for_enrolment", "Waiting for enrolment"
    CLAIM_SENT = "claim_sent", "Claim sent"
    RECEIVED = "received", "Received"


class Commission(models.Model):
    """What one University owes for one Student's Intake, in whole NPR, and where it is.

    `received_on` is set when it's marked Received and cleared if it's moved back; Received
    Commission counts as money in on that day.
    """

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="commissions")
    university = models.ForeignKey(University, on_delete=models.PROTECT, related_name="+")
    intake = models.CharField(max_length=INTAKE_LENGTH)
    amount = models.PositiveBigIntegerField()
    status = models.CharField(
        max_length=STATUS_MAX_LENGTH,
        choices=CommissionStatus.choices,
        default=CommissionStatus.WAITING_FOR_ENROLMENT,
    )
    received_on = models.DateField(null=True, blank=True)
    # None when a Platform Admin did it during Support Access; the name says who (issue 27).
    recorded_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    # The name as it was, so the record reads the same after a rename or deactivation.
    recorded_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-pk",)
        indexes = (models.Index(fields=("received_on",), name="commission_received_on"),)
        constraints = (
            models.CheckConstraint(
                condition=Q(amount__gte=1, amount__lte=MAX_AMOUNT),
                name="commission_amount_in_range",
            ),
            models.CheckConstraint(
                condition=Q(status=CommissionStatus.RECEIVED, received_on__isnull=False)
                | (~Q(status=CommissionStatus.RECEIVED) & Q(received_on__isnull=True)),
                name="commission_received_on_only_when_received",
            ),
        )

    def __str__(self) -> str:
        return f"{self.intake}: {self.amount}"
