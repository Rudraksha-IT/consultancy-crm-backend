"""Clocking in and out, months and the team view, the Working Day, and corrections (issue 56).

Call inside the Consultancy's schema. Who may do it is the views' job, through `access.policy`.
Every time comes from the server clock (`timezone.now`), never from the browser. The client
address only decides whether clocking is allowed; it is never stored or logged (ADR 0004).
Admins, and Staff on Approved Work From Home dates, clock from any network (issue 87): that is
decided by the Role and the dates, never by the address.
"""

from __future__ import annotations

import calendar
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from access.policy import clocks_from_any_network
from clock_in.days import (
    MonthSummary,
    SessionTimes,
    WorkingWeek,
    check_added_time,
    check_clock_out,
    check_not_a_holiday,
    day_bounds,
    duration_text,
    local_day,
    month_days,
    session_minutes,
    summarise_day,
    summarise_days,
)
from clock_in.exceptions import ClockRefusedError
from clock_in.models import (
    DEFAULT_WORKING_DAY_MINUTES,
    WORKING_DAY_ROW,
    CorrectionKind,
    Holiday,
    WeeklyOff,
    WorkingDay,
    WorkingDayChange,
    WorkSession,
    WorkSessionChange,
)
from clock_in.work_from_home import approved_on
from office_networks.services import is_on_an_office_network, office_networks
from staff.actors import actor_name, is_platform_visitor, stored_staff
from staff.models import Staff, StaffStatus
from tenancy.client_address import ClientAddress

NO_NETWORK_MESSAGE = "Ask your Admin to add the office network."
OFF_NETWORK_MESSAGES = {
    "in": "You can only clock in from the office network.",
    "out": "You can only clock out from the office network.",
}
ALREADY_IN_MESSAGE = "You're already clocked in."
NOT_IN_MESSAGE = "You're not clocked in today."
NOON = 12
# Working Day changes shown on the Configuration page, newest first.
WORKING_DAY_CHANGES_SHOWN = 10

logger = logging.getLogger(__name__)


class NotOnOfficeNetworkError(Exception):
    """Clocking was refused because the call didn't come from an Office Network."""


class Anywhere(StrEnum):
    """Why someone may clock from any network: their Role, or their Work From Home dates."""

    ADMIN = "admin"
    WORK_FROM_HOME = "work_from_home"


class OfficeNetworkState(StrEnum):
    """Where the caller is, for clocking: on an Office Network, off it, or none is set up."""

    ON = "on"
    OFF = "off"
    NONE = "none"


@dataclass(frozen=True)
class ClockState:
    """What the clock in the Home heading shows for one Staff member."""

    now: datetime
    office_network: OfficeNetworkState
    anywhere: Anywhere | None
    open_session: WorkSession | None
    forgotten_day: date | None
    today_minutes: int
    last_out: datetime | None


@dataclass(frozen=True)
class MissedTime:
    """Missed time an Admin adds to a day: from and to on that day, and why."""

    day: date
    start: time
    end: time
    reason: str


@dataclass(frozen=True)
class _Correction:
    kind: CorrectionKind
    before: str
    after: str
    reason: str


@dataclass(frozen=True)
class PersonNow:
    """One Staff member in "Who's in now"."""

    staff: Staff
    since: datetime | None
    left_at: datetime | None


def times_of(session: WorkSession) -> SessionTimes:
    """The times the rules in `clock_in.days` need."""
    return SessionTimes(
        id=session.pk,
        public_id=session.public_id,
        clocked_in_at=session.clocked_in_at,
        clocked_out_at=session.clocked_out_at,
        not_clocked_out=session.not_clocked_out,
        corrected=session.corrected,
        added_by_correction=session.added_by_correction,
        clock_out_by_correction=session.clock_out_by_correction,
        work_from_home=session.work_from_home,
    )


def clock_time(moment: datetime) -> str:
    """A moment as a clock reads in the office, e.g. "9:02 AM"."""
    local = timezone.localtime(moment)
    half = "AM" if local.hour < NOON else "PM"
    return f"{local.hour % NOON or NOON}:{local.minute:02d} {half}"


