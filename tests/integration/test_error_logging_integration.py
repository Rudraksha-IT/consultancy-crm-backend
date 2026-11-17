"""Every failure can be traced by its correlation ID (issue 58).

Unexpected errors are logged at ERROR with their traceback and answered with a reference;
security refusals at WARNING with a reason code; validation errors at DEBUG with field names
only. Nothing secret or personal is ever written.
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command, get_commands, load_command_class
from django_tenants.management.commands.migrate_schemas import MigrateSchemasCommand
from django_tenants.utils import tenant_context
from pytest_django.fixtures import Settings
from rest_framework import status

from codes.exceptions import CodeLimitReachedError, InvalidCodeError
from codes.services import MAX_ATTEMPTS, MAX_CODES_PER_HOUR, issue_code, use_code
from codes.types import CodePurpose
from config.commands import LoggedCommand
from config.correlation import get_correlation_id
from modules.services import switch_modules
from modules.types import Module
from office_networks.models import OfficeNetwork
from staff.models import Role, StaffStatus
from tests.conftest import (
    TEST_INTERNAL_API_TOKEN,
    TEST_PASSWORD,
    ConsultancyFactory,
    JsonLogs,
    LogsAt,
    api_client,
    use_access_token,
)

CONTEXT_URL = "/api/context/"
CONSULTANCY_URL = "/api/consultancy/"
DATES_URL = "/api/configuration/dates/"
CLOCK_IN_URL = "/api/clock/in/"
CORRELATION_HEADER = "X-Correlation-ID"
SOMETHING_WENT_WRONG = "Something went wrong."
LEAKED = "token=leaked-value"  # test-only text that must never be logged
CLIENT_IP = "203.0.113.77"
EXCEPTION_SOURCE = re.compile(r'File ".*configuration/views\.py", line \d+, in put')


def _broken(*_: object) -> None:
    raise RuntimeError(LEAKED)


def _secrets_absent(logs: JsonLogs, *secrets: str) -> None:
    text = logs.text()
    for secret in (TEST_PASSWORD, TEST_INTERNAL_API_TOKEN, *secrets):
        assert secret not in text


class TestUnexpectedErrorInAView:
    def test_one_error_line_with_traceback_tenant_and_correlation_id(
        self,
        make_consultancy: ConsultancyFactory,
        json_logs: JsonLogs,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("configuration.views.set_date_display", _broken)
        acme = make_consultancy("Acme Education", "acme")
        admin = acme.log_in_as(Role.ADMIN)
        sent = str(uuid.uuid4())

        response = acme.client.put(
            DATES_URL, {"date_display": "BS"}, format="json", HTTP_X_CORRELATION_ID=sent
        )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.json() == {"detail": SOMETHING_WENT_WRONG, "reference": sent}
        errors = [entry for entry in json_logs.entries() if entry["level"] == "ERROR"]
        assert len(errors) == 1
        [error] = errors
        assert error["message"] == f"unexpected error: method=PUT path={DATES_URL}"
        assert error["tenant"] == acme.host
        assert error["correlation_id"] == sent
        assert EXCEPTION_SOURCE.search(str(error["exception"]))
        assert "RuntimeError" in str(error["exception"])
        _secrets_absent(json_logs, "leaked-value", admin.email, '"BS"')

    def test_the_answer_has_no_traceback_sql_or_settings(
        self,
        make_consultancy: ConsultancyFactory,
        json_logs: JsonLogs,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("configuration.views.set_date_display", _broken)
        acme = make_consultancy("Acme Education", "acme")
        acme.log_in_as(Role.ADMIN)

        response = acme.client.put(DATES_URL, {"date_display": "BS"}, format="json")

        body = response.content.decode()
        for leak in ("Traceback", "RuntimeError", "leaked", "SELECT", "settings", ".py"):
            assert leak not in body
        assert response.json()["reference"] == response[CORRELATION_HEADER]
        json_logs.request_line(f"PUT {DATES_URL} 500")


class TestUnexpectedErrorOutsideAView:
    def test_a_middleware_failure_is_logged_and_answered_with_a_reference(
        self,
        make_consultancy: ConsultancyFactory,
        json_logs: JsonLogs,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("tenancy.middleware.get_status", _broken)
        acme = make_consultancy("Acme Education", "acme")
        acme.client.raise_request_exception = False

        response = acme.client.get(CONSULTANCY_URL)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        reference = response[CORRELATION_HEADER]
        assert response.json() == {"detail": SOMETHING_WENT_WRONG, "reference": reference}
        errors = [entry for entry in json_logs.entries() if entry["level"] == "ERROR"]
        assert len(errors) == 1
        assert errors[0]["correlation_id"] == reference
        assert "tenancy/middleware.py" in str(errors[0]["exception"])
        assert "leaked-value" not in json_logs.text()


class TestSecurityRefusals:
    def test_a_token_from_another_consultancy_is_one_warning_without_the_token(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        beta = make_consultancy("Beta Education", "beta")
        access = acme.tokens_for(acme.add_staff(Role.ADMIN)).access
        use_access_token(beta.client, access)

        response = beta.client.get(CONTEXT_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        warnings = [entry for entry in json_logs.entries() if entry["level"] == "WARNING"]
        assert [entry["message"] for entry in warnings] == [
            "token refused: reason=wrong_consultancy"
        ]
        assert warnings[0]["tenant"] == beta.host
        _secrets_absent(json_logs, access, access.split(".")[2])

    def test_a_bad_internal_token_is_one_warning_without_the_token(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        wrong = "wrong-internal-token-0123456789abcdefghij"

        response = api_client(acme.host, HTTP_X_INTERNAL_TOKEN=wrong).get(CONTEXT_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        warning = json_logs.only("internal token refused")
        assert warning["level"] == "WARNING"
        assert wrong not in json_logs.text()

    def test_a_malformed_authorization_header_is_a_warning(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")

        acme.client.get(CONTEXT_URL, HTTP_AUTHORIZATION="Basic c2l0YTpwYXNz")

        assert json_logs.only("token refused: reason=malformed")["level"] == "WARNING"
        assert "c2l0YTpwYXNz" not in json_logs.text()

    def test_a_token_of_a_deactivated_staff_member_is_a_warning_from_the_view(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        counsellor = acme.add_staff(Role.COUNSELLOR)
        access = acme.tokens_for(counsellor).access
        with tenant_context(acme.consultancy):
            counsellor.status = StaffStatus.DEACTIVATED
            counsellor.is_active = False
            counsellor.save()
        use_access_token(acme.client, access)

        response = acme.client.get(CONTEXT_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert json_logs.only("token refused: reason=out_of_date")["level"] == "WARNING"
        _secrets_absent(json_logs, access)

    def test_an_expired_token_is_ordinary_and_logged_at_info(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs, settings: Settings
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        settings.JWT_ACCESS_TOKEN_LIFETIME = timedelta(seconds=-1)
        access = acme.tokens_for(acme.add_staff(Role.COUNSELLOR)).access
        use_access_token(acme.client, access)

        response = acme.client.get(CONTEXT_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert json_logs.only("token refused: reason=expired")["level"] == "INFO"
        _secrets_absent(json_logs, access)

    def test_nobody_logged_in_is_not_a_warning(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")

        acme.client.get(CONTEXT_URL)

        assert [e for e in json_logs.entries() if e["level"] != "INFO"] == []


class TestValidationErrors:
    def test_field_names_only_at_debug(
        self, make_consultancy: ConsultancyFactory, json_logs_at: LogsAt
    ) -> None:
        logs = json_logs_at("DEBUG")
        acme = make_consultancy("Acme Education", "acme")
        acme.log_in_as(Role.ADMIN)

        response = acme.client.put(
            DATES_URL, {"date_display": "<script>sita@acme.test</script>"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        entry = logs.only("validation failed: fields=date_display")
        assert entry["level"] == "DEBUG"
        assert "sita@acme.test" not in logs.text()
        assert "<script>" not in logs.text()

    def test_not_logged_at_info(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        acme.log_in_as(Role.ADMIN)

        acme.client.put(DATES_URL, {"date_display": "XX"}, format="json")

        assert json_logs.with_message("validation failed") == []
        json_logs.request_line(f"PUT {DATES_URL} 400")

    def test_a_body_that_is_not_an_object(
        self, make_consultancy: ConsultancyFactory, json_logs_at: LogsAt
    ) -> None:
        logs = json_logs_at("DEBUG")
        acme = make_consultancy("Acme Education", "acme")
        acme.log_in_as(Role.ADMIN)

        acme.client.put(DATES_URL, ["BS"], format="json")

        assert logs.only("validation failed: fields=non_field_errors")["level"] == "DEBUG"


class TestRequestLine:
    def test_it_carries_the_duration(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")

        acme.client.get(CONSULTANCY_URL)

        assert json_logs.request_line(f"GET {CONSULTANCY_URL} 200")["level"] == "INFO"

    def test_a_slow_request_is_a_warning(
        self,
        make_consultancy: ConsultancyFactory,
        json_logs: JsonLogs,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
    ) -> None:
        settings.LOG_SLOW_REQUEST_MS = 1000
        ticks = iter([10.0, 11.5])
        monkeypatch.setattr("config.middleware.monotonic", lambda: next(ticks))
        acme = make_consultancy("Acme Education", "acme")

        acme.client.get(CONSULTANCY_URL)

        entry = json_logs.only(f"GET {CONSULTANCY_URL} 200 1500ms")
        assert entry["level"] == "WARNING"

    def test_just_under_the_limit_stays_info(
        self,
        make_consultancy: ConsultancyFactory,
        json_logs: JsonLogs,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
    ) -> None:
        settings.LOG_SLOW_REQUEST_MS = 1000
        ticks = iter([10.0, 10.999])
        monkeypatch.setattr("config.middleware.monotonic", lambda: next(ticks))
        acme = make_consultancy("Acme Education", "acme")

        acme.client.get(CONSULTANCY_URL)

        assert json_logs.only(f"GET {CONSULTANCY_URL} 200 999ms")["level"] == "INFO"


class TestLevels:
    def test_warning_hides_info_lines(
        self, make_consultancy: ConsultancyFactory, json_logs_at: LogsAt
    ) -> None:
        logs = json_logs_at("WARNING")
        acme = make_consultancy("Acme Education", "acme")

        acme.client.get(CONSULTANCY_URL)
        api_client(acme.host, HTTP_X_INTERNAL_TOKEN="").get(CONSULTANCY_URL)

        assert [entry["level"] for entry in logs.entries()] == ["WARNING"]

    def test_debug_shows_debug_lines(
        self, make_consultancy: ConsultancyFactory, json_logs_at: LogsAt
    ) -> None:
        logs = json_logs_at("DEBUG")
        acme = make_consultancy("Acme Education", "acme")
        acme.log_in_as(Role.ADMIN)

        acme.client.put(DATES_URL, {}, format="json")

        assert "DEBUG" in {entry["level"] for entry in logs.entries()}


class TestClockInOutsideTheOffice:
    def test_a_warning_without_the_address(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        switch_modules(acme.consultancy, {Module.CLOCK_IN})
        with tenant_context(acme.consultancy):
            OfficeNetwork.objects.create(label="Main office wifi", network="110.44.115.0/24")
        acme.log_in_as(Role.COUNSELLOR)

        response = acme.client.post(CLOCK_IN_URL, {}, format="json", HTTP_X_CLIENT_IP=CLIENT_IP)

        assert response.status_code == status.HTTP_403_FORBIDDEN

        warnings = [e for e in json_logs.entries() if e["level"] == "WARNING"]
        assert [e["message"] for e in warnings] == ["clock-in refused: not on an office network"]
        assert CLIENT_IP not in json_logs.text()


class TestCommands:
    def test_one_correlation_id_per_run_and_the_office_as_tenant(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        staff = acme.add_staff(Role.ADMIN)
        json_logs.clear()

        call_command("rebuild_staff_directory")

        entries = json_logs.entries()
        ids = {entry["correlation_id"] for entry in entries}
        assert len(ids) == 1
        assert uuid.UUID(ids.pop())
        assert entries[0]["message"] == "command started: name=rebuild_staff_directory"
        assert re.fullmatch(
            r"command finished: name=rebuild_staff_directory duration_ms=\d+",
            str(entries[-1]["message"]),
        )
        office = json_logs.only("staff directory office read: staff=1")
        assert office["tenant"] == acme.host
        json_logs.only("staff directory rebuilt: entries=1")
        assert staff.email not in json_logs.text()
        assert get_correlation_id() is None

    def test_two_runs_have_two_ids(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        make_consultancy("Acme Education", "acme")

        call_command("rebuild_staff_directory")
        call_command("rebuild_staff_directory")

        started = json_logs.with_message("command started")
        assert len({entry["correlation_id"] for entry in started}) == len(started) == 2

    def test_a_failing_run_is_one_error_line_and_re_raised(
        self,
        make_consultancy: ConsultancyFactory,
        json_logs: JsonLogs,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        make_consultancy("Acme Education", "acme")
        monkeypatch.setattr(
            "central_login.management.commands.rebuild_staff_directory.rebuild_directory",
            _broken,
        )

        with pytest.raises(RuntimeError):
            call_command("rebuild_staff_directory")

        error = json_logs.only("command failed: name=rebuild_staff_directory")
        assert error["level"] == "ERROR"
        assert "RuntimeError" in str(error["exception"])
        assert "leaked-value" not in json_logs.text()

    def test_migrate_schemas_is_the_logged_one(self) -> None:
        command = load_command_class(get_commands()["migrate_schemas"], "migrate_schemas")

        assert isinstance(command, LoggedCommand)
        assert isinstance(command, MigrateSchemasCommand)


class TestCodes:
    def test_reaching_the_code_limit_is_a_warning_with_ids_only(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        staff = acme.add_staff(Role.COUNSELLOR)

        with tenant_context(acme.consultancy):
            for _ in range(MAX_CODES_PER_HOUR):
                issue_code(staff, CodePurpose.RESET)
            with pytest.raises(CodeLimitReachedError):
                issue_code(staff, CodePurpose.RESET)

        warning = json_logs.only(f"code limit reached: staff_id={staff.pk} purpose=reset")
        assert warning["level"] == "WARNING"

    def test_running_out_of_tries_is_a_warning_without_the_code(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        staff = acme.add_staff(Role.COUNSELLOR)

        with tenant_context(acme.consultancy):
            code = issue_code(staff, CodePurpose.RESET)
            wrong = "000000" if code != "000000" else "111111"
            for _ in range(MAX_ATTEMPTS):
                with pytest.raises(InvalidCodeError):
                    use_code(staff, CodePurpose.RESET, wrong)

        warning = json_logs.only(f"code out of tries: staff_id={staff.pk} purpose=reset")
        assert warning["level"] == "WARNING"
        assert code not in json_logs.text()
