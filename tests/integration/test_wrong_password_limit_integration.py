"""Wrong passwords are limited per email on the staff and Platform logins (security ticket 01).

After 10 wrong passwords for one email in 15 minutes, every try for that email is refused until
the 15 minutes end, even with the right password. The answer is the same for an email that
belongs to nobody. A right password below the limit clears the count; reset password still works.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.core import mail
from django.utils import timezone
from django_tenants.utils import schema_context, tenant_context
from rest_framework import status
from rest_framework.test import APIClient

from auth_tokens.models import WrongPasswordCount
from auth_tokens.password_tries import WRONG_PASSWORD_WINDOW, WRONG_PASSWORDS_ALLOWED
from staff.models import Role
from tests.conftest import (
    PLATFORM_TEST_PASSWORD,
    PUBLIC_SCHEMA,
    TEST_PASSWORD,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

LOGIN_URL = "/api/auth/login/"
PLATFORM_LOGIN_URL = "/api/platform/auth/login/"
RESET_REQUEST_URL = "/api/auth/password-reset/"
RESET_COMPLETE_URL = "/api/auth/password-reset/complete/"
PLATFORM_RESET_REQUEST_URL = "/api/platform/auth/password-reset/"
PLATFORM_RESET_CONFIRM_URL = "/api/platform/auth/password-reset/confirm/"
WRONG_PASSWORD = "not-the-password"  # noqa: S105  # test-only credential
NEW_PASSWORD = "Kathmandu!2026"  # noqa: S105  # test-only credential
INVALID = {"detail": "Invalid email or password."}
USED_UP = {"detail": "Too many wrong passwords. Try again in 15 minutes, or reset your password."}
WINDOW_SECONDS = int(WRONG_PASSWORD_WINDOW.total_seconds())


def log_in(
    client: APIClient, email: str, password: str, url: str = LOGIN_URL
) -> _MonkeyPatchedResponse:
    """Submit a login form."""
    return client.post(url, {"email": email, "password": password}, format="json")


def use_up(client: APIClient, email: str, url: str = LOGIN_URL) -> None:
    """Send exactly the allowed number of wrong passwords for `email`."""
    for _ in range(WRONG_PASSWORDS_ALLOWED):
        assert log_in(client, email, WRONG_PASSWORD, url).json() == INVALID


def assert_used_up(response: _MonkeyPatchedResponse) -> None:
    """The refusal: 429, the message, and how long to wait."""
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert response.json() == USED_UP
    assert 0 < int(response["Retry-After"]) <= WINDOW_SECONDS


def move_window_back(schema: str) -> None:
    """Make every count in `schema` start one window ago, as if 15 minutes had passed."""
    started = timezone.now() - WRONG_PASSWORD_WINDOW - timedelta(seconds=1)
    with schema_context(schema):
        WrongPasswordCount.objects.update(window_started_at=started)


def emailed_code() -> str:
    """The 6-digit Code in the last email sent."""
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[-1].body))
    assert found is not None
    return found.group(1)


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office every staff test logs in to."""
    return make_consultancy("ABC Education", "abcedu")


class TestStaffLogin:
    def test_the_right_password_is_refused_after_ten_wrong_ones(
        self, abc: ConsultancyContext
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        use_up(abc.client, staff.email)

        assert_used_up(log_in(abc.client, staff.email, TEST_PASSWORD))
        assert_used_up(log_in(abc.client, staff.email, WRONG_PASSWORD))

    def test_an_unknown_email_gets_the_same_answers(self, abc: ConsultancyContext) -> None:
        use_up(abc.client, "nobody@abcedu.test")

        assert_used_up(log_in(abc.client, "nobody@abcedu.test", WRONG_PASSWORD))

    def test_the_email_is_counted_regardless_of_case(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")
        use_up(abc.client, " Sita@ABCedu.TEST ")

        assert_used_up(log_in(abc.client, staff.email, TEST_PASSWORD))

    def test_a_right_password_at_nine_wrong_clears_the_count(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        for _ in range(WRONG_PASSWORDS_ALLOWED - 1):
            log_in(abc.client, staff.email, WRONG_PASSWORD)

        assert log_in(abc.client, staff.email, TEST_PASSWORD).status_code == status.HTTP_200_OK
        for _ in range(WRONG_PASSWORDS_ALLOWED - 1):
            assert log_in(abc.client, staff.email, WRONG_PASSWORD).json() == INVALID
        assert log_in(abc.client, staff.email, TEST_PASSWORD).status_code == status.HTTP_200_OK

    def test_the_right_password_works_once_the_window_ends(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        use_up(abc.client, staff.email)
        move_window_back(abc.consultancy.schema_name)

        assert log_in(abc.client, staff.email, TEST_PASSWORD).status_code == status.HTTP_200_OK

    def test_old_counts_are_deleted_when_read(self, abc: ConsultancyContext) -> None:
        log_in(abc.client, "old@abcedu.test", WRONG_PASSWORD)
        move_window_back(abc.consultancy.schema_name)

        log_in(abc.client, "new@abcedu.test", WRONG_PASSWORD)

        with tenant_context(abc.consultancy):
            assert WrongPasswordCount.objects.count() == 1

    def test_another_email_is_not_affected(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        use_up(abc.client, "nobody@abcedu.test")

        assert log_in(abc.client, staff.email, TEST_PASSWORD).status_code == status.HTTP_200_OK

    def test_the_count_never_holds_the_email(self, abc: ConsultancyContext) -> None:
        log_in(abc.client, "sita@abcedu.test", WRONG_PASSWORD)

        with tenant_context(abc.consultancy):
            row = WrongPasswordCount.objects.get()
        assert "sita" not in row.email_hash
        assert re.fullmatch(r"[0-9a-f]{64}", row.email_hash)
        assert row.wrong_count == 1

    def test_reset_password_still_works_and_the_new_password_logs_in(
        self, abc: ConsultancyContext
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")
        use_up(abc.client, staff.email)

        requested = abc.client.post(RESET_REQUEST_URL, {"email": staff.email}, format="json")
        assert requested.status_code == status.HTTP_202_ACCEPTED
        body = {"email": staff.email, "code": emailed_code(), "password": NEW_PASSWORD}
        completed = abc.client.post(RESET_COMPLETE_URL, body, format="json")
        assert completed.status_code == status.HTTP_200_OK

        assert log_in(abc.client, staff.email, NEW_PASSWORD).status_code == status.HTTP_200_OK

    def test_the_log_line_holds_no_email(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        use_up(abc.client, "sita@abcedu.test")

        log_in(abc.client, "sita@abcedu.test", TEST_PASSWORD)

        lines = json_logs.with_message("Password tries used up")
        assert [line["message"] for line in lines] == ["Password tries used up: scope=staff"]
        assert "sita" not in json_logs.text().lower()

    def test_refused_input_never_counts(self, abc: ConsultancyContext) -> None:
        for _ in range(WRONG_PASSWORDS_ALLOWED + 1):
            response = abc.client.post(
                LOGIN_URL, {"email": "<script>@x", "password": WRONG_PASSWORD}, format="json"
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST

        with tenant_context(abc.consultancy):
            assert not WrongPasswordCount.objects.exists()


class TestPlatformLogin:
    def test_the_right_password_is_refused_after_ten_wrong_ones(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()
        use_up(platform_site.client, admin.email, PLATFORM_LOGIN_URL)

        response = log_in(
            platform_site.client, admin.email, PLATFORM_TEST_PASSWORD, PLATFORM_LOGIN_URL
        )

        assert_used_up(response)

    def test_an_unknown_email_gets_the_same_answers(self, platform_site: PlatformContext) -> None:
        use_up(platform_site.client, "nobody@platform.test", PLATFORM_LOGIN_URL)

        response = log_in(
            platform_site.client, "nobody@platform.test", WRONG_PASSWORD, PLATFORM_LOGIN_URL
        )

        assert_used_up(response)

    def test_a_right_password_at_nine_wrong_clears_the_count(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()
        client = platform_site.client
        for _ in range(WRONG_PASSWORDS_ALLOWED - 1):
            log_in(client, admin.email, WRONG_PASSWORD, PLATFORM_LOGIN_URL)
        right = log_in(client, admin.email, PLATFORM_TEST_PASSWORD, PLATFORM_LOGIN_URL)
        assert right.status_code == status.HTTP_200_OK

        with schema_context(PUBLIC_SCHEMA):
            assert not WrongPasswordCount.objects.exists()

    def test_the_right_password_works_once_the_window_ends(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()
        use_up(platform_site.client, admin.email, PLATFORM_LOGIN_URL)
        move_window_back(PUBLIC_SCHEMA)

        response = log_in(
            platform_site.client, admin.email, PLATFORM_TEST_PASSWORD, PLATFORM_LOGIN_URL
        )

        assert response.status_code == status.HTTP_200_OK

    def test_reset_password_still_works_and_the_new_password_logs_in(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()
        client = platform_site.client
        use_up(client, admin.email, PLATFORM_LOGIN_URL)

        requested = client.post(PLATFORM_RESET_REQUEST_URL, {"email": admin.email}, format="json")
        assert requested.status_code == status.HTTP_202_ACCEPTED
        body = {"email": admin.email, "code": emailed_code(), "new_password": NEW_PASSWORD}
        confirmed = client.post(PLATFORM_RESET_CONFIRM_URL, body, format="json")
        assert confirmed.status_code == status.HTTP_200_OK

        response = log_in(client, admin.email, NEW_PASSWORD, PLATFORM_LOGIN_URL)
        assert response.status_code == status.HTTP_200_OK

    def test_the_log_line_holds_no_email(
        self, platform_site: PlatformContext, json_logs: JsonLogs
    ) -> None:
        use_up(platform_site.client, "ops@platform.test", PLATFORM_LOGIN_URL)

        log_in(platform_site.client, "ops@platform.test", WRONG_PASSWORD, PLATFORM_LOGIN_URL)

        lines = json_logs.with_message("Password tries used up")
        assert [line["message"] for line in lines] == ["Password tries used up: scope=platform"]
        assert "ops@platform" not in json_logs.text().lower()

    def test_an_office_count_never_reaches_the_platform(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        admin = platform_site.add_admin()
        use_up(abc.client, admin.email)

        response = log_in(
            platform_site.client, admin.email, PLATFORM_TEST_PASSWORD, PLATFORM_LOGIN_URL
        )

        assert response.status_code == status.HTTP_200_OK
