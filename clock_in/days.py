"""The pure rules of Clock-in: days, totals, and which corrections are allowed (spec 106-109).

No database here. A Work Session belongs to the day it was clocked in on. Days split at midnight
in the Consultancy's time zone (Asia/Kathmandu). The weekly days off are Saturday unless an Admin
chose others (asked 2026-11-17). A holiday an Admin sets in Configuration counts the same way: never
"not in", never short, never corrected.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from django.utils import timezone

# The weekly days off, as `date.weekday()` numbers, when an Admin hasn't chosen others.
SATURDAY_ONLY = frozenset({calendar.SATURDAY})
MINUTES_PER_HOUR = 60

NOT_PAST_MESSAGE = "Only a past day can be corrected."
DAY_OFF_MESSAGE = "{weekday} is off, so it can't be corrected."
HOLIDAY_MESSAGE = "{name} is a holiday, so it can't be corrected."
NO_HOLIDAYS: Mapping[date, str] = {}
FULL_DAY_MESSAGE = "This day is already a full working day, so it can't be changed."
RECORDED_MESSAGE = "That clock-out was recorded and can't be changed."
END_BEFORE_START_MESSAGE = "The end must be after the start."
FUTURE_MESSAGE = "That time hasn't happened yet."
OVERLAP_MESSAGE = "That overlaps time already recorded on this day."
SET_CLOCK_OUT_FIRST_MESSAGE = "Set the missing clock-out first."


class CorrectionRefusedError(Exception):
    """A correction the rules don't allow; the message says why in plain words."""


@dataclass(frozen=True)
class WorkingWeek:
    """What makes a full week: the Working Day's length and the weekly days off."""

    working_day_minutes: int
    # `date.weekday()` numbers; Saturday unless an Admin chose others (asked 2026-11-17).
    days_off: frozenset[int] = SATURDAY_ONLY


@dataclass(frozen=True)
class SessionTimes:
    """One Work Session's times, as the rules need them."""

    id: int
    # Its id outside the server (ADR 0027), for the screens.
    public_id: UUID
    clocked_in_at: datetime
    clocked_out_at: datetime | None
    not_clocked_out: bool
    corrected: bool
    # How it was corrected, for the screens: added as missed time, or its clock-out was set.
    added_by_correction: bool = False
    clock_out_by_correction: bool = False
    # Clocked in on Approved Work From Home dates (issue 87).
    work_from_home: bool = False


@dataclass(frozen=True)
class DaySummary:
    """One day of one Staff member's month."""

    day: date
    sessions: tuple[SessionTimes, ...]
    minutes: int
    first_in: datetime | None
    last_out: datetime | None
    # One of the office's weekly days off (Saturday unless an Admin chose others).
    day_off: bool
    later: bool
    today: bool
    not_in: bool
    missing_clock_out: bool
    still_in: bool
    short: bool
    corrected: bool
    correctable: bool
    # The name of the office's holiday on this day, if it is one.
    holiday: str | None = None


@dataclass(frozen=True)
class MonthSummary:
    """One Staff member's month: every day, and the figures shown above the calendar."""

    days: tuple[DaySummary, ...]
    days_worked: int
    minutes: int
    short_days: int
    missing_clock_outs: int
    corrected: int


def local_day(moment: datetime) -> date:
    """The Consultancy's calendar day of a moment."""
    return timezone.localdate(moment)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """When `day` starts and ends (the next midnight) in the Consultancy's time zone."""
    zone = timezone.get_current_timezone()
    start = datetime.combine(day, time.min, tzinfo=zone)
    return start, datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)


def month_days(year: int, month: int) -> list[date]:
    """Every day of a month, in order."""
    last = calendar.monthrange(year, month)[1]
    return [date(year, month, number) for number in range(1, last + 1)]


def duration_text(minutes: int) -> str:
    """A length of time as people say it, e.g. "8 h 25 min", "1 h", "40 min"."""
    hours, rest = divmod(minutes, MINUTES_PER_HOUR)
    if hours and rest:
        return f"{hours} h {rest} min"
    return f"{hours} h" if hours else f"{rest} min"


