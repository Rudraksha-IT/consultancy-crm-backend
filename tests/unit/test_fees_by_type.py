"""Fees by type: the one calculator (spec 79-93, Implementation Decisions "Fees by type").

A Student has one Balance (all their Fees minus all their Payments, whatever the Fee Type).
Received counts every Payment; Remaining counts only Students who aren't Lost; To collect is
Received plus Remaining.
"""

from __future__ import annotations

import pytest

from fees.balance import balance_of
from fees.by_type import FeesByType, FeeTypeTotals, StudentLine, Totals, TypeSum, fees_by_type

APPLICATION, VISA, IELTS, OTHER = 1, 2, 3, 4
AARAV, SITA, HARI = 10, 20, 30


def sums(*rows: tuple[int, int, int, int]) -> list[TypeSum]:
    """(Student, Fee Type, Fees, paid) rows as the database adds them up."""
    return [TypeSum(student_id=s, fee_type_id=t, fees=f, paid=p) for s, t, f, p in rows]


def calculate(*rows: tuple[int, int, int, int], lost: frozenset[int] = frozenset()) -> FeesByType:
    """Fees by type for these rows."""
    return fees_by_type(sums(*rows), lost=lost)


def assert_adds_up(found: FeesByType) -> None:
    """To collect is always Received plus Remaining."""
    totals = found.totals
    assert totals.to_collect == totals.received + totals.remaining


class TestOneStudent:
    def test_paid_in_full_leaves_nothing(self) -> None:
        found = calculate((AARAV, VISA, 40000, 40000))

        assert found.totals == Totals(
            received=40000, remaining=0, to_collect=40000, lost_not_counted=0, owing=0
        )
        assert found.types == (FeeTypeTotals(fee_type_id=VISA, fees=40000, received=40000),)
        assert found.balances[AARAV] == balance_of(fees=40000, paid=40000)

    def test_part_paid_leaves_the_rest_to_pay(self) -> None:
        found = calculate((AARAV, VISA, 40000, 15000))

        assert (found.totals.received, found.totals.remaining) == (15000, 25000)
        assert found.totals.to_collect == 40000
        assert found.totals.owing == 1
        assert found.balances[AARAV].still_to_pay == 25000

    def test_paid_ahead_on_one_fee_type_settles_what_is_short_on_another(self) -> None:
        found = calculate((AARAV, VISA, 40000, 41000), (AARAV, APPLICATION, 25000, 24000))

        assert found.balances[AARAV].still_to_pay == 0
        assert found.balances[AARAV].overpaid == 0
        assert found.totals.remaining == 0
        assert found.totals.owing == 0
        assert found.totals.to_collect == 65000

    def test_paid_more_than_all_the_fees_owes_nothing_and_is_never_negative(self) -> None:
        found = calculate((AARAV, VISA, 40000, 45000))

        assert found.totals.remaining == 0
        assert found.totals.received == 45000
        assert found.balances[AARAV].overpaid == 5000

    def test_a_payment_with_no_fee_counts_in_received_and_in_the_balance(self) -> None:
        found = calculate((AARAV, VISA, 40000, 0), (AARAV, IELTS, 0, 10000))

        assert found.types == (
            FeeTypeTotals(fee_type_id=VISA, fees=40000, received=0),
            FeeTypeTotals(fee_type_id=IELTS, fees=0, received=10000),
        )
        assert found.balances[AARAV].still_to_pay == 30000
        assert found.totals.received == 10000
        assert found.totals.remaining == 30000

    def test_the_same_student_and_fee_type_twice_adds_up(self) -> None:
        found = calculate((AARAV, VISA, 20000, 5000), (AARAV, VISA, 20000, 5000))

        assert found.types == (FeeTypeTotals(fee_type_id=VISA, fees=40000, received=10000),)
        assert found.students_of(VISA)[0] == [
            StudentLine(student_id=AARAV, fee=40000, paid=10000, still_to_pay=30000)
        ]


