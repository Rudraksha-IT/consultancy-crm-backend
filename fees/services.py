"""Public services for Fees and Balances (spec 76a, 76b). Call inside the Consultancy's schema.

Who may see or change Fees is the views' job, through `access.policy`. Every change writes the
Fee and its History entry in one transaction.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from django.db import transaction
from django.db.models import (
    BigIntegerField,
    F,
    OuterRef,
    QuerySet,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, Greatest

from fee_types.models import FeeType
from fee_types.services import describe_for
from fees.balance import Balance, balance_of
from fees.by_type import FeesByType, StudentLine, TypeSum, fees_by_type
from fees.models import Fee
from fees.types import NewFee
from history.models import HistoryKind
from history.services import Change, record
from payments.models import Payment
from payments.money import format_npr
from staff.actors import actor_name, stored_staff
from staff.models import Staff
from students.models import Stage, Student

logger = logging.getLogger(__name__)


def fees_of(student: Student) -> QuerySet[Fee]:
    """The Student's Fees, oldest first."""
    return Fee.objects.filter(student=student).select_related("fee_type").order_by("pk")


def balance_of_student(student: Student) -> Balance:
    """What the Student is expected to pay, has paid, and still owes (spec 76b)."""
    fees = Fee.objects.filter(student=student).aggregate(total=Sum("amount"))["total"] or 0
    paid = Payment.objects.filter(student=student).aggregate(total=Sum("amount"))["total"] or 0
    return balance_of(fees=fees, paid=paid)


def describe_fee(fee_type_name: str, note: str, amount: int) -> str:
    """The Fee as History writes it, e.g. "Visa fee (first half), NPR 40,000"."""
    return f"{describe_for(fee_type_name, note)}, {format_npr(amount)}"


def _described(fee: Fee) -> str:
    return describe_fee(fee.fee_type.name, fee.note, fee.amount)


def add_fee(student: Student, details: NewFee, by: Staff) -> Fee:
    """Add a Fee to the Student, and to their History."""
    with transaction.atomic():
        fee = Fee.objects.create(
            student=student,
            fee_type=details.fee_type,
            note=details.note,
            amount=details.amount,
            added_by=stored_staff(by),
            added_by_name=actor_name(by),
        )
        record(
            student,
            HistoryKind.FEE_ADDED,
            by,
            Change(after=_described(fee)),
        )
    _log("Fee added", fee.pk, student.pk, by)
    return fee


def fee_with_student(fee_id: int) -> Fee:
    """One Fee with its Student.

    Raises:
        Fee.DoesNotExist: There is no such Fee.
    """
    return Fee.objects.select_related("student", "fee_type").get(pk=fee_id)


def change_fee(fee_id: int, details: NewFee, by: Staff) -> Fee:
    """Replace a Fee's Fee Type, note and amount, and note the change in History.

    Who added it, and when, stay as they were. Nothing is written when nothing changes.

    Raises:
        Fee.DoesNotExist: There is no such Fee.
    """
    with transaction.atomic():
        fee = Fee.objects.select_for_update().select_related("student", "fee_type").get(pk=fee_id)
        now = (details.fee_type.pk, details.note, details.amount)
        if (fee.fee_type_id, fee.note, fee.amount) == now:
            return fee
        before = _described(fee)
        fee.fee_type, fee.note, fee.amount = details.fee_type, details.note, details.amount
        fee.save(update_fields=["fee_type", "note", "amount"])
        record(
            fee.student, HistoryKind.FEE_EDITED, by, Change(before=before, after=_described(fee))
        )
    _log("Fee changed", fee.pk, fee.student_id, by)
    return fee


def delete_fee(fee_id: int, by: Staff) -> None:
    """Delete a Fee, keeping it in the Student's History.

    Raises:
        Fee.DoesNotExist: There is no such Fee.
    """
    with transaction.atomic():
        fee = Fee.objects.select_for_update().select_related("student", "fee_type").get(pk=fee_id)
        record(fee.student, HistoryKind.FEE_DELETED, by, Change(before=_described(fee)))
        fee_id, student_id = fee.pk, fee.student_id
        fee.delete()
    _log("Fee deleted", fee_id, student_id, by)


@dataclass(frozen=True)
class FeeTotals:
    """Across every Student with Fees: agreed, collected towards them, and still to collect."""

    agreed: int
    collected: int
    still_to_collect: int
    students: int
    owing: int


@dataclass(frozen=True)
class OwingStudent:
    """One Student who still owes: their Fees, what they paid, and what is still to pay."""

    student_id: int
    name: str
    fees: int
    paid: int
    still_to_pay: int


@dataclass(frozen=True)
class OwingStudents:
    """Students who still owe, most owed first, and the totals across everyone with Fees.

    `rows` is a lazy query of (id, name, fees, paid, still to pay); `owing_rows` reads a slice.
    """

    rows: QuerySet[Student, tuple[int, str, int, int, int]]
    totals: FeeTotals


