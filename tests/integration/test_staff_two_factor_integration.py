"""Two-step login for Staff, switched on per Staff member (issue 62).

The second step from issue 61, on the office's own address. An Admin switches it on or off on
Employees and resets it for someone who lost their phone; a Staff member manages their own app
in My settings. No login token is given out until both steps pass.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from django.core import mail
from django_tenants.utils import schema_context, tenant_context
from freezegun import freeze_time
from freezegun.api import FrozenDateTimeFactory
from pytest_django.fixtures import Settings
from rest_framework import status
from rest_framework.test import APIClient

from staff.models import Role, Staff, StaffStatus
from tenancy.models import Consultancy
from tests.conftest import (
    PUBLIC_SCHEMA,
    TEST_PASSWORD,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    run_queued_jobs,
    use_access_token,
)
from tests.support_access import open_as_platform_admin
from two_factor.models import EventKind, TwoFactorAccount, TwoFactorEvent
from two_factor.services import CHALLENGE_TRIES_ALLOWED
from two_factor.views import WRONG_NUMBER_MESSAGE

LOGIN_URL = "/api/auth/login/"
SETUP_URL = "/api/auth/two-factor/setup/"
CONFIRM_URL = "/api/auth/two-factor/confirm/"
VERIFY_URL = "/api/auth/two-factor/verify/"
CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
MINE_URL = "/api/me/two-factor/"
CONTEXT_URL = "/api/context/"
EMPLOYEES_URL = "/api/staff/"
RESET_REQUEST_URL = "/api/auth/password-reset/"
RESET_COMPLETE_URL = "/api/auth/password-reset/complete/"
JOIN_COMPLETE_URL = "/api/auth/join/complete/"
NEW_PASSWORD = "New-Horse-7-Staple!"  # noqa: S105  # test-only credential
NOW = datetime(2026, 11, 6, 9, 0, 0, tzinfo=UTC)
STEP = timedelta(seconds=30)

INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE staff_staff;--",
    "1 OR 1=1",
    "x{color:red}",
]


def switch_url(staff_id: object) -> str:
    """Where an Admin switches someone's two-step login on or off."""
    return f"/api/staff/{staff_id}/two-factor/"