def on_day(day: date, at: time) -> datetime:
    """`at` on `day`, in the Consultancy's time zone."""
    return datetime.combine(day, at, tzinfo=timezone.get_current_timezone())


# --- The Working Day ---------------------------------------------------------------------------


def working_day_minutes() -> int:
    """How long a full day is, in minutes (8 hours unless an Admin changed it)."""
    stored = WorkingDay.objects.filter(pk=WORKING_DAY_ROW).values_list("minutes", flat=True)
    return stored.first() or DEFAULT_WORKING_DAY_MINUTES


_DAYS_OFF: dict[str, frozenset[int]] = {
    WeeklyOff.SATURDAY: frozenset({calendar.SATURDAY}),
    WeeklyOff.SUNDAY: frozenset({calendar.SUNDAY}),
    WeeklyOff.BOTH: frozenset({calendar.SATURDAY, calendar.SUNDAY}),
    WeeklyOff.NONE: frozenset(),
}


def weekly_off() -> WeeklyOff:
    """The office's weekly days off, as chosen (Saturday unless an Admin changed it)."""
    stored = WorkingDay.objects.filter(pk=WORKING_DAY_ROW).values_list("weekly_off", flat=True)
    return WeeklyOff(stored.first() or WeeklyOff.SATURDAY)


def working_week() -> WorkingWeek:
    """The Working Day and the weekly days off, as the day rules need them."""
    return WorkingWeek(working_day_minutes(), _DAYS_OFF[weekly_off()])


def set_weekly_off(choice: WeeklyOff, by: Staff) -> None:
    """Choose the weekly days off (already checked); they decide every month from now on."""
    with transaction.atomic():
        row, _ = WorkingDay.objects.select_for_update().get_or_create(pk=WORKING_DAY_ROW)
        row.weekly_off = choice
        row.save(update_fields=["weekly_off"])
    logger.info("Weekly days off changed: weekly_off=%s, staff_id=%s", choice.value, by.pk)


def working_day_changes() -> QuerySet[WorkingDayChange]:
    """The latest changes of the Working Day, newest first."""
    return WorkingDayChange.objects.order_by("-at", "-pk")[:WORKING_DAY_CHANGES_SHOWN]


def set_working_day(minutes: int, by: Staff) -> None:
    """Set the Working Day (already checked) and record the change, if it is one."""
    with transaction.atomic():
        row, _ = WorkingDay.objects.select_for_update().get_or_create(pk=WORKING_DAY_ROW)
        old = row.minutes
        if old == minutes:
            return
        row.minutes = minutes
        row.save(update_fields=["minutes"])
        WorkingDayChange.objects.create(
            old_minutes=old,
            new_minutes=minutes,
            by_staff=stored_staff(by),
            by_name=actor_name(by),
            by_platform_admin=is_platform_visitor(by),
        )
    logger.info("Working day changed: minutes=%s", minutes)


# --- Holidays (asked 2026-11-16) ----------------------------------------------------------------


class HolidayTakenError(Exception):
    """There is already a holiday on that day."""


def holidays_between(first: date, last: date) -> dict[date, str]:
    """The office's holidays from `first` to `last` (both included), by day."""
    rows = Holiday.objects.filter(day__gte=first, day__lte=last).values_list("day", "name")
    return dict(rows)


def listed_holidays() -> QuerySet[Holiday]:
    """The holidays shown in Configuration: this year's and later ones, by day."""
    first = date(timezone.localdate().year, 1, 1)
    return Holiday.objects.filter(day__gte=first).order_by("day")


def add_holiday(day: date, name: str) -> Holiday:
    """Add a holiday (already checked).

    Raises:
        HolidayTakenError: There is already one on that day.
    """
    try:
        with transaction.atomic():
            holiday = Holiday.objects.create(day=day, name=name)
    except IntegrityError as error:
        raise HolidayTakenError from error
    logger.info("Holiday added: holiday_id=%s", holiday.pk)
    return holiday


