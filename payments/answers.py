"""Payments as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from collections.abc import Sequence

from fee_types.answers import fee_type_ref
from payments.models import Payment


def payment_answer(payment: Payment) -> dict[str, object]:
    """One Payment: who paid, how much, when (AD), how, its Fee Type, and its receipt number."""
    student = payment.student
    return {
        "id": payment.public_id,
        "receipt_number": payment.receipt_number,
        "student": {"id": student.public_id, "name": student.name, "phone": student.phone},
        "amount": payment.amount,
        "paid_on": payment.paid_on.isoformat(),
        "method": payment.method,
        "fee_type": fee_type_ref(payment.fee_type),
        "note": payment.note,
        "recorded_by": {"name": payment.recorded_by_name},
        "recorded_at": payment.recorded_at.isoformat(),
    }


def receipt_answer(payment: Payment, lines: Sequence[Payment]) -> dict[str, object]:
    """The receipt `payment` is on: the Payment itself, then every line and their total."""
    return {
        **payment_answer(payment),
        "lines": [
            {
                "id": line.public_id,
                "fee_type": fee_type_ref(line.fee_type),
                "note": line.note,
                "amount": line.amount,
                "paid_on": line.paid_on.isoformat(),
                "method": line.method,
            }
            for line in lines
        ],
        "total": sum(line.amount for line in lines),
    }
