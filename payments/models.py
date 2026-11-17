"""Payments: money received from a Student, always with what it was for (GLOSSARY: Payment)."""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import CHOICE_MAX_LENGTH, Student

PURPOSE_MAX_LENGTH = 200
# NPR 10 crore: far above any Fee, low enough to catch a slip of the keyboard.
MAX_AMOUNT = 100_000_000
RECEIPT_NUMBER_DIGITS = 6


class PaymentMethod(models.TextChoices):
    """How the Student paid (the methods offices in Nepal take)."""

    CASH = "cash", "Cash"
    ESEWA = "esewa", "eSewa"
    KHALTI = "khalti", "Khalti"
    FONEPAY_QR = "fonepay_qr", "Fonepay QR"
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    CHEQUE = "cheque", "Cheque"


class Payment(models.Model):
    """One Payment from a Student (spec 71): whole NPR, the day paid, how, and what for.

    Balances are calculated from Payments and Fees (issue 35); nothing here is an invoice.
    """

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="payments")
    amount = models.PositiveBigIntegerField()
    paid_on = models.DateField()
    method = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=PaymentMethod.choices)
    purpose = models.CharField(max_length=PURPOSE_MAX_LENGTH)
    # None when a Platform Admin did it during Support Access; the name says who (issue 27).
    recorded_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    # The name as it was, so the receipt reads the same after a rename or deactivation.
    recorded_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-paid_on", "-pk")
        constraints = (
            models.CheckConstraint(
                condition=Q(amount__gte=1, amount__lte=MAX_AMOUNT),
                name="payments_amount_in_range",
            ),
        )

    def __str__(self) -> str:
        return self.receipt_number

    @property
    def receipt_number(self) -> str:
        """The number printed on the receipt: the Payment's id, zero-padded."""
        return f"{self.pk:0{RECEIPT_NUMBER_DIGITS}d}"