def reset_url(staff_id: object) -> str:
    """Where an Admin resets someone's two-step login."""
    return f"/api/staff/{staff_id}/two-factor/reset/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """An office with an Admin logged in on its client."""
    office = make_consultancy("ABC Education", "abcedu")
    office.log_in_as(Role.ADMIN)
    return office


@pytest.fixture
def frozen() -> Iterator[FrozenDateTimeFactory]:
    """Stop the clock at NOW; tests move it on with `tick`."""
    with freeze_time(NOW) as clock:
        assert isinstance(clock, FrozenDateTimeFactory)
        yield clock


def require(office: ConsultancyContext, staff: Staff, *, on: bool = True) -> None:
    """Switch two-step login on (or off) for `staff`, as an Admin would."""
    with tenant_context(office.consultancy):
        Staff.objects.filter(pk=staff.pk).update(two_factor_required=on)


def stored(office: ConsultancyContext, staff: Staff) -> Staff:
    """The Staff member as saved now."""
    with tenant_context(office.consultancy):
        return Staff.objects.get(pk=staff.pk)


def log_in(client: APIClient, staff: Staff, password: str = TEST_PASSWORD) -> dict[str, Any]:
    """Log in with the password and return the answer."""
    response = client.post(LOGIN_URL, {"email": staff.email, "password": password}, format="json")
    assert response.status_code == status.HTTP_200_OK, response.json()
    body: dict[str, Any] = response.json()
    return body


def secret_of(uri: str) -> str:
    """The secret inside a setup URI."""
    return parse_qs(urlparse(uri).query)["secret"][0]


def number(secret: str, at: datetime | None = None) -> str:
    """The number the app shows at `at` (now by default)."""
    return pyotp.TOTP(secret).at(at or datetime.now(tz=UTC))


def wrong(secret: str, at: datetime) -> str:
    """A number that is wrong at `at`, one step either side too."""
    near = {number(secret, at + n * STEP) for n in (-1, 0, 1)}
    return next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in near)


def finish_setup(client: APIClient, challenge: str) -> tuple[str, dict[str, Any]]:
    """Set the app up with `challenge`; return the secret and the confirm answer."""
    uri = client.post(SETUP_URL, {"challenge": challenge}, format="json").json()["uri"]
    secret = secret_of(uri)
    confirm = client.post(
        CONFIRM_URL, {"challenge": challenge, "code": number(secret)}, format="json"
    )
    assert confirm.status_code == status.HTTP_200_OK, confirm.json()
    body: dict[str, Any] = confirm.json()
    return secret, body


def set_up(office: ConsultancyContext, staff: Staff) -> tuple[str, list[str]]:
    """Switch two-step login on for `staff`, set it up, and return the secret and codes."""
    require(office, staff)
    client = office.new_client()
    secret, body = finish_setup(client, log_in(client, staff)["challenge"])
    return secret, body["recovery_codes"]


def logged_in(office: ConsultancyContext, staff: Staff) -> APIClient:
    """A new client logged in as `staff` as saved now (a device)."""
    client = office.new_client()
    use_access_token(client, office.tokens_for(stored(office, staff)).access)
    return client


def events_of(office: ConsultancyContext, staff: Staff) -> list[TwoFactorEvent]:
    """The two-step login records of `staff`, oldest first."""
    with tenant_context(office.consultancy):
        return list(TwoFactorEvent.objects.filter(person_id=staff.pk).order_by("pk"))


def is_set_up(office: ConsultancyContext, staff: Staff) -> bool:
    """Whether `staff` has a confirmed app stored."""
    with tenant_context(office.consultancy):
        return TwoFactorAccount.objects.filter(person_id=staff.pk).exclude(secret="").exists()


def invite_body(role: Role) -> dict[str, object]:
    """A valid Add employee body for Ram."""
    return {
        "name": "Ram Thapa",
        "email": "ram@abcedu.test",
        "mobile": "+9779800000001",
        "job_title": "Counsellor",
        "role": role,
        "joined_on": "2026-11-01",
    }


def emailed_code() -> str:
    """The 6-digit Code in the last email."""
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[-1].body))
    assert found is not None
    return found.group(1)


class TestLogin:
    def test_switched_off_the_password_alone_logs_in(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)

        body = log_in(abc.new_client(), sita)

        assert "access" in body
        assert body["context"]["staff"]["id"] == str(sita.public_id)
        assert "two_factor" not in body

    def test_switched_on_setup_is_forced_and_no_token_given(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        require(abc, sita)

        body = log_in(abc.new_client(), sita)

        assert body["two_factor"] == "setup"
        assert set(body) == {"two_factor", "challenge"}

    def test_setup_then_confirm_logs_in_with_ten_recovery_codes(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        require(abc, sita)
        client = abc.new_client()

        _, body = finish_setup(client, log_in(client, sita)["challenge"])

        assert len(body["recovery_codes"]) == 10
        assert body["context"]["staff"]["id"] == str(sita.public_id)
        use_access_token(client, body["access"])
        assert client.get(CONTEXT_URL).status_code == status.HTTP_200_OK
        assert [e.what for e in events_of(abc, sita)] == [EventKind.SET_UP]

    def test_once_set_up_every_login_needs_a_number(
        self, abc: ConsultancyContext, frozen: FrozenDateTimeFactory
    ) -> None:
        sita = abc.add_staff(Role.FRONT_DESK)
        secret, _ = set_up(abc, sita)
        frozen.tick(STEP)
        client = abc.new_client()

        body = log_in(client, sita)
        response = client.post(
            VERIFY_URL,
            {"challenge": body["challenge"], "code": number(secret, NOW + STEP)},
            format="json",
        )

        assert body["two_factor"] == "verify"
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["context"]["staff"]["role"] == Role.FRONT_DESK
        use_access_token(client, response.json()["access"])
        assert client.get(CONTEXT_URL).status_code == status.HTTP_200_OK

    def test_the_second_step_names_the_device_from_the_browser(
        self, abc: ConsultancyContext, frozen: FrozenDateTimeFactory
    ) -> None:
        """Issue 71: the Next.js server sends the browser with the second step too."""
        sita = abc.add_staff(Role.FRONT_DESK)
        secret, _ = set_up(abc, sita)
        frozen.tick(STEP)
        client = abc.new_client()
        client.defaults.update(
            {
                "HTTP_X_BROWSER_KEY": "0123456789abcdef0123456789abcdef",
                "HTTP_X_BROWSER_AGENT": CHROME,
            }
        )

        body = log_in(client, sita)
        response = client.post(
            VERIFY_URL,
            {"challenge": body["challenge"], "code": number(secret, NOW + STEP)},
            format="json",
        )

        use_access_token(client, response.json()["access"])
        devices = client.get("/api/me/devices/").json()
        assert devices[0]["name"] == "Chrome on Windows"
        assert devices[0]["is_current"] is True

    def test_a_recovery_code_works_once(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.TEACHER)
        _, codes = set_up(abc, sita)
        client = abc.new_client()

        first = client.post(
            VERIFY_URL,
            {"challenge": log_in(client, sita)["challenge"], "recovery_code": codes[0]},
            format="json",
        )
        again = client.post(
            VERIFY_URL,
            {"challenge": log_in(client, sita)["challenge"], "recovery_code": codes[0]},
            format="json",
        )

        assert first.status_code == status.HTTP_200_OK
        assert again.status_code == status.HTTP_400_BAD_REQUEST
        assert EventKind.RECOVERY_CODE_USED in [e.what for e in events_of(abc, sita)]

    def test_wrong_and_replayed_numbers_are_refused_and_the_sixth_try_ends_it(
        self, abc: ConsultancyContext, frozen: FrozenDateTimeFactory
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        secret, _ = set_up(abc, sita)
        client = abc.new_client()
        challenge = log_in(client, sita)["challenge"]

        replayed = client.post(
            VERIFY_URL, {"challenge": challenge, "code": number(secret, NOW)}, format="json"
        )
        tries = [
            client.post(
                VERIFY_URL, {"challenge": challenge, "code": wrong(secret, NOW)}, format="json"
            )
            for _ in range(CHALLENGE_TRIES_ALLOWED)
        ]
        frozen.tick(STEP)
        ended = client.post(
            VERIFY_URL, {"challenge": challenge, "code": number(secret, NOW + STEP)}, format="json"
        )

        assert replayed.status_code == status.HTTP_400_BAD_REQUEST
        assert all(r.status_code == status.HTTP_400_BAD_REQUEST for r in tries)
        assert ended.status_code == status.HTTP_400_BAD_REQUEST
        assert ended.json()["start_again"] is True

    def test_a_platform_challenge_is_not_a_login_token(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        require(abc, sita)
        client = abc.new_client()

        use_access_token(client, log_in(client, sita)["challenge"])

        assert client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_password_reset_by_code_still_asks_for_the_second_step(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        set_up(abc, sita)
        client = abc.new_client()
        client.post(RESET_REQUEST_URL, {"email": sita.email}, format="json")

        response = client.post(
            RESET_COMPLETE_URL,
            {"email": sita.email, "code": emailed_code(), "password": NEW_PASSWORD},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["two_factor"] == "verify"
        assert "access" not in response.json()
        assert log_in(client, sita, NEW_PASSWORD)["two_factor"] == "verify"

    def test_joining_with_an_invite_code_still_asks_for_setup(
        self, abc: ConsultancyContext, settings: Settings
    ) -> None:
        settings.STAFF_TWO_FACTOR_DEFAULT = True
        invite = abc.client.post(
            EMPLOYEES_URL,
            invite_body(Role.COUNSELLOR),
            format="json",
        )
        assert invite.status_code == status.HTTP_201_CREATED, invite.json()
        client = abc.new_client()

        response = client.post(
            JOIN_COMPLETE_URL,
            {"email": "ram@abcedu.test", "code": emailed_code(), "password": NEW_PASSWORD},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert set(response.json()) == {"two_factor", "challenge"}
        assert response.json()["two_factor"] == "setup"
        _, body = finish_setup(client, response.json()["challenge"])
        assert len(body["recovery_codes"]) == 10

    def test_a_read_only_office_still_lets_people_log_in_with_both_steps(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        require(abc, sita)
        with schema_context(PUBLIC_SCHEMA):
            Consultancy.objects.filter(pk=abc.consultancy.pk).update(status="past_due")
        client = abc.new_client()

        _, body = finish_setup(client, log_in(client, sita)["challenge"])

        assert "access" in body


class TestEmployees:
    def test_the_list_shows_the_switch_and_whether_set_up_never_the_secret(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        secret, codes = set_up(abc, sita)
        ram = abc.add_staff(Role.FRONT_DESK)

        response = abc.client.get(EMPLOYEES_URL)

        by_id = {row["id"]: row for row in response.json()}
        assert by_id[str(sita.public_id)]["two_factor_required"] is True
        assert by_id[str(sita.public_id)]["two_factor_set_up"] is True
        assert by_id[str(ram.public_id)]["two_factor_required"] is False
        assert by_id[str(ram.public_id)]["two_factor_set_up"] is False
        assert secret not in response.content.decode()
        assert all(code not in response.content.decode() for code in codes)

    def test_an_admin_switches_it_on_and_the_person_is_signed_out(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        device = logged_in(abc, sita)

        response = abc.client.patch(switch_url(sita.public_id), {"required": True}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["two_factor_required"] is True
        assert response.json()["two_factor_set_up"] is False
        assert device.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert log_in(abc.new_client(), sita)["two_factor"] == "setup"
        [event] = events_of(abc, sita)
        assert event.what == EventKind.SWITCHED_ON
        assert event.by_name == "Test Admin"
        assert event.by_platform_admin is False

    def test_an_admin_switches_it_off_and_the_app_is_forgotten(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        set_up(abc, sita)

        response = abc.client.patch(switch_url(sita.public_id), {"required": False}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["two_factor_required"] is False
        assert not is_set_up(abc, sita)
        assert "access" in log_in(abc.new_client(), sita)
        assert events_of(abc, sita)[-1].what == EventKind.SWITCHED_OFF

    def test_setting_it_to_what_it_already_is_changes_nothing(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        device = logged_in(abc, sita)

        response = abc.client.patch(switch_url(sita.public_id), {"required": False}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert device.get(CONTEXT_URL).status_code == status.HTTP_200_OK
        assert events_of(abc, sita) == []

    def test_an_invited_person_can_be_switched_on_before_they_join(
        self, abc: ConsultancyContext
    ) -> None:
        invite = abc.client.post(
            EMPLOYEES_URL,
            invite_body(Role.COUNSELLOR),
            format="json",
        )

        response = abc.client.patch(
            switch_url(invite.json()["id"]), {"required": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == StaffStatus.INVITED

    def test_reset_removes_the_app_signs_out_and_is_recorded(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        secret, codes = set_up(abc, sita)
        device = logged_in(abc, sita)

        response = abc.client.post(reset_url(sita.public_id), format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["two_factor_required"] is True
        assert response.json()["two_factor_set_up"] is False
        assert not is_set_up(abc, sita)
        assert device.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert log_in(abc.new_client(), sita)["two_factor"] == "setup"
        event = events_of(abc, sita)[-1]
        assert event.what == EventKind.RESET
        assert event.by_name == "Test Admin"
        recorded = " ".join(f"{e.what} {e.by_name}" for e in events_of(abc, sita))
        assert secret not in recorded
        assert all(code not in recorded for code in codes)

    def test_reset_with_the_switch_off_lets_the_password_alone_in(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        set_up(abc, sita)
        require(abc, sita, on=False)

        response = abc.client.post(reset_url(sita.public_id), format="json")

        assert response.status_code == status.HTTP_200_OK
        assert "access" in log_in(abc.new_client(), sita)

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_other_roles_are_refused(
        self, make_consultancy: ConsultancyFactory, role: Role
    ) -> None:
        office = make_consultancy("ABC Education", "abcedu")
        sita = office.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")
        office.log_in_as(role)

        switch = office.client.patch(switch_url(sita.public_id), {"required": True}, format="json")
        reset = office.client.post(reset_url(sita.public_id), format="json")

        assert switch.status_code == status.HTTP_403_FORBIDDEN
        assert reset.status_code == status.HTTP_403_FORBIDDEN
        assert stored(office, sita).two_factor_required is False
        assert events_of(office, sita) == []

    def test_an_admin_cant_change_or_reset_their_own(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        office = make_consultancy("ABC Education", "abcedu")
        admin = office.log_in_as(Role.ADMIN)
        require(office, admin)

        switch = office.client.patch(
            switch_url(admin.public_id), {"required": False}, format="json"
        )
        reset = office.client.post(reset_url(admin.public_id), format="json")

        assert switch.status_code == status.HTTP_403_FORBIDDEN
        assert "My settings" in switch.json()["detail"]
        assert reset.status_code == status.HTTP_403_FORBIDDEN
        assert stored(office, admin).two_factor_required is True

    def test_someone_who_has_left_cant_be_changed(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=sita.pk).update(status=StaffStatus.DEACTIVATED)

        switch = abc.client.patch(switch_url(sita.public_id), {"required": True}, format="json")
        reset = abc.client.post(reset_url(sita.public_id), format="json")

        assert switch.status_code == status.HTTP_400_BAD_REQUEST
        assert reset.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_unknown_person_is_not_found(self, abc: ConsultancyContext) -> None:
        response = abc.client.patch(switch_url(999_999), {"required": True}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_switching_on_without_a_secrets_key_is_refused(
        self, abc: ConsultancyContext, settings: Settings
    ) -> None:
        settings.TWO_FACTOR_SECRETS_KEY = ""
        sita = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.patch(switch_url(sita.public_id), {"required": True}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, sita).two_factor_required is False

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"required": "true"},
            {"required": 1},
            {"required": None},
            {"required": [True]},
            {"required": True, "secret": "JBSWY3DPEHPK3PXP"},
            *({"required": text} for text in INJECTION_STRINGS),
        ],
    )
    def test_refused_input(self, abc: ConsultancyContext, body: dict[str, object]) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.patch(switch_url(sita.public_id), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, sita).two_factor_required is False

    def test_reset_takes_no_body(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.post(reset_url(sita.public_id), {"anything": 1}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_only_the_needed_methods(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)

        assert abc.client.get(switch_url(sita.public_id)).status_code == 405
        assert abc.client.post(switch_url(sita.public_id), {}, format="json").status_code == 405
        assert abc.client.get(reset_url(sita.public_id)).status_code == 405
        assert abc.client.delete(reset_url(sita.public_id)).status_code == 405

    def test_a_platform_admin_visitor_can_switch_and_reset_marked_as_platform_admin(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        visit = open_as_platform_admin(platform_site, abc)

        switch = visit.client.patch(switch_url(sita.public_id), {"required": True}, format="json")
        reset = visit.client.post(reset_url(sita.public_id), format="json")

        assert switch.status_code == status.HTTP_200_OK
        assert reset.status_code == status.HTTP_200_OK
        events = events_of(abc, sita)
        assert [e.what for e in events] == [EventKind.SWITCHED_ON, EventKind.RESET]
        assert all(e.by_platform_admin for e in events)
        assert all(e.by_name == "Platform Ops (Platform Admin)" for e in events)
        assert all(e.by_staff_id is None for e in events)

    def test_a_platform_admin_visitor_is_asked_for_no_second_step(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)

        assert visit.client.get(CONTEXT_URL).status_code == status.HTTP_200_OK


class TestMySettings:
    def test_shows_the_state_never_the_secret(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        secret, _ = set_up(abc, sita)

        response = logged_in(abc, sita).get(MINE_URL)

        assert response.json() == {
            "required": True,
            "set_up": True,
            "recovery_codes_left": 10,
            "can_switch_off": False,
        }
        assert secret not in response.content.decode()

    def test_switching_on_starts_setup_and_ends_this_login(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.FRONT_DESK)
        device = logged_in(abc, sita)

        response = device.post(MINE_URL, {"action": "on"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["two_factor"] == "setup"
        assert "access" not in response.json()
        assert device.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED
        _, body = finish_setup(abc.new_client(), response.json()["challenge"])
        assert len(body["recovery_codes"]) == 10
        assert [e.what for e in events_of(abc, sita)] == [EventKind.SWITCHED_ON, EventKind.SET_UP]
        assert events_of(abc, sita)[0].by_staff_id == sita.pk

    def test_switching_on_when_already_on_is_refused(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        set_up(abc, sita)

        response = logged_in(abc, sita).post(MINE_URL, {"action": "on"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_someone_who_switched_it_on_themselves_can_switch_it_off(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        answer = logged_in(abc, sita).post(MINE_URL, {"action": "on"}, format="json").json()
        secret, body = finish_setup(abc.new_client(), answer["challenge"])
        device = abc.new_client()
        use_access_token(device, body["access"])
        assert device.get(MINE_URL).json()["can_switch_off"] is True

        with freeze_time(datetime.now(tz=UTC) + STEP):
            response = device.post(
                MINE_URL,
                {"action": "off", "password": TEST_PASSWORD, "code": number(secret)},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["two_factor"]["required"] is False
        assert "access" in response.json()
        assert "access" in log_in(abc.new_client(), sita)

    def test_someone_an_admin_requires_it_of_cant_switch_it_off(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        secret, _ = set_up(abc, sita)

        with freeze_time(datetime.now(tz=UTC) + STEP):
            response = logged_in(abc, sita).post(
                MINE_URL,
                {"action": "off", "password": TEST_PASSWORD, "code": number(secret)},
                format="json",
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert stored(abc, sita).two_factor_required is True

    def test_an_admin_can_switch_their_own_off_with_password_and_number(
        self, abc: ConsultancyContext
    ) -> None:
        boss = abc.add_staff(Role.ADMIN, email="boss@abcedu.test")
        secret, _ = set_up(abc, boss)

        with freeze_time(datetime.now(tz=UTC) + STEP):
            device = logged_in(abc, boss)
            missing = device.post(
                MINE_URL, {"action": "off", "password": TEST_PASSWORD}, format="json"
            )
            wrong_password = device.post(
                MINE_URL,
                {"action": "off", "password": "Wrong-Pass-1!", "code": number(secret)},
                format="json",
            )
            response = device.post(
                MINE_URL,
                {"action": "off", "password": TEST_PASSWORD, "code": number(secret)},
                format="json",
            )

        assert missing.status_code == status.HTTP_400_BAD_REQUEST
        assert wrong_password.status_code == status.HTTP_400_BAD_REQUEST
        assert response.status_code == status.HTTP_200_OK
        assert stored(abc, boss).two_factor_required is False
        assert events_of(abc, boss)[-1].what == EventKind.SWITCHED_OFF

    def test_switching_off_when_off_or_with_a_wrong_number_is_refused(
        self, abc: ConsultancyContext
    ) -> None:
        boss = abc.add_staff(Role.ADMIN, email="boss@abcedu.test")
        already_off = logged_in(abc, boss).post(
            MINE_URL, {"action": "off", "password": TEST_PASSWORD, "code": "123456"}, format="json"
        )
        secret, _ = set_up(abc, boss)

        wrong_number = logged_in(abc, boss).post(
            MINE_URL,
            {
                "action": "off",
                "password": TEST_PASSWORD,
                "code": wrong(secret, datetime.now(tz=UTC)),
            },
            format="json",
        )

        assert already_off.status_code == status.HTTP_400_BAD_REQUEST
        assert wrong_number.status_code == status.HTTP_400_BAD_REQUEST
        assert wrong_number.json() == {"code": [WRONG_NUMBER_MESSAGE]}
        assert stored(abc, boss).two_factor_required is True

    def test_switching_on_without_a_secrets_key_is_refused(
        self, abc: ConsultancyContext, settings: Settings
    ) -> None:
        settings.TWO_FACTOR_SECRETS_KEY = ""
        sita = abc.add_staff(Role.COUNSELLOR)
        device = logged_in(abc, sita)

        response = device.post(MINE_URL, {"action": "on"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert device.get(CONTEXT_URL).status_code == status.HTTP_200_OK

    def test_new_recovery_codes_need_the_app_set_up(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)

        response = logged_in(abc, sita).post(
            MINE_URL, {"action": "new_recovery_codes", "code": "123456"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_admin_requiring_what_someone_chose_stops_them_switching_it_off(
        self, abc: ConsultancyContext
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        answer = logged_in(abc, sita).post(MINE_URL, {"action": "on"}, format="json").json()
        _, body = finish_setup(abc.new_client(), answer["challenge"])

        response = abc.client.patch(switch_url(sita.public_id), {"required": True}, format="json")

        device = abc.new_client()
        use_access_token(device, body["access"])
        assert response.status_code == status.HTTP_200_OK
        assert device.get(MINE_URL).json()["can_switch_off"] is False
        assert events_of(abc, sita)[-1].what == EventKind.SWITCHED_ON
        assert events_of(abc, sita)[-1].by_name == "Test Admin"

    def test_new_recovery_codes_need_a_number(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.TEACHER)
        secret, old = set_up(abc, sita)
        device = logged_in(abc, sita)

        with freeze_time(datetime.now(tz=UTC) + STEP):
            refused = device.post(
                MINE_URL,
                {"action": "new_recovery_codes", "code": wrong(secret, datetime.now(tz=UTC))},
                format="json",
            )
            response = device.post(
                MINE_URL, {"action": "new_recovery_codes", "code": number(secret)}, format="json"
            )

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()["recovery_codes"]) == 10
        assert set(response.json()["recovery_codes"]).isdisjoint(old)
        assert events_of(abc, sita)[-1].what == EventKind.NEW_RECOVERY_CODES

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"action": "reset"},
            {"action": "on", "extra": 1},
            {"action": "new_recovery_codes", "code": "12345a"},
            {"action": "new_recovery_codes", "code": "1234567"},
            *({"action": text} for text in INJECTION_STRINGS),
            *(
                {"action": "off", "password": TEST_PASSWORD, "code": text}
                for text in INJECTION_STRINGS
            ),
        ],
    )
    def test_refused_input(self, abc: ConsultancyContext, body: dict[str, object]) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)

        response = logged_in(abc, sita).post(MINE_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, sita).two_factor_required is False

    def test_a_platform_admin_visitor_has_no_two_step_login_here(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)

        assert visit.client.get(MINE_URL).status_code == status.HTTP_403_FORBIDDEN
        response = visit.client.post(MINE_URL, {"action": "on"}, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.new_client().get(MINE_URL).status_code == status.HTTP_401_UNAUTHORIZED


class TestEnvDefault:
    def test_new_staff_start_with_the_default_and_existing_staff_are_untouched(
        self, abc: ConsultancyContext, settings: Settings
    ) -> None:
        before = abc.add_staff(Role.COUNSELLOR)
        settings.STAFF_TWO_FACTOR_DEFAULT = True

        invite = abc.client.post(
            EMPLOYEES_URL,
            invite_body(Role.FRONT_DESK),
            format="json",
        )

        assert invite.json()["two_factor_required"] is True
        assert stored(abc, before).two_factor_required is False

    def test_off_by_default(self, abc: ConsultancyContext) -> None:
        invite = abc.client.post(
            EMPLOYEES_URL,
            invite_body(Role.FRONT_DESK),
            format="json",
        )

        assert invite.json()["two_factor_required"] is False

    def test_an_admin_added_from_the_platform_starts_with_the_default(
        self, platform_site: PlatformContext, settings: Settings
    ) -> None:
        settings.STAFF_TWO_FACTOR_DEFAULT = True
        platform_site.log_in()
        response = platform_site.client.post(
            "/api/platform/consultancies/",
            {
                "name": "Everest Education",
                "subdomain": "everest",
                "contact_email": "info@everest.test",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_202_ACCEPTED, response.content
        run_queued_jobs()
        added = platform_site.client.post(
            f"/api/platform/consultancies/{response.json()['id']}/admins/",
            {"name": "Sita Sharma", "email": "sita@everest.test"},
            format="json",
        )
        assert added.status_code == status.HTTP_201_CREATED, added.content

        with schema_context("everest"):
            admin = Staff.objects.get(email="sita@everest.test")
        assert admin.two_factor_required is True


class TestSecretsStayHidden:
    def test_no_secret_number_code_or_challenge_in_logs_or_later_answers(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        sita = abc.add_staff(Role.COUNSELLOR)
        require(abc, sita)
        client = abc.new_client()
        challenge = log_in(client, sita)["challenge"]
        secret, body = finish_setup(client, challenge)
        first_number = number(secret)
        employees = abc.client.get(EMPLOYEES_URL).content.decode()
        mine = logged_in(abc, sita).get(MINE_URL).content.decode()

        logs = json_logs.text()
        for hidden in (secret, first_number, challenge, *body["recovery_codes"]):
            assert hidden not in logs
            assert hidden not in employees
            assert hidden not in mine
        assert "two-step login set up" in logs