def remove_holiday(holiday_id: int) -> None:
    """Remove a holiday.

    Raises:
        Holiday.DoesNotExist: There is no such holiday here.
    """
    deleted, _ = Holiday.objects.filter(pk=holiday_id).delete()
    if not deleted:
        raise Holiday.DoesNotExist
    logger.info("Holiday removed: holiday_id=%s", holiday_id)


# --- Clocking in and out -----------------------------------------------------------------------


def office_network_state(address: ClientAddress | None) -> OfficeNetworkState:
    """Whether `address` (from `tenancy.client_address`) may clock, without saying it."""
    if not office_networks().exists():
        return OfficeNetworkState.NONE
    if is_on_an_office_network(address):
        return OfficeNetworkState.ON
    return OfficeNetworkState.OFF


def anywhere_today(staff: Staff) -> Anywhere | None:
    """Whether `staff` may clock from any network today, and why; None when they may not."""
    if clocks_from_any_network(staff):
        return Anywhere.ADMIN
    if approved_on(staff.pk, timezone.localdate()):
        return Anywhere.WORK_FROM_HOME
    return None


def clock_in(staff: Staff, address: ClientAddress | None) -> date | None:
    """Open a Work Session for `staff` now; return the day of one marked "not clocked out".

    Raises:
        NotOnOfficeNetworkError: Not on an Office Network, or none is set up.
        ClockRefusedError: Already clocked in today.
    """
    anywhere = anywhere_today(staff)
    _check_network(address, "in", anywhere)
    with transaction.atomic():
        _lock(staff.pk)
        now = timezone.now()
        open_one = _open_session(staff.pk)
        marked: date | None = None
        if open_one is not None:
            if local_day(open_one.clocked_in_at) == local_day(now):
                raise ClockRefusedError(ALREADY_IN_MESSAGE)
            open_one.not_clocked_out = True
            open_one.save(update_fields=["not_clocked_out"])
            marked = local_day(open_one.clocked_in_at)
        try:
            with transaction.atomic():
                session = WorkSession.objects.create(
                    staff=staff,
                    clocked_in_at=now,
                    work_from_home=anywhere is Anywhere.WORK_FROM_HOME,
                )
        except IntegrityError as error:
            raise ClockRefusedError(ALREADY_IN_MESSAGE) from error
    logger.info("Clocked in: work_session_id=%s", session.pk)
    return marked


def clock_out(staff: Staff, address: ClientAddress | None) -> int:
    """Close today's open Work Session now; return how many minutes it lasted.

    Raises:
        NotOnOfficeNetworkError: Not on an Office Network, or none is set up.
        ClockRefusedError: Not clocked in today.
    """
    _check_network(address, "out", anywhere_today(staff))
    with transaction.atomic():
        _lock(staff.pk)
        now = timezone.now()
        session = _open_session(staff.pk)
        if session is None or local_day(session.clocked_in_at) != local_day(now):
            raise ClockRefusedError(NOT_IN_MESSAGE)
        session.clocked_out_at = now
        session.save(update_fields=["clocked_out_at"])
    logger.info("Clocked out: work_session_id=%s", session.pk)
    return session_minutes(times_of(session), now)


def clock_state(staff: Staff, address: ClientAddress | None) -> ClockState:
    """What the Home clock shows: open or not, a forgotten clock-out, today's time so far."""
    now = timezone.now()
    today = local_day(now)
    open_one = _open_session(staff.pk)
    is_today = open_one is not None and local_day(open_one.clocked_in_at) == today
    start, end = day_bounds(today)
    todays = [times_of(s) for s in _sessions_of([staff.pk], start, end)]
    outs = [s.clocked_out_at for s in todays if s.clocked_out_at is not None]
    return ClockState(
        now=now,
        office_network=office_network_state(address),
        anywhere=anywhere_today(staff),
        open_session=open_one if is_today else None,
        forgotten_day=None if open_one is None or is_today else local_day(open_one.clocked_in_at),
        today_minutes=sum(session_minutes(s, now) for s in todays),
        last_out=max(outs) if outs else None,
    )


# --- Months and the team -----------------------------------------------------------------------


