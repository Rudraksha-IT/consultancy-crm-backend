"""Work From Home dates, and no network check for Admins (spec 104, 131-133, issue 87).

Admins clock in from any network. Other Staff do only on Approved Work From Home dates, asked for
by them and answered by an Admin, or set directly by an Admin. The address is only compared,
never stored or logged (ADR 0004 and its note of 2026-11-14); the "Home" label on a day comes
from the approved dates, never from the address.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django_tenants.utils import tenant_context
from freezegun import freeze_time
from rest_framework import status
from rest_framework.test import APIClient

from clock_in.models import WorkFromHome, WorkFromHomeStatus, WorkSession
from modules.services import switch_modules
from modules.types import Module
from notifications.models import Notification
from office_networks.models import OfficeNetwork
from staff.models import Role, Staff, StaffStatus
from tests.conftest import (
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    use_access_token,
)
from tests.integration.test_consultancy_status_integration import set_status
from tests.integration.test_stage_and_counsellor_integration import INJECTION_STRINGS
from tests.support_access import open_as_platform_admin

KTM = ZoneInfo("Asia/Kathmandu")
# "Now" in these tests: Thursday 5 November 2026, 11:00 in Kathmandu.
NOW = datetime(2026, 11, 5, 11, 0, tzinfo=KTM)
TODAY = date(2026, 11, 5)
YESTERDAY = date(2026, 11, 4)
TOMORROW = date(2026, 11, 6)
NEXT_WEEK = date(2026, 11, 12)
OFFICE_ADDRESS = "110.44.115.82"
HOME_ADDRESS = "27.34.20.9"

CLOCK_URL = "/api/clock/"
IN_URL = "/api/clock/in/"
OUT_URL = "/api/clock/out/"
MONTH_URL = "/api/clock/month/"
MINE_URL = "/api/clock/work-from-home/"
NOTIFICATIONS_URL = "/api/notifications/"
WAITING_URL = "/api/home/waiting/"

OFF_NETWORK_IN = {"detail": "You can only clock in from the office network."}
NO_NETWORK = {"detail": "Ask your Admin to add the office network."}
ADMIN_NEEDS_NONE = {
    "detail": "Admins can clock in from any network, so they never need Work from home dates."
}
ONLY_ADMIN = {"detail": "Only an Admin can answer or set Work from home dates."}
PLATFORM_CANT = {"detail": "A Platform Admin can't clock in or out."}
NOT_WAITING = {"detail": "These dates are no longer waiting for an answer."}
CANT_END = {"detail": "These dates have already ended."}
REASON_MESSAGE = "Say why."
END_MESSAGE = "The end date must be on or after the start date."
START_MESSAGE = "The start can't be before today."


@pytest.fixture(autouse=True)
def frozen_clock() -> Iterator[None]:
    """Every test here runs at `NOW`."""
    with freeze_time(NOW):
        yield


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """Clock-in on, the office wifi listed, and calls coming from home."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.CLOCK_IN})
    with tenant_context(office.consultancy):
        OfficeNetwork.objects.create(label="Main office wifi", network="110.44.115.0/24")
    office.client.defaults["HTTP_X_CLIENT_IP"] = HOME_ADDRESS
    return office


def client_as(office: ConsultancyContext, staff: Staff) -> APIClient:
    """A client logged in as `staff`, calling from home."""
    client = api_client(office.host)
    use_access_token(client, office.tokens_for(staff).access)
    client.defaults["HTTP_X_CLIENT_IP"] = HOME_ADDRESS
    return client


def person_url(staff_id: object) -> str:
    """One person's Work From Home dates (Admins)."""
    return f"/api/clock/staff/{staff_id}/work-from-home/"


def action_url(dates_id: object, action: str) -> str:
    """Approve, decline, cancel or end one set of dates."""
    return f"/api/clock/work-from-home/{dates_id}/{action}/"


