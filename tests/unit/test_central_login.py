"""Central login pieces that need no database: the email hash and answering before the work."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from pytest_django.fixtures import Settings

from central_login.directory import email_hash
from central_login.views import _port_of
from config.responses import AnswerThenRun
from tenancy.subdomains import build_host


class TestEmailHash:
    def test_same_email_in_any_case_or_spacing_gives_the_same_hash(self) -> None:
        assert email_hash(" Sita@Example.COM ") == email_hash("sita@example.com")

    def test_is_hex_sha256_and_never_the_email(self) -> None:
        hashed = email_hash("sita@example.com")
        assert len(hashed) == 64
        assert int(hashed, 16) >= 0
        assert "sita" not in hashed

    @pytest.mark.parametrize("key", ["", "too-short"])
    def test_refuses_a_missing_or_short_key(self, settings: Settings, key: str) -> None:
        settings.STAFF_DIRECTORY_KEY = key
        with pytest.raises(ImproperlyConfigured, match="STAFF_DIRECTORY_KEY"):
            email_hash("sita@example.com")


@pytest.mark.django_db  # closing a response releases the database connection, as Django does
class TestAnswerThenRun:
    def test_runs_the_work_only_when_the_answer_is_closed(self) -> None:
        ran: list[str] = []
        response = AnswerThenRun(lambda: ran.append("sent"), {"detail": "ok"}, status=202)
        assert ran == []
        response.close()
        assert ran == ["sent"]

    def test_still_closes_when_the_work_fails(self) -> None:
        def fail() -> None:
            raise LookupError

        response = AnswerThenRun(fail, {"detail": "ok"}, status=202)
        with pytest.raises(LookupError):
            response.close()
        assert response.closed


class TestPortOf:
    @pytest.mark.parametrize(
        ("host", "port"),
        [
            (f"{build_host('login')}:8080", "8080"),
            ("login.crm.com", None),
            ("login.crm.com:", None),
        ],
    )
    def test_reads_the_port(self, host: str, port: str | None) -> None:
        assert _port_of(host) == port