def month_of(staff: Staff, year: int, month: int) -> MonthSummary:
    """One Staff member's month, day by day."""
    [(_, summary)] = list(team_month_rows([staff], year, month))
    return summary


def month_corrections(staff: Staff, year: int, month: int) -> QuerySet[WorkSessionChange]:
    """The corrections of one Staff member's month, by day."""
    days = month_days(year, month)
    return WorkSessionChange.objects.filter(
        staff_id=staff.pk, day__gte=days[0], day__lte=days[-1]
    ).order_by("day", "at", "pk")


def team_staff(year: int, month: int) -> QuerySet[Staff]:
    """Who the month's team view lists: everyone who works here, and anyone who worked then."""
    start, end = _month_bounds(year, month)
    worked = WorkSession.objects.filter(clocked_in_at__gte=start, clocked_in_at__lt=end)
    return Staff.objects.filter(
        Q(status=StaffStatus.ACTIVE)
        | Q(status=StaffStatus.DEACTIVATED, pk__in=worked.values("staff_id"))
    ).order_by("name", "pk")


def team_month_rows(
    people: Iterable[Staff], year: int, month: int
) -> Iterator[tuple[Staff, MonthSummary]]:
    """Each person's month, reading every session of the month in one query."""
    people = list(people)
    start, end = _month_bounds(year, month)
    by_staff: dict[int, list[SessionTimes]] = {}
    for session in _sessions_of([p.pk for p in people], start, end):
        by_staff.setdefault(session.staff_id, []).append(times_of(session))
    week, now = working_week(), timezone.now()
    days = month_days(year, month)
    off = holidays_between(days[0], days[-1])
    for person in people:
        yield (
            person,
            summarise_days(
                days,
                by_staff.get(person.pk, ()),
                week=week,
                now=now,
                holidays=off,
            ),
        )


def who_is_in_now() -> list[PersonNow]:
    """Everyone who works here, with when they clocked in today or when they left."""
    now = timezone.now()
    start, end = day_bounds(local_day(now))
    people = list(Staff.objects.filter(status=StaffStatus.ACTIVE).order_by("name", "pk"))
    todays: dict[int, list[WorkSession]] = {}
    for session in _sessions_of([p.pk for p in people], start, end):
        todays.setdefault(session.staff_id, []).append(session)
    answer = []
    for person in people:
        sessions = todays.get(person.pk, [])
        open_now = [s for s in sessions if s.clocked_out_at is None and not s.not_clocked_out]
        outs = [s.clocked_out_at for s in sessions if s.clocked_out_at is not None]
        answer.append(
            PersonNow(
                staff=person,
                since=open_now[0].clocked_in_at if open_now else None,
                left_at=None if open_now or not outs else max(outs),
            )
        )
    return answer


# --- Corrections (Admins) ----------------------------------------------------------------------


def set_missing_clock_out(
    session_id: int, clocked_out_at: time, reason: str, by: Staff
) -> WorkSessionChange:
    """Set a forgotten clock-out on a past day, and record the correction (spec 108).

    Raises:
        WorkSession.DoesNotExist: There is no such Work Session here.
        CorrectionRefusedError: The rules don't allow it; the message says why.
    """
    staff_id = WorkSession.objects.values_list("staff_id", flat=True).get(pk=session_id)
    with transaction.atomic():
        _lock(staff_id)
        session = WorkSession.objects.get(pk=session_id)
        day = local_day(session.clocked_in_at)
        out = on_day(day, clocked_out_at)
        check_not_a_holiday(day, holidays_between(day, day))
        check_clock_out(
            times_of(session),
            out,
            _nearby(staff_id, day),
            week=working_week(),
            now=timezone.now(),
        )
        session.clocked_out_at = out
        session.clock_out_by_correction = True
        session.save(update_fields=["clocked_out_at", "clock_out_by_correction"])
        change = _record(
            session,
            _Correction(
                CorrectionKind.CLOCK_OUT_SET,
                before=f"In {clock_time(session.clocked_in_at)}, no clock-out",
                after=f"Out {clock_time(out)}",
                reason=reason,
            ),
            by=by,
        )
    logger.info("Clock-out corrected: work_session_id=%s, change_id=%s", session.pk, change.pk)
    return change


