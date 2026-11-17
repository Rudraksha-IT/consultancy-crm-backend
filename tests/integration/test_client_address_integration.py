"""The client's address is never logged or answered (ADR 0004), at any log level."""

from __future__ import annotations

import io
import logging
import logging.config
from collections.abc import Iterator

import pytest
from django.conf import settings
from django.db import connection
from rest_framework import status

from config.logging import build_logging
from staff.models import Role
from tests.conftest import ConsultancyFactory, PlatformContext

ADDRESS = "203.0.113.77"
IPV6_ADDRESS = "2001:db8::77"
CONSULTANCY_URL = "/api/consultancy/"
CONTEXT_URL = "/api/context/"
LOGIN_URL = "/api/auth/login/"


@pytest.fixture
def debug_logs() -> Iterator[io.StringIO]:
    """The standard JSON logging at DEBUG, the most it ever writes, into a buffer."""
    logging.config.dictConfig(build_logging("DEBUG", "json"))
    stream = io.StringIO()
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setStream(stream)
    yield stream
    logging.config.dictConfig(settings.LOGGING)


def _assert_never_seen(stream: io.StringIO, body: bytes) -> None:
    for address in (ADDRESS, IPV6_ADDRESS):
        assert address not in stream.getvalue()
        assert address.encode() not in body


class TestTheAddressIsNeverKept:
    def test_not_logged_or_answered_on_a_logged_in_call(
        self, make_consultancy: ConsultancyFactory, debug_logs: io.StringIO
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        acme.log_in_as(Role.ADMIN)
        acme.client.defaults["HTTP_X_CLIENT_IP"] = ADDRESS

        response = acme.client.get(CONTEXT_URL)

        assert response.status_code == status.HTTP_200_OK
        assert debug_logs.getvalue()
        _assert_never_seen(debug_logs, response.content)

    def test_not_logged_or_answered_on_a_refused_login(
        self, make_consultancy: ConsultancyFactory, debug_logs: io.StringIO
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        acme.client.defaults["HTTP_X_CLIENT_IP"] = IPV6_ADDRESS

        response = acme.client.post(
            LOGIN_URL, {"email": "nobody@acme.test", "password": "x"}, format="json"
        )

        assert response.status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED}
        _assert_never_seen(debug_logs, response.content)

    def test_not_logged_or_answered_on_a_server_error_or_not_found(
        self, make_consultancy: ConsultancyFactory, debug_logs: io.StringIO
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        acme.client.defaults["HTTP_X_CLIENT_IP"] = ADDRESS

        response = acme.client.get("/api/no-such-thing/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        _assert_never_seen(debug_logs, response.content)

    def test_not_logged_on_the_platform(
        self, platform_site: PlatformContext, debug_logs: io.StringIO
    ) -> None:
        platform_site.client.defaults["HTTP_X_CLIENT_IP"] = ADDRESS

        response = platform_site.client.get("/api/platform/me/")

        _assert_never_seen(debug_logs, response.content)

    def test_no_table_has_an_address_column(
        self, make_consultancy: ConsultancyFactory, debug_logs: io.StringIO
    ) -> None:
        acme = make_consultancy("Acme Education", "acme")
        acme.log_in_as(Role.ADMIN)
        acme.client.defaults["HTTP_X_CLIENT_IP"] = ADDRESS
        acme.client.get(CONTEXT_URL)
        acme.client.get(CONSULTANCY_URL)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_schema, table_name, column_name FROM information_schema.columns"
                " WHERE data_type IN ('inet', 'cidr')"
                " AND table_schema NOT IN ('pg_catalog', 'information_schema')"
            )
            address_columns = cursor.fetchall()
        assert address_columns == []
