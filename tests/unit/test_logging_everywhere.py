"""Every part of the backend logs the same way (issue 58).

Calls to outside services, the slow-request limit, gunicorn's own log, and the redaction of
personal data in messages and tracebacks.
"""

from __future__ import annotations

import io
import logging
import re
import smtplib
from collections.abc import Sequence
from datetime import timedelta
from importlib import import_module
from typing import ClassVar

import pytest
from botocore.stub import Stubber
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend
from gunicorn.config import Config
from gunicorn.glogging import Logger
from pytest_django.fixtures import Settings

from communication.exceptions import SendFailedError
from communication.models import SmsProvider
from communication.providers import HttpSmsGateway, SmtpEmailGateway
from communication.types import EmailAccount, SmsAccount
from config import gunicorn as gunicorn_settings
from config.logging import build_logging, read_slow_request_ms
from config.logging_utils import redact
from config.outside_calls import outside_call
from documents.exceptions import DocumentStorageError
from documents.storage import S3DocumentStorage
from tests.conftest import JsonLogs, restore_logging

SMS_TOKEN = "v2_secret-token"  # noqa: S105  # test-only credential
SPARROW = SmsAccount(provider=SmsProvider.SPARROW, sender="ACME", token=SMS_TOKEN)
MAIL_PASSWORD = "mail-password-x9"  # noqa: S105  # test-only credential
MAILBOX = EmailAccount(
    host="smtp.example.com",
    port=587,
    username="info@office.example",
    password=MAIL_PASSWORD,
    from_address="info@office.example",
    from_name="ABC Education",
)
RECIPIENT = "sita@example.com"
MOBILE = "9841000001"
TEXT = "Namaste, your visa is ready"
AWS_SECRET = "test-secret-key-never-logged"  # noqa: S105  # test-only credential
DONE = re.compile(r"outside call done: provider=(\w+) operation=(\w+) duration_ms=\d+")
FAILED = re.compile(r"outside call failed: provider=(\w+) operation=(\w+) duration_ms=\d+")


def _matching(logs: JsonLogs, pattern: re.Pattern[str]) -> list[dict[str, object]]:
    return [entry for entry in logs.entries() if pattern.fullmatch(str(entry["message"]))]


def _no_secrets(logs: JsonLogs, *secrets: str) -> None:
    text = logs.text()
    for secret in secrets:
        assert secret not in text


class TestOutsideCall:
    def test_success_is_info_with_provider_operation_and_duration(
        self, json_logs: JsonLogs
    ) -> None:
        with outside_call("s3", "get_object"):
            pass

        [entry] = _matching(json_logs, DONE)
        assert entry["level"] == "INFO"
        assert DONE.fullmatch(str(entry["message"])).groups() == ("s3", "get_object")  # type: ignore[union-attr]

    def test_failure_is_one_error_with_the_traceback_and_re_raised(
        self, json_logs: JsonLogs
    ) -> None:
        with pytest.raises(TimeoutError), outside_call("sparrow", "send_sms"):
            raise TimeoutError

        [entry] = json_logs.entries()
        assert entry["level"] == "ERROR"
        assert FAILED.fullmatch(str(entry["message"]))
        assert "TimeoutError" in str(entry["exception"])
        assert "test_logging_everywhere.py" in str(entry["exception"])


