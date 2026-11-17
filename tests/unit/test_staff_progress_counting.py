"""Staff Progress counting rules (spec 125, 357; issue 84). Pure: no database."""

from __future__ import annotations

from datetime import date

import pytest

from staff.models import Role
from staff_progress.counting import (
    COUNSELLOR_COUNTS,
    FRONT_DESK_COUNTS,
    RECORDED_SINCE,
    TEACHER_COUNTS,
    Count,
    StageMoveBy,
    counted_from,
    counts_for,
    is_too_long,
    tally_stage_moves,
)


class TestCardsForEachRole:
    def test_a_counsellor_gets_the_five_counsellor_cards_in_order(self) -> None:
        assert counts_for(Role.COUNSELLOR, batches_on=True, teaches=False) == (
            Count.MOVED_FORWARD,
            Count.APPLICATIONS_MADE,
            Count.VISAS_APPLIED_FOR,
            Count.STUDENTS_LOST,
            Count.TASKS_DONE,
        )

    def test_front_desk_gets_visitors_new_leads_and_payments(self) -> None:
        assert counts_for(Role.FRONT_DESK, batches_on=True, teaches=False) == (
            Count.VISITORS_REGISTERED,
            Count.NEW_LEADS,
            Count.PAYMENTS_TAKEN,
        )

    def test_a_teacher_gets_classes_taken_and_attendance_marked(self) -> None:
        assert counts_for(Role.TEACHER, batches_on=True, teaches=True) == (
            Count.CLASSES_TAKEN,
            Count.ATTENDANCE_MARKED,
        )

    def test_a_teacher_gets_no_cards_while_test_prep_classes_are_off(self) -> None:
        assert counts_for(Role.TEACHER, batches_on=False, teaches=True) == ()

    def test_an_admin_gets_the_counsellor_and_front_desk_cards(self) -> None:
        assert counts_for(Role.ADMIN, batches_on=True, teaches=False) == (
            *COUNSELLOR_COUNTS,
            *FRONT_DESK_COUNTS,
        )

    def test_an_admin_who_teaches_a_class_also_gets_the_teacher_cards(self) -> None:
        assert counts_for(Role.ADMIN, batches_on=True, teaches=True) == (
            *COUNSELLOR_COUNTS,
            *FRONT_DESK_COUNTS,
            *TEACHER_COUNTS,
        )

    def test_an_admin_who_teaches_gets_no_teacher_cards_while_classes_are_off(self) -> None:
        assert counts_for(Role.ADMIN, batches_on=False, teaches=True) == (
            *COUNSELLOR_COUNTS,
            *FRONT_DESK_COUNTS,
        )


class TestCountedFrom:
    def test_tasks_done_were_recorded_from_when_ticket_83_shipped(self) -> None:
        assert {Count.TASKS_DONE: date(2026, 11, 15)} == RECORDED_SINCE

    def test_a_period_starting_before_recording_began_says_when_it_began(self) -> None:
        assert counted_from(Count.TASKS_DONE, date(2026, 11, 1)) == date(2026, 11, 15)

    def test_a_period_starting_on_the_day_recording_began_needs_no_note(self) -> None:
        assert counted_from(Count.TASKS_DONE, date(2026, 11, 15)) is None

    def test_a_period_starting_after_needs_no_note(self) -> None:
        assert counted_from(Count.TASKS_DONE, date(2026, 12, 1)) is None

    @pytest.mark.parametrize("count", [c for c in Count if c is not Count.TASKS_DONE])
    def test_counts_recorded_from_the_start_never_need_a_note(self, count: Count) -> None:
        assert counted_from(count, date(2000, 1, 1)) is None


class TestPeriodLength:
    def test_one_day_is_fine(self) -> None:
        assert not is_too_long(date(2026, 11, 15), date(2026, 11, 15))

    def test_a_whole_year_to_the_same_date_is_fine(self) -> None:
        assert not is_too_long(date(2025, 11, 15), date(2026, 11, 15))

    def test_a_leap_year_to_the_same_date_is_fine(self) -> None:
        assert not is_too_long(date(2024, 1, 1), date(2025, 1, 1))

    def test_one_day_more_than_a_year_is_too_long(self) -> None:
        assert is_too_long(date(2025, 11, 15), date(2026, 11, 16))

    def test_from_the_29th_of_february_a_year_ends_on_the_1st_of_march(self) -> None:
        assert not is_too_long(date(2024, 2, 29), date(2025, 3, 1))
        assert is_too_long(date(2024, 2, 29), date(2025, 3, 2))


class TestStageMoves:
    def test_a_move_further_along_the_journey_is_forward(self) -> None:
        tally = tally_stage_moves([StageMoveBy(7, "lead", "counselling")])

        assert tally == {7: (1, 0)}

    def test_a_move_back_is_not_counted(self) -> None:
        assert tally_stage_moves([StageMoveBy(7, "visa", "offer")]) == {7: (0, 0)}

    def test_a_move_to_lost_counts_as_lost_not_forward(self) -> None:
        assert tally_stage_moves([StageMoveBy(7, "applying", "lost")]) == {7: (0, 1)}

    def test_coming_back_from_lost_is_not_forward(self) -> None:
        assert tally_stage_moves([StageMoveBy(7, "lost", "counselling")]) == {7: (0, 0)}

    def test_stages_that_are_gone_read_as_where_they_now_belong(self) -> None:
        moves = [StageMoveBy(7, "onboarded", "applying"), StageMoveBy(7, "offer", "completed")]

        assert tally_stage_moves(moves) == {7: (2, 0)}

    def test_unknown_values_are_skipped(self) -> None:
        assert tally_stage_moves([StageMoveBy(7, "nonsense", "visa")]) == {7: (0, 0)}

    def test_each_person_is_counted_separately(self) -> None:
        moves = [
            StageMoveBy(1, "lead", "counselling"),
            StageMoveBy(2, "counselling", "lost"),
            StageMoveBy(1, "counselling", "applying"),
        ]

        assert tally_stage_moves(moves) == {1: (2, 0), 2: (0, 1)}
