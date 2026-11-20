"""Every request is logged once, for its Consultancy and correlation ID, with nothing secret."""

from __future__ import annotations

import re
import uuid

import pytest
from django.conf import settings
from rest_framework import status

from config.correlation import get_correlation_id
from staff.models import Role
from tests.conftest import (
    PLATFORM_TEST_PASSWORD,
    TEST_PASSWORD,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    run_queued_jobs,
)

CONSULTANCY_URL = "/api/consultancy/"
LOGIN_URL = "/api/auth/login/"
LOGOUT_URL = "/api/auth/logout/"
DATES_URL = "/api/configuration/dates/"
PLATFORM_LOGIN_URL = "/api/platform/auth/login/"
PLATFORM_LOGOUT_URL = "/api/platform/auth/logout/"
CONSULTANCIES_URL = "/api/platform/consultancies/"
CORRELATION_HEADER = "X-Correlation-ID"
WRONG_PASSWORD = "Wrong-Pa55word!"  # noqa: S105  # test-only credential
WRONG_TOKEN = "wrong"  # noqa: S105  # test-only value
THEME = {
    "page": "ice",
    "sidebar": "#0f3440",
    "accent": "#0b6e8e",
    "dark": {"base": "teal", "sidebar": "brand", "accent": "auto"},
}


def _is_uuid(value: object) -> bool:
    return isinstance(value, str) and str(uuid.UUID(value)) == value