class TestS3:
    @pytest.fixture
    def storage(self, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> S3DocumentStorage:
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", AWS_SECRET)
        settings.DOCUMENTS_BUCKET = "crm-documents-test"
        settings.DOCUMENTS_S3_ENDPOINT_URL = "http://minio:9000"
        return S3DocumentStorage()

    def test_a_failed_upload_is_one_error_naming_s3_without_credentials(
        self, storage: S3DocumentStorage, json_logs: JsonLogs
    ) -> None:
        with Stubber(storage._client) as stubbed:
            stubbed.add_client_error("put_object", "AccessDenied")
            with pytest.raises(DocumentStorageError):
                storage.save("abc/documents/1/x.pdf", io.BytesIO(b"x"), "application/pdf")

        [entry] = _matching(json_logs, FAILED)
        assert entry["level"] == "ERROR"
        assert FAILED.fullmatch(str(entry["message"])).groups() == ("s3", "put_object")  # type: ignore[union-attr]
        assert "AccessDenied" in str(entry["exception"])
        _no_secrets(json_logs, AWS_SECRET, "test-access-key")

    def test_a_delete_is_info(self, storage: S3DocumentStorage, json_logs: JsonLogs) -> None:
        with Stubber(storage._client) as stubbed:
            stubbed.add_response("delete_object", {})
            storage.delete("abc/office-logo/1.png")

        [entry] = _matching(json_logs, DONE)
        assert DONE.fullmatch(str(entry["message"])).groups() == ("s3", "delete_object")  # type: ignore[union-attr]


class TestSms:
    def test_no_answer_is_one_error_without_token_text_or_number(self, json_logs: JsonLogs) -> None:
        def unreachable(*_: object) -> tuple[int, bytes]:
            raise TimeoutError

        with pytest.raises(SendFailedError):
            HttpSmsGateway(unreachable).send(SPARROW, MOBILE, TEXT)

        [entry] = _matching(json_logs, FAILED)
        assert FAILED.fullmatch(str(entry["message"])).groups() == ("sparrow", "send_sms")  # type: ignore[union-attr]
        assert "TimeoutError" in str(entry["exception"])
        _no_secrets(json_logs, SMS_TOKEN, MOBILE, TEXT)

    def test_a_refusal_is_an_error_too(self, json_logs: JsonLogs) -> None:
        def refusing(*_: object) -> tuple[int, bytes]:
            return 200, b'{"response_code": 1002, "response": "Invalid token v2_secret-token"}'

        with pytest.raises(SendFailedError):
            HttpSmsGateway(refusing).send(SPARROW, MOBILE, TEXT)

        [entry] = _matching(json_logs, FAILED)
        assert entry["level"] == "ERROR"
        _no_secrets(json_logs, SMS_TOKEN, MOBILE, TEXT)

    def test_a_sent_sms_is_info(self, json_logs: JsonLogs) -> None:
        HttpSmsGateway(lambda *_: (200, b'{"response_code": 200}')).send(SPARROW, MOBILE, TEXT)

        [entry] = _matching(json_logs, DONE)
        assert entry["level"] == "INFO"
        _no_secrets(json_logs, SMS_TOKEN, MOBILE, TEXT)


class RefusingBackend(BaseEmailBackend):
    """An email server that refuses the recipient, naming them and the password."""

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        raise smtplib.SMTPRecipientsRefused({RECIPIENT: (550, MAIL_PASSWORD.encode())})


class TestEmail:
    def test_a_refused_email_is_one_error_without_password_or_recipient(
        self, json_logs: JsonLogs
    ) -> None:
        gateway = SmtpEmailGateway(f"{__name__}.RefusingBackend")

        with pytest.raises(SendFailedError):
            gateway.send(MAILBOX, RECIPIENT, "A subject", TEXT)

        [entry] = _matching(json_logs, FAILED)
        assert FAILED.fullmatch(str(entry["message"])).groups() == ("smtp", "send_email")  # type: ignore[union-attr]
        assert "SMTPRecipientsRefused" in str(entry["exception"])
        _no_secrets(json_logs, RECIPIENT, TEXT, "A subject")

    def test_a_sent_email_is_info(self, json_logs: JsonLogs) -> None:
        SmtpEmailGateway("django.core.mail.backends.locmem.EmailBackend").send(
            MAILBOX, RECIPIENT, "A subject", TEXT
        )

        assert len(mail.outbox) == 1
        [entry] = _matching(json_logs, DONE)
        assert DONE.fullmatch(str(entry["message"])).groups() == ("smtp", "send_email")  # type: ignore[union-attr]
        _no_secrets(json_logs, RECIPIENT, TEXT, MAIL_PASSWORD)


class TestRedaction:
    @pytest.mark.parametrize(
        "personal",
        [
            "sita.sharma+visa@acme-education.com.np",
            "9841000001",
            "+977-9841000001",
            "9779801234567",
        ],
    )
    def test_emails_and_mobile_numbers_are_blanked(self, personal: str) -> None:
        assert personal not in redact(f"refused {{'{personal}': (550, b'no')}} id=12")
        assert "id=12" in redact(f"refused {personal} id=12")

    def test_ids_and_correlation_ids_are_kept(self) -> None:
        text = "student_id=9841 at 2026-11-05 ref 3fa85f64-5717-4562-b3fc-2c9984100001"

        assert redact(text) == text

    def test_a_traceback_is_blanked_too(self, json_logs: JsonLogs) -> None:
        try:
            raise ValueError(RECIPIENT)
        except ValueError:
            logging.getLogger("test").exception("could not send: kind=%s", "email")

        [entry] = json_logs.entries()
        assert "ValueError" in str(entry["exception"])
        assert RECIPIENT not in json_logs.text()


class TestSlowRequestSetting:
    def test_default_is_one_second(self) -> None:
        assert read_slow_request_ms({}) == 1000

    def test_a_whole_number_is_read(self) -> None:
        assert read_slow_request_ms({"LOG_SLOW_REQUEST_MS": "250"}) == 250

    @pytest.mark.parametrize("value", ["", "0", "-5", "1.5", "abc", "600001", "1e3"])
    def test_anything_else_refuses_to_start(self, value: str) -> None:
        with pytest.raises(ImproperlyConfigured, match="LOG_SLOW_REQUEST_MS"):
            read_slow_request_ms({"LOG_SLOW_REQUEST_MS": value})


_ELAPSED = timedelta(milliseconds=12)
_ENVIRON = {
    "REMOTE_ADDR": "172.19.0.6",
    "REQUEST_METHOD": "GET",
    "RAW_URI": "/api/context/",
    "SERVER_PROTOCOL": "HTTP/1.1",
    "QUERY_STRING": "",
}


class _Response:
    """What gunicorn's access log reads from an answer."""

    status = "200 OK"
    sent = 11
    headers: ClassVar[list[tuple[str, str]]] = []


class _Request:
    """What gunicorn's access log reads from a request."""

    headers: ClassVar[list[tuple[str, str]]] = []
    method = "GET"
    path = "/api/context/"
    query = ""
    version = (1, 1)
    uri = "/api/context/"


class TestGunicorn:
    def test_its_log_config_is_ours(self) -> None:
        assert gunicorn_settings.logconfig_dict == build_logging("INFO", "json")

    def test_a_worker_timeout_is_a_json_line_and_no_access_line_is_written(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config = Config()
        config.set("logconfig_dict", build_logging("INFO", "json"))
        gunicorn_logger = Logger(config)

        gunicorn_logger.critical("WORKER TIMEOUT (pid:%s)", 42)

        gunicorn_logger.access(_Response(), _Request(), _ENVIRON, _ELAPSED)
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 1
        assert "172.19.0.6" not in lines[0]
        line = lines[0]
        assert '"level": "CRITICAL"' in line
        assert '"message": "WORKER TIMEOUT (pid:42)"' in line
        restore_logging()


class TestEveryAppHasALogger:
    @pytest.mark.parametrize(
        "module",
        [
            "access.policy",
            "codes.services",
            "history.services",
            "home.services",
            "reports.services",
        ],
    )
    def test_it_logs_under_its_own_name(self, module: str) -> None:
        logger = import_module(module).logger

        assert isinstance(logger, logging.Logger)
        assert logger.name == module