class TestLostStudents:
    def test_what_a_lost_student_paid_counts_but_what_they_owe_does_not(self) -> None:
        found = calculate(
            (AARAV, VISA, 40000, 10000), (SITA, VISA, 40000, 0), lost=frozenset({SITA})
        )

        assert found.totals == Totals(
            received=10000, remaining=30000, to_collect=40000, lost_not_counted=40000, owing=1
        )
        assert found.types == (FeeTypeTotals(fee_type_id=VISA, fees=40000, received=10000),)

    def test_a_lost_student_is_listed_apart(self) -> None:
        found = calculate(
            (AARAV, VISA, 40000, 10000), (SITA, VISA, 40000, 5000), lost=frozenset({SITA})
        )

        counted, lost = found.students_of(VISA)

        assert counted == [StudentLine(student_id=AARAV, fee=40000, paid=10000, still_to_pay=30000)]
        assert lost == [StudentLine(student_id=SITA, fee=40000, paid=5000, still_to_pay=35000)]

    def test_a_student_who_came_back_is_counted_again(self) -> None:
        rows = ((SITA, VISA, 40000, 5000),)
        assert calculate(*rows, lost=frozenset({SITA})).totals.remaining == 0

        back = calculate(*rows)

        assert back.totals.remaining == 35000
        assert back.students_of(VISA) == (
            [StudentLine(student_id=SITA, fee=40000, paid=5000, still_to_pay=35000)],
            [],
        )

    def test_a_lost_student_who_paid_more_leaves_nothing_not_counted(self) -> None:
        found = calculate((SITA, VISA, 10000, 12000), lost=frozenset({SITA}))

        assert found.totals.lost_not_counted == 0
        assert found.totals.received == 12000


class TestSeveralStudentsAndTypes:
    def test_rows_per_fee_type_and_the_totals(self) -> None:
        found = calculate(
            (AARAV, APPLICATION, 25000, 25000),
            (AARAV, VISA, 40000, 10000),
            (SITA, VISA, 40000, 40000),
            (SITA, IELTS, 12000, 0),
            (HARI, IELTS, 12000, 6000),
            (HARI, OTHER, 0, 500),
        )

        assert found.types == (
            FeeTypeTotals(fee_type_id=APPLICATION, fees=25000, received=25000),
            FeeTypeTotals(fee_type_id=VISA, fees=80000, received=50000),
            FeeTypeTotals(fee_type_id=IELTS, fees=24000, received=6000),
            FeeTypeTotals(fee_type_id=OTHER, fees=0, received=500),
        )
        # Aarav owes 30,000; Sita 12,000; Hari 12,000 - 6,500 = 5,500.
        assert found.totals == Totals(
            received=81500, remaining=47500, to_collect=129000, lost_not_counted=0, owing=3
        )

    def test_a_fee_types_students_show_their_whole_still_to_pay(self) -> None:
        found = calculate(
            (AARAV, VISA, 40000, 40000), (AARAV, IELTS, 12000, 0), (SITA, VISA, 40000, 0)
        )

        counted, _ = found.students_of(VISA)

        assert sorted(counted, key=lambda line: line.student_id) == [
            StudentLine(student_id=AARAV, fee=40000, paid=40000, still_to_pay=12000),
            StudentLine(student_id=SITA, fee=40000, paid=0, still_to_pay=40000),
        ]

    def test_a_fee_type_nobody_used_has_no_students(self) -> None:
        assert calculate((AARAV, VISA, 1, 0)).students_of(IELTS) == ([], [])

    def test_nothing_at_all(self) -> None:
        found = calculate()

        assert found.types == ()
        assert found.totals == Totals(
            received=0, remaining=0, to_collect=0, lost_not_counted=0, owing=0
        )

    @pytest.mark.parametrize(
        ("rows", "lost"),
        [
            (((AARAV, VISA, 100, 0),), frozenset()),
            (((AARAV, VISA, 100, 300), (SITA, IELTS, 500, 20)), frozenset()),
            (((AARAV, VISA, 100, 30), (SITA, IELTS, 500, 20)), frozenset({SITA})),
            (((AARAV, VISA, 0, 30), (SITA, VISA, 7, 0), (HARI, OTHER, 9, 9)), frozenset({HARI})),
        ],
    )
    def test_to_collect_is_always_received_plus_remaining(
        self, rows: tuple[tuple[int, int, int, int], ...], lost: frozenset[int]
    ) -> None:
        assert_adds_up(calculate(*rows, lost=lost))

    def test_negative_sums_are_refused(self) -> None:
        with pytest.raises(ValueError, match="never negative"):
            calculate((AARAV, VISA, -1, 0))
