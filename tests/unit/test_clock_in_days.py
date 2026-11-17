"""The pure rules of Clock-in (spec 106-109, issue 56): days, totals and what may be corrected.

Days split at midnight in the Consultancy's time zone (Asia/Kathmandu, UTC+5:45).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from clock_in.days import (
    SATURDAY_ONLY,
    CorrectionRefusedError,
    SessionTimes,
    WorkingWeek,
    check_added_time,
    check_clock_out,
    check_not_a_holiday,
    day_bounds,
    duration_text,
    hours_text,
    local_day,
    month_days,
    session_minutes,
    summarise_day,
    summarise_days,
    summarise_month,
)

KTM = ZoneInfo("Asia/Kathmandu")
WORKING_DAY = 8 * 60
# "Now" in these tests: Thursday 5 November 2026, 11:00 in Kathmandu.
NOW = datetime(2026, 11, 5, 11, 0, tzinfo=KTM)
TODAY = date(2026, 11, 5)
WEDNESDAY = date(2026, 11, 4)
SATURDAY = date(2026, 10, 31)
WEEK = WorkingWeek(WORKING_DAY)


def at(day: date, hours: int, minutes: int = 0) -> datetime:
    """A moment on `day` in Kathmandu."""
    return datetime.combine(day, time(hours, minutes), tzinfo=KTM)


def session(
    start: datetime,
    end: datetime | None = None,
    *,
    pk: int = 1,
    not_clocked_out: bool = False,
    corrected: bool = False,
) -> SessionTimes:
    """One Work Session's times."""
    return SessionTimes(
        id=pk,
        clocked_in_at=start,
        clocked_out_at=end,
        not_clocked_out=not_clocked_out,
        corrected=corrected,
    )


class TestDaysInKathmandu:
    def test_a_day_starts_at_midnight_nepal_time(self) -> None:
        start, end = day_bounds(WEDNESDAY)

        assert start == datetime(2026, 11, 3, 18, 15, tzinfo=UTC)
        assert end == datetime(2026, 11, 4, 18, 15, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("moment", "day"),
        [
            (datetime(2026, 11, 3, 18, 14, tzinfo=UTC), date(2026, 11, 3)),
            (datetime(2026, 11, 3, 18, 15, tzinfo=UTC), date(2026, 11, 4)),
            (datetime(2026, 11, 4, 18, 14, 59, tzinfo=UTC), date(2026, 11, 4)),
        ],
    )
    def test_moments_fall_on_their_nepal_day(self, moment: datetime, day: date) -> None:
        assert local_day(moment) == day

    def test_month_days(self) -> None:
        assert month_days(2026, 2)[-1] == date(2026, 2, 28)
        assert month_days(2028, 2)[-1] == date(2028, 2, 29)
        assert len(month_days(2026, 10)) == 31


class TestSessionMinutes:
    def test_a_closed_session_counts_whole_minutes(self) -> None:
        worked = session(at(WEDNESDAY, 9, 2), at(WEDNESDAY, 17, 26).replace(second=59))

        assert session_minutes(worked, NOW) == 8 * 60 + 24

    def test_an_open_session_today_counts_up_to_now(self) -> None:
        assert session_minutes(session(at(TODAY, 9, 30)), NOW) == 90

    def test_an_open_session_from_an_earlier_day_counts_nothing(self) -> None:
        assert session_minutes(session(at(WEDNESDAY, 9)), NOW) == 0

    def test_a_not_clocked_out_session_counts_nothing(self) -> None:
        assert session_minutes(session(at(TODAY, 9), not_clocked_out=True), NOW) == 0


