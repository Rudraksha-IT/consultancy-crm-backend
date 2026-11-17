"""A Staff member's own Settings (spec 22c-22g, 18c; issue 38).

My Details (name and mobile only), Password, Notifications, light/dark and Signed-in devices.
Everyone can use them, whatever their Role; nobody can change their own Role, email or job title.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status
from rest_framework.test import APIClient

from auth_tokens.models import IssuedRefreshToken
from staff.models import Role, Staff
from staff.my_settings import capitalise_name
from tests.conftest import (
    TEST_PASSWORD,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    use_access_token,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

ME_URL = "/api/me/"
CHANGE_PW_URL = "/api/me/password/"
DEVICES_URL = "/api/me/devices/"
SIGN_OUT_OTHERS_URL = "/api/me/devices/sign-out-others/"
LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/refresh/"
LOGOUT_URL = "/api/auth/logout/"
CHROME_KEY = "0123456789abcdef0123456789abcdef"
PHONE_KEY = "fedcba9876543210fedcba9876543210"
CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)
CONTEXT_URL = "/api/context/"
NEW_PASSWORD = "Kathmandu!2027"  # noqa: S105  # test-only credential
UNKNOWN_FIELD = {"detail": ["Unknown field."]}
WRONG_CURRENT = {"current_password": ["That isn't your current password."]}
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE staff_staff; --",
    "}body{background:url(javascript:alert(1))}",
]
EVERY_ROLE = [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def counsellor(abc: ConsultancyContext) -> Staff:
    """A Counsellor of ABC with a job title and mobile, logged in on `abc.client`."""
    staff = abc.log_in_as(Role.COUNSELLOR)
    with tenant_context(abc.consultancy):
        staff.job_title = "Senior Counsellor"
        staff.mobile = "+9779812345670"
        staff.save()
    return staff


def stored(abc: ConsultancyContext, staff: Staff) -> Staff:
    """The Staff member as stored now."""
    with tenant_context(abc.consultancy):
        return Staff.objects.get(pk=staff.pk)


def log_in(client: APIClient, email: str, password: str = TEST_PASSWORD) -> dict[str, Any]:
    """Log in through the API, as a new device, and return the answer."""
    response = client.post(LOGIN_URL, {"email": email, "password": password}, format="json")
    assert response.status_code == status.HTTP_200_OK
    answer: dict[str, Any] = response.json()
    return answer


def device_client(abc: ConsultancyContext, staff: Staff) -> tuple[APIClient, dict[str, Any]]:
    """A new device logged in as `staff`, and its tokens."""
    client = abc.new_client()
    tokens = log_in(client, staff.email)
    use_access_token(client, tokens["access"])
    return client, tokens


def browser_client(
    abc: ConsultancyContext, staff: Staff, key: str, agent: str = CHROME_WINDOWS
) -> tuple[APIClient, dict[str, Any]]:
    """`staff` logged in on the browser the Next.js server knows by `key` (issue 71)."""
    client = abc.new_client()
    client.defaults.update({"HTTP_X_BROWSER_KEY": key, "HTTP_X_BROWSER_AGENT": agent})
    tokens = log_in(client, staff.email)
    use_access_token(client, tokens["access"])
    return client, tokens


def patch_me(client: APIClient, body: object) -> _MonkeyPatchedResponse:
    """Save some of my own details."""
    return client.patch(ME_URL, body, format="json")


def change_password(
    client: APIClient, current: str = TEST_PASSWORD, new: str = NEW_PASSWORD
) -> _MonkeyPatchedResponse:
    """Change my password."""
    return client.post(
        CHANGE_PW_URL, {"current_password": current, "new_password": new}, format="json"
    )


class TestMyDetails:
    def test_shows_my_details_and_choices(self, abc: ConsultancyContext, counsellor: Staff) -> None:
        response = abc.client.get(ME_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "name": counsellor.name,
            "mobile": "+9779812345670",
            "email": counsellor.email,
            "job_title": "Senior Counsellor",
            "role": "counsellor",
            "joined_on": counsellor.joined_on.isoformat(),
            "colour_mode": "device",
            "page_swipe": False,
            "notifications": {
                "task_given": True,
                "task_due_tomorrow": True,
                "morning_summary": False,
            },
        }

    def test_never_shows_anything_about_the_password(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        text = abc.client.get(ME_URL).content.decode()

        assert "password" not in text
        assert "auth_version" not in text

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_every_role_can_change_their_name_and_mobile(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        staff = abc.log_in_as(role)

        response = patch_me(abc.client, {"name": "Sita Sharma", "mobile": "981-234-5671"})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == "Sita Sharma"
        assert response.json()["mobile"] == "9812345671"
        saved = stored(abc, staff)
        assert (saved.name, saved.mobile) == ("Sita Sharma", "9812345671")

    @pytest.mark.parametrize(
        ("typed", "saved"),
        [
            ("sita sharma", "Sita Sharma"),
            ("  sita   kumari  sharma ", "Sita Kumari Sharma"),
            ("ram KC", "Ram KC"),
            ("o'neil mcdonald", "O'neil Mcdonald"),
        ],
    )
    def test_the_name_is_capitalised_on_save(
        self, abc: ConsultancyContext, counsellor: Staff, typed: str, saved: str
    ) -> None:
        response = patch_me(abc.client, {"name": typed})

        assert response.status_code == status.HTTP_200_OK
        assert stored(abc, counsellor).name == saved

    def test_the_mobile_can_be_cleared(self, abc: ConsultancyContext, counsellor: Staff) -> None:
        response = patch_me(abc.client, {"mobile": ""})

        assert response.status_code == status.HTTP_200_OK
        assert stored(abc, counsellor).mobile == ""

    def test_sending_nothing_changes_nothing(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        response = patch_me(abc.client, {})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == counsellor.name

    def test_changing_only_the_mobile_keeps_the_name(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        patch_me(abc.client, {"mobile": "9812345679"})

        assert stored(abc, counsellor).name == counsellor.name

    @pytest.mark.parametrize(
        "field",
        [
            {"role": "admin"},
            {"email": "boss@abcedu.test"},
            {"job_title": "Director"},
            {"joined_on": "2020-01-01"},
            {"status": "active"},
            {"auth_version": 0},
            {"id": 1},
        ],
    )
    def test_cant_change_own_role_email_job_title_or_anything_else_an_admin_sets(
        self, abc: ConsultancyContext, counsellor: Staff, field: dict[str, object]
    ) -> None:
        response = patch_me(abc.client, {"name": "Sita Sharma", **field})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == UNKNOWN_FIELD
        saved = stored(abc, counsellor)
        assert (saved.name, saved.role, saved.email, saved.job_title) == (
            counsellor.name,
            Role.COUNSELLOR,
            counsellor.email,
            "Senior Counsellor",
        )

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"name": ""}, "name"),
            ({"name": "   "}, "name"),
            ({"name": None}, "name"),
            ({"name": 123}, "name"),
            ({"name": ["Sita"]}, "name"),
            ({"name": "S" * 201}, "name"),
            ({"name": "Sita\nSharma"}, "name"),
            ({"mobile": "12345"}, "mobile"),
            ({"mobile": "98123456701234567"}, "mobile"),
            ({"mobile": 9812345670}, "mobile"),
            ({"mobile": "+977 98abc"}, "mobile"),
            ({"colour_mode": "blue"}, "colour_mode"),
            ({"colour_mode": True}, "colour_mode"),
            ({"notifications": {"task_given": "yes"}}, "notifications"),
            ({"notifications": {"task_given": 1}}, "notifications"),
            ({"notifications": {"task_given": None}}, "notifications"),
            ({"notifications": "all"}, "notifications"),
        ],
    )
    def test_refuses_bad_values(
        self, abc: ConsultancyContext, counsellor: Staff, body: dict[str, object], field: str
    ) -> None:
        response = patch_me(abc.client, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        assert stored(abc, counsellor).name == counsellor.name

    def test_refuses_an_unknown_notification_without_naming_it(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        response = patch_me(abc.client, {"notifications": {"<b>sms</b>": True}})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"notifications": UNKNOWN_FIELD}

    def test_refuses_a_body_that_isnt_an_object(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        response = patch_me(abc.client, ["Sita"])

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_stores_injection_shaped_names_as_plain_text(
        self, abc: ConsultancyContext, counsellor: Staff, text: str
    ) -> None:
        response = patch_me(abc.client, {"name": text})

        assert response.status_code == status.HTTP_200_OK
        assert stored(abc, counsellor).name == capitalise_name(text)
        assert response.json()["name"] == capitalise_name(text)

    def test_a_new_name_shows_in_the_context(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        patch_me(abc.client, {"name": "Sita Sharma"})

        assert abc.client.get(CONTEXT_URL).json()["staff"]["name"] == "Sita Sharma"

    def test_logs_ids_only(
        self, abc: ConsultancyContext, counsellor: Staff, json_logs: JsonLogs
    ) -> None:
        patch_me(abc.client, {"name": "Sita Sharma", "mobile": "9812345671"})

        assert f"My details saved: staff_id={counsellor.pk}" in json_logs.text()
        assert "Sita" not in json_logs.text()
        assert "9812345671" not in json_logs.text()


class TestNotifications:
    def test_saves_the_email_choices(self, abc: ConsultancyContext, counsellor: Staff) -> None:
        response = patch_me(
            abc.client, {"notifications": {"task_given": False, "morning_summary": True}}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["notifications"] == {
            "task_given": False,
            "task_due_tomorrow": True,
            "morning_summary": True,
        }
        assert abc.client.get(ME_URL).json()["notifications"]["morning_summary"] is True


class TestColourMode:
    @pytest.mark.parametrize("mode", ["light", "dark", "device"])
    def test_remembers_light_or_dark_per_person(
        self, abc: ConsultancyContext, counsellor: Staff, mode: str
    ) -> None:
        response = patch_me(abc.client, {"colour_mode": mode})

        assert response.status_code == status.HTTP_200_OK
        assert abc.client.get(CONTEXT_URL).json()["staff"]["colour_mode"] == mode

    def test_one_persons_choice_leaves_another_alone(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        other = abc.add_staff(Role.FRONT_DESK)

        patch_me(abc.client, {"colour_mode": "dark"})

        assert stored(abc, other).colour_mode == "device"

    def test_the_context_starts_with_the_device(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        assert abc.client.get(CONTEXT_URL).json()["staff"]["colour_mode"] == "device"


class TestChangePassword:
    def test_changes_it_and_logs_this_device_in_again(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        client, _ = device_client(abc, counsellor)

        response = change_password(client)

        assert response.status_code == status.HTTP_200_OK
        assert set(response.json()) == {"access", "refresh"}
        use_access_token(client, response.json()["access"])
        assert client.get(ME_URL).status_code == status.HTTP_200_OK
        assert stored(abc, counsellor).check_password(NEW_PASSWORD)
        assert not stored(abc, counsellor).check_password(TEST_PASSWORD)

    def test_the_new_password_logs_in_and_the_old_one_doesnt(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        change_password(abc.client)

        old = abc.new_client().post(
            LOGIN_URL, {"email": counsellor.email, "password": TEST_PASSWORD}, format="json"
        )
        assert old.status_code == status.HTTP_400_BAD_REQUEST
        log_in(abc.new_client(), counsellor.email, NEW_PASSWORD)

    def test_signs_other_devices_out_at_once(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        phone, phone_tokens = device_client(abc, counsellor)
        laptop, _ = device_client(abc, counsellor)

        assert change_password(laptop).status_code == status.HTTP_200_OK

        # The phone's access token has not expired, yet it stops working straight away.
        assert phone.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED
        refreshed = abc.new_client().post(
            REFRESH_URL, {"refresh": phone_tokens["refresh"]}, format="json"
        )
        assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED

    def test_this_devices_new_tokens_keep_it_as_the_same_device(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        laptop, _ = device_client(abc, counsellor)
        signed_in_at = laptop.get(DEVICES_URL).json()[0]["signed_in_at"]

        tokens = change_password(laptop).json()
        use_access_token(laptop, tokens["access"])

        devices = laptop.get(DEVICES_URL).json()
        # The fixture's own login was signed out, so it shows as logged out (issue 71).
        assert [device["logged_out_at"] is None for device in devices] == [True, False]
        assert devices[0]["is_current"] is True
        assert devices[0]["signed_in_at"] == signed_in_at

    def test_a_wrong_current_password_is_refused(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        phone, _ = device_client(abc, counsellor)

        response = change_password(abc.client, current="not-my-password")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == WRONG_CURRENT
        assert stored(abc, counsellor).check_password(TEST_PASSWORD)
        assert phone.get(ME_URL).status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        ("password", "unmet"),
        [
            ("short1!", ["min_length", "capital"]),
            ("alllowercase1!", ["capital"]),
            ("ALLUPPERCASE1!", ["small"]),
            ("NoNumbers!!", ["number"]),
            ("NoSymbols123", ["symbol"]),
        ],
    )
    def test_a_new_password_that_breaks_the_rules_is_refused(
        self, abc: ConsultancyContext, counsellor: Staff, password: str, unmet: list[str]
    ) -> None:
        response = change_password(abc.client, new=password)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["unmet_rules"] == unmet
        assert stored(abc, counsellor).check_password(TEST_PASSWORD)

    def test_the_rules_are_checked_before_the_current_password(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        response = change_password(abc.client, current="wrong", new="weak")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "unmet_rules" in response.json()

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"current_password": TEST_PASSWORD},
            {"new_password": NEW_PASSWORD},
            {"current_password": 123, "new_password": NEW_PASSWORD},
            {"current_password": TEST_PASSWORD, "new_password": None},
            {"current_password": TEST_PASSWORD, "new_password": "A1!" + "a" * 126},
            {"current_password": "x" * 129, "new_password": NEW_PASSWORD},
            {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD, "role": "admin"},
        ],
    )
    def test_refuses_missing_wrong_type_over_long_and_unknown_fields(
        self, abc: ConsultancyContext, counsellor: Staff, body: dict[str, object]
    ) -> None:
        response = abc.client.post(CHANGE_PW_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, counsellor).check_password(TEST_PASSWORD)
        assert stored(abc, counsellor).role == Role.COUNSELLOR

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_passwords_are_only_ever_hashed(
        self, abc: ConsultancyContext, counsellor: Staff, text: str
    ) -> None:
        password = f"Aa1!{text}"

        response = change_password(abc.client, new=password)

        assert response.status_code == status.HTTP_200_OK
        assert stored(abc, counsellor).check_password(password)

    def test_logs_ids_only(
        self, abc: ConsultancyContext, counsellor: Staff, json_logs: JsonLogs
    ) -> None:
        change_password(abc.client)
        change_password(abc.new_client(), current="wrong")

        text = json_logs.text()
        assert f"Password changed: staff_id={counsellor.pk}" in text
        assert NEW_PASSWORD not in text
        assert TEST_PASSWORD not in text


class TestSignedInDevices:
    def test_lists_each_device_this_browser_first(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        device_client(abc, counsellor)
        laptop, _ = device_client(abc, counsellor)

        devices = laptop.get(DEVICES_URL).json()

        # The fixture's own login, the phone and this laptop.
        assert len(devices) == 3
        assert [device["is_current"] for device in devices] == [True, False, False]
        assert set(devices[0]) == {
            "name",
            "signed_in_at",
            "last_used_at",
            "logged_out_at",
            "is_current",
        }

    def test_never_shows_tokens_or_ids(self, abc: ConsultancyContext, counsellor: Staff) -> None:
        laptop, tokens = browser_client(abc, counsellor, CHROME_KEY)

        text = laptop.get(DEVICES_URL).content.decode()

        assert tokens["refresh"] not in text
        assert CHROME_KEY not in text
        with tenant_context(abc.consultancy):
            for record in IssuedRefreshToken.objects.all():
                assert record.jti not in text
                assert record.device_id not in text

    def test_a_refresh_keeps_the_same_device(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        laptop, tokens = browser_client(abc, counsellor, CHROME_KEY)
        before = laptop.get(DEVICES_URL).json()

        renewed = abc.new_client().post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
        use_access_token(laptop, renewed.json()["access"])

        after = laptop.get(DEVICES_URL).json()
        assert len(after) == len(before)
        assert after[0]["is_current"] is True
        assert after[0]["signed_in_at"] == before[0]["signed_in_at"]
        assert after[0]["name"] == "Chrome on Windows"

    def test_shows_only_my_own_devices(self, abc: ConsultancyContext, counsellor: Staff) -> None:
        other = abc.add_staff(Role.FRONT_DESK)
        device_client(abc, other)

        assert len(abc.client.get(DEVICES_URL).json()) == 1


class TestDeviceNames:
    """Each device is named "browser on system" from what the browser says (issue 71)."""

    def test_names_the_device_from_the_browser_description(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        laptop, _ = browser_client(abc, counsellor, CHROME_KEY, agent=CHROME_WINDOWS)
        browser_client(abc, counsellor, PHONE_KEY, agent=SAFARI_IPHONE)

        devices = laptop.get(DEVICES_URL).json()

        assert [device["name"] for device in devices[:2]] == [
            "Chrome on Windows",
            "Safari on iOS",
        ]

    def test_a_login_without_a_description_is_unknown(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        devices = abc.client.get(DEVICES_URL).json()

        assert devices[0]["name"] == "Unknown browser on Unknown system"

    @pytest.mark.parametrize("agent", [*INJECTION_STRINGS, "Chrome/1 " + "x" * 5000])
    def test_odd_or_over_long_descriptions_never_reach_the_name(
        self, abc: ConsultancyContext, counsellor: Staff, agent: str
    ) -> None:
        laptop, _ = browser_client(abc, counsellor, CHROME_KEY, agent=agent)

        name = laptop.get(DEVICES_URL).json()[0]["name"]

        assert name in {"Unknown browser on Unknown system", "Chrome on Unknown system"}

    def test_the_description_is_ignored_without_the_internal_token(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        client = abc.new_client()
        client.defaults.pop("HTTP_X_INTERNAL_TOKEN")
        response = client.post(
            LOGIN_URL,
            {"email": counsellor.email, "password": TEST_PASSWORD},
            format="json",
            HTTP_X_BROWSER_KEY=CHROME_KEY,
            HTTP_X_BROWSER_AGENT=CHROME_WINDOWS,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestSameBrowser:
    """Logging in again on the same browser reuses its row (issue 71)."""

    def test_a_second_login_on_the_same_browser_reuses_its_row(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        browser_client(abc, counsellor, CHROME_KEY)
        again, _ = browser_client(abc, counsellor, CHROME_KEY)

        devices = again.get(DEVICES_URL).json()

        # The fixture's own login and this browser, once.
        assert len(devices) == 2
        assert devices[0]["is_current"] is True
        assert devices[0]["logged_out_at"] is None

    def test_the_older_login_on_that_browser_ends(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        _, first = browser_client(abc, counsellor, CHROME_KEY)
        browser_client(abc, counsellor, CHROME_KEY)

        refreshed = abc.new_client().post(REFRESH_URL, {"refresh": first["refresh"]}, format="json")

        assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED

    def test_another_browser_gets_its_own_row(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        browser_client(abc, counsellor, CHROME_KEY)
        phone, _ = browser_client(abc, counsellor, PHONE_KEY)

        assert len(phone.get(DEVICES_URL).json()) == 3

    def test_logins_without_a_browser_value_are_each_a_new_device(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        device_client(abc, counsellor)
        laptop, _ = device_client(abc, counsellor)

        assert len(laptop.get(DEVICES_URL).json()) == 3

    def test_a_malformed_browser_value_is_a_new_device(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        browser_client(abc, counsellor, "not-a-key")
        laptop, _ = browser_client(abc, counsellor, "not-a-key")

        assert len(laptop.get(DEVICES_URL).json()) == 3

    def test_two_people_on_one_browser_each_have_their_own_row(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        desk = abc.add_staff(Role.FRONT_DESK)
        browser_client(abc, counsellor, CHROME_KEY)
        desk_client, _ = browser_client(abc, desk, CHROME_KEY)
        mine, _ = browser_client(abc, counsellor, CHROME_KEY)

        assert len(desk_client.get(DEVICES_URL).json()) == 1
        assert len(mine.get(DEVICES_URL).json()) == 2

    def test_the_browser_value_is_never_stored_as_sent(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        browser_client(abc, counsellor, CHROME_KEY)

        with tenant_context(abc.consultancy):
            assert not IssuedRefreshToken.objects.filter(device_id__contains=CHROME_KEY).exists()


class TestLoggedOutDevices:
    """Every device still signed in shows, plus the 3 most recent logged-out ones (issue 71)."""

    def test_a_logged_out_device_shows_when_it_logged_out(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        _, phone_tokens = browser_client(abc, counsellor, PHONE_KEY, agent=SAFARI_IPHONE)
        abc.new_client().post(LOGOUT_URL, {"refresh": phone_tokens["refresh"]})

        devices = abc.client.get(DEVICES_URL).json()

        assert len(devices) == 2
        assert devices[0]["logged_out_at"] is None
        assert devices[1]["name"] == "Safari on iOS"
        assert devices[1]["logged_out_at"] is not None

    def test_an_expired_device_counts_as_logged_out(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        device_client(abc, counsellor)
        with tenant_context(abc.consultancy):
            newest = IssuedRefreshToken.objects.latest("id")
            newest.expires_at = timezone.now() - timedelta(seconds=1)
            newest.save()

        devices = abc.client.get(DEVICES_URL).json()

        assert devices[1]["logged_out_at"] == newest.expires_at.isoformat()

    def test_keeps_only_the_three_newest_logged_out_devices(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        for _ in range(5):
            _, tokens = device_client(abc, counsellor)
            abc.new_client().post(LOGOUT_URL, {"refresh": tokens["refresh"]})

        devices = abc.client.get(DEVICES_URL).json()

        assert len(devices) == 4
        logged_out = [device["logged_out_at"] for device in devices[1:]]
        assert None not in logged_out
        assert logged_out == sorted(logged_out, reverse=True)

    def test_older_logged_out_devices_are_removed(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        for _ in range(5):
            _, tokens = device_client(abc, counsellor)
            abc.new_client().post(LOGOUT_URL, {"refresh": tokens["refresh"]})

        with tenant_context(abc.consultancy):
            devices = set(
                IssuedRefreshToken.objects.filter(person_id=counsellor.pk).values_list(
                    "device_id", flat=True
                )
            )
        # This browser (the fixture's login) and the 3 newest logged-out devices.
        assert len(devices) == 4

    def test_every_device_still_signed_in_shows(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        for _ in range(5):
            device_client(abc, counsellor)

        devices = abc.client.get(DEVICES_URL).json()

        assert len(devices) == 6
        assert all(device["logged_out_at"] is None for device in devices)

    def test_signed_in_devices_come_before_logged_out_ones(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        _, tokens = device_client(abc, counsellor)
        abc.new_client().post(LOGOUT_URL, {"refresh": tokens["refresh"]})
        device_client(abc, counsellor)

        devices = abc.client.get(DEVICES_URL).json()

        assert [device["logged_out_at"] is None for device in devices] == [True, True, False]


class TestSignOutOtherDevices:
    def test_signs_every_other_device_out_at_once_and_keeps_this_one(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        phone, phone_tokens = device_client(abc, counsellor)
        laptop, _ = device_client(abc, counsellor)

        response = laptop.post(SIGN_OUT_OTHERS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert set(response.json()) == {"access", "refresh"}
        assert phone.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED
        refreshed = abc.new_client().post(
            REFRESH_URL, {"refresh": phone_tokens["refresh"]}, format="json"
        )
        assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED
        use_access_token(laptop, response.json()["access"])
        devices = laptop.get(DEVICES_URL).json()
        assert [device["is_current"] for device in devices] == [True, False, False]

    def test_the_other_devices_then_show_as_logged_out(
        self, abc: ConsultancyContext, counsellor: Staff
    ) -> None:
        browser_client(abc, counsellor, PHONE_KEY, agent=SAFARI_IPHONE)
        laptop, _ = browser_client(abc, counsellor, CHROME_KEY, agent=CHROME_WINDOWS)

        response = laptop.post(SIGN_OUT_OTHERS_URL)

        use_access_token(laptop, response.json()["access"])
        devices = laptop.get(DEVICES_URL).json()
        assert devices[0]["name"] == "Chrome on Windows"
        assert devices[0]["logged_out_at"] is None
        assert all(device["logged_out_at"] is not None for device in devices[1:])
        assert "Safari on iOS" in [device["name"] for device in devices[1:]]

    def test_the_password_still_works(self, abc: ConsultancyContext, counsellor: Staff) -> None:
        abc.client.post(SIGN_OUT_OTHERS_URL)

        log_in(abc.new_client(), counsellor.email)

    def test_refuses_a_body(self, abc: ConsultancyContext, counsellor: Staff) -> None:
        response = abc.client.post(SIGN_OUT_OTHERS_URL, {"everyone": True}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == UNKNOWN_FIELD

    def test_logs_ids_only(
        self, abc: ConsultancyContext, counsellor: Staff, json_logs: JsonLogs
    ) -> None:
        abc.client.post(SIGN_OUT_OTHERS_URL)

        assert f"Signed out of other devices: staff_id={counsellor.pk}" in json_logs.text()


class TestAccess:
    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("get", ME_URL),
            ("patch", ME_URL),
            ("post", CHANGE_PW_URL),
            ("get", DEVICES_URL),
            ("post", SIGN_OUT_OTHERS_URL),
        ],
    )
    def test_nobody_logged_in_is_401(self, abc: ConsultancyContext, method: str, url: str) -> None:
        response = getattr(abc.new_client(), method)(url, {}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("post", ME_URL),
            ("put", ME_URL),
            ("delete", ME_URL),
            ("get", CHANGE_PW_URL),
            ("patch", CHANGE_PW_URL),
            ("post", DEVICES_URL),
            ("delete", DEVICES_URL),
            ("get", SIGN_OUT_OTHERS_URL),
        ],
    )
    def test_only_the_methods_each_needs_are_allowed(
        self, abc: ConsultancyContext, counsellor: Staff, method: str, url: str
    ) -> None:
        response = getattr(abc.client, method)(url, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("url", [ME_URL, CHANGE_PW_URL, DEVICES_URL, SIGN_OUT_OTHERS_URL])
    def test_not_on_the_platform_host(self, platform_site: PlatformContext, url: str) -> None:
        platform_site.log_in()

        response = platform_site.client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND
