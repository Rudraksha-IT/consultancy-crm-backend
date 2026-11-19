from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fee_types.models import FeeType
from payments.models import PaymentMethod


@dataclass(frozen=True)
class NewPayment:
    """A Payment to record: whole NPR, the day it was paid (AD), how, its Fee Type and note."""

    amount: int
    paid_on: date
    method: PaymentMethod
    fee_type: FeeType
    note: str = ""


@dataclass(frozen=True)
class PaymentLine:
    """One line of a visit's Payments: whole NPR for one Fee Type, with its note."""

    fee_type: FeeType
    amount: int
    note: str = ""


@dataclass(frozen=True)
class NewPayments:
    """One visit's money (spec 73): the day it was paid (AD), how, and a Payment per line.

    The lines are saved together, all or none, and share one receipt.
    """

    paid_on: date
    method: PaymentMethod
    lines: tuple[PaymentLine, ...]


@dataclass(frozen=True)
class PaymentFilters:
    """The Payments list's filters (spec 74); empty or None means "any"."""

    paid_from: date | None = None
    paid_to: date | None = None
    student_id: int | None = None
    fee_type_id: int | None = None
    query: str = ""