class TestSummariseDay:
    def test_a_full_past_day(self) -> None:
        day = summarise_day(
            WEDNESDAY,
            [session(at(WEDNESDAY, 9, 2), at(WEDNESDAY, 17, 26))],
            week=WEEK,
            now=NOW,
        )

        assert day.minutes == 8 * 60 + 24
        assert day.first_in == at(WEDNESDAY, 9, 2)
        assert day.last_out == at(WEDNESDAY, 17, 26)
        assert not day.short
        assert not day.missing_clock_out
        assert not day.correctable

    def test_a_short_past_day_can_be_corrected(self) -> None:
        day = summarise_day(
            WEDNESDAY,
            [session(at(WEDNESDAY, 9), at(WEDNESDAY, 13, 10))],
            week=WEEK,
            now=NOW,
        )

        assert day.short
        assert day.correctable

    def test_two_sessions_add_up_and_the_last_out_is_the_latest(self) -> None:
        day = summarise_day(
            WEDNESDAY,
            [
                session(at(WEDNESDAY, 14), at(WEDNESDAY, 18), pk=2),
                session(at(WEDNESDAY, 9), at(WEDNESDAY, 13), pk=1),
            ],
            week=WEEK,
            now=NOW,
        )

        assert day.minutes == 8 * 60
        assert day.first_in == at(WEDNESDAY, 9)
        assert day.last_out == at(WEDNESDAY, 18)
        assert not day.short
        assert [s.id for s in day.sessions] == [1, 2]

    @pytest.mark.parametrize("flagged", [True, False])
    def test_a_past_day_without_a_clock_out_is_missing_one(self, *, flagged: bool) -> None:
        day = summarise_day(
            WEDNESDAY,
            [session(at(WEDNESDAY, 9), not_clocked_out=flagged)],
            week=WEEK,
            now=NOW,
        )

        assert day.missing_clock_out
        assert not day.short
        assert day.correctable

    def test_a_not_clocked_out_session_given_its_clock_out_is_whole_again(self) -> None:
        day = summarise_day(
            WEDNESDAY,
            [session(at(WEDNESDAY, 9), at(WEDNESDAY, 17), not_clocked_out=True, corrected=True)],
            week=WEEK,
            now=NOW,
        )

        assert not day.missing_clock_out
        assert day.minutes == 8 * 60
        assert not day.correctable

    def test_today_while_clocked_in(self) -> None:
        day = summarise_day(TODAY, [session(at(TODAY, 9))], week=WEEK, now=NOW)

        assert day.today
        assert day.still_in
        assert day.minutes == 120
        assert not day.short
        assert not day.missing_clock_out
        assert not day.correctable

    def test_an_empty_past_working_day_is_not_in_and_can_be_corrected(self) -> None:
        day = summarise_day(WEDNESDAY, [], week=WEEK, now=NOW)

        assert day.not_in
        assert day.correctable
        assert day.minutes == 0

    def test_saturday_is_off_and_never_corrected(self) -> None:
        day = summarise_day(
            SATURDAY,
            [session(at(SATURDAY, 10), at(SATURDAY, 11))],
            week=WEEK,
            now=NOW,
        )

        assert day.day_off
        assert not day.short
        assert not day.not_in
        assert not day.correctable

    def test_a_later_day(self) -> None:
        day = summarise_day(date(2026, 11, 6), [], week=WEEK, now=NOW)

        assert day.later
        assert not day.not_in
        assert not day.correctable

    def test_corrected_when_any_session_was(self) -> None:
        day = summarise_day(
            WEDNESDAY,
            [session(at(WEDNESDAY, 9), at(WEDNESDAY, 17), corrected=True)],
            week=WEEK,
            now=NOW,
        )

        assert day.corrected


class TestSummariseMonth:
    def test_figures_for_a_month(self) -> None:
        sessions = [
            session(at(date(2026, 10, 1), 9), at(date(2026, 10, 1), 17), pk=1),
            session(at(date(2026, 10, 2), 9), at(date(2026, 10, 2), 13), pk=2),
            session(at(date(2026, 10, 5), 9), pk=3, not_clocked_out=True),
            session(at(date(2026, 10, 6), 9), at(date(2026, 10, 6), 17), pk=4, corrected=True),
        ]

        month = summarise_month(2026, 10, sessions, week=WEEK, now=NOW)

        assert len(month.days) == 31
        assert month.days_worked == 4
        assert month.minutes == 8 * 60 + 4 * 60 + 8 * 60
        assert month.short_days == 1
        assert month.missing_clock_outs == 1
        assert month.corrected == 1


