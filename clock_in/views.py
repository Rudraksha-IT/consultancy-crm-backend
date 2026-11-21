"""REST endpoints for Clock-in (spec 103-111, 131-133; issues 56, 87), all in the Clock-in Module.

Every endpoint answers 404 while Clock-in is off, for every Role, before any other check.
Every Staff member clocks in and out (from an Office Network, unless an Admin or on Approved Work
From Home dates) and sees their own month. Staff ask for Work From Home dates; Admins answer,
set and end them.
Admins, and a Platform Admin in Support Access, see everyone's hours, correct a day and set the
Working Day; a Platform Admin never clocks (they are not Staff).
"""

from __future__ import annotations

from uuid import UUID

from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import (
    ADMIN_NEEDS_NO_WORK_FROM_HOME_MESSAGE,
    ONLY_ADMIN_WORK_FROM_HOME_MESSAGE,
    ONLY_ADMIN_WORKING_HOURS_MESSAGE,
    PLATFORM_CANT_CLOCK_MESSAGE,
    SOMEONE_ELSES_HOURS_MESSAGE,
    clocks_from_any_network,
    why_cannot_ask_work_from_home,
    why_cannot_clock,
    why_cannot_manage_work_from_home,
    why_cannot_manage_working_hours,
)
from clock_in.answers import (
    clock_answer,
    correction_answer,
    day_answer,
    figures_answer,
    holiday_answer,
    moment,
    person_now_answer,
    staff_answer,
    work_from_home_answer,
    working_day_change_answer,
)
from clock_in.days import CorrectionRefusedError
from clock_in.exceptions import ClockRefusedError
from clock_in.models import Holiday, WorkFromHome, WorkSession
from clock_in.serializers import (
    ClockOutCorrectionSerializer,
    HolidaySerializer,
    MissedTimeSerializer,
    MonthQuerySerializer,
    NothingSerializer,
    TeamMonthQuerySerializer,
    WeeklyOffSerializer,
    WorkFromHomeSerializer,
    WorkingDaySerializer,
)
from clock_in.services import (
    HolidayTakenError,
    MissedTime,
    NotOnOfficeNetworkError,
    add_holiday,
    add_missed_time,
    clock_in,
    clock_out,
    clock_state,
    listed_holidays,
    month_corrections,
    month_of,
    office_network_state,
    remove_holiday,
    set_missing_clock_out,
    set_weekly_off,
    set_working_day,
    team_month_rows,
    team_staff,
    weekly_off,
    who_is_in_now,
    working_day_changes,
    working_day_minutes,
)
from clock_in.work_from_home import (
    WorkFromHomeRefusedError,
    answer,
    ask,
    cancel,
    dates_of,
    end,
    set_directly,
)
from modules.permissions import requires_module
from modules.types import Module
from staff.actors import is_platform_visitor
from staff.context import get_request_staff
from staff.models import Staff, StaffStatus
from tenancy.client_address import client_address

ClockInModuleIsOn = requires_module(Module.CLOCK_IN)
NO_STORE = {"Cache-Control": "no-store"}


class CanClock(BasePermission):
    """Every Staff member clocks; a Platform Admin never does (issue 56)."""

    message = PLATFORM_CANT_CLOCK_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this person clock in and out."""
        user = request.user
        return isinstance(user, Staff) and why_cannot_clock(user) is None


class CanManageWorkingHours(BasePermission):
    """Everyone's hours, corrections and the Working Day are for Admins (spec 105a-108)."""

    message = ONLY_ADMIN_WORKING_HOURS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this person see and correct everyone's hours."""
        user = request.user
        return isinstance(user, Staff) and why_cannot_manage_working_hours(user) is None


class CanManageWorkFromHome(BasePermission):
    """Answering, setting and ending Work From Home dates is for Admins (spec 133)."""

    message = ONLY_ADMIN_WORK_FROM_HOME_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this person manage someone's dates."""
        user = request.user
        return isinstance(user, Staff) and why_cannot_manage_work_from_home(user) is None


MINE = (IsAuthenticated, ClockInModuleIsOn, CanClock)
TEAM = (IsAuthenticated, ClockInModuleIsOn, CanManageWorkingHours)
WORK_FROM_HOME_ADMIN = (IsAuthenticated, ClockInModuleIsOn, CanManageWorkFromHome)