class TestRequestLine:
    def test_one_info_line_per_request_without_the_query(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")

        acme.client.get(f"{CONSULTANCY_URL}?email=sita@acme.test")

        entry = json_logs.request_line(f"GET {CONSULTANCY_URL} 200")
        assert entry["level"] == "INFO"
        assert re.fullmatch(r"config/middleware\.py: __call__, line \d+", str(entry["source"]))
        assert "sita@acme.test" not in json_logs.text()

    def test_the_tenant_is_the_consultancys_host(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        beta = make_consultancy("Beta Education", "beta")

        acme.client.get(CONSULTANCY_URL)
        beta.client.get(CONSULTANCY_URL)

        tenants = [
            entry["tenant"] for entry in json_logs.with_message(f"GET {CONSULTANCY_URL} 200")
        ]
        assert tenants == [acme.host, beta.host]

    def test_the_forwarded_host_is_the_tenant(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        client = api_client("internal-backend", HTTP_X_FORWARDED_HOST=f"{acme.host}:3000")

        client.get(CONSULTANCY_URL)

        assert json_logs.request_line(f"GET {CONSULTANCY_URL} 200")["tenant"] == acme.host

    def test_the_platform_host_has_no_tenant(
        self, platform_site: PlatformContext, json_logs: JsonLogs
    ) -> None:
        platform_site.client.get("/api/platform/me/")

        assert json_logs.request_line("GET /api/platform/me/ 401")["tenant"] == "N/A"

    def test_a_refused_request_is_not_logged_under_the_previous_consultancy(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        acme.client.get(CONSULTANCY_URL)
        no_token = api_client(acme.host, HTTP_X_INTERNAL_TOKEN="")

        response = no_token.get(CONSULTANCY_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert json_logs.request_line(f"GET {CONSULTANCY_URL} 403")["tenant"] == "N/A"

    def test_a_path_cannot_forge_a_log_line(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")

        acme.client.get("/api/nothing%0A[INFO]%20forged/")

        entry = json_logs.request_line("GET /api/nothing\\n[INFO] forged/ 404")
        assert "\n" not in str(entry["message"])


class TestCorrelationId:
    def test_an_incoming_uuid_is_kept_and_returned(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        sent = str(uuid.uuid4())

        response = acme.client.get(CONSULTANCY_URL, HTTP_X_CORRELATION_ID=sent)

        assert response[CORRELATION_HEADER] == sent
        assert json_logs.request_line(f"GET {CONSULTANCY_URL} 200")["correlation_id"] == sent

    @pytest.mark.parametrize(
        "sent", ["not-a-uuid", "<script>alert(1)</script>", "1' OR '1'='1", "x" * 5000]
    )
    def test_anything_else_is_replaced(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs, sent: str
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")

        response = acme.client.get(CONSULTANCY_URL, HTTP_X_CORRELATION_ID=sent)

        returned = response[CORRELATION_HEADER]
        assert returned != sent
        assert _is_uuid(returned)
        assert json_logs.request_line(f"GET {CONSULTANCY_URL} 200")["correlation_id"] == returned
        assert sent not in json_logs.text()

    def test_a_missing_one_is_created(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")

        response = acme.client.get(CONSULTANCY_URL)

        assert _is_uuid(response[CORRELATION_HEADER])

    def test_every_line_of_one_request_shares_it(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        sent = str(uuid.uuid4())
        json_logs.clear()

        acme.client.post(
            LOGIN_URL,
            {"email": "nobody@acme.test", "password": WRONG_PASSWORD},
            format="json",
            HTTP_X_CORRELATION_ID=sent,
        )

        assert {entry["correlation_id"] for entry in json_logs.entries()} == {sent}
        assert get_correlation_id() is None

    def test_refused_requests_return_one_too(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")

        response = api_client(acme.host, HTTP_X_INTERNAL_TOKEN=WRONG_TOKEN).get(CONSULTANCY_URL)

        assert _is_uuid(response[CORRELATION_HEADER])


class TestStaffEvents:
    def test_a_failed_login_logs_no_email_or_password(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        counsellor = acme.add_staff(Role.COUNSELLOR)

        acme.client.post(
            LOGIN_URL, {"email": counsellor.email, "password": WRONG_PASSWORD}, format="json"
        )

        assert json_logs.only("Staff login failed")["level"] == "INFO"
        assert counsellor.email not in json_logs.text()
        assert WRONG_PASSWORD not in json_logs.text()

    def test_login_and_logout_are_logged_with_the_staff_id_only(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        counsellor = acme.add_staff(Role.COUNSELLOR)

        tokens = acme.client.post(
            LOGIN_URL, {"email": counsellor.email, "password": TEST_PASSWORD}, format="json"
        ).json()
        acme.client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")

        assert json_logs.only(f"Staff logged in: staff_id={counsellor.pk}")["tenant"] == acme.host
        json_logs.only(f"Staff logged out: staff_id={counsellor.pk}")
        assert counsellor.email not in json_logs.text()
        assert TEST_PASSWORD not in json_logs.text()

    def test_a_theme_change_is_logged_under_the_platform_admin(
        self,
        make_consultancy: ConsultancyFactory,
        platform_site: PlatformContext,
        json_logs: JsonLogs,
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        ops = platform_site.log_in()

        platform_site.client.put(
            f"{CONSULTANCIES_URL}{acme.consultancy.pk}/theme/", THEME, format="json"
        )

        consultancy_id = acme.consultancy.pk
        json_logs.only(
            f"Theme changed: consultancy_id={consultancy_id}, platform_admin_id={ops.pk}"
        )


class TestPlatformEvents:
    def test_platform_login_failure_success_and_logout(
        self, platform_site: PlatformContext, json_logs: JsonLogs
    ) -> None:
        admin = platform_site.add_admin()
        client = platform_site.client

        client.post(
            PLATFORM_LOGIN_URL, {"email": admin.email, "password": WRONG_PASSWORD}, format="json"
        )
        tokens = client.post(
            PLATFORM_LOGIN_URL,
            {"email": admin.email, "password": PLATFORM_TEST_PASSWORD},
            format="json",
        ).json()
        client.post(PLATFORM_LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")

        json_logs.only("Platform Admin login failed")
        json_logs.only(f"Platform Admin logged in: platform_admin_id={admin.pk}")
        json_logs.only(f"Platform Admin logged out: platform_admin_id={admin.pk}")
        assert admin.email not in json_logs.text()
        assert WRONG_PASSWORD not in json_logs.text()

    def test_creating_and_terminating_a_consultancy_are_logged(
        self, platform_site: PlatformContext, json_logs: JsonLogs
    ) -> None:
        platform_site.log_in()
        body = {
            "name": "Everest Education",
            "subdomain": "everest",
        }

        created = platform_site.client.post(CONSULTANCIES_URL, body, format="json").json()
        run_queued_jobs()
        platform_site.client.post(
            f"{CONSULTANCIES_URL}{created['id']}/status/",
            {"status": "terminated", "reason": "Left us, see sita@everest.test"},
            format="json",
        )

        json_logs.only(f"Consultancy set up: consultancy_id={created['id']}")
        changed = json_logs.with_message("Consultancy Status changed: ")
        assert len(changed) == 1
        assert f"consultancy_id={created['id']}, from=evaluation, to=terminated" in str(
            changed[0]["message"]
        )
        assert "Left us" not in json_logs.text()
        assert "sita@everest.test" not in json_logs.text()
        assert TEST_PASSWORD not in json_logs.text()


class TestStartup:
    def test_settings_logging_is_json_at_info_by_default(self) -> None:
        assert settings.LOGGING["root"] == {"handlers": ["json_handler"], "level": "INFO"}

    def test_the_request_log_runs_first_after_the_unlogged_health_check(self) -> None:
        # The health check (ticket 14) is answered before it, so it is never logged per request.
        assert settings.MIDDLEWARE[:2] == [
            "config.health.HealthMiddleware",
            "config.middleware.RequestLogMiddleware",
        ]
