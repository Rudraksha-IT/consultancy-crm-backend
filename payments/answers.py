"""Payments as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from fee_types.answers import fee_type_ref
from payments.models import Payment


def receipt_answer(payment: Payment) -> dict[str, object]:
    """One Payment as its receipt shows it: who paid, how much, when (AD), how, and Fee Type."""
    student = payment.student
    return {
        "id": payment.pk,
        "receipt_number": payment.receipt_number,
        "student": {"id": student.pk, "name": student.name, "phone": student.phone},
        "amount": payment.amount,
        "paid_on": payment.paid_on.isoformat(),
        "method": payment.method,
        "fee_type": fee_type_ref(payment.fee_type),
        "note": payment.note,
        "recorded_by": {"name": payment.recorded_by_name},
        "recorded_at": payment.recorded_at.isoformat(),
    }
