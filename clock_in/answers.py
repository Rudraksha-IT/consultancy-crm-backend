"""Clock-in as the API returns it. Built by hand so nothing extra (never an address) leaks out."""

from __future__ import annotations

from datetime import datetime

from clock_in.days import DaySummary, MonthSummary, SessionTimes, is_missing_clock_out
from clock_in.models import Holiday, WorkFromHome, WorkingDayChange, WorkSessionChange
from clock_in.services import ClockState, PersonNow
from staff.models import Staff, StaffStatus


def moment(value: datetime | None) -> str | None:
    """A stored moment as ISO 8601 in UTC ("Z"), or None."""
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def clock_answer(state: ClockState) -> dict[str, object]:
    """The Home clock: the server's time, the network state, and the day so far."""
    session = state.open_session
    return {
        "now": moment(state.now),
        "office_network": state.office_network.value,
        "anywhere": None if state.anywhere is None else state.anywhere.value,
        "open": None
        if session is None
        else {"id": session.public_id, "clocked_in_at": moment(session.clocked_in_at)},
        "forgotten_day": None if state.forgotten_day is None else state.forgotten_day.isoformat(),
        "today_minutes": state.today_minutes,
        "last_out": moment(state.last_out),
    }


def staff_answer(staff: Staff) -> dict[str, object]:
    """Who: name and job title, and whether they have left."""
    return {
        "id": staff.public_id,
        "name": staff.name,
        "job_title": staff.job_title,
        "has_left": staff.status == StaffStatus.DEACTIVATED,
    }


def figures_answer(summary: MonthSummary) -> dict[str, object]:
    """The figures above a month, and a row of the team view."""
    return {
        "days_worked": summary.days_worked,
        "minutes": summary.minutes,
        "short_days": summary.short_days,
        "missing_clock_outs": summary.missing_clock_outs,
        "corrected": summary.corrected,
    }


def _session_answer(session: SessionTimes, now: datetime) -> dict[str, object]:
    return {
        "id": session.public_id,
        "clocked_in_at": moment(session.clocked_in_at),
        "clocked_out_at": moment(session.clocked_out_at),
        "missing_clock_out": is_missing_clock_out(session, now),
        "added_by_correction": session.added_by_correction,
        "clock_out_by_correction": session.clock_out_by_correction,
        "work_from_home": session.work_from_home,
    }


def day_answer(day: DaySummary, now: datetime) -> dict[str, object]:
    """One day of a month: its time, how it is marked, and its sessions."""
    return {
        "date": day.day.isoformat(),
        "minutes": day.minutes,
        "first_in": moment(day.first_in),
        "last_out": moment(day.last_out),
        "day_off": day.day_off,
        "later": day.later,
        "today": day.today,
        "not_in": day.not_in,
        "missing_clock_out": day.missing_clock_out,
        "still_in": day.still_in,
        "short": day.short,
        "corrected": day.corrected,
        "correctable": day.correctable,
        # The office's holiday on this day, by name (asked 2026-11-16).
        "holiday": day.holiday,
        # Worked on Approved Work From Home dates: shown as "Home" (issue 87).
        "home": any(s.work_from_home for s in day.sessions),
        "sessions": [_session_answer(s, now) for s in day.sessions],
    }


def correction_answer(change: WorkSessionChange) -> dict[str, object]:
    """One correction: which day, before, after, why, who and when."""
    return {
        "id": change.public_id,
        "day": change.day.isoformat(),
        "before": change.before,
        "after": change.after,
        "reason": change.reason,
        "by": change.by_name,
        "at": moment(change.at),
    }


def person_now_answer(person: PersonNow) -> dict[str, object]:
    """One person in "Who's in now"."""
    if person.since is not None:
        state = "in"
    elif person.left_at is not None:
        state = "left"
    else:
        state = "not_in"
    return {
        "id": person.staff.public_id,
        "name": person.staff.name,
        "job_title": person.staff.job_title,
        "state": state,
        "since": moment(person.since),
        "left_at": moment(person.left_at),
    }


def working_day_change_answer(change: WorkingDayChange) -> dict[str, object]:
    """One change of the Working Day."""
    return {
        "id": change.public_id,
        "old_minutes": change.old_minutes,
        "new_minutes": change.new_minutes,
        "by": change.by_name,
        "at": moment(change.at),
    }


def work_from_home_answer(dates: WorkFromHome) -> dict[str, object]:
    """One set of Work From Home dates: when, why, where it is, who asked or set and answered."""
    return {
        "id": dates.public_id,
        "staff_id": dates.staff.public_id,
        "starts_on": dates.starts_on.isoformat(),
        "ends_on": None if dates.ends_on is None else dates.ends_on.isoformat(),
        "reason": dates.reason,
        "status": dates.status,
        "set_directly": dates.set_directly,
        "asked_by": dates.asked_by_name,
        "asked_at": moment(dates.asked_at),
        "answered_by": dates.answered_by_name or None,
        "answered_at": moment(dates.answered_at),
    }


def holiday_answer(holiday: Holiday) -> dict[str, object]:
    """One holiday: its day and name."""
    return {"id": holiday.public_id, "date": holiday.day.isoformat(), "name": holiday.name}
