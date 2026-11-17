"""A Platform Admin who forgot their password resets it with an emailed Code (issue 63).

Steps: Your email -> Enter your code -> Choose a password, on the Platform address only. The
same Code rules as Staff (issue 08); the Code is in the Platform scope and never mixes with an
office's.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from django.conf import settings
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from django_tenants.utils import schema_context
from pytest_django.fixtures import Settings
from rest_framework import status
from rest_framework.test import APIClient

from codes.services import MAX_ATTEMPTS, MAX_CODES_PER_HOUR
from config.responses import AnswerThenRun
from platform_admin.models import (
    PlatformAdmin,
    PlatformAdminChange,
    PlatformAdminCode,
    PlatformResetRequest,
)
from platform_admin.password_reset import MAX_REQUESTS_PER_EMAIL_PER_HOUR
from staff.models import Role
from tenancy.subdomains import get_central_login_host, get_platform_host
from tests.conftest import (
    PLATFORM_TEST_PASSWORD,
    PUBLIC_SCHEMA,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    use_access_token,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

REQUEST_URL = "/api/platform/auth/password-reset/"
VERIFY_URL = "/api/platform/auth/password-reset/verify/"
CONFIRM_URL = "/api/platform/auth/password-reset/confirm/"
LOGIN_URL = "/api/platform/auth/login/"
ME_URL = "/api/platform/me/"
REFRESH_URL = "/api/platform/auth/refresh/"
STAFF_CODE_URL = "/api/auth/password-reset/code/"
STAFF_REQUEST_URL = "/api/auth/password-reset/"
EMAIL = "ops@platform.test"
NEW_PASSWORD = "Kathmandu!2026"  # noqa: S105  # test-only credential
SENT_ANSWER = {"detail": "If that email belongs to someone here, we've sent them a 6-digit code."}
CODE_ACCEPTED = {"detail": "That code is right."}
CODE_REFUSED = {
    "detail": "That code isn't right or has run out. Check the email, or send yourself a new one."
}
CHANGED_ANSWER = {"detail": "Your password was changed. Log in with your new password."}
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE platform_admin_platformadmin; --",
    "}body{background:url(x)}",
    "123456<b>",
]


@pytest.fixture
def admin(platform_site: PlatformContext) -> PlatformAdmin:
    """The Platform Admin who forgot their password."""
    return platform_site.add_admin(email=EMAIL)


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """An office, to show its address never takes a Platform Code."""
    return make_consultancy("ABC Education", "abcedu")


def post(client: APIClient, url: str, body: object) -> _MonkeyPatchedResponse:
    """POST `body` as JSON."""
    return client.post(url, body, format="json")


def emailed_code(index: int = -1) -> str:
    """The 6-digit Code in an email sent during the test (the last one by default)."""
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[index].body))
    assert found is not None
    return found.group(1)


def request_code(site: PlatformContext, email: str = EMAIL) -> str:
    """Ask for a reset Code on the Platform and return the one emailed."""
    response = post(site.client, REQUEST_URL, {"email": email})
    assert response.status_code == status.HTTP_202_ACCEPTED
    return emailed_code()


def confirm(
    site: PlatformContext, code: str, new_password: object = NEW_PASSWORD, email: str = EMAIL
) -> _MonkeyPatchedResponse:
    """Submit the last step: the new password."""
    body = {"email": email, "code": code, "new_password": new_password}
    return post(site.client, CONFIRM_URL, body)


def log_in(client: APIClient, password: str, email: str = EMAIL) -> _MonkeyPatchedResponse:
    """Log in on the Platform."""
    return post(client, LOGIN_URL, {"email": email, "password": password})


def wrong(code: str) -> str:
    """A different 6-digit Code."""
    return f"{(int(code) + 1) % 1_000_000:06d}"


def stored(admin: PlatformAdmin) -> PlatformAdmin:
    """The Platform Admin as saved now."""
    with schema_context(PUBLIC_SCHEMA):
        return PlatformAdmin.objects.get(pk=admin.pk)


class TestRequestCode:
    def test_a_code_is_emailed_with_the_platform_address(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        response = post(platform_site.client, REQUEST_URL, {"email": admin.email})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SENT_ANSWER
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == [EMAIL]
        assert message.from_email == settings.DEFAULT_FROM_EMAIL
        assert "Platform" in message.subject
        assert get_platform_host() in message.body
        assert "30 minutes" in message.body
        assert re.fullmatch(r"\d{6}", emailed_code())

    def test_the_email_never_carries_a_link_with_the_code(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)

        body = str(mail.outbox[0].body)
        assert "http" not in body
        assert not re.search(rf"[/?=&]{code}", body)
        sent = mail.outbox[0]
        assert isinstance(sent, EmailMultiAlternatives)
        assert sent.alternatives == []

    def test_the_email_is_matched_regardless_of_case(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        post(platform_site.client, REQUEST_URL, {"email": " Ops@Platform.TEST "})

        assert mail.outbox[0].to == [EMAIL]

    def test_an_unknown_email_gets_the_same_answer_and_no_email(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        response = post(platform_site.client, REQUEST_URL, {"email": "nobody@platform.test"})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SENT_ANSWER
        assert mail.outbox == []

    def test_an_inactive_platform_admin_gets_the_same_answer_and_no_email(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        with schema_context(PUBLIC_SCHEMA):
            PlatformAdmin.objects.filter(pk=admin.pk).update(is_active=False)

        response = post(platform_site.client, REQUEST_URL, {"email": admin.email})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SENT_ANSWER
        assert mail.outbox == []

    def test_a_staff_email_gets_the_same_answer_and_no_email(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        staff = abc.add_staff(Role.ADMIN, email="sita@abcedu.test")

        response = post(platform_site.client, REQUEST_URL, {"email": staff.email})

        assert response.json() == SENT_ANSWER
        assert mail.outbox == []

    def test_the_email_is_sent_after_the_answer(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        response = post(platform_site.client, REQUEST_URL, {"email": EMAIL})

        # The lookup and the email run when the server closes the sent answer, so neither can
        # change the answer or how long it takes (the test client closes it too).
        assert isinstance(response, AnswerThenRun)
        assert len(mail.outbox) == 1

    def test_send_me_a_new_code_replaces_the_old_one(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        old = request_code(platform_site)
        new = request_code(platform_site)
        if new == old:
            new = request_code(platform_site)

        body = {"email": EMAIL, "code": old}
        assert post(platform_site.client, VERIFY_URL, body).json() == CODE_REFUSED
        body = {"email": EMAIL, "code": new}
        assert post(platform_site.client, VERIFY_URL, body).status_code == status.HTTP_200_OK

    def test_too_many_codes_for_one_person_get_the_same_answer_and_no_more_email(
        self,
        platform_site: PlatformContext,
        admin: PlatformAdmin,
        monkeypatch: pytest.MonkeyPatch,
        json_logs: JsonLogs,
    ) -> None:
        # The per-email limit normally stops first; lift it to reach the Code's own limit.
        monkeypatch.setattr(
            "platform_admin.password_reset.MAX_REQUESTS_PER_EMAIL_PER_HOUR", MAX_CODES_PER_HOUR + 1
        )
        for _ in range(MAX_CODES_PER_HOUR):
            request_code(platform_site)

        response = post(platform_site.client, REQUEST_URL, {"email": EMAIL})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SENT_ANSWER
        assert len(mail.outbox) == MAX_CODES_PER_HOUR
        assert json_logs.only(f"code limit reached: platform_admin_id={admin.pk} purpose=reset")

    def test_a_mail_failure_still_gets_the_same_answer_and_is_logged(
        self,
        platform_site: PlatformContext,
        admin: PlatformAdmin,
        settings: Settings,
        json_logs: JsonLogs,
    ) -> None:
        settings.EMAIL_BACKEND = "tests.fakes.BrokenEmailBackend"

        response = post(platform_site.client, REQUEST_URL, {"email": EMAIL})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SENT_ANSWER
        [failure] = json_logs.with_message("outside call failed: provider=email ")
        assert failure["level"] == "ERROR"
        [line] = json_logs.with_message("Platform password reset code not sent")
        assert line["level"] == "WARNING"
        assert "exception" in line
        assert EMAIL not in json_logs.text()


class TestRateLimitPerEmail:
    def test_requests_for_one_email_are_limited_by_its_hash(
        self, platform_site: PlatformContext, admin: PlatformAdmin, json_logs: JsonLogs
    ) -> None:
        assert MAX_REQUESTS_PER_EMAIL_PER_HOUR <= MAX_CODES_PER_HOUR
        for _ in range(MAX_REQUESTS_PER_EMAIL_PER_HOUR):
            post(platform_site.client, REQUEST_URL, {"email": "Ops@platform.test"})
        sent = len(mail.outbox)

        response = post(platform_site.client, REQUEST_URL, {"email": EMAIL})

        assert response.json() == SENT_ANSWER
        assert len(mail.outbox) == sent
        warning = json_logs.only("Platform password reset limit reached")
        assert warning["level"] == "WARNING"
        assert EMAIL not in json_logs.text()

    def test_unknown_emails_are_limited_too(
        self, platform_site: PlatformContext, json_logs: JsonLogs
    ) -> None:
        for _ in range(MAX_REQUESTS_PER_EMAIL_PER_HOUR + 1):
            post(platform_site.client, REQUEST_URL, {"email": "nobody@platform.test"})

        assert json_logs.with_message("Platform password reset limit reached")

    def test_each_email_has_its_own_limit(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        for _ in range(MAX_REQUESTS_PER_EMAIL_PER_HOUR):
            post(platform_site.client, REQUEST_URL, {"email": "nobody@platform.test"})

        request_code(platform_site)

    def test_the_limit_ends_after_an_hour(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        for _ in range(MAX_REQUESTS_PER_EMAIL_PER_HOUR):
            post(platform_site.client, REQUEST_URL, {"email": EMAIL})
        _age_everything(timedelta(hours=1, seconds=1))
        sent = len(mail.outbox)

        post(platform_site.client, REQUEST_URL, {"email": EMAIL})

        assert len(mail.outbox) == sent + 1

    def test_no_raw_email_is_stored(self, platform_site: PlatformContext) -> None:
        post(platform_site.client, REQUEST_URL, {"email": "nobody@platform.test"})

        with schema_context(PUBLIC_SCHEMA):
            rows = list(PlatformResetRequest.objects.values_list("email_hash", flat=True))
        assert len(rows) == 1
        assert "nobody" not in rows[0]
        assert re.fullmatch(r"[0-9a-f]{64}", rows[0])


def _age_everything(by: timedelta) -> None:
    """Pretend every reset request and Code was made `by` earlier."""
    with schema_context(PUBLIC_SCHEMA):
        for request in PlatformResetRequest.objects.all():
            PlatformResetRequest.objects.filter(pk=request.pk).update(
                requested_at=request.requested_at - by
            )
        for code in PlatformAdminCode.objects.all():
            PlatformAdminCode.objects.filter(pk=code.pk).update(
                created_at=code.created_at - by, expires_at=code.expires_at - by
            )


class TestCheckCode:
    def test_the_right_code_is_accepted_and_not_used_up(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)

        response = post(platform_site.client, VERIFY_URL, {"email": EMAIL, "code": code})

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == CODE_ACCEPTED
        assert confirm(platform_site, code).status_code == status.HTTP_200_OK

    def test_a_wrong_code_is_refused_and_logged_as_a_warning(
        self, platform_site: PlatformContext, admin: PlatformAdmin, json_logs: JsonLogs
    ) -> None:
        code = request_code(platform_site)

        response = post(platform_site.client, VERIFY_URL, {"email": EMAIL, "code": wrong(code)})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CODE_REFUSED
        line = json_logs.only("Platform password reset code refused")
        assert line["level"] == "WARNING"
        assert code not in json_logs.text()
        assert wrong(code) not in json_logs.text()

    def test_an_unknown_email_gets_the_same_refusal(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)

        body = {"email": "nobody@platform.test", "code": code}
        assert post(platform_site.client, VERIFY_URL, body).json() == CODE_REFUSED

    def test_an_expired_code_is_refused(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)
        _age_everything(timedelta(minutes=30, seconds=1))

        body = {"email": EMAIL, "code": code}
        assert post(platform_site.client, VERIFY_URL, body).json() == CODE_REFUSED

    def test_too_many_wrong_codes_end_the_code(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)
        for _ in range(MAX_ATTEMPTS):
            post(platform_site.client, VERIFY_URL, {"email": EMAIL, "code": wrong(code)})

        body = {"email": EMAIL, "code": code}
        assert post(platform_site.client, VERIFY_URL, body).json() == CODE_REFUSED
        assert confirm(platform_site, code).json() == CODE_REFUSED


class TestChoosePassword:
    def test_the_whole_flow_changes_the_password(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)
        assert (
            post(platform_site.client, VERIFY_URL, {"email": EMAIL, "code": code}).status_code
            == status.HTTP_200_OK
        )

        response = confirm(platform_site, code)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == CHANGED_ANSWER
        assert "access" not in response.json()
        client = platform_site.new_client()
        assert log_in(client, PLATFORM_TEST_PASSWORD).status_code == status.HTTP_400_BAD_REQUEST
        assert log_in(client, NEW_PASSWORD).status_code == status.HTTP_200_OK

    def test_existing_platform_sessions_are_signed_out(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        tokens = platform_site.tokens_for(admin)
        signed_in = platform_site.new_client()
        use_access_token(signed_in, tokens.access)
        assert signed_in.get(ME_URL).status_code == status.HTTP_200_OK

        confirm(platform_site, request_code(platform_site))

        assert signed_in.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED
        refreshed = post(platform_site.new_client(), REFRESH_URL, {"refresh": tokens.refresh})
        assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED

    def test_the_change_is_recorded_without_the_password(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        confirm(platform_site, request_code(platform_site))

        with schema_context(PUBLIC_SCHEMA):
            change = PlatformAdminChange.objects.filter(platform_admin=admin).last()
        assert change is not None
        assert change.fields == ["password"]
        assert change.by_id == admin.pk

    def test_the_code_works_only_once(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)
        confirm(platform_site, code)

        assert confirm(platform_site, code, "Pokhara!2027").json() == CODE_REFUSED

    def test_a_wrong_code_changes_nothing(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)

        response = confirm(platform_site, wrong(code))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CODE_REFUSED
        assert stored(admin).check_password(PLATFORM_TEST_PASSWORD)

    def test_a_weak_password_returns_the_unmet_rules_and_keeps_the_code(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)

        response = confirm(platform_site, code, "weakpass")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": "The password doesn't meet every rule yet.",
            "unmet_rules": ["capital", "number", "symbol"],
        }
        assert confirm(platform_site, code).status_code == status.HTTP_200_OK

    def test_an_inactive_platform_admin_cannot_reset(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)
        with schema_context(PUBLIC_SCHEMA):
            PlatformAdmin.objects.filter(pk=admin.pk).update(is_active=False)

        assert confirm(platform_site, code).json() == CODE_REFUSED
        assert stored(admin).check_password(PLATFORM_TEST_PASSWORD)

    def test_the_reset_is_logged_with_ids_only(
        self, platform_site: PlatformContext, admin: PlatformAdmin, json_logs: JsonLogs
    ) -> None:
        code = request_code(platform_site)
        sent = json_logs.only(f"Platform password reset code sent: platform_admin_id={admin.pk}")
        assert sent["level"] == "INFO"

        confirm(platform_site, code)

        done = json_logs.only(f"Platform password reset: platform_admin_id={admin.pk}")
        assert done["level"] == "INFO"
        for secret in (code, NEW_PASSWORD, EMAIL):
            assert secret not in json_logs.text()


class TestTwoStepLoginIsNeverSkipped:
    def test_after_a_reset_login_still_asks_for_the_number(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        with schema_context(PUBLIC_SCHEMA):
            PlatformAdmin.objects.filter(pk=admin.pk).update(two_factor_required=True)

        confirm(platform_site, request_code(platform_site))
        response = log_in(platform_site.new_client(), NEW_PASSWORD)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["two_factor"] == "setup"
        assert "access" not in response.json()
        assert "refresh" not in response.json()

    def test_with_the_app_set_up_login_asks_for_a_number(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        _set_up_two_factor(platform_site, admin)

        confirm(platform_site, request_code(platform_site))
        response = log_in(platform_site.new_client(), NEW_PASSWORD)

        assert response.json()["two_factor"] == "verify"
        assert "access" not in response.json()


def _set_up_two_factor(site: PlatformContext, admin: PlatformAdmin) -> None:
    """Switch two-step login on and set the app up, as the login would."""
    with schema_context(PUBLIC_SCHEMA):
        PlatformAdmin.objects.filter(pk=admin.pk).update(two_factor_required=True)
    client = site.new_client()
    challenge = log_in(client, PLATFORM_TEST_PASSWORD).json()["challenge"]
    setup = post(client, "/api/platform/auth/two-factor/setup/", {"challenge": challenge}).json()
    secret = parse_qs(urlparse(setup["uri"]).query)["secret"][0]
    body = {"challenge": challenge, "code": pyotp.TOTP(secret).now()}
    confirmed = post(client, "/api/platform/auth/two-factor/confirm/", body)
    assert confirmed.status_code == status.HTTP_200_OK, confirmed.json()


class TestCodeScopes:
    def test_the_reset_endpoints_do_not_exist_on_an_office_host(
        self, abc: ConsultancyContext, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)
        bodies = {
            REQUEST_URL: {"email": EMAIL},
            VERIFY_URL: {"email": EMAIL, "code": code},
            CONFIRM_URL: {"email": EMAIL, "code": code, "new_password": NEW_PASSWORD},
        }
        for url, body in bodies.items():
            assert post(abc.client, url, body).status_code == status.HTTP_404_NOT_FOUND

    def test_a_platform_code_is_refused_by_an_office_reset(
        self, abc: ConsultancyContext, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        abc.add_staff(Role.ADMIN, email=EMAIL)
        code = request_code(platform_site)

        response = post(abc.client, STAFF_CODE_URL, {"email": EMAIL, "code": code})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert (
            post(platform_site.client, VERIFY_URL, {"email": EMAIL, "code": code}).status_code
            == status.HTTP_200_OK
        )

    def test_a_platform_code_is_refused_on_the_central_login_host(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)
        central = api_client(get_central_login_host())

        for url in (VERIFY_URL, STAFF_CODE_URL):
            response = post(central, url, {"email": EMAIL, "code": code})
            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_office_reset_code_is_refused_on_the_platform(
        self, abc: ConsultancyContext, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        abc.add_staff(Role.ADMIN, email=EMAIL)
        assert (
            post(abc.client, STAFF_REQUEST_URL, {"email": EMAIL}).status_code
            == status.HTTP_202_ACCEPTED
        )
        office_code = emailed_code()

        assert post(
            platform_site.client, VERIFY_URL, {"email": EMAIL, "code": office_code}
        ).json() == (CODE_REFUSED)
        assert confirm(platform_site, office_code).json() == CODE_REFUSED
        assert stored(admin).check_password(PLATFORM_TEST_PASSWORD)


class TestRefusedInput:
    @pytest.mark.parametrize("url", [REQUEST_URL, VERIFY_URL, CONFIRM_URL])
    def test_only_post_is_allowed(self, platform_site: PlatformContext, url: str) -> None:
        for method in ("get", "put", "patch", "delete"):
            response = getattr(platform_site.client, method)(url)
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"email": None},
            {"email": 12345},
            {"email": [EMAIL]},
            {"email": "not-an-email"},
            {"email": f"{'a' * 250}@platform.test"},
            {"email": EMAIL, "is_active": True},
            [],
            EMAIL,
        ],
    )
    def test_bad_request_bodies_are_refused(
        self, platform_site: PlatformContext, admin: PlatformAdmin, body: object
    ) -> None:
        response = post(platform_site.client, REQUEST_URL, body)

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
            "abcdef",
            " 123456",
            "١٢٣٤٥٦",
            *INJECTION_STRINGS,
        ],
    )
    def test_codes_that_are_not_six_digits_are_refused(
        self, platform_site: PlatformContext, admin: PlatformAdmin, code: object
    ) -> None:
        request_code(platform_site)

        for url, extra in ((VERIFY_URL, {}), (CONFIRM_URL, {"new_password": NEW_PASSWORD})):
            response = post(platform_site.client, url, {"email": EMAIL, "code": code, **extra})
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "code" in response.json()

    @pytest.mark.parametrize("email", INJECTION_STRINGS)
    def test_injection_shaped_emails_are_refused(
        self, platform_site: PlatformContext, email: str
    ) -> None:
        bodies = {
            REQUEST_URL: {"email": email},
            VERIFY_URL: {"email": email, "code": "123456"},
            CONFIRM_URL: {"email": email, "code": "123456", "new_password": NEW_PASSWORD},
        }
        for url, body in bodies.items():
            response = post(platform_site.client, url, body)
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "email" in response.json()

    @pytest.mark.parametrize(
        "new_password", [None, 12345678, [NEW_PASSWORD], "A1!" + "a" * 126, ""]
    )
    def test_bad_passwords_are_refused_and_keep_the_code(
        self, platform_site: PlatformContext, admin: PlatformAdmin, new_password: object
    ) -> None:
        code = request_code(platform_site)

        response = confirm(platform_site, code, new_password)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert confirm(platform_site, code).status_code == status.HTTP_200_OK

    def test_missing_fields_are_refused(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)

        response = post(platform_site.client, CONFIRM_URL, {"email": EMAIL, "code": code})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "new_password" in response.json()

    def test_unknown_fields_are_refused(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)
        body = {"email": EMAIL, "code": code, "new_password": NEW_PASSWORD, "is_active": True}

        response = post(platform_site.client, CONFIRM_URL, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert confirm(platform_site, code).status_code == status.HTTP_200_OK

    def test_a_password_with_markup_is_stored_only_as_a_hash(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        password = "<script>A1!</script>"  # noqa: S105

        response = confirm(platform_site, request_code(platform_site), password)

        assert response.status_code == status.HTTP_200_OK
        assert password not in stored(admin).password

    def test_refusals_never_repeat_the_email_or_code(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        code = request_code(platform_site)

        response = confirm(platform_site, wrong(code), "weak")

        text = response.content.decode()
        for secret in (EMAIL, code, wrong(code), "weak"):
            assert secret not in text


def test_codes_expire_on_their_own_clock(platform_site: PlatformContext) -> None:
    """Sanity: the stored expiry is 30 minutes after it was made."""
    admin = platform_site.add_admin(email=EMAIL)
    request_code(platform_site)

    with schema_context(PUBLIC_SCHEMA):
        record = PlatformAdminCode.objects.get(platform_admin=admin)
    assert record.expires_at - record.created_at == timedelta(minutes=30)
    assert record.created_at <= timezone.now()
