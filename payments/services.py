"""Public services for Payments (spec 71-76). Call inside the Consultancy's schema.

Who may record, see, change or delete a Payment is the views' job, through `access.policy`.
Every change writes the Payment and its History entry in one transaction.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.db.models import Q, QuerySet, Sum

from fee_types.services import describe_for
from history.models import HistoryKind
from history.services import Change, record
from payments.models import Payment, PaymentMethod
from payments.money import format_npr
from payments.types import NewPayment, PaymentFilters
from staff.actors import actor_name, stored_staff
from staff.models import Staff
from students.models import Student

logger = logging.getLogger(__name__)

PHONE_SEPARATORS = re.compile(r"[\s-]")
DIGITS = re.compile(r"^[0-9]+\Z")
# Larger numbers can't be a receipt number (and wouldn't fit the id column).
MAX_RECEIPT_DIGITS = 12


def record_payment(student: Student, details: NewPayment, by: Staff) -> Payment:
    """Record a Payment from the Student, received now by `by`, and add it to their History."""
    with transaction.atomic():
        payment = Payment.objects.create(
            student=student,
            amount=details.amount,
            paid_on=details.paid_on,
            method=details.method,
            fee_type=details.fee_type,
            note=details.note,
            recorded_by=stored_staff(by),
            recorded_by_name=actor_name(by),
        )
        record(
            student,
            HistoryKind.PAYMENT_RECORDED,
            by,
            Change(after=describe_payment(payment), detail=f"Receipt {payment.receipt_number}"),
        )
    logger.info(
        "Payment recorded: payment_id=%s, student_id=%s, by_staff_id=%s",
        payment.pk,
        student.pk,
        by.pk,
    )
    return payment


def describe_payment(payment: Payment) -> str:
    """The Payment as History writes it, e.g. "NPR 25,000, Visa fee (first half), eSewa"."""
    method = PaymentMethod(payment.method).label
    return f"{format_npr(payment.amount)}, {paid_for(payment)}, {method}"


def paid_for(payment: Payment) -> str:
    """What the Payment was for: its Fee Type and note, e.g. "Visa fee (first half)"."""
    return describe_for(payment.fee_type.name, payment.note)


def payment_with_student(payment_id: int) -> Payment:
    """One Payment with its Student.

    Raises:
        Payment.DoesNotExist: There is no such Payment.
    """
    return Payment.objects.select_related("student", "fee_type").get(pk=payment_id)


def fee_type_of_payment(payment_id: int) -> int | None:
    """The id of the Payment's Fee Type, or None when there is no such Payment."""
    found: int | None = (
        Payment.objects.filter(pk=payment_id).values_list("fee_type_id", flat=True).first()
    )
    return found


def describe_deleted_payment(payment: Payment) -> str:
    """The deleted Payment as History keeps it, e.g. "NPR 25,000, Visa fee, eSewa, 30 Oct 2026"."""
    return f"{describe_payment(payment)}, {_written_date(payment.paid_on)}"


def describe_changes(payment: Payment, details: NewPayment) -> tuple[str, str] | None:
    """What an edit changes, as History's before and after; None when it changes nothing.

    Only the changed fields are named, in the form's order, e.g. ("Amount NPR 25,000",
    "Amount NPR 30,000").
    """
    pairs = [
        (label, old, new)
        for label, old, new in (
            ("amount", format_npr(payment.amount), format_npr(details.amount)),
            ("date", _written_date(payment.paid_on), _written_date(details.paid_on)),
            ("method", PaymentMethod(payment.method).label, details.method.label),
            ("for", paid_for(payment), describe_for(details.fee_type.name, details.note)),
        )
        if old != new
    ]
    if not pairs:
        return None
    before = "; ".join(f"{label} {old}" for label, old, _ in pairs)
    after = "; ".join(f"{label} {new}" for label, _, new in pairs)
    return before[:1].upper() + before[1:], after[:1].upper() + after[1:]


def change_payment(payment_id: int, details: NewPayment, by: Staff) -> Payment:
    """Replace a Payment's amount, date, method, Fee Type and note; History names what changed.

    Who recorded it, and when, stay as they were. Nothing is written when nothing changes.

    Raises:
        Payment.DoesNotExist: There is no such Payment.
    """
    with transaction.atomic():
        payment = (
            Payment.objects.select_for_update()
            .select_related("student", "fee_type")
            .get(pk=payment_id)
        )
        change = describe_changes(payment, details)
        if change is None:
            return payment
        payment.amount = details.amount
        payment.paid_on = details.paid_on
        payment.method = details.method
        payment.fee_type = details.fee_type
        payment.note = details.note
        payment.save(update_fields=["amount", "paid_on", "method", "fee_type", "note"])
        record(
            payment.student,
            HistoryKind.PAYMENT_EDITED,
            by,
            Change(before=change[0], after=change[1], detail=_receipt(payment)),
        )
    _log("Payment changed", payment.pk, payment.student_id, by)
    return payment


def delete_payment(payment_id: int, by: Staff) -> None:
    """Delete a Payment, keeping its details in the Student's History.

    Raises:
        Payment.DoesNotExist: There is no such Payment.
    """
    with transaction.atomic():
        payment = (
            Payment.objects.select_for_update()
            .select_related("student", "fee_type")
            .get(pk=payment_id)
        )
        record(
            payment.student,
            HistoryKind.PAYMENT_DELETED,
            by,
            Change(before=describe_deleted_payment(payment), detail=_receipt(payment)),
        )
        payment_id, student_id = payment.pk, payment.student_id
        payment.delete()
    _log("Payment deleted", payment_id, student_id, by)


@dataclass(frozen=True)
class FoundPayments:
    """The Payments that match a search, newest first, and what they add up to."""

    payments: QuerySet[Payment]
    total: int


def find_payments(filters: PaymentFilters) -> FoundPayments:
    """Every Payment matching the filters, newest first, with their total (spec 74)."""
    payments = Payment.objects.select_related("student", "fee_type").filter(_matching(filters))
    total = payments.aggregate(total=Sum("amount"))["total"] or 0
    return FoundPayments(payments=payments.order_by("-paid_on", "-pk"), total=total)


def _matching(filters: PaymentFilters) -> Q:
    """Every filter that is set, together; an empty `Q` matches everything."""
    conditions = {
        "paid_on__gte": filters.paid_from,
        "paid_on__lte": filters.paid_to,
        "student_id": filters.student_id,
        "fee_type_id": filters.fee_type_id,
    }
    match = Q(**{lookup: value for lookup, value in conditions.items() if value is not None})
    return match & _search(filters.query) if filters.query else match


def _search(query: str) -> Q:
    """The Student's name, their phone's digits, or the receipt number."""
    match = Q(student__name__icontains=query)
    phone = PHONE_SEPARATORS.sub("", query)
    if phone:
        match |= Q(student__phone__contains=phone)
    if DIGITS.match(query) and len(query) <= MAX_RECEIPT_DIGITS:
        match |= Q(pk=int(query))
    return match


def _written_date(day: date) -> str:
    """e.g. "30 Oct 2026" (AD)."""
    return f"{day.day} {day:%b %Y}"


def _receipt(payment: Payment) -> str:
    return f"Receipt {payment.receipt_number}"


def _log(what: str, payment_id: int, student_id: int, by: Staff) -> None:
    """Log ids only: never the note, amounts or names."""
    logger.info(
        "%s: payment_id=%s, student_id=%s, by_staff_id=%s", what, payment_id, student_id, by.pk
    )
