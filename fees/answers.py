"""Fees and Balances as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from collections.abc import Iterable

from fee_types.answers import fee_type_ref
from fee_types.models import FeeType
from fees.balance import Balance
from fees.by_type import FeesByType, FeeTypeTotals, Totals
from fees.models import Fee
from fees.services import FeeTypeStudent, OwingStudent


def fee_answer(fee: Fee) -> dict[str, object]:
    """One Fee: its Fee Type and note, how much, and who added it when."""
    return {
        "id": fee.public_id,
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


def by_type_fee_type(fee_type: FeeType) -> dict[str, object]:
    """A Fee Type on Fees by type: hidden ones still show their money (spec 69)."""
    return {"id": fee_type.pk, "name": fee_type.name, "hidden": fee_type.hidden}


def by_type_totals_answer(totals: Totals) -> dict[str, object]:
    """To collect, Received and Remaining, and what Lost Students left (spec 80, 87)."""
    return {
        "to_collect": totals.to_collect,
        "received": totals.received,
        "remaining": totals.remaining,
        "lost_not_counted": totals.lost_not_counted,
        "owing": totals.owing,
    }


def fees_by_type_answer(found: FeesByType, fee_types: Iterable[FeeType]) -> dict[str, object]:
    """The totals and one row per Fee Type, in the office's order (spec 80, 81)."""
    rows = {row.fee_type_id: row for row in found.types}
    return {
        "totals": by_type_totals_answer(found.totals),
        "fee_types": [
            {
                "fee_type": by_type_fee_type(fee_type),
                "fees": rows[fee_type.pk].fees,
                "received": rows[fee_type.pk].received,
            }
            for fee_type in fee_types
        ],
    }


def fee_type_row(found: FeesByType, fee_type_id: int) -> FeeTypeTotals:
    """One Fee Type's row, all zero when nobody used it."""
    return next(
        (row for row in found.types if row.fee_type_id == fee_type_id),
        FeeTypeTotals(fee_type_id=fee_type_id, fees=0, received=0),
    )


def fee_type_student_answer(row: FeeTypeStudent) -> dict[str, object]:
    """One Student under one Fee Type (spec 82, 89)."""
    return {
        "student": {"id": row.student_id, "name": row.name, "stage": row.stage},
        "lost_reason": row.lost_reason,
        "fee": row.fee,
        "paid": row.paid,
        "still_to_pay": row.still_to_pay,
    }