def _refused(error: Exception, code: int) -> Response:
    return Response({"detail": str(error)}, status=code)


def _check_nothing_sent(request: Request) -> None:
    NothingSerializer(data=request.data or {}).is_valid(raise_exception=True)


class MyClockView(APIView):
    """The Home clock: open or not, a forgotten clock-out, today so far (GET)."""

    permission_classes = MINE

    def get(self, request: Request) -> Response:
        """Return the clock for the person logged in."""
        state = clock_state(get_request_staff(request), client_address(request))
        return Response(clock_answer(state), headers=NO_STORE)


class ClockInView(APIView):
    """Clock in now, from an Office Network only (POST)."""

    permission_classes = MINE
    parser_classes = (JSONParser,)

    def post(self, request: Request) -> Response:
        """Open a Work Session at the server's time."""
        _check_nothing_sent(request)
        staff, address = get_request_staff(request), client_address(request)
        try:
            marked = clock_in(staff, address)
        except NotOnOfficeNetworkError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error, status.HTTP_403_FORBIDDEN)
        except ClockRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error, status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "clock": clock_answer(clock_state(staff, address)),
                "marked_not_clocked_out": None if marked is None else marked.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class ClockOutView(APIView):
    """Clock out now, from an Office Network only (POST)."""

    permission_classes = MINE
    parser_classes = (JSONParser,)

    def post(self, request: Request) -> Response:
        """Close today's Work Session at the server's time."""
        _check_nothing_sent(request)
        staff, address = get_request_staff(request), client_address(request)
        try:
            minutes = clock_out(staff, address)
        except NotOnOfficeNetworkError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error, status.HTTP_403_FORBIDDEN)
        except ClockRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error, status.HTTP_400_BAD_REQUEST)
        return Response({"clock": clock_answer(clock_state(staff, address)), "minutes": minutes})


class MonthView(APIView):
    """One month, day by day: one's own, or (Admins) anyone's (GET)."""

    permission_classes = (IsAuthenticated, ClockInModuleIsOn)

    def get(self, request: Request) -> Response:
        """Return the month's days, figures and corrections."""
        query = MonthQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        staff = self._whose(get_request_staff(request), query.staff_id())
        year, month = query.year_month()
        summary = month_of(staff, year, month)
        now = timezone.now()
        return Response(
            {
                "month": f"{year:04d}-{month:02d}",
                "today": timezone.localdate(now).isoformat(),
                "working_day_minutes": working_day_minutes(),
                "staff": staff_answer(staff),
                "figures": figures_answer(summary),
                "days": [day_answer(day, now) for day in summary.days],
                "corrections": [
                    correction_answer(c) for c in month_corrections(staff, year, month)
                ],
            },
            headers=NO_STORE,
        )

    @staticmethod
    def _whose(viewer: Staff, staff_id: UUID | None) -> Staff:
        if staff_id is None or staff_id == viewer.public_id:
            if is_platform_visitor(viewer):
                raise PermissionDenied(PLATFORM_CANT_CLOCK_MESSAGE)
            return viewer
        if why_cannot_manage_working_hours(viewer) is not None:
            raise PermissionDenied(SOMEONE_ELSES_HOURS_MESSAGE)
        found = Staff.objects.exclude(status=StaffStatus.INVITED).filter(public_id=staff_id).first()
        if found is None:
            raise NotFound
        return found


class TeamNowView(APIView):
    """Who's in now (GET), for Admins."""

    permission_classes = TEAM

    def get(self, request: Request) -> Response:
        """Return everyone who works here with their state today."""
        people = who_is_in_now()
        return Response(
            {
                "now": moment(timezone.now()),
                "office_network": office_network_state(client_address(request)).value,
                "clocked_in_count": sum(1 for p in people if p.since is not None),
                "people": [person_now_answer(p) for p in people],
            },
            headers=NO_STORE,
        )


