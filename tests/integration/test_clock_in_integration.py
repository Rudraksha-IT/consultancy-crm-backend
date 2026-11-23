"""Clock in and out from the Office Network, months, the team view, the Working Day, corrections.

Spec 103-111 (with 105a), issue 56, ADR 0004. Times come from the server clock only; the
client address only decides whether clocking is allowed and is never stored or logged.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction
from django_tenants.utils import tenant_context
from freezegun import freeze_time
from rest_framework import status

from clock_in.exceptions import ClockInRecordIsAppendOnlyError
from clock_in.models import Holiday, WorkingDayChange, WorkSession, WorkSessionChange
from modules.services import switch_modules
from modules.types import Module
from office_networks.models import OfficeNetwork
from staff.models import Role, Staff, StaffStatus
from tests.conftest import (
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_datetime,
)
from tests.integration.test_consultancy_status_integration import set_status
from tests.integration.test_stage_and_counsellor_integration import INJECTION_STRINGS
from tests.support_access import open_as_platform_admin

# A record UUID that no office has (ADR 0027).
SOME_UUID = "00000000-0000-4000-8000-000000000001"

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

KTM = ZoneInfo("Asia/Kathmandu")
# "Now" in these tests: Thursday 5 November 2026, 11:00 in Kathmandu.
NOW = datetime(2026, 11, 5, 11, 0, tzinfo=KTM)
TODAY = date(2026, 11, 5)
WEDNESDAY = date(2026, 11, 4)
TUESDAY = date(2026, 11, 3)
SATURDAY = date(2026, 10, 31)
OFFICE_ADDRESS = "110.44.115.82"
HOME_ADDRESS = "27.34.20.9"

CLOCK_URL = "/api/clock/"
IN_URL = "/api/clock/in/"
OUT_URL = "/api/clock/out/"
MONTH_URL = "/api/clock/month/"
TEAM_NOW_URL = "/api/clock/team/now/"
TEAM_URL = "/api/clock/team/"
WORKING_DAY_URL = "/api/clock/working-day/"
DAYS_OFF_URL = "/api/clock/days-off/"
DAYS_OFF_MESSAGE = "Choose Saturday, Sunday, Saturday and Sunday, or none."
SUNDAY = date(2026, 11, 1)
HOLIDAYS_URL = "/api/clock/holidays/"
EXPORTS_URL = "/api/exports/"

NO_NETWORK = {"detail": "Ask your Admin to add the office network."}
# Spec 27: the office's address may have changed; the same words for clocking in and out.
OFF_NETWORK_MESSAGE = (
    "This network isn't saved as an office network. At the office? The office's internet "
    "address may have changed. Ask an Admin to open the CRM here; it takes one tap."
)
OFF_NETWORK_IN = OFF_NETWORK_OUT = {"detail": OFF_NETWORK_MESSAGE}
ALREADY_IN = {"detail": "You're already clocked in."}
NOT_IN = {"detail": "You're not clocked in today."}
PLATFORM_CANT_CLOCK = {"detail": "A Platform Admin can't clock in or out."}
ONLY_ADMIN = {"detail": "Only an Admin can see and correct everyone's working hours."}
SOMEONE_ELSES = {"detail": "Only an Admin can see someone else's working hours."}
WORKING_DAY_MESSAGE = "Enter between 4 and 12 hours, in half hours."
REASON_MESSAGE = "Say why you're correcting it."
TIME_MESSAGE = "Enter a time such as 17:30."
MONTH_MESSAGE = "Choose a month such as 2026-10."


@pytest.fixture(autouse=True)
def frozen_clock() -> Iterator[None]:
    """Every test here runs at `NOW`."""
    with freeze_time(NOW):
        yield


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office every test works in: Clock-in on, its wifi listed, calls from the office."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.CLOCK_IN})
    add_network(office, "110.44.115.0/24")
    office.client.defaults["HTTP_X_CLIENT_IP"] = OFFICE_ADDRESS
    return office


def add_network(office: ConsultancyContext, network: str) -> None:
    """Store an Office Network directly."""
    with tenant_context(office.consultancy):
        OfficeNetwork.objects.create(label="Main office wifi", network=network)


def at(day: date, hours: int, minutes: int = 0) -> datetime:
    """A moment on `day` in Kathmandu."""
    return datetime.combine(day, time(hours, minutes), tzinfo=KTM)


def iso(moment: datetime) -> str | None:
    """A moment as the API writes it."""
    return api_datetime(moment.astimezone(UTC))


def worked(
    office: ConsultancyContext,
    staff: Staff,
    start: datetime,
    end: datetime | None = None,
    **fields: Any,  # noqa: ANN401  # any WorkSession field
) -> WorkSession:
    """Store a Work Session directly."""
    with tenant_context(office.consultancy):
        return WorkSession.objects.create(
            staff=staff, clocked_in_at=start, clocked_out_at=end, **fields
        )


def sessions(office: ConsultancyContext) -> list[tuple[int, datetime, datetime | None, bool]]:
    """Every Work Session as stored: (staff, in, out, not clocked out), oldest first."""
    with tenant_context(office.consultancy):
        rows = WorkSession.objects.order_by("pk")
        return list(
            rows.values_list("staff_id", "clocked_in_at", "clocked_out_at", "not_clocked_out")
        )


def corrections(office: ConsultancyContext) -> list[WorkSessionChange]:
    """Every Work Session Change, oldest first."""
    with tenant_context(office.consultancy):
        return list(WorkSessionChange.objects.order_by("pk"))


def holiday_url(holiday_id: object) -> str:
    """One holiday."""
    return f"/api/clock/holidays/{holiday_id}/"


def holidays(office: ConsultancyContext) -> list[tuple[date, str]]:
    """Every holiday as stored: (day, name), by day."""
    with tenant_context(office.consultancy):
        return list(Holiday.objects.order_by("day").values_list("day", "name"))


def add_holiday(office: ConsultancyContext, day: date, name: str) -> Holiday:
    """Store a holiday directly."""
    with tenant_context(office.consultancy):
        return Holiday.objects.create(day=day, name=name)


def clock_out_url(session_id: object) -> str:
    """Set a missing clock-out on one Work Session."""
    return f"/api/clock/sessions/{session_id}/clock-out/"


def missed_time_url(staff_id: object) -> str:
    """Add missed time to one Staff member's day."""
    return f"/api/clock/staff/{staff_id}/missed-time/"


def day_of(answer: dict[str, Any], day: date) -> dict[str, Any]:
    """One day of a month answer."""
    found: dict[str, Any] = next(d for d in answer["days"] if d["date"] == day.isoformat())
    return found


