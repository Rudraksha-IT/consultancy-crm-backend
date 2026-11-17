"""Payments as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from payments.models import Payment


def receipt_answer(payment: Payment) -> dict[str, object]:
    """One Payment as its receipt shows it: who paid, how much, when (AD), how and what for."""
    student = payment.student
    return {
        "id": payment.pk,
        "receipt_number": payment.receipt_number,
        "student": {"id": student.pk, "name": student.name, "phone": student.phone},
        "amount": payment.amount,
        "paid_on": payment.paid_on.isoformat(),
        "method": payment.method,
        "purpose": payment.purpose,
        "recorded_by": {"name": payment.recorded_by_name},
        "recorded_at": payment.recorded_at.isoformat(),
    }