class TestCheckClockOut:
    def test_a_missing_clock_out_can_be_set(self) -> None:
        forgotten = session(at(WEDNESDAY, 9), not_clocked_out=True)

        check_clock_out(forgotten, at(WEDNESDAY, 17), [forgotten], week=WEEK, now=NOW)

    def test_a_recorded_clock_out_never_changes(self) -> None:
        done = session(at(WEDNESDAY, 9), at(WEDNESDAY, 12))

        with pytest.raises(CorrectionRefusedError) as refused:
            check_clock_out(done, at(WEDNESDAY, 13), [done], week=WEEK, now=NOW)

        assert str(refused.value) == "That clock-out was recorded and can't be changed."

    def test_today_is_refused(self) -> None:
        open_now = session(at(TODAY, 9))

        with pytest.raises(CorrectionRefusedError) as refused:
            check_clock_out(open_now, at(TODAY, 10), [open_now], week=WEEK, now=NOW)

        assert str(refused.value) == "Only a past day can be corrected."

    def test_saturday_is_refused(self) -> None:
        forgotten = session(at(SATURDAY, 9), not_clocked_out=True)

        with pytest.raises(CorrectionRefusedError) as refused:
            check_clock_out(forgotten, at(SATURDAY, 12), [forgotten], week=WEEK, now=NOW)

        assert str(refused.value) == "Saturday is off, so it can't be corrected."

    @pytest.mark.parametrize("end", [at(WEDNESDAY, 9), at(WEDNESDAY, 8, 30)])
    def test_ending_before_the_start_is_refused(self, end: datetime) -> None:
        forgotten = session(at(WEDNESDAY, 9), not_clocked_out=True)

        with pytest.raises(CorrectionRefusedError) as refused:
            check_clock_out(forgotten, end, [forgotten], week=WEEK, now=NOW)

        assert str(refused.value) == "The end must be after the start."

    def test_overlapping_another_session_is_refused(self) -> None:
        forgotten = session(at(WEDNESDAY, 9), pk=1, not_clocked_out=True)
        later = session(at(WEDNESDAY, 13), at(WEDNESDAY, 15), pk=2)

        with pytest.raises(CorrectionRefusedError) as refused:
            check_clock_out(
                forgotten,
                at(WEDNESDAY, 14),
                [forgotten, later],
                week=WEEK,
                now=NOW,
            )

        assert str(refused.value) == "That overlaps time already recorded on this day."

    def test_past_the_working_day_is_refused_saying_by_how_much(self) -> None:
        forgotten = session(at(WEDNESDAY, 9), not_clocked_out=True)

        with pytest.raises(CorrectionRefusedError) as refused:
            check_clock_out(
                forgotten,
                at(WEDNESDAY, 17, 40),
                [forgotten],
                week=WEEK,
                now=NOW,
            )

        assert str(refused.value) == (
            "That makes the day 8 h 40 min, 40 min longer than the 8 h working day. "
            "Only missing time can be added."
        )

    def test_exactly_the_working_day_is_allowed(self) -> None:
        forgotten = session(at(WEDNESDAY, 9), not_clocked_out=True)

        check_clock_out(forgotten, at(WEDNESDAY, 17), [forgotten], week=WEEK, now=NOW)


