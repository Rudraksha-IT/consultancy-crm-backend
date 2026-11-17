"""The Reports' calculations: months, sign-ups, visas, sources, counsellors, classes, countries.

Issues 28, 30 and 53.
"""

from __future__ import annotations

from datetime import date

import pytest

from expenses.models import ExpenseCategory
from reports.calculations import (
    Amount,
    ClassMark,
    GrantedVisa,
    MoneyMonth,
    MonthCounts,
    NewVisitor,
    StageMove,
    by_category,
    class_attendance,
    countries_granted,
    countries_wanted,
    is_forward,
    money_months,
    month_of,
    months_back,
    sorted_sources,
    stage_from_history,
    student_months,
)
from students.models import Country, Source, Stage

OCT = date(2026, 10, 5)
SEP = date(2026, 9, 20)


def move(student: int, on: date, before: Stage, after: Stage) -> StageMove:
    """One Stage change on a day."""
    return StageMove(student_id=student, on=on, before=before, after=after)


class TestMonths:
    def test_the_last_six_months_end_with_this_one(self) -> None:
        assert months_back(date(2026, 11, 3), 6) == [
            "2026-06",
            "2026-07",
            "2026-08",
            "2026-09",
            "2026-10",
            "2026-11",
        ]

    def test_twelve_months_cross_the_year(self) -> None:
        months = months_back(date(2026, 3, 31), 12)

        assert months[0] == "2025-04"
        assert months[-1] == "2026-03"
        assert len(months) == 12

    def test_a_day_belongs_to_its_month(self) -> None:
        assert month_of(date(2026, 1, 9)) == "2026-01"


class TestForward:
    @pytest.mark.parametrize(
        ("before", "after"),
        [
            (Stage.LEAD, Stage.COUNSELLING),
            (Stage.COUNSELLING, Stage.APPLYING),
            (Stage.APPLYING, Stage.OFFER),
            (Stage.OFFER, Stage.VISA),
            (Stage.LEAD, Stage.APPLYING),
        ],
    )
    def test_a_move_further_along_the_journey_is_forward(self, before: Stage, after: Stage) -> None:
        assert is_forward(move(1, OCT, before, after))

    @pytest.mark.parametrize(
        ("before", "after"),
        [
            (Stage.VISA, Stage.COUNSELLING),
            (Stage.VISA, Stage.OFFER),
            (Stage.COUNSELLING, Stage.LEAD),
            (Stage.COUNSELLING, Stage.LOST),
            (Stage.LOST, Stage.COUNSELLING),
            (Stage.LEAD, Stage.LEAD),
        ],
    )
    def test_going_back_losing_and_coming_back_are_not_forward(
        self, before: Stage, after: Stage
    ) -> None:
        assert not is_forward(move(1, OCT, before, after))


class TestStageFromHistory:
    @pytest.mark.parametrize("stage", list(Stage))
    def test_every_stage_reads_as_itself(self, stage: Stage) -> None:
        assert stage_from_history(stage.value) == stage

    def test_stages_that_are_gone_read_as_where_they_now_belong(self) -> None:
        assert stage_from_history("onboarded") == Stage.COUNSELLING
        assert stage_from_history("completed") == Stage.VISA

    def test_anything_else_is_not_a_stage(self) -> None:
        assert stage_from_history("<b>lead</b>") is None