def store(
    office: ConsultancyContext,
    staff: Staff,
    starts_on: date,
    ends_on: date | None,
    state: WorkFromHomeStatus = WorkFromHomeStatus.APPROVED,
) -> WorkFromHome:
    """Store Work From Home dates directly."""
    with tenant_context(office.consultancy):
        return WorkFromHome.objects.create(
            staff=staff,
            starts_on=starts_on,
            ends_on=ends_on,
            reason="Plumber coming",
            status=state,
            asked_by_staff=staff,
            asked_by_name=staff.name,
        )


def stored(office: ConsultancyContext) -> list[WorkFromHome]:
    """Every set of dates, oldest first."""
    with tenant_context(office.consultancy):
        return list(WorkFromHome.objects.order_by("pk"))


def sessions(office: ConsultancyContext) -> list[WorkSession]:
    """Every Work Session, oldest first."""
    with tenant_context(office.consultancy):
        return list(WorkSession.objects.order_by("pk"))


def bell(client: APIClient) -> list[dict[str, Any]]:
    """The notifications of whoever `client` is."""
    results: list[dict[str, Any]] = client.get(NOTIFICATIONS_URL).json()["results"]
    return results


def ask(client: APIClient, **body: object) -> Any:  # noqa: ANN401  # a test response
    """Ask for dates; a good request unless `body` says otherwise."""
    wanted: dict[str, object] = {
        "starts_on": TOMORROW.isoformat(),
        "ends_on": NEXT_WEEK.isoformat(),
        "reason": "Looking after my mother",
    }
    wanted.update(body)
    return client.post(MINE_URL, wanted, format="json")


