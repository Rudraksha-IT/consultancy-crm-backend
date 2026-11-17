"""Communication's pure pieces: sealed secrets, mobile numbers and the provider interface.

Issue 25. The SMS companies are never called: their requests are built and their answers read
by plain functions, and the HTTP post is handed in.
"""

from __future__ import annotations

import json
import smtplib
from collections.abc import Mapping, Sequence
from email.utils import parseaddr

import pytest
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend
from pytest_django.fixtures import Settings

from communication.exceptions import SendFailedError
from communication.models import SmsProvider
from communication.providers import (
    AAKASH_URL,
    EMAIL_REFUSED,
    EMAIL_UNREACHABLE,
    SMS_REFUSED,
    SMS_UNREACHABLE,
    SPARROW_URL,
    HttpSmsGateway,
    SmtpEmailGateway,
    email_gateway,
    nepali_mobile,
    sms_accepted,
    sms_gateway,
    sms_request,
)
from communication.sealing import seal, unseal
from communication.types import EmailAccount, SmsAccount
from tests.fakes import FakeEmailGateway, FakeSmsGateway

SPARROW_TOKEN = "v2_secret-token"  # noqa: S105  # test-only credential
AAKASH_TOKEN = "aakash-secret-token"  # noqa: S105  # test-only credential
SPARROW = SmsAccount(provider=SmsProvider.SPARROW, sender="ACME", token=SPARROW_TOKEN)
AAKASH = SmsAccount(provider=SmsProvider.AAKASH, sender="", token=AAKASH_TOKEN)
MAILBOX = EmailAccount(
    host="smtp.example.com",
    port=587,
    username="info@office.example",
    password="mail-password",  # noqa: S106  # test-only credential
    from_address="info@office.example",
    from_name="ABC Education",
)
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


class TestSealing:
    def test_a_sealed_secret_opens_to_what_was_sealed(self) -> None:
        assert unseal(seal("v2_secret-token")) == "v2_secret-token"

    def test_the_sealed_text_never_shows_the_secret(self) -> None:
        sealed = seal("v2_secret-token")

        assert "v2_secret-token" not in sealed
        assert seal("v2_secret-token") != sealed  # Each seal is different.

    def test_another_key_cannot_open_it(self, settings: Settings) -> None:
        sealed = seal("v2_secret-token")
        settings.PROVIDER_SECRETS_KEY = "another-key-of-at-least-thirty-two-characters"

        with pytest.raises(ValueError, match="can't be opened"):
            unseal(sealed)

    @pytest.mark.parametrize("key", ["", "too-short"])
    def test_a_missing_or_short_key_is_refused(self, settings: Settings, key: str) -> None:
        settings.PROVIDER_SECRETS_KEY = key

        with pytest.raises(ImproperlyConfigured):
            seal("v2_secret-token")


class TestNepaliMobile:
    @pytest.mark.parametrize(
        ("phone", "expected"),
        [
            ("9841000001", "9841000001"),
            ("9801234567", "9801234567"),
            ("9612345678", "9612345678"),
            ("+9779841000001", "9841000001"),
            ("9779841000001", "9841000001"),
            ("984-100 0001", "9841000001"),
        ],
    )
    def test_a_nepali_mobile_number_is_ten_digits(self, phone: str, expected: str) -> None:
        assert nepali_mobile(phone) == expected

    @pytest.mark.parametrize(
        "phone", ["", "014412345", "98410-2233", "98410000011", "8841000001", "+919841000001"]
    )
    def test_anything_else_is_not(self, phone: str) -> None:
        assert nepali_mobile(phone) is None


