"""Expenses: money the Consultancy pays out (GLOSSARY: Expense; spec 76c)."""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from payments.models import MAX_AMOUNT
from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import CHOICE_MAX_LENGTH

PAID_TO_MAX_LENGTH = 200


class ExpenseCategory(models.TextChoices):
    """What kind of Expense it is: the starting list, to be confirmed with the Consultancy."""

    OFFICE_RENT = "office_rent", "Office rent"
    SALARIES = "salaries", "Salaries"
    INTERNET = "internet", "Internet"
    ELECTRICITY = "electricity", "Electricity"
    MARKETING = "marketing", "Marketing"
    EDUCATION_FAIR = "education_fair", "Education fair"
    OFFICE_SUPPLIES = "office_supplies", "Office supplies"
    TEA_AND_SNACKS = "tea_and_snacks", "Tea and snacks"
    OTHER = "other", "Other"


class ExpenseMethod(models.TextChoices):
    """How the office paid: the Payment methods, and a card."""

    CASH = "cash", "Cash"
    ESEWA = "esewa", "eSewa"
    KHALTI = "khalti", "Khalti"
    FONEPAY_QR = "fonepay_qr", "Fonepay QR"
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    CHEQUE = "cheque", "Cheque"
    CARD = "card", "Card"


class Expense(models.Model):
    """One Expense: the day paid (AD), what kind, who to, whole NPR and how."""

    spent_on = models.DateField()
    category = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=ExpenseCategory.choices)
    paid_to = models.CharField(max_length=PAID_TO_MAX_LENGTH)
    amount = models.PositiveBigIntegerField()
    method = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=ExpenseMethod.choices)
    # None when a Platform Admin did it during Support Access; the name says who (issue 27).
    recorded_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    # The name as it was, so the list reads the same after a rename or deactivation.
    recorded_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-spent_on", "-pk")
        indexes = (models.Index(fields=("spent_on",), name="expenses_spent_on"),)
        constraints = (
            models.CheckConstraint(
                condition=Q(amount__gte=1, amount__lte=MAX_AMOUNT),
                name="expenses_amount_in_range",
            ),
        )

    def __str__(self) -> str:
        return self.paid_to