class TeamMonthView(APIView):
    """Everyone's month (GET), for Admins."""

    permission_classes = TEAM

    def get(self, request: Request) -> Response:
        """Return each person's figures for the month."""
        query = TeamMonthQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        year, month = query.year_month()
        rows = team_month_rows(team_staff(year, month), year, month)
        return Response(
            {
                "month": f"{year:04d}-{month:02d}",
                "working_day_minutes": working_day_minutes(),
                "rows": [
                    {"staff": staff_answer(person), **figures_answer(summary)}
                    for person, summary in rows
                ],
            },
            headers=NO_STORE,
        )


class WorkingDayView(APIView):
    """The Working Day and its changes (GET), or a new one (PUT), for Admins."""

    permission_classes = TEAM
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return the Working Day in minutes and its latest changes."""
        return Response(self._answer())

    def put(self, request: Request) -> Response:
        """Check and set the Working Day, and record who changed it."""
        body = WorkingDaySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        set_working_day(body.working_day(), by=get_request_staff(request))
        return Response(self._answer())

    @staticmethod
    def _answer() -> dict[str, object]:
        return working_day_answer()


def working_day_answer() -> dict[str, object]:
    """The Working Day, the weekly days off, and the Working Day's latest changes."""
    return {
        "minutes": working_day_minutes(),
        "weekly_off": weekly_off().value,
        "changes": [working_day_change_answer(c) for c in working_day_changes()],
    }


class WeeklyOffView(APIView):
    """Choose the office's weekly days off (PUT), for Admins (asked 2026-11-17)."""

    permission_classes = TEAM
    parser_classes = (JSONParser,)

    def put(self, request: Request) -> Response:
        """Check and store the choice; answer like the Working Day."""
        body = WeeklyOffSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        set_weekly_off(body.choice(), by=get_request_staff(request))
        return Response(working_day_answer())


HOLIDAY_TAKEN_MESSAGE = "There is already a holiday on that day."


class HolidaysView(APIView):
    """The office's holidays (GET), or a new one (POST), for Admins (asked 2026-11-16)."""

    permission_classes = TEAM
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return this year's and later holidays, by day."""
        return Response({"holidays": [holiday_answer(h) for h in listed_holidays()]})

    def post(self, request: Request) -> Response:
        """Check and add one; one holiday a day."""
        body = HolidaySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            holiday = add_holiday(body.day(), body.holiday_name())
        except HolidayTakenError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"date": [HOLIDAY_TAKEN_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(holiday_answer(holiday), status=status.HTTP_201_CREATED)


class HolidayView(APIView):
    """Remove one holiday (DELETE), for Admins."""

    permission_classes = TEAM

    def delete(self, request: Request, holiday_id: int) -> Response:
        """Remove it, or answer 404 if it isn't here."""
        try:
            remove_holiday(holiday_id)
        except Holiday.DoesNotExist as error:
            raise NotFound from error
        return Response(status=status.HTTP_204_NO_CONTENT)


class SetClockOutView(APIView):
    """Set a forgotten clock-out on a past day (POST), for Admins."""

    permission_classes = TEAM
    parser_classes = (JSONParser,)

    def post(self, request: Request, session_id: int) -> Response:
        """Check and set it, and record the correction."""
        body = ClockOutCorrectionSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            change = set_missing_clock_out(
                session_id, body.clock_out(), body.reason_text(), by=get_request_staff(request)
            )
        except WorkSession.DoesNotExist as error:
            raise NotFound from error
        except CorrectionRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error, status.HTTP_400_BAD_REQUEST)
        return Response(correction_answer(change), status=status.HTTP_201_CREATED)


class MissedTimeView(APIView):
    """Add missed time to a past short or empty day (POST), for Admins."""

    permission_classes = TEAM
    parser_classes = (JSONParser,)

    def post(self, request: Request, staff_id: int) -> Response:
        """Check and add it, and record the correction."""
        body = MissedTimeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        start, end = body.span()
        missed = MissedTime(day=body.day(), start=start, end=end, reason=body.reason_text())
        try:
            change = add_missed_time(staff_id, missed, by=get_request_staff(request))
        except Staff.DoesNotExist as error:
            raise NotFound from error
        except CorrectionRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error, status.HTTP_400_BAD_REQUEST)
        return Response(correction_answer(change), status=status.HTTP_201_CREATED)