class TestStudentMonths:
    def test_nothing_happened_gives_zeros_for_every_month(self) -> None:
        assert student_months(["2026-09", "2026-10"], [], []) == [
            MonthCounts("2026-09"),
            MonthCounts("2026-10"),
        ]

    def test_new_visitors_are_counted_in_the_month_they_came(self) -> None:
        counts = student_months(["2026-09", "2026-10"], [SEP, OCT, OCT, date(2026, 8, 1)], [])

        assert [row.new_visitors for row in counts] == [1, 2]

    def test_signing_up_counts_once_per_student_when_they_first_reach_counselling(self) -> None:
        moves = [
            move(1, SEP, Stage.LEAD, Stage.COUNSELLING),
            move(1, OCT, Stage.COUNSELLING, Stage.APPLYING),
            move(2, OCT, Stage.COUNSELLING, Stage.LEAD),
        ]

        counts = student_months(["2026-09", "2026-10"], [], moves)

        assert [row.signed_up for row in counts] == [1, 0]
        assert [row.applied for row in counts] == [0, 1]

    def test_skipping_counselling_still_counts_as_signing_up_and_applying(self) -> None:
        counts = student_months(["2026-10"], [], [move(1, OCT, Stage.LEAD, Stage.APPLYING)])

        assert (counts[0].signed_up, counts[0].applied) == (1, 1)

    def test_a_student_who_signed_up_before_the_months_shown_is_not_counted_again(self) -> None:
        moves = [
            move(1, date(2026, 3, 1), Stage.LEAD, Stage.COUNSELLING),
            move(1, date(2026, 4, 1), Stage.COUNSELLING, Stage.LOST),
            move(1, SEP, Stage.LOST, Stage.COUNSELLING),
            move(1, OCT, Stage.COUNSELLING, Stage.LEAD),
            move(1, OCT, Stage.LEAD, Stage.COUNSELLING),
        ]

        counts = student_months(["2026-09", "2026-10"], [], moves)

        assert [row.signed_up for row in counts] == [0, 0]

    def test_moves_are_taken_in_date_order_whatever_order_they_arrive_in(self) -> None:
        moves = [
            move(1, OCT, Stage.COUNSELLING, Stage.APPLYING),
            move(1, SEP, Stage.LEAD, Stage.COUNSELLING),
        ]

        counts = student_months(["2026-09", "2026-10"], [], moves)

        assert [row.signed_up for row in counts] == [1, 0]

    def test_visas_refused_and_lost_count_every_time_they_happen(self) -> None:
        moves = [
            move(1, SEP, Stage.VISA, Stage.COUNSELLING),
            move(2, OCT, Stage.VISA, Stage.OFFER),
            move(3, OCT, Stage.LEAD, Stage.LOST),
            move(4, OCT, Stage.COUNSELLING, Stage.LEAD),
        ]

        counts = student_months(["2026-09", "2026-10"], [], moves)

        assert [row.visas_refused for row in counts] == [1, 1]
        assert [row.lost for row in counts] == [0, 1]

    def test_visas_granted_count_on_the_day_each_was_granted(self) -> None:
        granted = [SEP, OCT, OCT, date(2026, 8, 1)]

        counts = student_months(["2026-09", "2026-10"], [], [], granted)

        assert [row.visas_granted for row in counts] == [1, 2]


class TestSortedSources:
    def test_every_source_is_listed_most_first_then_in_the_fixed_order(self) -> None:
        rows = sorted_sources({Source.FACEBOOK: 3, Source.REFERRAL: 3, Source.WALK_IN: 5})

        assert rows[:3] == [(Source.WALK_IN, 5), (Source.FACEBOOK, 3), (Source.REFERRAL, 3)]
        assert [source for source, _ in rows[3:]] == [
            Source.PHONE_CALL,
            Source.WEBSITE,
            Source.SUB_AGENT,
            Source.EVENT_FAIR,
            Source.OTHER,
        ]
        assert all(count == 0 for _, count in rows[3:])


def mark(batch: int, student: int, *, attended: bool) -> ClassMark:
    """One Student's mark on one class day: attended (Present, Late) or not (Absent, Leave)."""
    return ClassMark(batch_id=batch, student_id=student, attended=attended)


class TestClassAttendance:
    def test_a_class_share_is_the_average_of_its_students_shares(self) -> None:
        # Student 1 came 2 of 2 days (100%), Student 2 came 1 of 3 days (33%): 67%, not 3 of 5.
        marks = [
            mark(7, 1, attended=True),
            mark(7, 1, attended=True),
            mark(7, 2, attended=True),
            mark(7, 2, attended=False),
            mark(7, 2, attended=False),
        ]

        assert class_attendance(marks) == {7: 67}

    def test_each_class_is_counted_on_its_own(self) -> None:
        marks = [mark(7, 1, attended=True), mark(8, 1, attended=False)]

        assert class_attendance(marks) == {7: 100, 8: 0}

    def test_halves_round_up(self) -> None:
        # 1 of 2 days, and 0 of 1 day: (50 + 0) / 2 = 25; then 1 of 8 days is 12.5%, shown 13%.
        assert class_attendance(
            [mark(7, 1, attended=True), mark(7, 1, attended=False), mark(7, 2, attended=False)]
        ) == {7: 25}
        assert class_attendance(
            [mark(9, 1, attended=True), *(mark(9, 1, attended=False) for _ in range(7))]
        ) == {9: 13}

    def test_a_class_without_marks_is_left_out(self) -> None:
        assert class_attendance([]) == {}


