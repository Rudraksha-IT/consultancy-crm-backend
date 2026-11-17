from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from payments.models import PaymentMethod


@dataclass(frozen=True)
class NewPayment:
    """A Payment to record: whole NPR, the day it was paid (AD), how, and what it was for."""

    amount: int
    paid_on: date
    method: PaymentMethod
    purpose: str


@dataclass(frozen=True)
class PaymentFilters:
    """The Payments list's filters (spec 74); empty or None means "any"."""

    paid_from: date | None = None
    paid_to: date | None = None
    student_id: int | None = None
    purpose: str = ""
    query: str = ""
