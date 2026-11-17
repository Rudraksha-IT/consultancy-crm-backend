"""A Staff member who forgot their password resets it with a 6-digit Code sent by email.

Steps: Your email -> Enter your code -> Choose a password (spec 16, 18c; issue 08).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from django.core import mail
from django_tenants.utils import tenant_context
from rest_framework import status
from rest_framework.test import APIClient

from codes.services import MAX_ATTEMPTS, MAX_CODES_PER_HOUR
from staff.models import Role, Staff, StaffStatus
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

REQUEST_URL = "/api/auth/password-reset/"
CODE_URL = "/api/auth/password-reset/code/"
COMPLETE_URL = "/api/auth/password-reset/complete/"
LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/refresh/"
CONTEXT_URL = "/api/context/"
NEW_PASSWORD = "Kathmandu!2026"  # noqa: S105  # test-only credential
SENT_ANSWER = {"detail": "If that email belongs to someone here, we've sent them a 6-digit code."}
CODE_REFUSED = {
    "detail": "That code isn't right or has run out. Check the email, or send yourself a new one."
}
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE staff_staff; --",
    "}body{background:url(x)}",
    "123456<b>",
]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def post(client: APIClient, url: str, body: object) -> _MonkeyPatchedResponse:
    """POST `body` as JSON."""
    return client.post(url, body, format="json")


def emailed_code(index: int = -1) -> str:
    """The 6-digit Code in an email sent during the test (the last one by default)."""
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[index].body))
    assert found is not None
    return found.group(1)


def request_code(consultancy: ConsultancyContext, email: str) -> str:
    """Ask for a reset Code and return the one emailed."""
    response = post(consultancy.client, REQUEST_URL, {"email": email})
    assert response.status_code == status.HTTP_202_ACCEPTED
    return emailed_code()


def complete(
    consultancy: ConsultancyContext, email: str, code: str, password: object = NEW_PASSWORD
) -> _MonkeyPatchedResponse:
    """Submit the last step: the new password."""
    return post(
        consultancy.client, COMPLETE_URL, {"email": email, "code": code, "password": password}
    )


def wrong(code: str) -> str:
    """A different 6-digit Code."""
    return f"{(int(code) + 1) % 1_000_000:06d}"


def deactivate(consultancy: ConsultancyContext, staff: Staff) -> None:
    """Mark a Staff member inactive."""
    with tenant_context(consultancy.consultancy):
        Staff.objects.filter(pk=staff.pk).update(status=StaffStatus.DEACTIVATED)


class TestRequestCode:
    def test_a_code_is_emailed_to_the_staff_member(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")

        response = post(abc.client, REQUEST_URL, {"email": staff.email})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SENT_ANSWER
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ["sita@abcedu.test"]
        assert "ABC Education" in message.subject
        assert "30 minutes" in message.body
        assert re.fullmatch(r"\d{6}", emailed_code())

    def test_the_email_is_matched_regardless_of_case(self, abc: ConsultancyContext) -> None:
        abc.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")

        post(abc.client, REQUEST_URL, {"email": " Sita@ABCedu.test "})

        assert mail.outbox[0].to == ["sita@abcedu.test"]

    def test_an_unknown_email_gets_the_same_answer_and_no_email(
        self, abc: ConsultancyContext
    ) -> None:
        response = post(abc.client, REQUEST_URL, {"email": "nobody@abcedu.test"})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SENT_ANSWER
        assert mail.outbox == []

    def test_inactive_staff_get_the_same_answer_and_no_email(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        deactivate(abc, staff)

        response = post(abc.client, REQUEST_URL, {"email": staff.email})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SENT_ANSWER
        assert mail.outbox == []

    def test_too_many_requests_get_the_same_answer_and_no_more_email(
        self, abc: ConsultancyContext
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        for _ in range(MAX_CODES_PER_HOUR):
            post(abc.client, REQUEST_URL, {"email": staff.email})

        response = post(abc.client, REQUEST_URL, {"email": staff.email})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SENT_ANSWER
        assert len(mail.outbox) == MAX_CODES_PER_HOUR

    def test_send_me_a_new_code_replaces_the_old_one(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        old = request_code(abc, staff.email)
        new = request_code(abc, staff.email)
        if new == old:
            new = request_code(abc, staff.email)

        assert post(abc.client, CODE_URL, {"email": staff.email, "code": old}).json() == (
            CODE_REFUSED
        )
        assert (
            post(abc.client, CODE_URL, {"email": staff.email, "code": new}).status_code
            == status.HTTP_200_OK
        )

    def test_the_code_and_email_never_reach_the_logs(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")

        code = request_code(abc, staff.email)

        assert code not in json_logs.text()
        assert "sita@abcedu.test" not in json_logs.text()
        assert json_logs.with_message(f"Password reset code sent: staff_id={staff.pk}")


class TestCheckCode:
    def test_the_right_code_is_accepted(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)

        response = post(abc.client, CODE_URL, {"email": staff.email, "code": code})

        assert response.status_code == status.HTTP_200_OK

    def test_checking_does_not_use_up_the_code(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)
        post(abc.client, CODE_URL, {"email": staff.email, "code": code})

        assert complete(abc, staff.email, code).status_code == status.HTTP_200_OK

    def test_a_wrong_code_is_refused(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)

        response = post(abc.client, CODE_URL, {"email": staff.email, "code": wrong(code)})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CODE_REFUSED

    def test_an_unknown_email_gets_the_same_refusal(self, abc: ConsultancyContext) -> None:
        response = post(abc.client, CODE_URL, {"email": "nobody@abcedu.test", "code": "123456"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CODE_REFUSED

    def test_too_many_wrong_codes_end_the_code(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)
        for _ in range(MAX_ATTEMPTS):
            post(abc.client, CODE_URL, {"email": staff.email, "code": wrong(code)})

        response = post(abc.client, CODE_URL, {"email": staff.email, "code": code})

        assert response.json() == CODE_REFUSED


class TestChoosePassword:
    def test_the_new_password_logs_the_staff_member_in(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)

        response = complete(abc, staff.email, code)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert set(body) == {"access", "refresh", "context"}
        assert body["context"]["staff"]["email"] == staff.email
        use_access_token(abc.client, body["access"])
        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_200_OK

    def test_the_new_password_names_the_device_from_the_browser(
        self, abc: ConsultancyContext
    ) -> None:
        """Issue 71: a reset by Code is a login, so its device is named too."""
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)
        abc.client.defaults["HTTP_X_BROWSER_AGENT"] = (
            "Mozilla/5.0 (X11; Linux x86_64) Firefox/132.0"
        )

        body = complete(abc, staff.email, code).json()

        use_access_token(abc.client, body["access"])
        assert abc.client.get("/api/me/devices/").json()[0]["name"] == "Firefox on Linux"

    def test_the_new_password_works_and_the_old_one_does_not(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        complete(abc, staff.email, request_code(abc, staff.email))
        client = abc.new_client()

        old = post(client, LOGIN_URL, {"email": staff.email, "password": TEST_PASSWORD})
        new = post(client, LOGIN_URL, {"email": staff.email, "password": NEW_PASSWORD})

        assert old.status_code == status.HTTP_400_BAD_REQUEST
        assert new.status_code == status.HTTP_200_OK

    def test_every_other_login_ends_at_once(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        before = abc.tokens_for(staff)
        complete(abc, staff.email, request_code(abc, staff.email))
        client = abc.new_client()
        use_access_token(client, before.access)

        assert client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert (
            post(abc.new_client(), REFRESH_URL, {"refresh": before.refresh}).status_code
            == status.HTTP_401_UNAUTHORIZED
        )

    def test_the_code_works_only_once(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)
        complete(abc, staff.email, code)

        response = complete(abc, staff.email, code, password="Another!2027")  # noqa: S106

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CODE_REFUSED

    def test_a_wrong_code_changes_nothing(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)

        response = complete(abc, staff.email, wrong(code))

        assert response.json() == CODE_REFUSED
        login = post(abc.new_client(), LOGIN_URL, {"email": staff.email, "password": TEST_PASSWORD})
        assert login.status_code == status.HTTP_200_OK

    def test_a_weak_password_returns_the_unmet_rules_and_keeps_the_code(
        self, abc: ConsultancyContext
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)

        response = complete(abc, staff.email, code, password="kathmandu")  # noqa: S106

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": "The password doesn't meet every rule yet.",
            "unmet_rules": ["capital", "number", "symbol"],
        }
        assert complete(abc, staff.email, code).status_code == status.HTTP_200_OK

    def test_inactive_staff_cannot_reset(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)
        deactivate(abc, staff)

        response = complete(abc, staff.email, code)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CODE_REFUSED
        assert post(abc.client, CODE_URL, {"email": staff.email, "code": code}).json() == (
            CODE_REFUSED
        )

    def test_the_reset_is_logged_with_ids_only(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)
        json_logs.clear()

        complete(abc, staff.email, code)

        assert json_logs.with_message(f"Password reset: staff_id={staff.pk}")
        assert code not in json_logs.text()
        assert NEW_PASSWORD not in json_logs.text()
        assert staff.email not in json_logs.text()


class TestRefusedInput:
    @pytest.mark.parametrize("url", [REQUEST_URL, CODE_URL, COMPLETE_URL])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, url: str) -> None:
        for method in ("get", "put", "patch", "delete"):
            response = getattr(abc.client, method)(url)
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"email": None},
            {"email": 12345},
            {"email": ["a@b.test"]},
            {"email": "not-an-email"},
            {"email": f"{'a' * 250}@b.test"},
            {"email": "sita@abcedu.test", "role": "admin"},
            [],
            "sita@abcedu.test",
        ],
    )
    def test_bad_request_bodies_are_refused(self, abc: ConsultancyContext, body: object) -> None:
        abc.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")

        response = post(abc.client, REQUEST_URL, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert mail.outbox == []

    @pytest.mark.parametrize(
        "code",
        [
            123456,
            None,
            "",
            "12345",
            "1234567",
            "12345a",
            " 123456",
            "123 456",
            "١٢٣٤٥٦",
            *INJECTION_STRINGS,
        ],
    )
    def test_codes_that_are_not_six_digits_are_refused(
        self, abc: ConsultancyContext, code: object
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        request_code(abc, staff.email)

        for url, extra in ((CODE_URL, {}), (COMPLETE_URL, {"password": NEW_PASSWORD})):
            response = post(abc.client, url, {"email": staff.email, "code": code, **extra})
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "code" in response.json()

    @pytest.mark.parametrize("email", INJECTION_STRINGS)
    def test_injection_shaped_emails_are_refused(self, abc: ConsultancyContext, email: str) -> None:
        bodies = {
            REQUEST_URL: {"email": email},
            CODE_URL: {"email": email, "code": "123456"},
            COMPLETE_URL: {"email": email, "code": "123456", "password": NEW_PASSWORD},
        }
        for url, body in bodies.items():
            response = post(abc.client, url, body)
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "email" in response.json()

    @pytest.mark.parametrize("password", [None, 12345678, ["Kathmandu!2026"], "A1!" + "a" * 126])
    def test_bad_passwords_are_refused_and_keep_the_code(
        self, abc: ConsultancyContext, password: object
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)

        response = complete(abc, staff.email, code, password=password)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert complete(abc, staff.email, code).status_code == status.HTTP_200_OK

    def test_unknown_fields_are_refused(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        code = request_code(abc, staff.email)
        body = {"email": staff.email, "code": code, "password": NEW_PASSWORD, "is_active": True}

        response = post(abc.client, COMPLETE_URL, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert complete(abc, staff.email, code).status_code == status.HTTP_200_OK

    def test_a_password_with_markup_is_stored_only_as_a_hash(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        password = "<script>A1!</script>"  # noqa: S105

        response = complete(abc, staff.email, request_code(abc, staff.email), password=password)

        assert response.status_code == status.HTTP_200_OK
        with tenant_context(abc.consultancy):
            stored = Staff.objects.get(pk=staff.pk).password
        assert password not in stored

    def test_the_platform_host_has_no_password_reset(self, platform_site: PlatformContext) -> None:
        response = post(platform_site.client, REQUEST_URL, {"email": "ops@platform.test"})

        assert response.status_code == status.HTTP_404_NOT_FOUND
