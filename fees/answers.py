"""Fees and Balances as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from collections.abc import Iterable

from fee_types.answers import fee_type_ref
from fees.balance import Balance
from fees.models import Fee
from fees.services import OwingStudent


def fee_answer(fee: Fee) -> dict[str, object]:
    """One Fee: its Fee Type and note, how much, and who added it when."""
    return {
        "id": fee.pk,
        "fee_type": fee_type_ref(fee.fee_type),
        "note": fee.note,
        "amount": fee.amount,
        "added_by": {"name": fee.added_by_name},
        "added_at": fee.added_at.isoformat(),
    }


def student_fees_answer(fees: Iterable[Fee], balance: Balance | None) -> dict[str, object]:
    """A Student's Fees and their total; the Balance only for those who may see it (spec 73)."""
    listed = list(fees)
    return {
        "fees": [fee_answer(fee) for fee in listed],
        "total": sum(fee.amount for fee in listed),
        "balance": None
        if balance is None
        else {
            "paid": balance.paid,
            "still_to_pay": balance.still_to_pay,
            "overpaid": balance.overpaid,
        },
    }


def owing_student_answer(row: OwingStudent) -> dict[str, object]:
    """One row of the Student fees list (spec 76b)."""
    return {
        "student": {"id": row.student_id, "name": row.name},
        "fees": row.fees,
        "paid": row.paid,
        "still_to_pay": row.still_to_pay,
    }