class TestClockingIn:
    def test_on_the_office_network_it_records_the_server_time(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.post(IN_URL, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert sessions(abc) == [(me.pk, NOW, None, False)]
        body = response.json()
        assert body["marked_not_clocked_out"] is None
        assert body["clock"]["open"]["clocked_in_at"] == iso(NOW)
        assert body["clock"]["office_network"] == "on"

    def test_an_ipv6_office_network(self, abc: ConsultancyContext) -> None:
        add_network(abc, "2400:1a00:b030::/48")
        abc.log_in_as(Role.FRONT_DESK)
        abc.client.defaults["HTTP_X_CLIENT_IP"] = "2400:1a00:b030:9::1"

        assert abc.client.post(IN_URL, format="json").status_code == status.HTTP_201_CREATED

    def test_an_ipv6_office_group_added_from_the_suggestion_covers_every_device_in_it(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        abc.client.defaults["HTTP_X_CLIENT_IP"] = "2400:1a00:b030:9:a1b2:c3d4:e5f6:1"
        suggested = abc.client.get("/api/office-networks/current/").json()["suggested_network"]
        added = abc.client.post(
            "/api/office-networks/", {"label": "Office", "network": suggested}, format="json"
        )
        abc.log_in_as(Role.COUNSELLOR)

        tried = {}
        for device in ("2400:1a00:b030:9::1", "2400:1a00:b030:9:ffff:ffff:ffff:fffe"):
            abc.client.defaults["HTTP_X_CLIENT_IP"] = device
            tried[device] = abc.client.post(IN_URL, format="json").status_code
            abc.client.post(OUT_URL, format="json")
        abc.client.defaults["HTTP_X_CLIENT_IP"] = "2400:1a00:b030:a::1"
        other_group = abc.client.post(IN_URL, format="json")

        assert added.status_code == status.HTTP_201_CREATED
        assert added.json()["network"] == "2400:1a00:b030:9::/64"
        assert set(tried.values()) == {status.HTTP_201_CREATED}
        assert other_group.status_code == status.HTTP_403_FORBIDDEN
        assert other_group.json() == OFF_NETWORK_IN

    def test_an_ipv4_suggestion_stays_the_one_address(self, abc: ConsultancyContext) -> None:
        with tenant_context(abc.consultancy):
            OfficeNetwork.objects.all().delete()
        abc.log_in_as(Role.ADMIN)
        abc.client.defaults["HTTP_X_CLIENT_IP"] = "27.34.20.9"
        suggested = abc.client.get("/api/office-networks/current/").json()["suggested_network"]
        abc.client.post(
            "/api/office-networks/", {"label": "Office", "network": suggested}, format="json"
        )
        abc.log_in_as(Role.COUNSELLOR)
        abc.client.defaults["HTTP_X_CLIENT_IP"] = "27.34.20.10"

        refused = abc.client.post(IN_URL, format="json")
        abc.client.defaults["HTTP_X_CLIENT_IP"] = "27.34.20.9"
        allowed = abc.client.post(IN_URL, format="json")

        assert suggested == "27.34.20.9"
        assert refused.json() == OFF_NETWORK_IN
        assert allowed.status_code == status.HTTP_201_CREATED

    def test_a_single_ipv6_address_saved_before_stays_only_that_address(
        self, abc: ConsultancyContext
    ) -> None:
        add_network(abc, "2400:1a00:b030:9::1/128")
        abc.log_in_as(Role.COUNSELLOR)
        abc.client.defaults["HTTP_X_CLIENT_IP"] = "2400:1a00:b030:9::2"

        refused = abc.client.post(IN_URL, format="json")
        abc.client.defaults["HTTP_X_CLIENT_IP"] = "2400:1a00:b030:9::1"
        allowed = abc.client.post(IN_URL, format="json")

        assert refused.status_code == status.HTTP_403_FORBIDDEN
        assert allowed.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("address", [HOME_ADDRESS, None, "not-an-address", "10.0.0.5"])
    def test_anywhere_else_is_refused_and_nothing_recorded(
        self, abc: ConsultancyContext, address: str | None
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        if address is None:
            del abc.client.defaults["HTTP_X_CLIENT_IP"]
        else:
            abc.client.defaults["HTTP_X_CLIENT_IP"] = address

        response = abc.client.post(IN_URL, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == OFF_NETWORK_IN
        assert sessions(abc) == []

    def test_the_address_only_counts_with_the_internal_token(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        abc.client.defaults["HTTP_X_INTERNAL_TOKEN"] = "wrong"  # noqa: S105  # test-only

        response = abc.client.post(IN_URL, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert sessions(abc) == []

    def test_with_no_office_network_staff_are_told_to_ask_their_admin(
        self, abc: ConsultancyContext
    ) -> None:
        with tenant_context(abc.consultancy):
            OfficeNetwork.objects.all().delete()
        abc.log_in_as(Role.TEACHER)

        response = abc.client.post(IN_URL, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NO_NETWORK
        assert sessions(abc) == []

    def test_clocking_in_twice_today_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        worked(abc, me, at(TODAY, 9))

        response = abc.client.post(IN_URL, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ALREADY_IN
        assert len(sessions(abc)) == 1

    def test_a_forgotten_session_is_marked_not_clocked_out_and_a_new_one_starts(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        worked(abc, me, at(WEDNESDAY, 9, 2))

        response = abc.client.post(IN_URL, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["marked_not_clocked_out"] == WEDNESDAY.isoformat()
        assert sessions(abc) == [
            (me.pk, at(WEDNESDAY, 9, 2), None, True),
            (me.pk, NOW, None, False),
        ]

    def test_times_from_the_browser_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.post(IN_URL, {"clocked_in_at": "2026-11-05T03:00:00Z"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert sessions(abc) == []

    def test_the_database_allows_one_open_session_per_person(self, abc: ConsultancyContext) -> None:
        me = abc.add_staff(Role.COUNSELLOR)
        worked(abc, me, at(TODAY, 9))

        with pytest.raises(IntegrityError), transaction.atomic():
            worked(abc, me, at(TODAY, 10))

    def test_the_address_is_never_logged_or_stored(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        abc.client.post(IN_URL, format="json")
        abc.client.get(CLOCK_URL)
        abc.client.post(OUT_URL, format="json")
        abc.client.defaults["HTTP_X_CLIENT_IP"] = HOME_ADDRESS
        abc.client.post(IN_URL, format="json")

        assert OFFICE_ADDRESS not in json_logs.text()
        assert HOME_ADDRESS not in json_logs.text()
        assert OFFICE_ADDRESS not in str(sessions(abc))
        assert {field.name for field in WorkSession._meta.get_fields()} == {
            "id",
            "public_id",
            "staff",
            "clocked_in_at",
            "clocked_out_at",
            "not_clocked_out",
            "added_by_correction",
            "clock_out_by_correction",
            "work_from_home",
        }


class TestClockingOut:
    def test_clocking_out_closes_todays_session_at_the_server_time(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        worked(abc, me, at(TODAY, 9, 2))

        response = abc.client.post(OUT_URL, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert sessions(abc) == [(me.pk, at(TODAY, 9, 2), NOW, False)]
        assert response.json()["minutes"] == 118
        assert response.json()["clock"]["open"] is None
        assert response.json()["clock"]["last_out"] == iso(NOW)

    def test_with_nothing_open_it_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.post(OUT_URL, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NOT_IN

    def test_a_session_left_open_on_an_earlier_day_is_not_closed(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        worked(abc, me, at(WEDNESDAY, 9))

        response = abc.client.post(OUT_URL, format="json")

        assert response.json() == NOT_IN
        assert sessions(abc) == [(me.pk, at(WEDNESDAY, 9), None, False)]

    def test_off_the_office_network_it_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        worked(abc, me, at(TODAY, 9))
        abc.client.defaults["HTTP_X_CLIENT_IP"] = HOME_ADDRESS

        response = abc.client.post(OUT_URL, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == OFF_NETWORK_OUT
        assert sessions(abc) == [(me.pk, at(TODAY, 9), None, False)]


class TestMyClock:
    def test_not_clocked_in(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.get(CLOCK_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "now": iso(NOW),
            "office_network": "on",
            "anywhere": None,
            "open": None,
            "forgotten_day": None,
            "today_minutes": 0,
            "last_out": None,
        }
        assert response["Cache-Control"] == "no-store"

    def test_clocked_in_with_what_was_worked_earlier_today(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        worked(abc, me, at(TODAY, 7), at(TODAY, 8))
        open_one = worked(abc, me, at(TODAY, 9, 30))

        body = abc.client.get(CLOCK_URL).json()

        assert body["open"] == {
            "id": str(open_one.public_id),
            "clocked_in_at": iso(at(TODAY, 9, 30)),
        }
        assert body["today_minutes"] == 60 + 90
        assert body["last_out"] == iso(at(TODAY, 8))

    def test_a_forgotten_clock_out_is_shown(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        worked(abc, me, at(WEDNESDAY, 9))

        body = abc.client.get(CLOCK_URL).json()

        assert body["open"] is None
        assert body["forgotten_day"] == WEDNESDAY.isoformat()

    @pytest.mark.parametrize(
        ("address", "networks", "state"),
        [
            (OFFICE_ADDRESS, True, "on"),
            (HOME_ADDRESS, True, "off"),
            (OFFICE_ADDRESS, False, "none"),
        ],
    )
    def test_the_office_network_state(
        self, abc: ConsultancyContext, address: str, *, networks: bool, state: str
    ) -> None:
        if not networks:
            with tenant_context(abc.consultancy):
                OfficeNetwork.objects.all().delete()
        abc.log_in_as(Role.COUNSELLOR)
        abc.client.defaults["HTTP_X_CLIENT_IP"] = address

        assert abc.client.get(CLOCK_URL).json()["office_network"] == state


class TestMyMonth:
    def test_this_month_by_default_with_its_figures(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        worked(abc, me, at(date(2026, 11, 2), 9), at(date(2026, 11, 2), 17, 30))
        worked(abc, me, at(TUESDAY, 9), at(TUESDAY, 13))
        worked(abc, me, at(WEDNESDAY, 9), not_clocked_out=True)
        worked(abc, me, at(TODAY, 9))

        response = abc.client.get(MONTH_URL)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["month"] == "2026-11"
        assert body["today"] == TODAY.isoformat()
        assert body["working_day_minutes"] == 480
        assert body["staff"] == {
            "id": str(me.public_id),
            "name": me.name,
            "job_title": "",
            "has_left": False,
        }
        assert len(body["days"]) == 30
        assert body["figures"] == {
            "days_worked": 4,
            "minutes": 8 * 60 + 30 + 4 * 60 + 120,
            "short_days": 1,
            "missing_clock_outs": 1,
            "corrected": 0,
        }
        tuesday = day_of(body, TUESDAY)
        assert tuesday == {
            "date": "2026-11-03",
            "minutes": 240,
            "first_in": iso(at(TUESDAY, 9)),
            "last_out": iso(at(TUESDAY, 13)),
            "day_off": False,
            "later": False,
            "today": False,
            "not_in": False,
            "missing_clock_out": False,
            "still_in": False,
            "short": True,
            "corrected": False,
            "correctable": True,
            "holiday": None,
            "home": False,
            "sessions": [
                {
                    "id": tuesday["sessions"][0]["id"],
                    "clocked_in_at": iso(at(TUESDAY, 9)),
                    "clocked_out_at": iso(at(TUESDAY, 13)),
                    "missing_clock_out": False,
                    "added_by_correction": False,
                    "clock_out_by_correction": False,
                    "work_from_home": False,
                }
            ],
        }
        assert day_of(body, WEDNESDAY)["missing_clock_out"] is True
        assert day_of(body, TODAY)["still_in"] is True
        assert day_of(body, date(2026, 11, 7))["day_off"] is True
        assert day_of(body, date(2026, 11, 1))["not_in"] is True
        assert day_of(body, date(2026, 11, 6))["later"] is True
        assert body["corrections"] == []

    def test_days_split_at_midnight_nepal_time(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        # 18:20 UTC on the 3rd is 00:05 on the 4th in Kathmandu.
        start = datetime(2026, 11, 3, 18, 20, tzinfo=UTC)
        worked(abc, me, start, datetime(2026, 11, 3, 19, 20, tzinfo=UTC))

        body = abc.client.get(MONTH_URL).json()

        assert day_of(body, TUESDAY)["minutes"] == 0
        assert day_of(body, WEDNESDAY)["minutes"] == 60

    def test_another_month(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        worked(abc, me, at(SATURDAY, 10), at(SATURDAY, 12))

        body = abc.client.get(MONTH_URL, {"month": "2026-10"}).json()

        assert body["month"] == "2026-10"
        assert len(body["days"]) == 31
        assert day_of(body, SATURDAY)["day_off"] is True
        assert day_of(body, SATURDAY)["minutes"] == 120
        assert day_of(body, SATURDAY)["correctable"] is False

    @pytest.mark.parametrize(
        "month", ["2026-13", "2026-1", "26-10", "1999-12", "2101-01", "<script>", "2026-10-01"]
    )
    def test_a_bad_month_is_refused(self, abc: ConsultancyContext, month: str) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.get(MONTH_URL, {"month": month})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"month": [MONTH_MESSAGE]}

    def test_unknown_query_fields_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        assert abc.client.get(MONTH_URL, {"colour": "red"}).status_code == 400

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_an_admin_sees_someone_elses_month(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        other = abc.add_staff(Role.ADMIN, email="boss@abc.test")
        abc.log_in_as(role)

        response = abc.client.get(MONTH_URL, {"staff": str(other.public_id)})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == SOMEONE_ELSES

    def test_an_admin_sees_anyones_month_including_someone_who_left(
        self, abc: ConsultancyContext
    ) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="left@abc.test")
        worked(abc, other, at(TUESDAY, 9), at(TUESDAY, 17))
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=other.pk).update(status=StaffStatus.DEACTIVATED)
        abc.log_in_as(Role.ADMIN)

        body = abc.client.get(MONTH_URL, {"staff": str(other.public_id)}).json()

        assert body["staff"]["has_left"] is True
        assert body["figures"]["days_worked"] == 1

    @pytest.mark.parametrize("staff", ["999999", "abc", "0", "-1"])
    def test_someone_who_isnt_here_is_not_found_or_refused(
        self, abc: ConsultancyContext, staff: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(MONTH_URL, {"staff": staff})

        assert response.status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND}

    def test_an_invited_person_has_no_month(self, abc: ConsultancyContext) -> None:
        invited = abc.add_staff(Role.COUNSELLOR, email="new@abc.test")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=invited.pk).update(status=StaffStatus.INVITED)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(MONTH_URL, {"staff": str(invited.public_id)})

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestWhoIsInNow:
    def test_who_is_in_who_left_and_who_isnt_in_today(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        hari = abc.add_staff(Role.FRONT_DESK, email="hari@abc.test")
        gone = abc.add_staff(Role.TEACHER, email="gone@abc.test")
        worked(abc, sita, at(TODAY, 9, 2))
        worked(abc, hari, at(TODAY, 7), at(TODAY, 10))
        worked(abc, admin, at(WEDNESDAY, 9))
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=gone.pk).update(status=StaffStatus.DEACTIVATED)

        response = abc.client.get(TEAM_NOW_URL)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["clocked_in_count"] == 1
        assert body["office_network"] == "on"
        people = {p["id"]: p for p in body["people"]}
        assert set(people) == {str(admin.public_id), str(sita.public_id), str(hari.public_id)}
        assert people[str(sita.public_id)]["state"] == "in"
        assert people[str(sita.public_id)]["since"] == iso(at(TODAY, 9, 2))
        assert people[str(hari.public_id)]["state"] == "left"
        assert people[str(hari.public_id)]["left_at"] == iso(at(TODAY, 10))
        assert people[str(admin.public_id)]["state"] == "not_in"


class TestTeamMonth:
    def test_everyone_who_works_here_or_worked_that_month(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        left_worked = abc.add_staff(Role.FRONT_DESK, email="left1@abc.test")
        left_idle = abc.add_staff(Role.FRONT_DESK, email="left2@abc.test")
        invited = abc.add_staff(Role.TEACHER, email="invited@abc.test")
        worked(abc, sita, at(TUESDAY, 9), at(TUESDAY, 13))
        worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)
        worked(abc, left_worked, at(date(2026, 11, 2), 9), at(date(2026, 11, 2), 17))
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk__in=[left_worked.pk, left_idle.pk]).update(
                status=StaffStatus.DEACTIVATED
            )
            Staff.objects.filter(pk=invited.pk).update(status=StaffStatus.INVITED)

        response = abc.client.get(TEAM_URL, {"month": "2026-11"})

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["month"] == "2026-11"
        assert body["working_day_minutes"] == 480
        rows = {row["staff"]["id"]: row for row in body["rows"]}
        assert set(rows) == {str(s.public_id) for s in (admin, sita, left_worked)}
        assert rows[str(sita.public_id)] == {
            "staff": {
                "id": str(sita.public_id),
                "name": sita.name,
                "job_title": "",
                "has_left": False,
            },
            "days_worked": 2,
            "minutes": 240,
            "short_days": 1,
            "missing_clock_outs": 1,
            "corrected": 0,
        }
        assert rows[str(left_worked.public_id)]["staff"]["has_left"] is True

    def test_a_bad_month_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(TEAM_URL, {"month": "October"})

        assert response.json() == {"month": [MONTH_MESSAGE]}


class TestWorkingDay:
    def test_eight_hours_until_an_admin_changes_it(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(WORKING_DAY_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"minutes": 480, "weekly_off": "saturday", "changes": []}

    def test_a_change_is_recorded_with_old_and_new(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(WORKING_DAY_URL, {"minutes": 450}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "minutes": 450,
            "weekly_off": "saturday",
            "changes": [
                {
                    "id": response.json()["changes"][0]["id"],
                    "old_minutes": 480,
                    "new_minutes": 450,
                    "by": "Test Admin",
                    "at": iso(NOW),
                }
            ],
        }

    def test_the_new_working_day_decides_short_days(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)
        worked(abc, me, at(TUESDAY, 9), at(TUESDAY, 16, 30))
        assert day_of(abc.client.get(MONTH_URL).json(), TUESDAY)["short"] is True

        abc.client.put(WORKING_DAY_URL, {"minutes": 450}, format="json")

        assert day_of(abc.client.get(MONTH_URL).json(), TUESDAY)["short"] is False

    @pytest.mark.parametrize(
        "minutes", [210, 750, 455, 0, -480, "480", 480.0, 480.5, True, None, "8 h", 10**9]
    )
    def test_refused_values(self, abc: ConsultancyContext, minutes: object) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(WORKING_DAY_URL, {"minutes": minutes}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"minutes": [WORKING_DAY_MESSAGE]}
        assert abc.client.get(WORKING_DAY_URL).json()["minutes"] == 480

    @pytest.mark.parametrize("minutes", [240, 270, 720])
    def test_four_to_twelve_hours_in_half_hours(
        self, abc: ConsultancyContext, minutes: int
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(WORKING_DAY_URL, {"minutes": minutes}, format="json")

        assert response.json()["minutes"] == minutes

    def test_missing_and_unknown_fields_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        missing = abc.client.put(WORKING_DAY_URL, {}, format="json")
        unknown = abc.client.put(WORKING_DAY_URL, {"minutes": 450, "x": 1}, format="json")

        assert missing.status_code == unknown.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(abc.consultancy):
            assert not WorkingDayChange.objects.exists()

    def test_changes_are_append_only(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        abc.client.put(WORKING_DAY_URL, {"minutes": 450}, format="json")

        with tenant_context(abc.consultancy):
            change = WorkingDayChange.objects.get()
            with pytest.raises(ClockInRecordIsAppendOnlyError):
                change.save()
            with pytest.raises(ClockInRecordIsAppendOnlyError):
                change.delete()
            with pytest.raises(ClockInRecordIsAppendOnlyError):
                WorkingDayChange.objects.update(new_minutes=600)


class TestWeeklyDaysOff:
    """Working hours → Weekly days off (asked 2026-11-17): Saturday unless an Admin chooses."""

    def test_saturday_until_an_admin_changes_it(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        body = abc.client.get(MONTH_URL, {"month": "2026-10"}).json()

        assert abc.client.get(WORKING_DAY_URL).json()["weekly_off"] == "saturday"
        assert day_of(body, SATURDAY)["day_off"] is True
        assert day_of(body, date(2026, 10, 25))["day_off"] is False

    @pytest.mark.parametrize("choice", ["saturday", "sunday", "both", "none"])
    def test_an_admin_chooses_any_of_the_four(self, abc: ConsultancyContext, choice: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(DAYS_OFF_URL, {"weekly_off": choice}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"minutes": 480, "weekly_off": choice, "changes": []}
        assert abc.client.get(WORKING_DAY_URL).json()["weekly_off"] == choice

    def test_sunday_off_makes_saturday_a_working_day_everywhere(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR)

        abc.client.put(DAYS_OFF_URL, {"weekly_off": "sunday"}, format="json")

        mine = abc.client.get(MONTH_URL).json()
        assert day_of(mine, SUNDAY)["day_off"] is True
        assert day_of(mine, SUNDAY)["not_in"] is False
        october = abc.client.get(MONTH_URL, {"month": "2026-10"}).json()
        assert day_of(october, SATURDAY)["day_off"] is False
        assert day_of(october, SATURDAY)["not_in"] is True
        assert day_of(october, SATURDAY)["correctable"] is True
        team = abc.client.get(TEAM_URL, {"month": "2026-10"})
        assert team.status_code == status.HTTP_200_OK
        added = abc.client.post(
            missed_time_url(sita.public_id),
            {"day": "2026-10-31", "from": "09:00", "to": "12:00", "reason": "Office open."},
            format="json",
        )
        assert added.status_code == status.HTTP_201_CREATED, added.json()
        refused = abc.client.post(
            missed_time_url(sita.public_id),
            {"day": "2026-11-01", "from": "09:00", "to": "12:00", "reason": "x"},
            format="json",
        )
        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert "Sunday is off, so it can't be corrected." in str(refused.json())
        assert admin.pk != sita.pk

    def test_both_days_off(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        abc.client.put(DAYS_OFF_URL, {"weekly_off": "both"}, format="json")

        october = abc.client.get(MONTH_URL, {"month": "2026-10"}).json()
        assert day_of(october, SATURDAY)["day_off"] is True
        assert day_of(abc.client.get(MONTH_URL).json(), SUNDAY)["day_off"] is True

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_can_change_it(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = abc.client.put(DAYS_OFF_URL, {"weekly_off": "none"}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN

    def test_requires_a_logged_in_staff_member(self, abc: ConsultancyContext) -> None:
        response = abc.client.put(DAYS_OFF_URL, {"weekly_off": "none"}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize(
        "value",
        [
            "Saturday",
            "monday",
            "",
            None,
            True,
            6,
            ["sunday"],
            {"day": "sunday"},
            pytest.param("s" * 5000, id="over-long"),
            "<script>alert(1)</script>",
            "sunday'; DROP TABLE clock_in_workingday; --",
        ],
    )
    def test_refuses_anything_but_the_four(self, abc: ConsultancyContext, value: object) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(DAYS_OFF_URL, {"weekly_off": value}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"weekly_off": [DAYS_OFF_MESSAGE]}
        assert abc.client.get(WORKING_DAY_URL).json()["weekly_off"] == "saturday"

    def test_missing_unknown_and_form_bodies_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        missing = abc.client.put(DAYS_OFF_URL, {}, format="json")
        unknown = abc.client.put(
            DAYS_OFF_URL, {"weekly_off": "none", "minutes": 300}, format="json"
        )
        form = abc.client.put(DAYS_OFF_URL, {"weekly_off": "none"})

        assert missing.status_code == unknown.status_code == status.HTTP_400_BAD_REQUEST
        assert form.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        answer = abc.client.get(WORKING_DAY_URL).json()
        assert answer["weekly_off"] == "saturday"
        assert answer["minutes"] == 480

    def test_only_put(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        for send in (abc.client.get, abc.client.post, abc.client.patch, abc.client.delete):
            assert send(DAYS_OFF_URL).status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logged_without_anything_typed(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)

        abc.client.put(DAYS_OFF_URL, {"weekly_off": "sunday"}, format="json")

        messages = [entry["message"] for entry in json_logs.entries()]
        assert f"Weekly days off changed: weekly_off=sunday, staff_id={admin.pk}" in messages


HOLIDAY_DAY_MESSAGE = "Choose a day such as 2026-11-04."
HOLIDAY_NAME_MESSAGE = "Give the holiday a name."


class TestHolidays:
    """Holidays set in Configuration (asked 2026-11-16): like Saturday, off and never short."""

    def test_none_until_an_admin_adds_one(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(HOLIDAYS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"holidays": []}

    def test_an_admin_adds_one_and_it_is_listed_by_day(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        add_holiday(abc, date(2026, 12, 25), "Christmas")

        response = abc.client.post(
            HOLIDAYS_URL, {"date": "2026-10-21", "name": "  Dashain  "}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        added = response.json()
        assert added == {"id": added["id"], "date": "2026-10-21", "name": "Dashain"}
        listed = abc.client.get(HOLIDAYS_URL).json()["holidays"]
        assert [(h["date"], h["name"]) for h in listed] == [
            ("2026-10-21", "Dashain"),
            ("2026-12-25", "Christmas"),
        ]

    def test_last_years_holidays_are_not_listed_but_still_count(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.ADMIN)
        add_holiday(abc, date(2025, 10, 2), "Dashain")
        worked(abc, me, at(date(2025, 10, 2), 10), at(date(2025, 10, 2), 11))

        listed = abc.client.get(HOLIDAYS_URL).json()["holidays"]
        month = abc.client.get(MONTH_URL, {"month": "2025-10"}).json()

        assert listed == []
        assert day_of(month, date(2025, 10, 2))["holiday"] == "Dashain"

    def test_a_holiday_is_off_never_short_and_never_not_in(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)
        add_holiday(abc, TUESDAY, "Tihar")
        add_holiday(abc, WEDNESDAY, "Bhai Tika")
        worked(abc, me, at(TUESDAY, 10), at(TUESDAY, 12))

        month = abc.client.get(MONTH_URL).json()

        tuesday, wednesday = day_of(month, TUESDAY), day_of(month, WEDNESDAY)
        assert tuesday["holiday"] == "Tihar"
        assert tuesday["minutes"] == 120
        assert tuesday["short"] is tuesday["correctable"] is False
        assert wednesday["holiday"] == "Bhai Tika"
        assert wednesday["not_in"] is wednesday["correctable"] is False
        assert day_of(month, date(2026, 11, 2))["holiday"] is None
        assert month["figures"]["short_days"] == 0

    def test_the_team_month_counts_holidays_as_off(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)
        add_holiday(abc, TUESDAY, "Tihar")
        worked(abc, me, at(TUESDAY, 10), at(TUESDAY, 12))

        rows = abc.client.get(TEAM_URL).json()["rows"]

        assert [row["short_days"] for row in rows] == [0]

    def test_a_holiday_cant_be_corrected(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        add_holiday(abc, TUESDAY, "Tihar")
        forgotten = worked(abc, sita, at(TUESDAY, 9), not_clocked_out=True)

        set_out = abc.client.post(
            clock_out_url(forgotten.public_id),
            {"clocked_out_at": "12:00", "reason": "x"},
            format="json",
        )
        added = abc.client.post(
            missed_time_url(sita.public_id),
            {"day": "2026-11-03", "from": "13:00", "to": "14:00", "reason": "x"},
            format="json",
        )

        refused = {"detail": "Tihar is a holiday, so it can't be corrected."}
        assert set_out.json() == added.json() == refused
        assert corrections(abc) == []

    def test_anyone_can_still_clock_in_on_a_holiday(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        add_holiday(abc, TODAY, "Tihar")

        assert abc.client.post(IN_URL, format="json").status_code == status.HTTP_201_CREATED

    def test_an_admin_removes_one(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        dashain = add_holiday(abc, date(2026, 10, 21), "Dashain")

        response = abc.client.delete(holiday_url(dashain.public_id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert holidays(abc) == []

    def test_removing_one_that_isnt_there_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.delete(holiday_url(999)).status_code == status.HTTP_404_NOT_FOUND

    def test_one_holiday_a_day(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        add_holiday(abc, date(2026, 10, 21), "Dashain")

        response = abc.client.post(
            HOLIDAYS_URL, {"date": "2026-10-21", "name": "Ghatasthapana"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"date": ["There is already a holiday on that day."]}
        assert holidays(abc) == [(date(2026, 10, 21), "Dashain")]

    @pytest.mark.parametrize(
        ("body", "errors"),
        [
            ({"date": "21/10/2026", "name": "Dashain"}, {"date": [HOLIDAY_DAY_MESSAGE]}),
            ({"date": "2026-02-30", "name": "Dashain"}, {"date": [HOLIDAY_DAY_MESSAGE]}),
            ({"date": 20261021, "name": "Dashain"}, {"date": [HOLIDAY_DAY_MESSAGE]}),
            ({"date": "1999-10-21", "name": "Dashain"}, {"date": [HOLIDAY_DAY_MESSAGE]}),
            ({"name": "Dashain"}, {"date": [HOLIDAY_DAY_MESSAGE]}),
            ({"date": "2026-10-21"}, {"name": [HOLIDAY_NAME_MESSAGE]}),
            ({"date": "2026-10-21", "name": "   "}, {"name": [HOLIDAY_NAME_MESSAGE]}),
            ({"date": "2026-10-21", "name": None}, {"name": [HOLIDAY_NAME_MESSAGE]}),
            ({"date": "2026-10-21", "name": 7}, {"name": [HOLIDAY_NAME_MESSAGE]}),
            ({"date": "2026-10-21", "name": "D" * 61}, {"name": ["Use at most 60 characters."]}),
            (
                {"date": "2026-10-21", "name": "Dashain\nTika"},
                {"name": ["Use letters, numbers and punctuation only."]},
            ),
            (
                {"date": "2026-10-21", "name": "Dashain\x00"},
                {"name": ["Use letters, numbers and punctuation only."]},
            ),
        ],
    )
    def test_bad_input_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, body: dict[str, object], errors: dict[str, list[str]]
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(HOLIDAYS_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == errors
        assert holidays(abc) == []

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            HOLIDAYS_URL, {"date": "2026-10-21", "name": "Dashain", "paid": True}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert holidays(abc) == []

    @pytest.mark.parametrize("name", INJECTION_STRINGS)
    def test_injection_shaped_names_are_kept_as_plain_text(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            HOLIDAYS_URL, {"date": "2026-10-21", "name": name[:60]}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert holidays(abc) == [(date(2026, 10, 21), name[:60].strip())]

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(HOLIDAYS_URL, {"date": "2026-10-21", "name": "Dashain"})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_adding_and_removing_are_logged_without_the_name(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        added = abc.client.post(
            HOLIDAYS_URL, {"date": "2026-10-21", "name": "Dashain"}, format="json"
        ).json()
        with tenant_context(abc.consultancy):
            number = Holiday.objects.get(public_id=added["id"]).pk  # logs keep the number
        abc.client.delete(holiday_url(added["id"]))

        messages = [entry["message"] for entry in json_logs.entries()]
        assert f"Holiday added: holiday_id={number}" in messages
        assert f"Holiday removed: holiday_id={number}" in messages
        assert not any("Dashain" in message for message in messages)


class TestSettingAMissingClockOut:
    def test_an_admin_sets_it_with_a_reason_and_it_is_recorded(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(WEDNESDAY, 9, 2), not_clocked_out=True)

        response = abc.client.post(
            clock_out_url(forgotten.public_id),
            {"clocked_out_at": "17:00", "reason": "Forgot to clock out. Left at 5 PM."},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert sessions(abc) == [(sita.pk, at(WEDNESDAY, 9, 2), at(WEDNESDAY, 17), True)]
        [change] = corrections(abc)
        assert (change.staff_id, change.day, change.kind) == (sita.pk, WEDNESDAY, "clock_out_set")
        assert (change.before, change.after) == ("In 9:02 AM, no clock-out", "Out 5:00 PM")
        assert change.reason == "Forgot to clock out. Left at 5 PM."
        assert (change.by_staff_id, change.by_name, change.at) == (admin.pk, "Test Admin", NOW)
        assert response.json() == {
            "id": str(change.public_id),
            "day": "2026-11-04",
            "before": "In 9:02 AM, no clock-out",
            "after": "Out 5:00 PM",
            "reason": "Forgot to clock out. Left at 5 PM.",
            "by": "Test Admin",
            "at": iso(NOW),
        }

    def test_the_staff_member_sees_the_correction_on_their_month(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)
        abc.log_in_as(Role.ADMIN)
        abc.client.post(
            clock_out_url(forgotten.public_id),
            {"clocked_out_at": "16:00", "reason": "Left at four."},
            format="json",
        )
        sitas = abc.new_client()
        sitas.credentials(HTTP_AUTHORIZATION=f"Bearer {abc.tokens_for(sita).access}")

        body = sitas.get(MONTH_URL).json()

        wednesday = day_of(body, WEDNESDAY)
        assert wednesday["corrected"] is True
        assert wednesday["missing_clock_out"] is False
        assert wednesday["minutes"] == 7 * 60
        assert wednesday["sessions"][0]["clock_out_by_correction"] is True
        assert [(c["day"], c["after"], c["reason"]) for c in body["corrections"]] == [
            ("2026-11-04", "Out 4:00 PM", "Left at four.")
        ]
        assert body["figures"]["corrected"] == 1

    def test_a_platform_admin_corrects_under_their_own_name(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)
        visitor = open_as_platform_admin(platform_site, abc).client

        response = visitor.post(
            clock_out_url(forgotten.public_id),
            {"clocked_out_at": "17:00", "reason": "Asked by the office."},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        [change] = corrections(abc)
        assert (change.by_staff_id, change.by_name, change.by_platform_admin) == (
            None,
            "Platform Ops (Platform Admin)",
            True,
        )

    def test_a_clock_out_the_clock_recorded_never_changes(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        done = worked(abc, sita, at(WEDNESDAY, 9), at(WEDNESDAY, 12))

        response = abc.client.post(
            clock_out_url(done.public_id), {"clocked_out_at": "13:00", "reason": "x"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "That clock-out was recorded and can't be changed."}
        assert sessions(abc)[0][2] == at(WEDNESDAY, 12)
        assert corrections(abc) == []

    def test_a_corrected_clock_out_never_changes_either(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)
        body = {"clocked_out_at": "12:00", "reason": "Left at noon."}
        abc.client.post(clock_out_url(forgotten.public_id), body, format="json")

        again = abc.client.post(
            clock_out_url(forgotten.public_id), {**body, "clocked_out_at": "13:00"}, format="json"
        )

        assert again.status_code == status.HTTP_400_BAD_REQUEST
        assert len(corrections(abc)) == 1

    def test_todays_open_session_cant_be_corrected(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        open_now = worked(abc, sita, at(TODAY, 9))

        response = abc.client.post(
            clock_out_url(open_now.public_id),
            {"clocked_out_at": "10:00", "reason": "x"},
            format="json",
        )

        assert response.json() == {"detail": "Only a past day can be corrected."}

    def test_saturday_cant_be_corrected(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(SATURDAY, 9), not_clocked_out=True)

        response = abc.client.post(
            clock_out_url(forgotten.public_id),
            {"clocked_out_at": "12:00", "reason": "x"},
            format="json",
        )

        assert response.json() == {"detail": "Saturday is off, so it can't be corrected."}

    def test_ending_before_the_start_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)

        response = abc.client.post(
            clock_out_url(forgotten.public_id),
            {"clocked_out_at": "08:00", "reason": "x"},
            format="json",
        )

        assert response.json() == {"detail": "The end must be after the start."}

    def test_overlapping_another_session_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)
        worked(abc, sita, at(WEDNESDAY, 13), at(WEDNESDAY, 15))

        response = abc.client.post(
            clock_out_url(forgotten.public_id),
            {"clocked_out_at": "14:00", "reason": "x"},
            format="json",
        )

        assert response.json() == {"detail": "That overlaps time already recorded on this day."}

    def test_past_the_working_day_is_refused_saying_by_how_much(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)

        response = abc.client.post(
            clock_out_url(forgotten.public_id),
            {"clocked_out_at": "17:40", "reason": "x"},
            format="json",
        )

        assert response.json() == {
            "detail": "That makes the day 8 h 40 min, 40 min longer than the 8 h working day. "
            "Only missing time can be added."
        }
        assert corrections(abc) == []

    def test_a_session_that_isnt_here_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            clock_out_url(999999), {"clocked_out_at": "17:00", "reason": "x"}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        ("body", "field", "message"),
        [
            ({"clocked_out_at": "17:00"}, "reason", REASON_MESSAGE),
            ({"clocked_out_at": "17:00", "reason": ""}, "reason", REASON_MESSAGE),
            ({"clocked_out_at": "17:00", "reason": "   "}, "reason", REASON_MESSAGE),
            ({"clocked_out_at": "17:00", "reason": 5}, "reason", REASON_MESSAGE),
            (
                {"clocked_out_at": "17:00", "reason": "x" * 301},
                "reason",
                "Use at most 300 characters.",
            ),
            ({"reason": "x"}, "clocked_out_at", TIME_MESSAGE),
            ({"clocked_out_at": "25:00", "reason": "x"}, "clocked_out_at", TIME_MESSAGE),
            ({"clocked_out_at": "5:30 PM", "reason": "x"}, "clocked_out_at", TIME_MESSAGE),
            ({"clocked_out_at": "17:30:00", "reason": "x"}, "clocked_out_at", TIME_MESSAGE),
            ({"clocked_out_at": 1730, "reason": "x"}, "clocked_out_at", TIME_MESSAGE),
            ({"clocked_out_at": None, "reason": "x"}, "clocked_out_at", TIME_MESSAGE),
            ({"clocked_out_at": "<b>17</b>", "reason": "x"}, "clocked_out_at", TIME_MESSAGE),
        ],
    )
    def test_bad_input_is_refused(
        self, abc: ConsultancyContext, body: dict[str, object], field: str, message: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)

        response = abc.client.post(clock_out_url(forgotten.public_id), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()[field] == [message]
        assert corrections(abc) == []

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)

        response = abc.client.post(
            clock_out_url(forgotten.public_id),
            {"clocked_out_at": "17:00", "reason": "x", "clocked_in_at": "07:00"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert sessions(abc)[0][1:3] == (at(WEDNESDAY, 9), None)

    @pytest.mark.parametrize("reason", INJECTION_STRINGS)
    def test_injection_shaped_reasons_are_kept_as_plain_text(
        self, abc: ConsultancyContext, reason: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)

        response = abc.client.post(
            clock_out_url(forgotten.public_id),
            {"clocked_out_at": "17:00", "reason": reason},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["reason"] == reason

    def test_changes_are_append_only(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        forgotten = worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)
        abc.client.post(
            clock_out_url(forgotten.public_id),
            {"clocked_out_at": "17:00", "reason": "x"},
            format="json",
        )

        with tenant_context(abc.consultancy):
            change = WorkSessionChange.objects.get()
            with pytest.raises(ClockInRecordIsAppendOnlyError):
                change.save()
            with pytest.raises(ClockInRecordIsAppendOnlyError):
                change.delete()
            with pytest.raises(ClockInRecordIsAppendOnlyError):
                WorkSessionChange.objects.all().delete()


class TestAddingMissedTime:
    def test_on_an_empty_working_day(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")

        response = abc.client.post(
            missed_time_url(sita.public_id),
            {"day": "2026-11-04", "from": "09:00", "to": "17:00", "reason": "Phone was flat."},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert sessions(abc) == [(sita.pk, at(WEDNESDAY, 9), at(WEDNESDAY, 17), False)]
        [change] = corrections(abc)
        assert (change.kind, change.before, change.after) == (
            "time_added",
            "No time recorded",
            "9:00 AM to 5:00 PM added",
        )
        with tenant_context(abc.consultancy):
            assert WorkSession.objects.get().added_by_correction is True

    def test_on_a_short_day(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        worked(abc, sita, at(WEDNESDAY, 9), at(WEDNESDAY, 13))

        response = abc.client.post(
            missed_time_url(sita.public_id),
            {"day": "2026-11-04", "from": "14:00", "to": "18:00", "reason": "Client visit."},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert corrections(abc)[0].before == "Worked 4 h"

    @pytest.mark.parametrize(
        ("day", "start", "end", "message"),
        [
            ("2026-11-05", "07:00", "08:00", "Only a past day can be corrected."),
            ("2026-11-06", "09:00", "10:00", "Only a past day can be corrected."),
            ("2026-10-31", "09:00", "10:00", "Saturday is off, so it can't be corrected."),
            (
                "2026-11-03",
                "18:00",
                "18:30",
                "This day is already a full working day, so it can't be changed.",
            ),
            ("2026-11-02", "13:00", "12:00", "The end must be after the start."),
            ("2026-11-02", "11:00", "14:00", "That overlaps time already recorded on this day."),
            (
                "2026-11-02",
                "13:00",
                "18:15",
                "That makes the day 8 h 15 min, 15 min longer than the 8 h working day. "
                "Only missing time can be added.",
            ),
        ],
    )
    def test_refused(
        self, abc: ConsultancyContext, day: str, start: str, end: str, message: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        worked(abc, sita, at(TUESDAY, 9), at(TUESDAY, 17, 30))
        worked(abc, sita, at(date(2026, 11, 2), 9), at(date(2026, 11, 2), 12))
        before = sessions(abc)

        response = abc.client.post(
            missed_time_url(sita.public_id),
            {"day": day, "from": start, "to": end, "reason": "x"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": message}
        assert sessions(abc) == before
        assert corrections(abc) == []

    def test_a_day_missing_a_clock_out_needs_that_set_first(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")
        worked(abc, sita, at(WEDNESDAY, 9), not_clocked_out=True)

        response = abc.client.post(
            missed_time_url(sita.public_id),
            {"day": "2026-11-04", "from": "07:00", "to": "08:00", "reason": "x"},
            format="json",
        )

        assert response.json() == {"detail": "Set the missing clock-out first."}

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"from": "09:00", "to": "10:00", "reason": "x"}, "day"),
            ({"day": "04/11/2026", "from": "09:00", "to": "10:00", "reason": "x"}, "day"),
            ({"day": 20261104, "from": "09:00", "to": "10:00", "reason": "x"}, "day"),
            ({"day": "2026-11-04", "to": "10:00", "reason": "x"}, "from"),
            ({"day": "2026-11-04", "from": "9", "to": "10:00", "reason": "x"}, "from"),
            ({"day": "2026-11-04", "from": "09:00", "to": "24:00", "reason": "x"}, "to"),
            ({"day": "2026-11-04", "from": "09:00", "to": "10:00"}, "reason"),
        ],
    )
    def test_bad_input_is_refused(
        self, abc: ConsultancyContext, body: dict[str, object], field: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abc.test")

        response = abc.client.post(missed_time_url(sita.public_id), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        assert sessions(abc) == []

    @pytest.mark.parametrize("staff_id", [999999, 0])
    def test_someone_who_isnt_here_is_not_found(
        self, abc: ConsultancyContext, staff_id: int
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            missed_time_url(staff_id),
            {"day": "2026-11-04", "from": "09:00", "to": "10:00", "reason": "x"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestPlatformAdmin:
    def test_can_view_but_never_clock(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visitor = open_as_platform_admin(platform_site, abc).client
        visitor.defaults["HTTP_X_CLIENT_IP"] = OFFICE_ADDRESS

        clock_in = visitor.post(IN_URL, format="json")
        clock_out = visitor.post(OUT_URL, format="json")
        mine = visitor.get(CLOCK_URL)

        assert clock_in.status_code == clock_out.status_code == mine.status_code == 403
        assert clock_in.json() == clock_out.json() == mine.json() == PLATFORM_CANT_CLOCK
        assert sessions(abc) == []
        assert visitor.get(TEAM_NOW_URL).status_code == status.HTTP_200_OK
        assert visitor.get(TEAM_URL).status_code == status.HTTP_200_OK
        assert visitor.get(WORKING_DAY_URL).status_code == status.HTTP_200_OK

    def test_working_day_change_is_under_their_own_name(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visitor = open_as_platform_admin(platform_site, abc).client

        visitor.put(WORKING_DAY_URL, {"minutes": 420}, format="json")

        with tenant_context(abc.consultancy):
            change = WorkingDayChange.objects.get()
        assert (change.by_staff_id, change.by_name, change.by_platform_admin) == (
            None,
            "Platform Ops (Platform Admin)",
            True,
        )


class TestExport:
    def test_the_team_month_exports_as_csv_with_an_export_record(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        worked(abc, admin, at(TUESDAY, 9), at(TUESDAY, 13), clock_out_by_correction=True)
        worked(abc, admin, at(WEDNESDAY, 9), not_clocked_out=True)

        response = abc.client.post(
            EXPORTS_URL, {"list": "clock_in", "filters": {"month": "2026-11"}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Disposition"] == 'attachment; filename="clock_in-2026-11-05.csv"'
        lines = response.content.decode("utf-8-sig").splitlines()
        assert lines == [
            "Employee,Job title,Days worked,Hours,Short days,No clock-out,Corrected",
            "Test Admin,,2,4.0,1,1,1",
        ]
        record = abc.client.get(EXPORTS_URL).json()["results"][0]
        assert (record["list"], record["filters"], record["row_count"]) == (
            "clock_in",
            "Month: November 2026",
            1,
        )

    @pytest.mark.parametrize("filters", [{"month": "2026-13"}, {"month": 5}, {"staff": "1"}])
    def test_bad_filters_are_refused(
        self, abc: ConsultancyContext, filters: dict[str, object]
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            EXPORTS_URL, {"list": "clock_in", "filters": filters}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_with_clock_in_off_it_isnt_there(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        switch_modules(abc.consultancy, set())

        response = abc.client.post(EXPORTS_URL, {"list": "clock_in", "filters": {}}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND


MY_ENDPOINTS = [("get", CLOCK_URL), ("post", IN_URL), ("post", OUT_URL), ("get", MONTH_URL)]
ADMIN_ENDPOINTS = [
    ("get", TEAM_NOW_URL),
    ("get", TEAM_URL),
    ("get", WORKING_DAY_URL),
    ("put", WORKING_DAY_URL),
    ("get", HOLIDAYS_URL),
    ("post", HOLIDAYS_URL),
    ("delete", holiday_url(SOME_UUID)),
    ("post", clock_out_url(SOME_UUID)),
    ("post", missed_time_url(SOME_UUID)),
]


def call(client: APIClient, method: str, url: str) -> _MonkeyPatchedResponse:
    """Call one endpoint with a good body where it takes one."""
    bodies: dict[str, dict[str, object]] = {
        WORKING_DAY_URL: {"minutes": 450},
        HOLIDAYS_URL: {"date": "2026-10-21", "name": "Dashain"},
        clock_out_url(SOME_UUID): {"clocked_out_at": "17:00", "reason": "x"},
        missed_time_url(SOME_UUID): {
            "day": "2026-11-04",
            "from": "09:00",
            "to": "10:00",
            "reason": "x",
        },
    }
    if method in {"post", "put"}:
        response: _MonkeyPatchedResponse = getattr(client, method)(
            url, bodies.get(url, {}), format="json"
        )
        return response
    answered: _MonkeyPatchedResponse = getattr(client, method)(url)
    return answered


class TestWhoMay:
    @pytest.mark.parametrize("role", list(Role))
    def test_every_role_clocks_in_and_out_and_sees_their_month(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)

        assert abc.client.post(IN_URL, format="json").status_code == status.HTTP_201_CREATED
        assert abc.client.get(CLOCK_URL).status_code == status.HTTP_200_OK
        assert abc.client.post(OUT_URL, format="json").status_code == status.HTTP_200_OK
        assert abc.client.get(MONTH_URL).status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    @pytest.mark.parametrize(("method", "url"), ADMIN_ENDPOINTS)
    def test_only_admins_see_and_correct_everyones_hours(
        self, abc: ConsultancyContext, role: Role, method: str, url: str
    ) -> None:
        abc.log_in_as(role)

        response = call(abc.client, method, url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN
        assert sessions(abc) == []

    @pytest.mark.parametrize(("method", "url"), MY_ENDPOINTS + ADMIN_ENDPOINTS)
    def test_logged_out_is_refused(self, abc: ConsultancyContext, method: str, url: str) -> None:
        response = call(abc.new_client(), method, url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("role", list(Role))
    @pytest.mark.parametrize(("method", "url"), MY_ENDPOINTS + ADMIN_ENDPOINTS)
    def test_with_clock_in_off_nothing_is_there_for_anyone(
        self, abc: ConsultancyContext, role: Role, method: str, url: str
    ) -> None:
        switch_modules(abc.consultancy, set())
        abc.log_in_as(role)

        response = call(abc.client, method, url)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert sessions(abc) == []

    def test_switching_clock_in_off_hides_sessions_and_on_again_brings_them_back(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.ADMIN)
        worked(abc, me, at(TUESDAY, 9), at(TUESDAY, 17))

        switch_modules(abc.consultancy, set())
        hidden = abc.client.get(MONTH_URL)
        switch_modules(abc.consultancy, {Module.CLOCK_IN})

        assert hidden.status_code == status.HTTP_404_NOT_FOUND
        assert day_of(abc.client.get(MONTH_URL).json(), TUESDAY)["minutes"] == 480

    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("get", IN_URL),
            ("put", OUT_URL),
            ("delete", CLOCK_URL),
            ("post", MONTH_URL),
            ("delete", WORKING_DAY_URL),
            ("post", TEAM_URL),
            ("get", clock_out_url(SOME_UUID)),
            ("delete", missed_time_url(SOME_UUID)),
            ("put", HOLIDAYS_URL),
            ("delete", HOLIDAYS_URL),
            ("get", holiday_url(SOME_UUID)),
            ("put", holiday_url(SOME_UUID)),
        ],
    )
    def test_only_the_methods_needed_are_allowed(
        self, abc: ConsultancyContext, method: str, url: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(url)

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(WORKING_DAY_URL, {"minutes": "450"})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("value", ["past_due", "suspended"])
    def test_a_read_only_office_refuses_clocking_but_still_shows_months(
        self, abc: ConsultancyContext, value: str
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        set_status(abc, value)

        response = abc.client.post(IN_URL, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert sessions(abc) == []
        assert abc.client.get(MONTH_URL).status_code == status.HTTP_200_OK