def hours_text(minutes: int) -> str:
    """A Working Day in hours, e.g. "8 h" or "7.5 h"."""
    hours = minutes / MINUTES_PER_HOUR
    return f"{hours:g} h"


def _minutes_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // MINUTES_PER_HOUR))


def is_missing_clock_out(session: SessionTimes, now: datetime) -> bool:
    """No clock-out yet, and either marked "not clocked out" or left open on a day that ended.

    Once an Admin sets the missing clock-out, the session is no longer missing one.
    """
    if session.clocked_out_at is not None:
        return False
    return session.not_clocked_out or local_day(session.clocked_in_at) < local_day(now)


def session_minutes(session: SessionTimes, now: datetime) -> int:
    """How long a session counts for: to its clock-out, or to now while open today."""
    if session.clocked_out_at is not None:
        return _minutes_between(session.clocked_in_at, session.clocked_out_at)
    if is_missing_clock_out(session, now):
        return 0
    return _minutes_between(session.clocked_in_at, now)


def summarise_day(
    day: date,
    sessions: Iterable[SessionTimes],
    *,
    week: WorkingWeek,
    now: datetime,
    holiday: str | None = None,
) -> DaySummary:
    """One day from the sessions clocked in on it; `holiday` names it if it is one."""
    ordered = tuple(sorted(sessions, key=lambda s: (s.clocked_in_at, s.id)))
    today = local_day(now)
    day_off = day.weekday() in week.days_off
    off = day_off or holiday is not None
    past = day < today
    minutes = sum(session_minutes(s, now) for s in ordered)
    missing = any(is_missing_clock_out(s, now) for s in ordered)
    outs = [s.clocked_out_at for s in ordered if s.clocked_out_at is not None]
    return DaySummary(
        day=day,
        sessions=ordered,
        minutes=minutes,
        first_in=ordered[0].clocked_in_at if ordered else None,
        last_out=max(outs) if outs else None,
        day_off=day_off,
        later=day > today,
        today=day == today,
        not_in=past and not off and not ordered,
        missing_clock_out=missing,
        still_in=day == today and any(_is_open_now(s, now) for s in ordered),
        short=past
        and not off
        and bool(ordered)
        and not missing
        and minutes < week.working_day_minutes,
        corrected=any(s.corrected for s in ordered),
        correctable=past and not off and (missing or minutes < week.working_day_minutes),
        holiday=holiday,
    )


def summarise_month(
    year: int,
    month: int,
    sessions: Iterable[SessionTimes],
    *,
    week: WorkingWeek,
    now: datetime,
) -> MonthSummary:
    """A month of days from the sessions clocked in during it, with no holidays."""
    return summarise_days(
        month_days(year, month),
        sessions,
        week=week,
        now=now,
        holidays=NO_HOLIDAYS,
    )


def summarise_days(
    days: Sequence[date],
    sessions: Iterable[SessionTimes],
    *,
    week: WorkingWeek,
    now: datetime,
    holidays: Mapping[date, str],
) -> MonthSummary:
    """Days of a month from the sessions clocked in during them, with the office's holidays."""
    by_day: dict[date, list[SessionTimes]] = {}
    for session in sessions:
        by_day.setdefault(local_day(session.clocked_in_at), []).append(session)
    summaries = tuple(
        summarise_day(
            day,
            by_day.get(day, ()),
            week=week,
            now=now,
            holiday=holidays.get(day),
        )
        for day in days
    )
    return MonthSummary(
        days=summaries,
        days_worked=sum(1 for day in summaries if day.sessions),
        minutes=sum(day.minutes for day in summaries),
        short_days=sum(1 for day in summaries if day.short),
        missing_clock_outs=sum(1 for day in summaries if day.missing_clock_out),
        corrected=sum(1 for day in summaries for s in day.sessions if s.corrected),
    )


