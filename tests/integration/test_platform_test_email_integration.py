"""The Platform's test email (spec 2026-11-18 stories 3-4; issue 05).

Platform → Settings shows whether email works ("working", "failed" or "untested") and has
"Send a test email", which emails the signed-in Platform Admin through the product's own SMTP
account. A failure answers with a reference to quote (the request's correlation ID), never the
mail server's words. Platform-only, and limited per Platform Admin.
"""

from __future__ import annotations

from datetime import timedelta
from email.utils import parseaddr
from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from django_tenants.utils import schema_context
from rest_framework import status

from emails.looks import PLATFORM_ACCENT, PRODUCT_NAME
from platform_admin.email_check import MAX_TESTS_PER_HOUR
from platform_admin.models import EmailTest
from staff.models import Role
from tenancy.subdomains import get_central_login_host
from tests.conftest import (
    PUBLIC_SCHEMA,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    use_access_token,
)

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

    from platform_admin.models import PlatformAdmin

STATUS_URL = "/api/platform/email/"
TEST_URL = "/api/platform/email/test/"
CORRELATION_ID = "5f0c2a7e-1b2d-4c3e-9f4a-7b6c5d4e3f2a"
FAILED_MESSAGE = "The test email couldn't be sent. Check the email values in the deploy."
LIMIT_MESSAGE = "Too many test emails. Try again in an hour."
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE platform_admin_emailtest; --",
    "}body{background:url(x)}",
]
UNTESTED = {"state": "untested", "tested_at": None, "reference": None}


def send_test(
    client: APIClient, body: object = None, correlation_id: str | None = None
) -> _MonkeyPatchedResponse:
    """Press "Send a test email" (with no body unless `body` is given)."""
    if correlation_id is not None:
        return client.post(TEST_URL, HTTP_X_CORRELATION_ID=correlation_id)
    if body is None:
        return client.post(TEST_URL)
    return client.post(TEST_URL, body, format="json")


def email_status(client: APIClient) -> Any:  # noqa: ANN401  # JSON
    """What Platform → Settings shows."""
    response = client.get(STATUS_URL)
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()


def stored_tests() -> list[EmailTest]:
    """Every stored test, oldest first."""
    with schema_context(PUBLIC_SCHEMA):
        return list(EmailTest.objects.order_by("pk"))


@pytest.fixture
def ops(platform_site: PlatformContext) -> PlatformAdmin:
    """A Platform Admin logged in on the Platform's client."""
    return platform_site.log_in()