def owing_students(*, leave_out_lost: bool = False) -> OwingStudents:
    """Every Student who still owes, most first then by name, with the office's totals (76b).

    Students with no Fees are left out; an overpaid Student counts only their Fees as collected.
    With `leave_out_lost`, Lost Students are left out too, as Fees by type counts (spec 87, 91).
    """
    money = BigIntegerField()
    # Subqueries, not joins, so Fees and Payments never multiply each other's sums.
    fees_total = Coalesce(_sum_per_student(Fee.objects.all()), Value(0), output_field=money)
    paid_total = Coalesce(_sum_per_student(Payment.objects.all()), Value(0), output_field=money)
    with_fees = (
        Student.objects.annotate(fees_total=fees_total, paid_total=paid_total)
        .annotate(
            still_to_pay=Greatest(F("fees_total") - F("paid_total"), Value(0), output_field=money)
        )
        .filter(fees_total__gt=0)
    )
    if leave_out_lost:
        with_fees = with_fees.exclude(stage=Stage.LOST)
    balances = [
        balance_of(fees=fees, paid=paid)
        for fees, paid in with_fees.values_list("fees_total", "paid_total")
    ]
    totals = FeeTotals(
        agreed=sum(balance.fees for balance in balances),
        collected=sum(balance.collected for balance in balances),
        still_to_collect=sum(balance.still_to_pay for balance in balances),
        students=len(balances),
        owing=sum(1 for balance in balances if balance.still_to_pay > 0),
    )
    rows = (
        with_fees.filter(still_to_pay__gt=0)
        .order_by("-still_to_pay", "name", "pk")
        .values_list("pk", "name", "fees_total", "paid_total", "still_to_pay")
    )
    return OwingStudents(rows=rows, totals=totals)


def owing_rows(rows: Iterable[tuple[int, str, int, int, int]]) -> list[OwingStudent]:
    """The rows of `OwingStudents.rows` (or a page of them) as `OwingStudent`s."""
    return [OwingStudent(*row) for row in rows]


def office_fees_by_type() -> FeesByType:
    """Fees by type for the whole office, for all time (spec 79-93).

    The database adds up each Student's Fees and Payments per Fee Type; the calculator does
    the rest.
    """
    sums = [
        TypeSum(student_id=student, fee_type_id=fee_type, fees=total, paid=0)
        for student, fee_type, total in _sums_per_student_and_type(Fee.objects.all())
    ] + [
        TypeSum(student_id=student, fee_type_id=fee_type, fees=0, paid=total)
        for student, fee_type, total in _sums_per_student_and_type(Payment.objects.all())
    ]
    lost = set(Student.objects.filter(stage=Stage.LOST).values_list("pk", flat=True))
    return fees_by_type(sums, lost=lost)


def fee_types_of(found: FeesByType) -> list[FeeType]:
    """The Fee Types that have money in `found`, in the office's own order."""
    ids = [row.fee_type_id for row in found.types]
    return list(FeeType.objects.filter(pk__in=ids).order_by("position", "pk"))


@dataclass(frozen=True)
class FeeTypeStudent:
    """One Student under one Fee Type, with what the page shows about them."""

    student_id: int
    name: str
    stage: str
    lost_reason: str
    fee: int
    paid: int
    still_to_pay: int


def fee_type_students(found: FeesByType, fee_type_id: int, *, lost: bool) -> list[FeeTypeStudent]:
    """One Fee Type's counted (or Lost) Students, most still to pay first, then by name."""
    counted, lost_lines = found.students_of(fee_type_id)
    lines: list[StudentLine] = lost_lines if lost else counted
    people = {
        pk: (name, stage, reason)
        for pk, name, stage, reason in Student.objects.filter(
            pk__in=[line.student_id for line in lines]
        ).values_list("pk", "name", "stage", "lost_reason")
    }
    rows = [
        FeeTypeStudent(
            student_id=line.student_id,
            name=people[line.student_id][0],
            stage=people[line.student_id][1],
            lost_reason=people[line.student_id][2] if lost else "",
            fee=line.fee,
            paid=line.paid,
            still_to_pay=line.still_to_pay,
        )
        for line in lines
    ]
    return sorted(rows, key=lambda row: (-row.still_to_pay, row.name, row.student_id))


def _sums_per_student_and_type(
    amounts: QuerySet[Fee] | QuerySet[Payment],
) -> Iterable[tuple[int, int, int]]:
    """(Student, Fee Type, sum of `amount`) for every pair that has any."""
    return (
        amounts.order_by()
        .values("student_id", "fee_type_id")
        .annotate(total=Sum("amount"))
        .values_list("student_id", "fee_type_id", "total")
    )


def _sum_per_student(amounts: QuerySet[Fee] | QuerySet[Payment]) -> Subquery:
    """The sum of `amount` for the outer Student."""
    return Subquery(
        amounts.filter(student=OuterRef("pk"))
        .order_by()
        .values("student")
        .annotate(total=Sum("amount"))
        .values("total")
    )


def _log(what: str, fee_id: int, student_id: int, by: Staff) -> None:
    """Log ids only: never the note, amounts or names."""
    logger.info("%s: fee_id=%s, student_id=%s, by_staff_id=%s", what, fee_id, student_id, by.pk)
