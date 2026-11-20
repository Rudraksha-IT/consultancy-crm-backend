"""Fees by type: where the money comes from, for all time (spec 79-93).

The one calculator beside the Balance. A Student has ONE Balance: all their Fees minus all
their Payments, whatever the Fee Type, so money paid ahead for one Fee Type settles what is short
on another; no "extra" is ever kept per Fee Type. Received counts every Payment (Lost or not);
Remaining is the still to pay of Students who aren't Lost; To collect is Received plus Remaining.
Pure: the database adds up each Student's Fees and Payments per Fee Type and passes them in.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fees.balance import Balance, balance_of

if TYPE_CHECKING:
    from collections.abc import Set as AbstractSet


@dataclass(frozen=True)
class TypeSum:
    """One Student's Fees and Payments for one Fee Type, as the database adds them up."""

    student_id: int
    fee_type_id: int
    fees: int
    paid: int


@dataclass(frozen=True)
class FeeTypeTotals:
    """One Fee Type: the Fees added (Students who aren't Lost) and every Payment Received."""

    fee_type_id: int
    fees: int
    received: int


@dataclass(frozen=True)
class StudentLine:
    """One Student under one Fee Type: the Fee and paid for it, and their whole still to pay."""

    student_id: int
    fee: int
    paid: int
    still_to_pay: int


@dataclass(frozen=True)
class Totals:
    """The office's totals. `lost_not_counted` is Lost Students' still to pay, left out."""

    received: int
    remaining: int
    to_collect: int
    lost_not_counted: int
    owing: int


@dataclass(frozen=True)
class FeesByType:
    """The totals, one row per Fee Type (first seen first), and each Student's Balance."""

    totals: Totals
    types: tuple[FeeTypeTotals, ...]
    balances: Mapping[int, Balance]
    lost: frozenset[int]
    _per_type: Mapping[int, Mapping[int, tuple[int, int]]] = field(repr=False, compare=False)

    def students_of(self, fee_type_id: int) -> tuple[list[StudentLine], list[StudentLine]]:
        """Every Student with a Fee or Payment of this Fee Type: (counted, Lost), unsorted."""
        counted: list[StudentLine] = []
        lost: list[StudentLine] = []
        for student_id, (fee, paid) in self._per_type.get(fee_type_id, {}).items():
            line = StudentLine(
                student_id=student_id,
                fee=fee,
                paid=paid,
                still_to_pay=self.balances[student_id].still_to_pay,
            )
            (lost if student_id in self.lost else counted).append(line)
        return counted, lost


def fees_by_type(sums: Iterable[TypeSum], lost: AbstractSet[int]) -> FeesByType:
    """Fees by type for these sums; `lost` names the Students whose Stage is Lost.

    Raises:
        ValueError: A sum is negative.
    """
    per_type: dict[int, dict[int, tuple[int, int]]] = defaultdict(dict)
    per_student: dict[int, tuple[int, int]] = {}
    for row in sums:
        balance_of(fees=row.fees, paid=row.paid)  # refuses a negative sum
        fee, paid = per_type[row.fee_type_id].get(row.student_id, (0, 0))
        per_type[row.fee_type_id][row.student_id] = (fee + row.fees, paid + row.paid)
        fees_total, paid_total = per_student.get(row.student_id, (0, 0))
        per_student[row.student_id] = (fees_total + row.fees, paid_total + row.paid)

    lost_ids = frozenset(lost)
    balances = {sid: balance_of(fees=f, paid=p) for sid, (f, p) in per_student.items()}
    counted = [b for sid, b in balances.items() if sid not in lost_ids]
    received = sum(b.paid for b in balances.values())
    remaining = sum(b.still_to_pay for b in counted)
    types = tuple(
        FeeTypeTotals(
            fee_type_id=type_id,
            fees=sum(f for sid, (f, _) in students.items() if sid not in lost_ids),
            received=sum(p for _, p in students.values()),
        )
        for type_id, students in per_type.items()
    )
    totals = Totals(
        received=received,
        remaining=remaining,
        to_collect=received + remaining,
        lost_not_counted=sum(b.still_to_pay for sid, b in balances.items() if sid in lost_ids),
        owing=sum(1 for b in counted if b.still_to_pay > 0),
    )
    return FeesByType(
        totals=totals, types=types, balances=balances, lost=lost_ids, _per_type=per_type
    )