def check_clock_out(
    session: SessionTimes,
    clocked_out_at: datetime,
    nearby: Sequence[SessionTimes],
    *,
    week: WorkingWeek,
    now: datetime,
) -> None:
    """Refuse setting a missing clock-out unless every rule allows it (spec 108).

    `nearby` holds the Staff member's sessions around that day, the session itself included.

    Raises:
        CorrectionRefusedError: Why it isn't allowed.
    """
    if session.clocked_out_at is not None:
        raise CorrectionRefusedError(RECORDED_MESSAGE)
    day = local_day(session.clocked_in_at)
    _check_past_working_day(day, now, week.days_off)
    others = [s for s in nearby if s.id != session.id]
    span = (session.clocked_in_at, clocked_out_at)
    _check_span(span, others, now)
    _check_total(day, span, others, week.working_day_minutes, now)


def check_added_time(
    day: date,
    span: tuple[datetime, datetime],
    nearby: Sequence[SessionTimes],
    *,
    week: WorkingWeek,
    now: datetime,
) -> None:
    """Refuse adding missed time (`span`: start, end) to `day` unless the rules allow it.

    Spec 108. `nearby` holds the Staff member's sessions around that day.

    Raises:
        CorrectionRefusedError: Why it isn't allowed.
    """
    _check_past_working_day(day, now, week.days_off)
    summary = summarise_day(day, _on(day, nearby), week=week, now=now)
    if summary.missing_clock_out:
        raise CorrectionRefusedError(SET_CLOCK_OUT_FIRST_MESSAGE)
    if summary.minutes >= week.working_day_minutes:
        raise CorrectionRefusedError(FULL_DAY_MESSAGE)
    _check_span(span, nearby, now)
    _check_total(day, span, nearby, week.working_day_minutes, now)


def _is_open_now(session: SessionTimes, now: datetime) -> bool:
    return session.clocked_out_at is None and not is_missing_clock_out(session, now)


def _on(day: date, sessions: Iterable[SessionTimes]) -> list[SessionTimes]:
    return [s for s in sessions if local_day(s.clocked_in_at) == day]


def check_not_a_holiday(day: date, holidays: Mapping[date, str]) -> None:
    """Refuse correcting a holiday, like a weekly day off (asked 2026-11-16).

    Raises:
        CorrectionRefusedError: `day` is one of the office's holidays.
    """
    if day in holidays:
        raise CorrectionRefusedError(HOLIDAY_MESSAGE.format(name=holidays[day]))


def _check_past_working_day(day: date, now: datetime, days_off: frozenset[int]) -> None:
    if day >= local_day(now):
        raise CorrectionRefusedError(NOT_PAST_MESSAGE)
    if day.weekday() in days_off:
        raise CorrectionRefusedError(
            DAY_OFF_MESSAGE.format(weekday=calendar.day_name[day.weekday()])
        )


def _check_span(
    span: tuple[datetime, datetime], others: Iterable[SessionTimes], now: datetime
) -> None:
    start, end = span
    if end <= start:
        raise CorrectionRefusedError(END_BEFORE_START_MESSAGE)
    if end > now:
        raise CorrectionRefusedError(FUTURE_MESSAGE)
    for other in others:
        other_end = other.clocked_out_at or other.clocked_in_at
        if other.clocked_in_at < end and other_end > start:
            raise CorrectionRefusedError(OVERLAP_MESSAGE)


def _check_total(
    day: date,
    span: tuple[datetime, datetime],
    others: Iterable[SessionTimes],
    working_day_minutes: int,
    now: datetime,
) -> None:
    total = sum(session_minutes(s, now) for s in _on(day, others))
    total += _minutes_between(*span)
    if total > working_day_minutes:
        over = total - working_day_minutes
        raise CorrectionRefusedError(
            f"That makes the day {duration_text(total)}, {duration_text(over)} longer than the "
            f"{hours_text(working_day_minutes)} working day. Only missing time can be added."
        )