class TestAdminsClockAnywhere:
    def test_an_admin_clocks_in_and_out_from_home(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        clocked_in = abc.client.post(IN_URL, format="json")
        clocked_out = abc.client.post(OUT_URL, format="json")

        assert clocked_in.status_code == status.HTTP_201_CREATED
        assert clocked_out.status_code == status.HTTP_200_OK
        assert clocked_in.json()["clock"]["anywhere"] == "admin"
        assert sessions(abc)[0].work_from_home is False

    def test_an_admin_clocks_in_with_no_office_network_set_up(
        self, abc: ConsultancyContext
    ) -> None:
        with tenant_context(abc.consultancy):
            OfficeNetwork.objects.all().delete()
        abc.log_in_as(Role.ADMIN)

        assert abc.client.post(IN_URL, format="json").status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_anyone_else_without_dates_is_refused(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)

        response = abc.client.post(IN_URL, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == OFF_NETWORK_IN
        assert sessions(abc) == []
        assert abc.client.get(CLOCK_URL).json()["anywhere"] is None

    def test_a_platform_admin_still_never_clocks(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visitor = open_as_platform_admin(platform_site, abc).client

        response = visitor.post(IN_URL, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == PLATFORM_CANT


class TestClockingOnWorkFromHomeDates:
    @pytest.mark.parametrize("ends_on", [TODAY, NEXT_WEEK, None])
    def test_on_approved_dates_staff_clock_in_and_out_from_home(
        self, abc: ConsultancyContext, ends_on: date | None
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        store(abc, me, YESTERDAY, ends_on)

        clocked_in = abc.client.post(IN_URL, format="json")
        clocked_out = abc.client.post(OUT_URL, format="json")

        assert clocked_in.status_code == status.HTTP_201_CREATED
        assert clocked_out.status_code == status.HTTP_200_OK
        assert clocked_in.json()["clock"]["anywhere"] == "work_from_home"
        assert sessions(abc)[0].work_from_home is True

    def test_also_with_no_office_network_set_up(self, abc: ConsultancyContext) -> None:
        with tenant_context(abc.consultancy):
            OfficeNetwork.objects.all().delete()
        me = abc.log_in_as(Role.TEACHER)
        store(abc, me, TODAY, TODAY)

        assert abc.client.post(IN_URL, format="json").status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize(
        ("starts_on", "ends_on"),
        [(TOMORROW, NEXT_WEEK), (date(2026, 11, 1), YESTERDAY)],
    )
    def test_outside_the_dates_it_is_refused(
        self, abc: ConsultancyContext, starts_on: date, ends_on: date
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        store(abc, me, starts_on, ends_on)

        response = abc.client.post(IN_URL, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == OFF_NETWORK_IN
        assert sessions(abc) == []

    def test_with_no_network_and_no_dates_the_message_is_unchanged(
        self, abc: ConsultancyContext
    ) -> None:
        with tenant_context(abc.consultancy):
            OfficeNetwork.objects.all().delete()
        abc.log_in_as(Role.FRONT_DESK)

        assert abc.client.post(IN_URL, format="json").json() == NO_NETWORK

    @pytest.mark.parametrize(
        "state",
        [
            WorkFromHomeStatus.WAITING,
            WorkFromHomeStatus.DECLINED,
            WorkFromHomeStatus.CANCELLED,
            WorkFromHomeStatus.ENDED,
        ],
    )
    def test_dates_that_are_not_approved_give_no_permission(
        self, abc: ConsultancyContext, state: WorkFromHomeStatus
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        store(abc, me, YESTERDAY, None, state)

        assert abc.client.post(IN_URL, format="json").status_code == status.HTTP_403_FORBIDDEN
        assert sessions(abc) == []

    def test_someone_elses_dates_give_no_permission(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.TEACHER)
        store(abc, other, YESTERDAY, None)
        abc.log_in_as(Role.COUNSELLOR)

        assert abc.client.post(IN_URL, format="json").status_code == status.HTTP_403_FORBIDDEN

    def test_on_their_dates_at_the_office_the_day_is_still_home(
        self, abc: ConsultancyContext
    ) -> None:
        """The label comes from the approved dates, never from the address."""
        me = abc.log_in_as(Role.COUNSELLOR)
        store(abc, me, TODAY, TODAY)
        abc.client.defaults["HTTP_X_CLIENT_IP"] = OFFICE_ADDRESS

        abc.client.post(IN_URL, format="json")

        assert sessions(abc)[0].work_from_home is True

    def test_the_clock_says_working_from_home_today(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        store(abc, me, TODAY, None)

        clock = abc.client.get(CLOCK_URL).json()

        assert clock["anywhere"] == "work_from_home"
        assert clock["office_network"] == "off"


class TestTheHomeLabel:
    def test_a_day_worked_on_the_dates_shows_home(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        store(abc, me, TODAY, TODAY)
        abc.client.post(IN_URL, format="json")
        with tenant_context(abc.consultancy):
            WorkSession.objects.create(
                staff=me,
                clocked_in_at=datetime.combine(YESTERDAY, time(9), tzinfo=KTM),
                clocked_out_at=datetime.combine(YESTERDAY, time(17), tzinfo=KTM),
            )

        days = {d["date"]: d for d in abc.client.get(MONTH_URL).json()["days"]}

        assert days[TODAY.isoformat()]["home"] is True
        assert days[YESTERDAY.isoformat()]["home"] is False
        assert days["2026-11-01"]["home"] is False

    def test_an_admin_sees_the_home_label_on_someone_elses_month(
        self, abc: ConsultancyContext
    ) -> None:
        person = abc.add_staff(Role.TEACHER)
        store(abc, person, TODAY, TODAY)
        client_as(abc, person).post(IN_URL, format="json")
        abc.log_in_as(Role.ADMIN)

        month = abc.client.get(MONTH_URL, {"staff": str(person.public_id)}).json()

        assert next(d for d in month["days"] if d["date"] == TODAY.isoformat())["home"] is True


class TestAsking:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_staff_ask_and_every_admin_is_told(self, abc: ConsultancyContext, role: Role) -> None:
        admin = abc.add_staff(Role.ADMIN)
        me = abc.log_in_as(role)

        response = ask(abc.client)

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["starts_on"] == TOMORROW.isoformat()
        assert body["ends_on"] == NEXT_WEEK.isoformat()
        assert body["reason"] == "Looking after my mother"
        assert body["status"] == "waiting"
        assert body["set_directly"] is False
        assert body["asked_by"] == me.name
        [dates] = stored(abc)
        assert (dates.staff_id, dates.status) == (me.pk, WorkFromHomeStatus.WAITING)
        [told] = bell(client_as(abc, admin))
        assert told["kind"] == "work_from_home_asked"
        assert told["by"]["name"] == me.name
        assert told["work_from_home"] == {
            "id": str(dates.public_id),
            "staff_id": str(me.public_id),
            "starts_on": TOMORROW.isoformat(),
            "ends_on": NEXT_WEEK.isoformat(),
            "status": "waiting",
        }

    def test_with_no_end_date(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = ask(abc.client, ends_on=None, starts_on=TODAY.isoformat())
        without = ask(abc.client, ends_on="", starts_on=TODAY.isoformat())

        assert response.status_code == without.status_code == status.HTTP_201_CREATED
        assert [d.ends_on for d in stored(abc)] == [None, None]

    def test_my_dates_are_listed_newest_first(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        store(abc, me, date(2026, 10, 1), date(2026, 10, 2))
        ask(abc.client)
        store(abc, abc.add_staff(Role.TEACHER), TODAY, TODAY)

        mine = abc.client.get(MINE_URL).json()

        assert mine["can_ask"] is True
        assert [d["starts_on"] for d in mine["results"]] == ["2026-11-06", "2026-10-01"]

    def test_an_admin_never_needs_dates(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = ask(abc.client)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ADMIN_NEEDS_NONE
        assert abc.client.get(MINE_URL).json() == {"can_ask": False, "results": []}
        assert stored(abc) == []

    def test_a_platform_admin_cant_ask(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visitor = open_as_platform_admin(platform_site, abc).client

        assert ask(visitor).json() == PLATFORM_CANT
        assert visitor.get(MINE_URL).status_code == status.HTTP_403_FORBIDDEN
        assert stored(abc) == []

    @pytest.mark.parametrize(
        ("body", "field", "message"),
        [
            (
                {"ends_on": YESTERDAY.isoformat(), "starts_on": TODAY.isoformat()},
                "ends_on",
                END_MESSAGE,
            ),
            ({"starts_on": YESTERDAY.isoformat()}, "starts_on", START_MESSAGE),
            (
                {"starts_on": "2028-01-01", "ends_on": None},
                "starts_on",
                "Choose a start within a year.",
            ),
            ({"starts_on": "2026-02-30"}, "starts_on", "Choose a day such as 2026-11-04."),
            ({"starts_on": "06/11/2026"}, "starts_on", "Choose a day such as 2026-11-04."),
            ({"starts_on": 20261106}, "starts_on", "Choose a day such as 2026-11-04."),
            ({"starts_on": None}, "starts_on", "Choose a day such as 2026-11-04."),
            ({"ends_on": 5}, "ends_on", "Choose a day such as 2026-11-04."),
            ({"reason": ""}, "reason", REASON_MESSAGE),
            ({"reason": "   "}, "reason", REASON_MESSAGE),
            ({"reason": None}, "reason", REASON_MESSAGE),
            ({"reason": ["a"]}, "reason", REASON_MESSAGE),
            ({"reason": "x" * 201}, "reason", "Use at most 200 characters."),
            ({"reason": "Ill\x00"}, "reason", None),
        ],
    )
    def test_bad_input_is_refused_and_nothing_stored(
        self,
        abc: ConsultancyContext,
        body: dict[str, object],
        field: str,
        message: str | None,
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = ask(abc.client, **body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        if message is not None:
            assert response.json()[field] == [message]
        assert stored(abc) == []

    def test_200_characters_are_fine(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        assert ask(abc.client, reason="x" * 200).status_code == status.HTTP_201_CREATED

    def test_missing_fields_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.post(MINE_URL, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.json()) == {"starts_on", "reason"}

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = ask(abc.client, status="approved", staff=1)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc) == []

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_reasons_are_kept_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = ask(abc.client, reason=text)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["reason"] == text.strip()

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.post(MINE_URL, {"starts_on": TODAY.isoformat(), "reason": "Plumber"})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


class TestCancelling:
    def test_the_person_who_asked_cancels_a_waiting_request(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        dates_id = ask(abc.client).json()["id"]

        response = abc.client.post(action_url(dates_id, "cancel"), format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "cancelled"
        assert stored(abc)[0].status == WorkFromHomeStatus.CANCELLED

    def test_once_answered_it_cant_be_cancelled(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        dates = store(abc, me, TOMORROW, None)

        response = abc.client.post(action_url(dates.public_id, "cancel"), format="json")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == NOT_WAITING
        assert stored(abc)[0].status == WorkFromHomeStatus.APPROVED

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.ADMIN])
    def test_nobody_else_cancels_it(self, abc: ConsultancyContext, role: Role) -> None:
        other = abc.add_staff(Role.TEACHER, email=f"teacher2@{abc.host}")
        dates = store(abc, other, TOMORROW, None, WorkFromHomeStatus.WAITING)
        abc.log_in_as(role)

        response = abc.client.post(action_url(dates.public_id, "cancel"), format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored(abc)[0].status == WorkFromHomeStatus.WAITING

    def test_a_body_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        dates_id = ask(abc.client).json()["id"]

        response = abc.client.post(
            action_url(dates_id, "cancel"), {"status": "approved"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc)[0].status == WorkFromHomeStatus.WAITING


class TestAnswering:
    @pytest.mark.parametrize(
        ("action", "state", "kind"),
        [
            ("approve", WorkFromHomeStatus.APPROVED, "work_from_home_approved"),
            ("decline", WorkFromHomeStatus.DECLINED, "work_from_home_declined"),
        ],
    )
    def test_an_admin_answers_and_the_person_is_told(
        self, abc: ConsultancyContext, action: str, state: WorkFromHomeStatus, kind: str
    ) -> None:
        person = abc.add_staff(Role.COUNSELLOR)
        dates_id = ask(client_as(abc, person)).json()["id"]
        admin = abc.log_in_as(Role.ADMIN)

        response = abc.client.post(action_url(dates_id, action), format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == state.value
        assert response.json()["answered_by"] == admin.name
        [dates] = stored(abc)
        assert (dates.status, dates.answered_by_staff_id) == (state, admin.pk)
        [told] = bell(client_as(abc, person))
        assert told["kind"] == kind
        assert told["by"]["name"] == admin.name
        assert told["work_from_home"]["status"] == state.value

    def test_approved_dates_then_let_them_clock_in_from_home(self, abc: ConsultancyContext) -> None:
        person = abc.add_staff(Role.COUNSELLOR)
        their_client = client_as(abc, person)
        dates_id = ask(their_client, starts_on=TODAY.isoformat()).json()["id"]
        before = their_client.post(IN_URL, format="json")
        abc.log_in_as(Role.ADMIN)
        abc.client.post(action_url(dates_id, "approve"), format="json")

        after = their_client.post(IN_URL, format="json")

        assert before.status_code == status.HTTP_403_FORBIDDEN
        assert after.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("action", ["approve", "decline"])
    @pytest.mark.parametrize(
        "state",
        [WorkFromHomeStatus.APPROVED, WorkFromHomeStatus.DECLINED, WorkFromHomeStatus.CANCELLED],
    )
    def test_only_a_waiting_request_is_answered(
        self, abc: ConsultancyContext, action: str, state: WorkFromHomeStatus
    ) -> None:
        dates = store(abc, abc.add_staff(Role.COUNSELLOR), TOMORROW, None, state)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(action_url(dates.public_id, action), format="json")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == NOT_WAITING
        assert stored(abc)[0].status == state

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    @pytest.mark.parametrize("action", ["approve", "decline", "end"])
    def test_only_an_admin_answers_or_ends(
        self, abc: ConsultancyContext, role: Role, action: str
    ) -> None:
        state = WorkFromHomeStatus.APPROVED if action == "end" else WorkFromHomeStatus.WAITING
        dates = store(abc, abc.add_staff(Role.ADMIN), TOMORROW, None, state)
        me = abc.log_in_as(role)
        mine = store(abc, me, TOMORROW, None, state)

        theirs = abc.client.post(action_url(dates.public_id, action), format="json")
        own = abc.client.post(action_url(mine.public_id, action), format="json")

        assert theirs.status_code == own.status_code == status.HTTP_403_FORBIDDEN
        assert theirs.json() == own.json() == ONLY_ADMIN
        assert [d.status for d in stored(abc)] == [state, state]

    def test_a_platform_admin_answers_under_their_own_name(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        person = abc.add_staff(Role.COUNSELLOR)
        dates_id = ask(client_as(abc, person)).json()["id"]
        visitor = open_as_platform_admin(platform_site, abc).client

        response = visitor.post(action_url(dates_id, "approve"), format="json")

        assert response.status_code == status.HTTP_200_OK
        [dates] = stored(abc)
        assert (dates.answered_by_staff_id, dates.answered_by_name) == (
            None,
            "Platform Ops (Platform Admin)",
        )
        assert dates.answered_by_platform_admin is True
        assert bell(client_as(abc, person))[0]["by"]["name"] == "Platform Ops (Platform Admin)"

    def test_unknown_dates_are_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        for action in ("approve", "decline", "end", "cancel"):
            response = abc.client.post(action_url(999, action), format="json")
            assert response.status_code == status.HTTP_404_NOT_FOUND


class TestSettingDirectly:
    def test_an_admin_sets_dates_and_the_person_is_told(self, abc: ConsultancyContext) -> None:
        person = abc.add_staff(Role.TEACHER)
        admin = abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            person_url(person.public_id),
            {"starts_on": TODAY.isoformat(), "ends_on": None, "reason": "Works from Pokhara"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["status"] == "approved"
        assert body["set_directly"] is True
        assert body["asked_by"] == admin.name
        assert body["ends_on"] is None
        [told] = bell(client_as(abc, person))
        assert told["kind"] == "work_from_home_set"
        assert told["by"]["name"] == admin.name
        assert client_as(abc, person).post(IN_URL, format="json").status_code == 201

    def test_an_admin_sees_a_persons_dates(self, abc: ConsultancyContext) -> None:
        person = abc.add_staff(Role.TEACHER)
        store(abc, person, TODAY, None)
        store(abc, abc.add_staff(Role.FRONT_DESK), TODAY, None)
        abc.log_in_as(Role.ADMIN)

        answer = abc.client.get(person_url(person.public_id)).json()

        assert answer["staff"]["id"] == str(person.public_id)
        assert answer["needs_dates"] is True
        assert [d["staff_id"] for d in answer["results"]] == [str(person.public_id)]

    def test_an_admin_needs_no_dates(self, abc: ConsultancyContext) -> None:
        other_admin = abc.add_staff(Role.ADMIN, email=f"admin2@{abc.host}")
        abc.log_in_as(Role.ADMIN)

        listed = abc.client.get(person_url(other_admin.public_id)).json()
        response = abc.client.post(
            person_url(other_admin.public_id),
            {"starts_on": TODAY.isoformat(), "reason": "x"},
            format="json",
        )

        assert listed["needs_dates"] is False
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ADMIN_NEEDS_NONE
        assert stored(abc) == []

    def test_someone_invited_left_or_unknown_is_not_found(self, abc: ConsultancyContext) -> None:
        left = abc.add_staff(Role.TEACHER)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=left.pk).update(status=StaffStatus.DEACTIVATED)
        abc.log_in_as(Role.ADMIN)

        for staff_id in (left.pk, 999):
            response = abc.client.post(
                person_url(staff_id), {"starts_on": TODAY.isoformat(), "reason": "x"}, format="json"
            )
            assert response.status_code == status.HTTP_404_NOT_FOUND
        assert abc.client.get(person_url(999)).status_code == status.HTTP_404_NOT_FOUND
        assert stored(abc) == []

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_an_admin_sets_or_sees_someones_dates(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        me = abc.log_in_as(role)

        for staff_id in (me.public_id, abc.add_staff(Role.ADMIN).public_id):
            seen = abc.client.get(person_url(staff_id))
            set_ = abc.client.post(
                person_url(staff_id), {"starts_on": TODAY.isoformat(), "reason": "x"}, format="json"
            )
            assert seen.status_code == set_.status_code == status.HTTP_403_FORBIDDEN
            assert set_.json() == ONLY_ADMIN
        assert stored(abc) == []

    def test_bad_input_is_refused(self, abc: ConsultancyContext) -> None:
        person = abc.add_staff(Role.TEACHER)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            person_url(person.public_id),
            {
                "starts_on": TODAY.isoformat(),
                "ends_on": YESTERDAY.isoformat(),
                "reason": "x",
                "status": "waiting",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc) == []


class TestEnding:
    def test_started_dates_end_today(self, abc: ConsultancyContext) -> None:
        person = abc.add_staff(Role.COUNSELLOR)
        dates = store(abc, person, YESTERDAY, None)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(action_url(dates.public_id, "end"), format="json")

        assert response.status_code == status.HTTP_200_OK
        assert (response.json()["status"], response.json()["ends_on"]) == (
            "approved",
            TODAY.isoformat(),
        )
        assert client_as(abc, person).post(IN_URL, format="json").status_code == 201
        with freeze_time(datetime(2026, 11, 6, 9, tzinfo=KTM)):
            later = client_as(abc, person).post(IN_URL, format="json")
        assert later.status_code == status.HTTP_403_FORBIDDEN

    def test_dates_not_started_yet_are_ended_and_give_nothing(
        self, abc: ConsultancyContext
    ) -> None:
        dates = store(abc, abc.add_staff(Role.COUNSELLOR), TOMORROW, NEXT_WEEK)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(action_url(dates.public_id, "end"), format="json")

        assert response.json()["status"] == "ended"
        assert stored(abc)[0].status == WorkFromHomeStatus.ENDED

    @pytest.mark.parametrize(
        ("state", "ends_on"),
        [
            (WorkFromHomeStatus.APPROVED, YESTERDAY),
            (WorkFromHomeStatus.WAITING, None),
            (WorkFromHomeStatus.ENDED, None),
            (WorkFromHomeStatus.DECLINED, None),
        ],
    )
    def test_what_has_ended_or_isnt_approved_cant_be_ended(
        self, abc: ConsultancyContext, state: WorkFromHomeStatus, ends_on: date | None
    ) -> None:
        dates = store(abc, abc.add_staff(Role.COUNSELLOR), date(2026, 11, 1), ends_on, state)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(action_url(dates.public_id, "end"), format="json")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == CANT_END


class TestHomeWaiting:
    def test_an_admin_sees_how_many_requests_wait(self, abc: ConsultancyContext) -> None:
        first = abc.add_staff(Role.COUNSELLOR)
        store(abc, first, TOMORROW, None, WorkFromHomeStatus.WAITING)
        store(abc, abc.add_staff(Role.TEACHER), TOMORROW, None, WorkFromHomeStatus.WAITING)
        store(abc, first, TOMORROW, None, WorkFromHomeStatus.DECLINED)
        abc.log_in_as(Role.ADMIN)

        waiting = abc.client.get(WAITING_URL).json()["work_from_home_requests"]

        assert waiting == {"count": 2, "first_staff_id": str(first.public_id)}

    def test_a_counsellor_sees_none(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        store(abc, me, TOMORROW, None, WorkFromHomeStatus.WAITING)

        waiting = abc.client.get(WAITING_URL).json()["work_from_home_requests"]

        assert waiting == {"count": 0, "first_staff_id": None}

    def test_with_clock_in_off_none_are_counted_or_told(self, abc: ConsultancyContext) -> None:
        admin = abc.add_staff(Role.ADMIN)
        ask(client_as(abc, abc.add_staff(Role.COUNSELLOR)))
        switch_modules(abc.consultancy, set())
        client = client_as(abc, admin)

        assert client.get(WAITING_URL).json()["work_from_home_requests"]["count"] == 0
        assert bell(client) == []
        assert client.get(NOTIFICATIONS_URL).json()["unread_count"] == 0


# A record UUID that no office has (ADR 0027).
SOME_UUID = "00000000-0000-4000-8000-000000000001"
ENDPOINTS = [
    ("get", MINE_URL),
    ("post", MINE_URL),
    ("post", action_url(SOME_UUID, "cancel")),
    ("post", action_url(SOME_UUID, "approve")),
    ("post", action_url(SOME_UUID, "decline")),
    ("post", action_url(SOME_UUID, "end")),
    ("get", person_url(SOME_UUID)),
    ("post", person_url(SOME_UUID)),
]


class TestWhoMay:
    @pytest.mark.parametrize(("method", "url"), ENDPOINTS)
    def test_logged_out_is_refused(self, abc: ConsultancyContext, method: str, url: str) -> None:
        response = getattr(abc.new_client(), method)(url, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("role", list(Role))
    @pytest.mark.parametrize(("method", "url"), ENDPOINTS)
    def test_with_clock_in_off_nothing_is_there(
        self, abc: ConsultancyContext, role: Role, method: str, url: str
    ) -> None:
        switch_modules(abc.consultancy, set())
        abc.log_in_as(role)

        response = getattr(abc.client, method)(url, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("put", MINE_URL),
            ("delete", MINE_URL),
            ("get", action_url(SOME_UUID, "approve")),
            ("delete", action_url(SOME_UUID, "end")),
            ("patch", person_url(SOME_UUID)),
        ],
    )
    def test_only_the_methods_needed_are_allowed(
        self, abc: ConsultancyContext, method: str, url: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(url, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("value", ["past_due", "suspended"])
    def test_a_read_only_office_refuses_changes_but_shows_dates(
        self, abc: ConsultancyContext, value: str
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        store(abc, me, TOMORROW, None)
        set_status(abc, value)

        assert ask(abc.client).status_code == status.HTTP_403_FORBIDDEN
        assert abc.client.get(MINE_URL).status_code == status.HTTP_200_OK
        assert len(stored(abc)) == 1

    def test_a_deactivated_persons_token_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=me.pk).update(status=StaffStatus.DEACTIVATED)

        assert ask(abc.client).status_code == status.HTTP_401_UNAUTHORIZED


class TestNoAddressKept:
    def test_the_address_is_never_logged_or_stored(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        admin = abc.add_staff(Role.ADMIN)
        me = abc.log_in_as(Role.COUNSELLOR)
        dates_id = ask(abc.client, starts_on=TODAY.isoformat()).json()["id"]
        client_as(abc, admin).post(action_url(dates_id, "approve"), format="json")
        client_as(abc, admin).post(IN_URL, format="json")

        abc.client.post(IN_URL, format="json")
        abc.client.get(CLOCK_URL)
        abc.client.post(OUT_URL, format="json")
        abc.client.defaults["HTTP_X_CLIENT_IP"] = OFFICE_ADDRESS
        abc.client.get(CLOCK_URL)

        logs = json_logs.text()
        assert HOME_ADDRESS not in logs
        assert OFFICE_ADDRESS not in logs
        assert "Looking after my mother" not in logs
        with tenant_context(abc.consultancy):
            kept = (
                str(list(WorkFromHome.objects.values()))
                + str(list(WorkSession.objects.values()))
                + str(list(Notification.objects.values()))
            )
        assert HOME_ADDRESS not in kept
        assert OFFICE_ADDRESS not in kept
        assert me.pk in [s.staff_id for s in sessions(abc)]
        assert {field.name for field in WorkFromHome._meta.get_fields()} == {
            "id",
            "public_id",
            "staff",
            "starts_on",
            "ends_on",
            "reason",
            "status",
            "asked_at",
            "asked_by_staff",
            "asked_by_name",
            "asked_by_platform_admin",
            "answered_at",
            "answered_by_staff",
            "answered_by_name",
            "answered_by_platform_admin",
            "notifications",
        }
