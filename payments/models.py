"""Payments: money received from a Student, always with its Fee Type (GLOSSARY: Payment)."""

from __future__ import annotations

from typing import Any

from django.db import connection, models
from django.db.models import Q

from config.public_ids import public_id_field
from fee_types.models import NOTE_MAX_LENGTH, FeeType
from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import CHOICE_MAX_LENGTH, Student

# NPR 10 crore: far above any Fee, low enough to catch a slip of the keyboard.
MAX_AMOUNT = 100_000_000
RECEIPT_NUMBER_DIGITS = 6
# The most lines (Payments) one receipt takes in one go (ticket 07).
MAX_RECEIPT_LINES = 10


class PaymentMethod(models.TextChoices):
    """How the Student paid (the methods offices in Nepal take)."""

    CASH = "cash", "Cash"
    ESEWA = "esewa", "eSewa"
    KHALTI = "khalti", "Khalti"
    FONEPAY_QR = "fonepay_qr", "Fonepay QR"
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    CHEQUE = "cheque", "Cheque"


class Payment(models.Model):
    """One Payment from a Student (spec 71): whole NPR, the day paid, how, and its Fee Type.

    Payments recorded together share one receipt (spec 73): `receipt` is the id of the first of
    them. A Payment saved on its own gets its own id as its receipt. Balances are calculated
    from Payments and Fees (issue 35); nothing here is an invoice.
    """

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="payments")
    amount = models.PositiveBigIntegerField()
    paid_on = models.DateField()
    method = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=PaymentMethod.choices)
    fee_type = models.ForeignKey(FeeType, on_delete=models.PROTECT, related_name="+")
    # Optional, needed for Other; payments typed before Fee Types keep their old text here.
    note = models.CharField(max_length=NOTE_MAX_LENGTH, blank=True, default="")
    # None when a Platform Admin did it during Support Access; the name says who (issue 27).
    recorded_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    # The name as it was, so the receipt reads the same after a rename or deactivation.
    recorded_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)
    recorded_at = models.DateTimeField(auto_now_add=True)
    # Kept when the Payment is edited; the receipt shows its lines as they are now.
    receipt = models.PositiveBigIntegerField(db_index=True)

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

    def save(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401  # Model.save's own
        """Save it; a new Payment with no receipt yet takes its own id as its receipt."""
        if self.receipt is None:
            if self.pk is None:
                self.pk = _next_payment_id()
            self.receipt = self.pk
        super().save(*args, **kwargs)

    @property
    def receipt_number(self) -> str:
        """The number printed on the receipt: its first Payment's id, zero-padded."""
        return f"{self.receipt:0{RECEIPT_NUMBER_DIGITS}d}"


def _next_payment_id() -> int:
    """Take the next Payment id from the table's own sequence (this Consultancy's schema)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval(pg_get_serial_sequence(%s, 'id'))", [Payment._meta.db_table])
        row = cursor.fetchone()
    return int(row[0])
