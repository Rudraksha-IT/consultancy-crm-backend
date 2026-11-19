"""Fees: what a Student is expected to pay for one service (GLOSSARY: Fee; spec 76a)."""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from fee_types.models import NOTE_MAX_LENGTH, FeeType
from payments.models import MAX_AMOUNT
from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import Student


class Fee(models.Model):
    """One Fee for a Student: its Fee Type, an optional note and the amount in whole NPR.

    Balances are worked out from Fees and Payments and never stored (`fees.balance`).
    """

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="fees")
    fee_type = models.ForeignKey(FeeType, on_delete=models.PROTECT, related_name="+")
    # Optional, needed for Other; fees typed before Fee Types keep their old text here.
    note = models.CharField(max_length=NOTE_MAX_LENGTH, blank=True, default="")
    amount = models.PositiveBigIntegerField()
    # None when a Platform Admin did it during Support Access; the name says who (issue 27).
    added_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    # The name as it was, so the list reads the same after a rename or deactivation.
    added_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("pk",)
        constraints = (
            models.CheckConstraint(
                condition=Q(amount__gte=1, amount__lte=MAX_AMOUNT), name="fees_amount_in_range"
            ),
        )

    def __str__(self) -> str:
        return f"Fee {self.pk}"