class TestStatus:
    def test_not_tested_yet(
        self, platform_site: PlatformContext, ops: PlatformAdmin, settings: Settings
    ) -> None:
        assert email_status(platform_site.client) == {
            "email": UNTESTED,
            "from_address": settings.DEFAULT_FROM_EMAIL,
            "product_name": PRODUCT_NAME,
            "sent_to": str(ops.email),
        }

    def test_every_platform_admin_sees_the_last_test(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        send_test(platform_site.client)
        other = platform_site.new_client()
        use_access_token(
            other, platform_site.tokens_for(platform_site.add_admin("mina@platform.test")).access
        )

        shown = email_status(other)

        assert shown["email"]["state"] == "working"
        assert shown["sent_to"] == "mina@platform.test"


class TestSending:
    def test_it_emails_the_signed_in_platform_admin_in_the_platforms_look(
        self, platform_site: PlatformContext, ops: PlatformAdmin, settings: Settings
    ) -> None:
        response = send_test(platform_site.client)

        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["email"]["state"] == "working"
        [sent] = mail.outbox
        assert isinstance(sent, EmailMultiAlternatives)
        assert sent.to == [str(ops.email)]
        assert parseaddr(sent.from_email) == (PRODUCT_NAME, settings.DEFAULT_FROM_EMAIL)
        assert sent.reply_to == []
        assert sent.subject == f"Test email from {PRODUCT_NAME}"
        [(html, _)] = sent.alternatives
        assert PLATFORM_ACCENT in str(html)
        assert "Hello Platform Ops," in str(sent.body)

    def test_a_sent_test_shows_as_working_with_its_time(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        before = timezone.now()

        answer = send_test(platform_site.client).json()

        assert answer == email_status(platform_site.client)
        shown = answer["email"]
        assert shown["state"] == "working"
        assert shown["reference"] is None
        assert shown["tested_at"] >= before.isoformat().replace("+00:00", "Z")

    def test_a_failure_gives_a_reference_and_never_the_servers_words(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        settings: Settings,
        json_logs: JsonLogs,
    ) -> None:
        settings.EMAIL_BACKEND = "tests.fakes.BrokenEmailBackend"

        response = send_test(platform_site.client, correlation_id=CORRELATION_ID)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["email"]["state"] == "failed"
        assert body["email"]["reference"] == CORRELATION_ID
        assert body["detail"] == FAILED_MESSAGE
        text = response.content.decode()
        assert "550" not in text
        assert "refused" not in text.lower()
        assert email_status(platform_site.client)["email"]["state"] == "failed"
        [line] = json_logs.with_message("Platform test email failed")
        assert line["level"] == "WARNING"
        assert line["correlation_id"] == CORRELATION_ID
        assert str(ops.email) not in json_logs.text()

    def test_a_later_success_shows_as_working_again(
        self, platform_site: PlatformContext, ops: PlatformAdmin, settings: Settings
    ) -> None:
        settings.EMAIL_BACKEND = "tests.fakes.BrokenEmailBackend"
        send_test(platform_site.client)
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

        send_test(platform_site.client)

        assert email_status(platform_site.client)["email"]["state"] == "working"

    def test_logs_hold_ids_only(
        self, platform_site: PlatformContext, ops: PlatformAdmin, json_logs: JsonLogs
    ) -> None:
        send_test(platform_site.client)

        line = json_logs.only(f"Platform test email sent: platform_admin_id={ops.pk}")
        assert line["level"] == "INFO"
        assert str(ops.email) not in json_logs.text()


class TestLimit:
    def test_each_platform_admin_has_a_limit_per_hour(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        for _ in range(MAX_TESTS_PER_HOUR):
            assert send_test(platform_site.client).status_code == status.HTTP_200_OK

        refused = send_test(platform_site.client)

        assert refused.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert refused.json() == {"detail": LIMIT_MESSAGE}
        assert len(mail.outbox) == MAX_TESTS_PER_HOUR

    def test_another_platform_admin_has_their_own_limit(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        for _ in range(MAX_TESTS_PER_HOUR):
            send_test(platform_site.client)
        other = platform_site.new_client()
        use_access_token(
            other, platform_site.tokens_for(platform_site.add_admin("mina@platform.test")).access
        )

        assert send_test(other).status_code == status.HTTP_200_OK

    def test_the_limit_ends_after_an_hour_and_old_tests_are_cleared(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        for _ in range(MAX_TESTS_PER_HOUR):
            send_test(platform_site.client)
        with schema_context(PUBLIC_SCHEMA):
            EmailTest.objects.update(tested_at=timezone.now() - timedelta(hours=2))

        assert send_test(platform_site.client).status_code == status.HTTP_200_OK
        # Only the newest old one is kept (for the status), plus the new one.
        assert len(stored_tests()) == 2


class TestRefused:
    def test_logged_out_is_refused(self, platform_site: PlatformContext) -> None:
        client = platform_site.new_client()

        assert send_test(client).status_code == status.HTTP_401_UNAUTHORIZED
        assert client.get(STATUS_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert mail.outbox == []

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_staff_of_any_role_are_refused(
        self, platform_site: PlatformContext, make_consultancy: ConsultancyFactory, role: Role
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        access = abc.tokens_for(abc.log_in_as(role)).access
        on_platform = platform_site.new_client()
        use_access_token(on_platform, access)

        for client in (abc.client, on_platform):
            assert send_test(client).status_code in {401, 403, 404}
            assert client.get(STATUS_URL).status_code in {401, 403, 404}
        assert mail.outbox == []
        assert stored_tests() == []

    def test_an_office_or_the_central_login_host_has_none_of_it(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        make_consultancy: ConsultancyFactory,
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        token = platform_site.tokens_for(ops).access
        for client in (abc.new_client(), api_client(get_central_login_host())):
            use_access_token(client, token)
            assert send_test(client).status_code in {401, 404}
            assert client.get(STATUS_URL).status_code in {401, 404}
        assert mail.outbox == []

    @pytest.mark.parametrize(
        "body",
        [
            {"to": "someone@evil.example"},
            {"email": "someone@evil.example"},
            *({"note": value} for value in INJECTION_STRINGS),
            {"to": "x" * 5000},
            ["someone@evil.example"],
            [],
            "",
            "someone@evil.example",
            42,
        ],
    )
    def test_anything_sent_with_it_is_refused(
        self, platform_site: PlatformContext, ops: PlatformAdmin, body: object
    ) -> None:
        response = send_test(platform_site.client, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert mail.outbox == []
        assert stored_tests() == []

    def test_an_empty_object_is_fine(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        assert send_test(platform_site.client, {}).status_code == status.HTTP_200_OK
        assert mail.outbox[0].to == [str(ops.email)]

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_only_the_methods_needed_are_allowed(
        self, platform_site: PlatformContext, ops: PlatformAdmin, method: str
    ) -> None:
        client = platform_site.client
        assert getattr(client, method)(TEST_URL).status_code == 405
        assert getattr(client, method)(STATUS_URL).status_code == 405
        assert client.get(TEST_URL).status_code == 405
        assert client.post(STATUS_URL).status_code == 405
