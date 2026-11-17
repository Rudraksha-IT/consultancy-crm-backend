"""A new employee joins: work email and the emailed Code, then their own password (spec 18b, 18c).

Steps: Enter your code -> Choose a password (issue 07). Finishing logs them in.
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

STAFF_URL = "/api/staff/"
JOIN_URL = "/api/auth/join/"
CODE_URL = "/api/auth/join/code/"
COMPLETE_URL = "/api/auth/join/complete/"
LOGIN_URL = "/api/auth/login/"
CONTEXT_URL = "/api/context/"
EMAIL = "sita@abcedu.test"
NEW_PASSWORD = "Kathmandu!2026"  # noqa: S105  # test-only credential
SENT_ANSWER = {"detail": "If that email has an invite here, we've sent it a new 6-digit code."}
CODE_REFUSED = {
    "detail": "That code isn't right or has run out. Check the email, or send yourself a new one."
}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def invited(abc: ConsultancyContext) -> int:
    """Invite Sita as a Counsellor and return her id; her Code is the last email."""
    admin_client = abc.new_client()
    use_access_token(admin_client, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)
    body = {
        "name": "Sita Sharma",
        "mobile": "9812345670",
        "email": EMAIL,
        "job_title": "Counsellor",
        "role": "counsellor",
        "joined_on": "2026-11-01",
    }
    response = admin_client.post(STAFF_URL, body, format="json")
    assert response.status_code == status.HTTP_201_CREATED
    staff_id: int = response.json()["id"]
    return staff_id


def post(client: APIClient, url: str, body: object) -> _MonkeyPatchedResponse:
    """POST `body` as JSON."""
    return client.post(url, body, format="json")


def emailed_code(index: int = -1) -> str:
    """The 6-digit Code in an email sent during the test (the last one by default)."""
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[index].body))
    assert found is not None
    return found.group(1)


def wrong(code: str) -> str:
    """A different 6-digit Code."""
    return f"{(int(code) + 1) % 1_000_000:06d}"


def complete(
    consultancy: ConsultancyContext, code: str, password: object = NEW_PASSWORD, email: str = EMAIL
) -> _MonkeyPatchedResponse:
    """Submit the last step: the person's own password."""
    return post(
        consultancy.client, COMPLETE_URL, {"email": email, "code": code, "password": password}
    )


def stored(consultancy: ConsultancyContext, staff_id: int) -> Staff:
    """The Staff member as stored."""
    with tenant_context(consultancy.consultancy):
        return Staff.objects.get(pk=staff_id)


@pytest.mark.usefixtures("invited")
class TestCheckCode:
    def test_the_right_code_is_accepted_without_using_it_up(self, abc: ConsultancyContext) -> None:
        code = emailed_code()

        first = post(abc.client, CODE_URL, {"email": EMAIL, "code": code})
        again = post(abc.client, CODE_URL, {"email": " Sita@ABCedu.test ", "code": code})

        assert first.status_code == status.HTTP_200_OK
        assert again.status_code == status.HTTP_200_OK

    def test_a_wrong_code_is_refused(self, abc: ConsultancyContext) -> None:
        response = post(abc.client, CODE_URL, {"email": EMAIL, "code": wrong(emailed_code())})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CODE_REFUSED

    def test_an_unknown_email_gets_the_same_refusal(self, abc: ConsultancyContext) -> None:
        response = post(abc.client, CODE_URL, {"email": "x@abcedu.test", "code": emailed_code()})

        assert response.json() == CODE_REFUSED

    def test_too_many_wrong_codes_end_the_code(self, abc: ConsultancyContext) -> None:
        code = emailed_code()
        for _ in range(MAX_ATTEMPTS):
            post(abc.client, CODE_URL, {"email": EMAIL, "code": wrong(code)})

        response = post(abc.client, CODE_URL, {"email": EMAIL, "code": code})

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestJoin:
    def test_joining_activates_the_account_and_logs_them_in(
        self, abc: ConsultancyContext, invited: int
    ) -> None:
        response = complete(abc, emailed_code())

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert set(body) == {"access", "refresh", "context"}
        assert body["context"]["staff"]["email"] == EMAIL
        assert body["context"]["staff"]["role"] == "counsellor"
        assert stored(abc, invited).status == StaffStatus.ACTIVE

    def test_joining_names_the_device_from_the_browser(
        self, abc: ConsultancyContext, invited: int
    ) -> None:
        """Issue 71: joining is a login, so its device is named too."""
        abc.client.defaults["HTTP_X_BROWSER_AGENT"] = (
            "Mozilla/5.0 (X11; Linux x86_64) Firefox/132.0"
        )

        body = complete(abc, emailed_code()).json()

        use_access_token(abc.client, body["access"])
        assert abc.client.get("/api/me/devices/").json()[0]["name"] == "Firefox on Linux"

    def test_they_can_then_log_in_with_their_password(
        self, abc: ConsultancyContext, invited: int
    ) -> None:
        complete(abc, emailed_code())

        login = post(abc.new_client(), LOGIN_URL, {"email": EMAIL, "password": NEW_PASSWORD})

        assert login.status_code == status.HTTP_200_OK

    def test_the_code_works_only_once(self, abc: ConsultancyContext, invited: int) -> None:
        code = emailed_code()
        complete(abc, code)

        response = complete(abc, code, password="Pokhara!2027")  # noqa: S106

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CODE_REFUSED

    def test_a_reset_code_cannot_be_used_to_join(
        self, abc: ConsultancyContext, invited: int
    ) -> None:
        post(abc.client, "/api/auth/password-reset/", {"email": EMAIL})

        response = post(abc.client, CODE_URL, {"email": EMAIL, "code": wrong(emailed_code())})

        assert len(mail.outbox) == 1  # An invited person gets no reset Code.
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_weak_password_returns_the_unmet_rules_and_keeps_the_code(
        self, abc: ConsultancyContext, invited: int
    ) -> None:
        code = emailed_code()

        weak = complete(abc, code, password="kathmandu")  # noqa: S106

        assert weak.status_code == status.HTTP_400_BAD_REQUEST
        assert weak.json() == {
            "detail": "The password doesn't meet every rule yet.",
            "unmet_rules": ["capital", "number", "symbol"],
        }
        assert stored(abc, invited).status == StaffStatus.INVITED
        assert complete(abc, code).status_code == status.HTTP_200_OK

    def test_people_who_already_joined_cannot_join_again(self, abc: ConsultancyContext) -> None:
        active = abc.add_staff(Role.COUNSELLOR, email=EMAIL)

        sent = post(abc.client, JOIN_URL, {"email": active.email})
        refused = complete(abc, "123456")

        assert sent.json() == SENT_ANSWER
        assert mail.outbox == []
        assert refused.json() == CODE_REFUSED
        login = post(abc.new_client(), LOGIN_URL, {"email": EMAIL, "password": TEST_PASSWORD})
        assert login.status_code == status.HTTP_200_OK

    def test_a_cancelled_invite_cannot_be_used(self, abc: ConsultancyContext, invited: int) -> None:
        code = emailed_code()
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=invited).delete()

        assert complete(abc, code).json() == CODE_REFUSED

    def test_joining_is_logged_with_ids_only(
        self, abc: ConsultancyContext, invited: int, json_logs: JsonLogs
    ) -> None:
        code = emailed_code()

        complete(abc, code)

        json_logs.only(f"Employee joined: staff_id={invited}")
        assert "sita" not in json_logs.text().lower()
        assert code not in json_logs.text()
        assert NEW_PASSWORD not in json_logs.text()