def add_missed_time(staff_id: int, missed: MissedTime, by: Staff) -> WorkSessionChange:
    """Add missed time to a past short or empty day, and record the correction (spec 108).

    Raises:
        Staff.DoesNotExist: There is no such Staff member here (an invited one has no days).
        CorrectionRefusedError: The rules don't allow it; the message says why.
    """
    with transaction.atomic():
        staff = _lock(staff_id)
        day = missed.day
        nearby = _nearby(staff_id, day)
        start_at, end_at = on_day(day, missed.start), on_day(day, missed.end)
        week, now = working_week(), timezone.now()
        check_not_a_holiday(day, holidays_between(day, day))
        check_added_time(
            day,
            (start_at, end_at),
            nearby,
            week=week,
            now=now,
        )
        before = summarise_day(
            day,
            [s for s in nearby if local_day(s.clocked_in_at) == day],
            week=week,
            now=now,
        )
        session = WorkSession.objects.create(
            staff=staff, clocked_in_at=start_at, clocked_out_at=end_at, added_by_correction=True
        )
        change = _record(
            session,
            _Correction(
                CorrectionKind.TIME_ADDED,
                before=f"Worked {duration_text(before.minutes)}"
                if before.sessions
                else "No time recorded",
                after=f"{clock_time(start_at)} to {clock_time(end_at)} added",
                reason=missed.reason,
            ),
            by=by,
        )
    logger.info("Missed time added: work_session_id=%s, change_id=%s", session.pk, change.pk)
    return change


# --- Helpers -----------------------------------------------------------------------------------


def _check_network(
    address: ClientAddress | None, direction: str, anywhere: Anywhere | None
) -> None:
    if anywhere is not None:
        logger.info("clock-%s from any network: %s", direction, anywhere.value)
        return
    state = office_network_state(address)
    if state is OfficeNetworkState.NONE:
        logger.info("clock-%s refused: no office network set up", direction)
        raise NotOnOfficeNetworkError(NO_NETWORK_MESSAGE)
    if state is OfficeNetworkState.OFF:
        # Only that it didn't match: the address itself is never logged (ADR 0004).
        logger.warning("clock-%s refused: not on an office network", direction)
        raise NotOnOfficeNetworkError(OFF_NETWORK_MESSAGES[direction])


def _lock(staff_id: int) -> Staff:
    """Lock one Staff member's row, so their clocking and corrections happen one at a time.

    Raises:
        Staff.DoesNotExist: No such Staff member, or only invited.
    """
    return Staff.objects.select_for_update().exclude(status=StaffStatus.INVITED).get(pk=staff_id)


def _open_session(staff_id: int) -> WorkSession | None:
    return WorkSession.objects.filter(
        staff_id=staff_id, clocked_out_at__isnull=True, not_clocked_out=False
    ).first()


def _sessions_of(staff_ids: Iterable[int], start: datetime, end: datetime) -> QuerySet[WorkSession]:
    return WorkSession.objects.filter(
        staff_id__in=list(staff_ids), clocked_in_at__gte=start, clocked_in_at__lt=end
    ).order_by("clocked_in_at", "pk")


def _nearby(staff_id: int, day: date) -> list[SessionTimes]:
    """The Staff member's sessions that could touch `day`: from the day before to its end."""
    start, end = day_bounds(day)
    return [times_of(s) for s in _sessions_of([staff_id], start - timedelta(days=1), end)]


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    days = month_days(year, month)
    return day_bounds(days[0])[0], day_bounds(days[-1])[1]


def _record(session: WorkSession, correction: _Correction, by: Staff) -> WorkSessionChange:
    return WorkSessionChange.objects.create(
        work_session=session,
        staff_id=session.staff_id,
        day=local_day(session.clocked_in_at),
        kind=correction.kind,
        before=correction.before,
        after=correction.after,
        reason=correction.reason,
        by_staff=stored_staff(by),
        by_name=actor_name(by),
        by_platform_admin=is_platform_visitor(by),
    )