class TestCheckAddedTime:
    def test_missed_time_on_an_empty_day(self) -> None:
        check_added_time(
            WEDNESDAY,
            (at(WEDNESDAY, 9), at(WEDNESDAY, 17)),
            [],
            week=WEEK,
            now=NOW,
        )

    def test_missed_time_on_a_short_day(self) -> None:
        check_added_time(
            WEDNESDAY,
            (at(WEDNESDAY, 14), at(WEDNESDAY, 17)),
            [session(at(WEDNESDAY, 9), at(WEDNESDAY, 13))],
            week=WEEK,
            now=NOW,
        )

    def test_a_full_day_cant_be_opened(self) -> None:
        with pytest.raises(CorrectionRefusedError) as refused:
            check_added_time(
                WEDNESDAY,
                (at(WEDNESDAY, 18), at(WEDNESDAY, 18, 30)),
                [session(at(WEDNESDAY, 9), at(WEDNESDAY, 17, 30))],
                week=WEEK,
                now=NOW,
            )

        assert str(refused.value) == (
            "This day is already a full working day, so it can't be changed."
        )

    def test_a_day_missing_a_clock_out_needs_that_set_first(self) -> None:
        with pytest.raises(CorrectionRefusedError) as refused:
            check_added_time(
                WEDNESDAY,
                (at(WEDNESDAY, 7), at(WEDNESDAY, 8)),
                [session(at(WEDNESDAY, 9), not_clocked_out=True)],
                week=WEEK,
                now=NOW,
            )

        assert str(refused.value) == "Set the missing clock-out first."

    @pytest.mark.parametrize(
        ("day", "message"),
        [
            (TODAY, "Only a past day can be corrected."),
            (date(2026, 11, 9), "Only a past day can be corrected."),
            (SATURDAY, "Saturday is off, so it can't be corrected."),
        ],
    )
    def test_today_later_days_and_saturday_are_refused(self, day: date, message: str) -> None:
        with pytest.raises(CorrectionRefusedError) as refused:
            check_added_time(day, (at(day, 8), at(day, 9)), [], week=WEEK, now=NOW)

        assert str(refused.value) == message

    def test_ending_before_the_start_is_refused(self) -> None:
        with pytest.raises(CorrectionRefusedError) as refused:
            check_added_time(
                WEDNESDAY,
                (at(WEDNESDAY, 13), at(WEDNESDAY, 12)),
                [],
                week=WEEK,
                now=NOW,
            )

        assert str(refused.value) == "The end must be after the start."

    @pytest.mark.parametrize(
        ("start", "end"),
        [((8, 0), (9, 30)), ((12, 0), (14, 0)), ((10, 0), (11, 0)), ((8, 0), (14, 0))],
    )
    def test_overlapping_recorded_time_is_refused(
        self, start: tuple[int, int], end: tuple[int, int]
    ) -> None:
        with pytest.raises(CorrectionRefusedError) as refused:
            check_added_time(
                WEDNESDAY,
                (at(WEDNESDAY, *start), at(WEDNESDAY, *end)),
                [session(at(WEDNESDAY, 9), at(WEDNESDAY, 13))],
                week=WEEK,
                now=NOW,
            )

        assert str(refused.value) == "That overlaps time already recorded on this day."

    def test_touching_recorded_time_is_allowed(self) -> None:
        check_added_time(
            WEDNESDAY,
            (at(WEDNESDAY, 13), at(WEDNESDAY, 14)),
            [session(at(WEDNESDAY, 9), at(WEDNESDAY, 13))],
            week=WEEK,
            now=NOW,
        )

    def test_past_the_working_day_is_refused_saying_by_how_much(self) -> None:
        with pytest.raises(CorrectionRefusedError) as refused:
            check_added_time(
                WEDNESDAY,
                (at(WEDNESDAY, 13), at(WEDNESDAY, 18, 15)),
                [session(at(WEDNESDAY, 9), at(WEDNESDAY, 12))],
                week=WEEK,
                now=NOW,
            )

        assert str(refused.value) == (
            "That makes the day 8 h 15 min, 15 min longer than the 8 h working day. "
            "Only missing time can be added."
        )


class TestWords:
    @pytest.mark.parametrize(
        ("minutes", "text"), [(0, "0 min"), (40, "40 min"), (60, "1 h"), (505, "8 h 25 min")]
    )
    def test_duration_text(self, minutes: int, text: str) -> None:
        assert duration_text(minutes) == text

    @pytest.mark.parametrize(("minutes", "text"), [(480, "8 h"), (450, "7.5 h"), (240, "4 h")])
    def test_hours_text(self, minutes: int, text: str) -> None:
        assert hours_text(minutes) == text


DASHAIN = date(2026, 10, 21)  # a Wednesday
HOLIDAYS = {DASHAIN: "Dashain"}