@pytest.mark.usefixtures("invited")
class TestSendNewCode:
    def test_send_me_a_new_code_replaces_the_old_one(self, abc: ConsultancyContext) -> None:
        first = emailed_code()

        response = post(abc.client, JOIN_URL, {"email": EMAIL})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SENT_ANSWER
        assert len(mail.outbox) == 2
        second = emailed_code()
        assert mail.outbox[-1].subject == "Your code to join ABC Education"
        if first != second:
            assert post(abc.client, CODE_URL, {"email": EMAIL, "code": first}).status_code == 400

    def test_too_many_requests_get_the_same_answer_and_no_more_email(
        self, abc: ConsultancyContext
    ) -> None:
        for _ in range(MAX_CODES_PER_HOUR - 1):
            post(abc.client, JOIN_URL, {"email": EMAIL})

        response = post(abc.client, JOIN_URL, {"email": EMAIL})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SENT_ANSWER
        assert len(mail.outbox) == MAX_CODES_PER_HOUR

    def test_an_unknown_email_gets_the_same_answer_and_no_email(
        self, abc: ConsultancyContext
    ) -> None:
        mail.outbox.clear()

        response = post(abc.client, JOIN_URL, {"email": "nobody@abcedu.test"})

        assert response.json() == SENT_ANSWER
        assert mail.outbox == []


class TestRefusedInput:
    @pytest.mark.parametrize("url", [JOIN_URL, CODE_URL, COMPLETE_URL])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, url: str) -> None:
        for method in ("get", "put", "patch", "delete"):
            response = getattr(abc.client, method)(url)
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"email": EMAIL},
            {"email": EMAIL, "code": 123456},
            {"email": EMAIL, "code": "12345"},
            {"email": EMAIL, "code": "\uff11\uff12\uff13\uff14\uff15\uff16"},
            {"email": "<script>alert(1)</script>", "code": "123456"},
            {"email": "'; DROP TABLE staff_staff; --", "code": "123456"},
            {"email": EMAIL, "code": "123456", "password": 12345678},
            {"email": EMAIL, "code": "123456", "password": "A1!a" * 40},
            {"email": EMAIL, "code": "123456", "password": NEW_PASSWORD, "role": "admin"},
            [],
        ],
    )
    def test_bad_bodies_are_refused(
        self, abc: ConsultancyContext, invited: int, body: object
    ) -> None:
        response = post(abc.client, COMPLETE_URL, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, invited).status == StaffStatus.INVITED
        assert stored(abc, invited).role == Role.COUNSELLOR

    def test_a_password_with_markup_is_stored_only_as_a_hash(
        self, abc: ConsultancyContext, invited: int
    ) -> None:
        password = "<script>A1!</script>"  # noqa: S105

        complete(abc, emailed_code(), password=password)

        staff = stored(abc, invited)
        assert password not in staff.password
        assert staff.check_password(password)

    def test_the_platform_host_has_no_join(self, platform_site: PlatformContext) -> None:
        response = post(platform_site.client, COMPLETE_URL, {"email": EMAIL})

        assert response.status_code == status.HTTP_404_NOT_FOUND