class TestSmsRequests:
    def test_sparrow_gets_its_token_sender_number_and_text(self) -> None:
        assert sms_request(SPARROW, "9841000001", "Namaste") == (
            SPARROW_URL,
            {"token": "v2_secret-token", "from": "ACME", "to": "9841000001", "text": "Namaste"},
        )

    def test_aakash_gets_its_token_number_and_text(self) -> None:
        assert sms_request(AAKASH, "9841000001", "Namaste") == (
            AAKASH_URL,
            {"auth_token": "aakash-secret-token", "to": "9841000001", "text": "Namaste"},
        )

    def test_both_addresses_are_https(self) -> None:
        assert SPARROW_URL.startswith("https://")
        assert AAKASH_URL.startswith("https://")

    @pytest.mark.parametrize(
        ("provider", "status", "body", "accepted"),
        [
            (SmsProvider.SPARROW, 200, {"response_code": 200, "count": 1}, True),
            (SmsProvider.SPARROW, 200, {"response_code": 1002}, False),
            (SmsProvider.SPARROW, 403, {"response_code": 1002}, False),
            (SmsProvider.AAKASH, 200, {"error": False, "message": "1 message sent"}, True),
            (SmsProvider.AAKASH, 200, {"error": True, "message": "Invalid token"}, False),
            (SmsProvider.AAKASH, 500, {"error": False}, False),
        ],
    )
    def test_their_answers_say_whether_it_was_sent(
        self, provider: SmsProvider, status: int, body: object, accepted: bool
    ) -> None:
        assert sms_accepted(provider, status, json.dumps(body).encode()) is accepted

    @pytest.mark.parametrize("body", [b"", b"<html>", b"[1]", b'"ok"', b"\xff"])
    def test_an_answer_that_isnt_their_json_means_not_sent(self, body: bytes) -> None:
        assert sms_accepted(SmsProvider.SPARROW, 200, body) is False
        assert sms_accepted(SmsProvider.AAKASH, 200, body) is False


class RecordingPost:
    """Stands in for the HTTP post: remembers each call and answers as told."""

    def __init__(
        self, status: int = 200, body: object = None, error: Exception | None = None
    ) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.status = status
        self.body = json.dumps(body if body is not None else {"response_code": 200}).encode()
        self.error = error

    def __call__(self, url: str, fields: Mapping[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, dict(fields)))
        if self.error is not None:
            raise self.error
        return self.status, self.body


class TestHttpSmsGateway:
    def test_it_posts_the_request_once(self) -> None:
        post = RecordingPost()

        HttpSmsGateway(post).send(SPARROW, "9841000001", "Namaste")

        assert post.calls == [(SPARROW_URL, sms_request(SPARROW, "9841000001", "Namaste")[1])]

    def test_a_refusal_fails_with_a_safe_reason(self) -> None:
        post = RecordingPost(body={"response_code": 1002, "response": "Invalid token v2_x"})

        with pytest.raises(SendFailedError) as failure:
            HttpSmsGateway(post).send(SPARROW, "9841000001", "Namaste")

        assert failure.value.reason == SMS_REFUSED

    def test_no_answer_fails_as_unreachable(self) -> None:
        post = RecordingPost(error=TimeoutError())

        with pytest.raises(SendFailedError) as failure:
            HttpSmsGateway(post).send(AAKASH, "9841000001", "Namaste")

        assert failure.value.reason == SMS_UNREACHABLE


class TestSmtpEmailGateway:
    def test_it_sends_from_the_offices_address(self) -> None:
        SmtpEmailGateway(LOCMEM).send(MAILBOX, "sita@example.com", "A subject", "Namaste")

        [sent] = mail.outbox
        assert sent.to == ["sita@example.com"]
        assert parseaddr(sent.from_email) == ("ABC Education", "info@office.example")
        assert (sent.subject, sent.body) == ("A subject", "Namaste")

    @pytest.mark.parametrize(
        ("backend", "reason"),
        [("UnreachableBackend", EMAIL_UNREACHABLE), ("RefusingBackend", EMAIL_REFUSED)],
    )
    def test_a_failure_gives_a_safe_reason(self, backend: str, reason: str) -> None:
        gateway = SmtpEmailGateway(f"{__name__}.{backend}")

        with pytest.raises(SendFailedError) as failure:
            gateway.send(MAILBOX, "sita@example.com", "A subject", "Namaste")

        assert failure.value.reason == reason


class UnreachableBackend(BaseEmailBackend):
    """An email server that can't be reached."""

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        raise ConnectionRefusedError


class RefusingBackend(BaseEmailBackend):
    """An email server that refuses the login."""

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Bad credentials for mail-password")


class TestChosenGateways:
    def test_the_settings_choose_the_gateways(self) -> None:
        # Tests swap in the fakes (tests/conftest.py).
        assert isinstance(sms_gateway(), FakeSmsGateway)
        assert isinstance(email_gateway(), FakeEmailGateway)