class TestHolidays:
    """A holiday set in Configuration counts like Saturday: off, never short, never corrected."""

    def test_an_empty_holiday_is_not_in_nor_correctable(self) -> None:
        day = summarise_day(DASHAIN, [], week=WEEK, now=NOW, holiday="Dashain")

        assert day.holiday == "Dashain"
        assert not day.not_in
        assert not day.correctable

    def test_a_little_work_on_a_holiday_is_never_a_short_day(self) -> None:
        day = summarise_day(
            DASHAIN,
            [session(at(DASHAIN, 10), at(DASHAIN, 12))],
            week=WEEK,
            now=NOW,
            holiday="Dashain",
        )

        assert day.minutes == 120
        assert not day.short
        assert not day.correctable

    def test_an_ordinary_day_has_no_holiday(self) -> None:
        day = summarise_day(WEDNESDAY, [], week=WEEK, now=NOW)

        assert day.holiday is None

    def test_a_month_names_its_holidays_and_counts_them_as_off(self) -> None:
        sessions = [session(at(DASHAIN, 10), at(DASHAIN, 12))]

        month = summarise_days(
            month_days(2026, 10),
            sessions,
            week=WEEK,
            now=NOW,
            holidays=HOLIDAYS,
        )

        dashain = next(day for day in month.days if day.day == DASHAIN)
        assert dashain.holiday == "Dashain"
        assert month.days_worked == 1
        assert month.short_days == 0

    def test_a_holiday_cant_be_corrected(self) -> None:
        with pytest.raises(CorrectionRefusedError) as refused:
            check_not_a_holiday(DASHAIN, HOLIDAYS)

        assert str(refused.value) == "Dashain is a holiday, so it can't be corrected."

    def test_any_other_day_passes(self) -> None:
        check_not_a_holiday(WEDNESDAY, HOLIDAYS)


SUNDAY = date(2026, 11, 1)
SATURDAY_AND_SUNDAY = frozenset({SATURDAY.weekday(), SUNDAY.weekday()})


class TestWeeklyDaysOff:
    """Configuration → Working hours → Weekly days off (asked 2026-11-17): Saturday by default."""

    def test_saturday_is_the_day_off_unless_told_otherwise(self) -> None:
        assert frozenset({SATURDAY.weekday()}) == SATURDAY_ONLY
        sunday = summarise_day(SUNDAY, [], week=WEEK, now=NOW)

        assert not sunday.day_off
        assert sunday.not_in

    def test_sunday_off_makes_saturday_a_working_day(self) -> None:
        sunday_only = frozenset({SUNDAY.weekday()})
        sunday = summarise_day(SUNDAY, [], week=WorkingWeek(WORKING_DAY, sunday_only), now=NOW)
        saturday = summarise_day(SATURDAY, [], week=WorkingWeek(WORKING_DAY, sunday_only), now=NOW)

        assert sunday.day_off
        assert not sunday.not_in
        assert not sunday.correctable
        assert not saturday.day_off
        assert saturday.not_in
        assert saturday.correctable

    def test_both_days_off(self) -> None:
        month = summarise_days(
            [SATURDAY, SUNDAY],
            [],
            week=WorkingWeek(WORKING_DAY, SATURDAY_AND_SUNDAY),
            now=NOW,
            holidays={},
        )

        assert [day.day_off for day in month.days] == [True, True]
        assert not any(day.not_in for day in month.days)

    def test_no_days_off_makes_every_day_a_working_day(self) -> None:
        saturday = summarise_day(SATURDAY, [], week=WorkingWeek(WORKING_DAY, frozenset()), now=NOW)

        assert not saturday.day_off
        assert saturday.not_in

    def test_a_day_off_is_never_corrected_and_says_which_day(self) -> None:
        with pytest.raises(CorrectionRefusedError) as refused:
            check_added_time(
                SUNDAY,
                (at(SUNDAY, 8), at(SUNDAY, 9)),
                [],
                week=WorkingWeek(WORKING_DAY, SATURDAY_AND_SUNDAY),
                now=NOW,
            )

        assert str(refused.value) == "Sunday is off, so it can't be corrected."

    def test_a_working_saturday_can_be_corrected(self) -> None:
        check_added_time(
            SATURDAY,
            (at(SATURDAY, 8), at(SATURDAY, 9)),
            [],
            week=WorkingWeek(WORKING_DAY, frozenset()),
            now=NOW,
        )

    def test_a_forgotten_clock_out_on_a_working_saturday_can_be_set(self) -> None:
        forgotten = session(at(SATURDAY, 9), not_clocked_out=True)

        check_clock_out(
            forgotten,
            at(SATURDAY, 12),
            [forgotten],
            week=WorkingWeek(WORKING_DAY, frozenset({SUNDAY.weekday()})),
            now=NOW,
        )