class TestMoneyMonths:
    """Issue 29: money in (Payments + Received Commission) and money out (Expenses) per month."""

    MONTHS = ("2026-09", "2026-10")

    def test_each_month_adds_up_its_own_days(self) -> None:
        rows = money_months(
            self.MONTHS,
            payments=[Amount(SEP, 18000), Amount(OCT, 40000), Amount(date(2026, 10, 31), 2000)],
            commission=[Amount(OCT, 290000)],
            expenses=[Amount(SEP, 85000), Amount(date(2026, 9, 1), 6500)],
        )

        assert rows == [
            MoneyMonth("2026-09", from_students=18000, from_commission=0, money_out=91500),
            MoneyMonth("2026-10", from_students=42000, from_commission=290000, money_out=0),
        ]

    def test_money_in_is_students_plus_commission(self) -> None:
        row = MoneyMonth("2026-10", from_students=42000, from_commission=290000, money_out=1)

        assert row.money_in == 332000

    def test_days_outside_the_months_are_left_out(self) -> None:
        rows = money_months(
            self.MONTHS,
            payments=[Amount(date(2026, 8, 31), 5), Amount(date(2026, 11, 1), 7)],
            commission=[Amount(date(2025, 10, 5), 9)],
            expenses=[Amount(date(2027, 9, 5), 11)],
        )

        assert rows == [MoneyMonth("2026-09"), MoneyMonth("2026-10")]

    def test_no_money_still_lists_every_month_in_order(self) -> None:
        rows = money_months(self.MONTHS, payments=[], commission=[], expenses=[])

        assert [row.month for row in rows] == list(self.MONTHS)
        assert all(row.money_in == row.money_out == 0 for row in rows)


class TestExpensesByCategory:
    def test_adds_up_each_category_largest_first(self) -> None:
        rows = by_category(
            [
                (ExpenseCategory.INTERNET, 2500),
                (ExpenseCategory.OFFICE_RENT, 85000),
                (ExpenseCategory.INTERNET, 2500),
                (ExpenseCategory.SALARIES, 120000),
            ]
        )

        assert rows == [
            (ExpenseCategory.SALARIES, 120000),
            (ExpenseCategory.OFFICE_RENT, 85000),
            (ExpenseCategory.INTERNET, 5000),
        ]

    def test_ties_keep_the_categories_fixed_order(self) -> None:
        rows = by_category([(ExpenseCategory.OTHER, 100), (ExpenseCategory.OFFICE_RENT, 100)])

        assert rows == [(ExpenseCategory.OFFICE_RENT, 100), (ExpenseCategory.OTHER, 100)]

    def test_nothing_spent_lists_nothing(self) -> None:
        assert by_category([]) == []


class TestCountries:
    """Issue 53: Visas granted by country and the countries new visitors want."""

    START, END = date(2026, 6, 1), date(2026, 11, 5)

    def test_granted_visas_in_the_range_count_by_country_most_first(self) -> None:
        rows = countries_granted(
            [
                GrantedVisa(self.START, Country.UK),
                GrantedVisa(date(2026, 8, 1), Country.AUSTRALIA),
                GrantedVisa(self.END, Country.AUSTRALIA),
            ],
            self.START,
            self.END,
        )

        assert rows == [(Country.AUSTRALIA, 2), (Country.UK, 1)]

    def test_both_ends_of_the_range_are_included_and_days_outside_are_not(self) -> None:
        rows = countries_granted(
            [
                GrantedVisa(date(2026, 5, 31), Country.CANADA),
                GrantedVisa(self.START, Country.USA),
                GrantedVisa(self.END, Country.USA),
                GrantedVisa(date(2026, 11, 6), Country.CANADA),
            ],
            self.START,
            self.END,
        )

        assert rows == [(Country.USA, 2)]

    def test_ties_keep_the_countries_fixed_order(self) -> None:
        rows = countries_granted(
            [GrantedVisa(self.END, Country.OTHER), GrantedVisa(self.END, Country.JAPAN)],
            self.START,
            self.END,
        )

        assert rows == [(Country.JAPAN, 1), (Country.OTHER, 1)]

    def test_new_visitors_count_once_each_by_the_country_they_want(self) -> None:
        rows = countries_wanted(
            [
                NewVisitor(1, self.START, Country.CANADA),
                NewVisitor(1, self.END, Country.CANADA),
                NewVisitor(2, self.END, Country.UK),
                NewVisitor(3, date(2026, 8, 9), Country.CANADA),
            ],
            self.START,
            self.END,
        )

        assert rows == [(Country.CANADA, 2), (Country.UK, 1)]

    def test_a_visitor_who_came_outside_the_range_is_left_out(self) -> None:
        rows = countries_wanted(
            [
                NewVisitor(1, date(2026, 5, 31), Country.CANADA),
                NewVisitor(2, date(2026, 11, 6), Country.UK),
            ],
            self.START,
            self.END,
        )

        assert rows == []

    def test_nothing_in_the_range_lists_nothing(self) -> None:
        assert countries_granted([], self.START, self.END) == []
        assert countries_wanted([], self.START, self.END) == []
