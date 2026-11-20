"""`GET /api/health/`: what a deploy waits on before switching versions (ticket 14).

Only the Next.js server and Docker's healthcheck call it, on the internal network, with the
internal token. It needs no login, answers no data, and says only whether the database answers.
"""

from __future__ import annotations

import pytest
from django.db import OperationalError
from django.test import Client
from rest_framework import status

from tests.conftest import TEST_INTERNAL_API_TOKEN, JsonLogs

HEALTH_URL = "/api/health/"
# Docker's healthcheck calls the container by a name that belongs to no office.
INTERNAL_HOST = "localhost:8000"


def internal_client(host: str = INTERNAL_HOST, *, token: str = TEST_INTERNAL_API_TOKEN) -> Client:
    """A plain client calling the way Docker's healthcheck and the Next.js server do."""
    return Client(HTTP_HOST=host, HTTP_X_INTERNAL_TOKEN=token)


@pytest.mark.django_db
class TestHealth:
    def test_answers_ok_after_asking_the_database(self) -> None:
        response = internal_client().get(HEALTH_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}
        assert response["Cache-Control"] == "no-store"

    def test_needs_no_login_and_ignores_any_token_or_query(self) -> None:
        response = internal_client().get(
            f"{HEALTH_URL}?verbose=1", HTTP_AUTHORIZATION="Bearer not-a-token"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

    @pytest.mark.parametrize("host", [INTERNAL_HOST, "backend:8000", "evil.example.com"])
    def test_answers_on_any_internal_host_without_naming_an_office(self, host: str) -> None:
        response = internal_client(host).get(HEALTH_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "head", "options"])
    def test_allows_only_get(self, method: str) -> None:
        response = getattr(internal_client(), method)(HEALTH_URL)

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert response["Allow"] == "GET"

    @pytest.mark.parametrize("token", ["", "wrong-token-0123456789abcdef0123456789"])
    def test_is_refused_without_the_internal_token(self, token: str) -> None:
        response = internal_client(token=token).get(HEALTH_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "status" not in response.json()

    def test_says_unavailable_when_the_database_doesnt_answer(
        self, monkeypatch: pytest.MonkeyPatch, json_logs: JsonLogs
    ) -> None:
        def refuse() -> None:
            message = "connection to server at db (10.0.0.5) failed: secret-detail"
            raise OperationalError(message)

        monkeypatch.setattr("config.health.ask_database", refuse)

        response = internal_client().get(HEALTH_URL)

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json() == {"status": "unavailable"}
        assert "health check failed" in json_logs.text()
        assert "secret-detail" not in json_logs.text()
        assert "10.0.0.5" not in json_logs.text()

    def test_a_healthy_answer_writes_no_log_line(self, json_logs: JsonLogs) -> None:
        internal_client().get(HEALTH_URL)

        assert json_logs.text() == ""

    def test_other_paths_still_need_an_office_host(self) -> None:
        response = internal_client().get("/api/health/extra/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