class MyWorkFromHomeView(APIView):
    """My Work From Home dates (GET), or ask for new ones (POST) (spec 132)."""

    permission_classes = MINE
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return my dates, newest first, and whether I may ask (Admins never need to)."""
        staff = get_request_staff(request)
        if why_cannot_ask_work_from_home(staff) is not None:
            return Response({"can_ask": False, "results": []})
        return Response(
            {"can_ask": True, "results": [work_from_home_answer(d) for d in dates_of(staff.pk)]}
        )

    def post(self, request: Request) -> Response:
        """Ask for dates; every Admin is told."""
        staff = get_request_staff(request)
        refusal = why_cannot_ask_work_from_home(staff)
        if refusal is not None:
            raise PermissionDenied(refusal)
        body = WorkFromHomeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        asked = ask(staff, body.new_dates())
        return Response(work_from_home_answer(asked), status=status.HTTP_201_CREATED)


class CancelWorkFromHomeView(APIView):
    """Take back my own waiting request (POST)."""

    permission_classes = MINE
    parser_classes = (JSONParser,)

    def post(self, request: Request, dates_id: int) -> Response:
        """Cancel it; anyone else's is not found."""
        _check_nothing_sent(request)
        try:
            dates = cancel(dates_id, get_request_staff(request))
        except WorkFromHome.DoesNotExist as error:
            raise NotFound from error
        except WorkFromHomeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error, status.HTTP_409_CONFLICT)
        return Response(work_from_home_answer(dates))


class AnswerWorkFromHomeView(APIView):
    """Approve or decline a waiting request (POST), for Admins."""

    permission_classes = WORK_FROM_HOME_ADMIN
    parser_classes = (JSONParser,)
    approve = True

    def post(self, request: Request, dates_id: int) -> Response:
        """Answer it; the person is told."""
        _check_nothing_sent(request)
        try:
            dates = answer(dates_id, get_request_staff(request), approve=self.approve)
        except WorkFromHome.DoesNotExist as error:
            raise NotFound from error
        except WorkFromHomeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error, status.HTTP_409_CONFLICT)
        return Response(work_from_home_answer(dates))


class ApproveWorkFromHomeView(AnswerWorkFromHomeView):
    """Approve a waiting request (POST), for Admins."""


class DeclineWorkFromHomeView(AnswerWorkFromHomeView):
    """Decline a waiting request (POST), for Admins."""

    approve = False


class EndWorkFromHomeView(APIView):
    """End Approved dates today (POST), for Admins."""

    permission_classes = WORK_FROM_HOME_ADMIN
    parser_classes = (JSONParser,)

    def post(self, request: Request, dates_id: int) -> Response:
        """End them: from tomorrow the person needs the office network again."""
        _check_nothing_sent(request)
        try:
            dates = end(dates_id)
        except WorkFromHome.DoesNotExist as error:
            raise NotFound from error
        except WorkFromHomeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error, status.HTTP_409_CONFLICT)
        return Response(work_from_home_answer(dates))


class PersonWorkFromHomeView(APIView):
    """One person's dates (GET), or set new ones for them directly (POST), for Admins."""

    permission_classes = WORK_FROM_HOME_ADMIN
    parser_classes = (JSONParser,)

    def get(self, request: Request, staff_id: int) -> Response:
        """Return the person's dates, and whether they need any (Admins don't)."""
        person = Staff.objects.exclude(status=StaffStatus.INVITED).filter(pk=staff_id).first()
        if person is None:
            raise NotFound
        return Response(
            {
                "staff": staff_answer(person),
                "needs_dates": not clocks_from_any_network(person),
                "results": [work_from_home_answer(d) for d in dates_of(person.pk)],
            }
        )

    def post(self, request: Request, staff_id: int) -> Response:
        """Set dates, Approved at once; the person is told."""
        person = Staff.objects.filter(pk=staff_id, status=StaffStatus.ACTIVE).first()
        if person is None:
            raise NotFound
        body = WorkFromHomeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        if clocks_from_any_network(person):
            return Response(
                {"detail": ADMIN_NEEDS_NO_WORK_FROM_HOME_MESSAGE},
                status=status.HTTP_400_BAD_REQUEST,
            )
        made = set_directly(person, body.new_dates(), by=get_request_staff(request))
        return Response(work_from_home_answer(made), status=status.HTTP_201_CREATED)
