"""The client's address comes only from X-Client-IP on a call with the internal token (ADR 0004)."""

from __future__ import annotations

import re
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path

import pytest
from django.http import HttpRequest
from django.test import RequestFactory

from tenancy.client_address import client_address
from tests.conftest import TEST_INTERNAL_API_TOKEN

BACKEND = Path(__file__).resolve().parents[2]
# The one module allowed to read a client-address header.
READER = BACKEND / "tenancy" / "client_address.py"
CLIENT_ADDRESS_HEADERS = re.compile(
    r"x[-_]client[-_]ip|x[-_]forwarded[-_]for|x[-_]real[-_]ip|cf[-_]connecting[-_]ip"
    r"|true[-_]client[-_]ip|remote_addr|http_forwarded\b|[\"']forwarded[\"']",
    re.IGNORECASE,
)
SKIPPED_DIRECTORIES = {".venv", "tests", "migrations", "__pycache__"}


def _request(**headers: str) -> HttpRequest:
    request: HttpRequest = RequestFactory().get("/api/consultancy/", headers=headers)
    return request


def _trusted(**headers: str) -> HttpRequest:
    return _request(**{"X-Internal-Token": TEST_INTERNAL_API_TOKEN, **headers})


class TestClientAddress:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("203.0.113.7", IPv4Address("203.0.113.7")),
            ("2001:db8::7", IPv6Address("2001:db8::7")),
            ("2001:DB8::7", IPv6Address("2001:db8::7")),
        ],
    )
    def test_reads_x_client_ip_on_a_call_with_the_internal_token(
        self, value: str, expected: IPv4Address | IPv6Address
    ) -> None:
        assert client_address(_trusted(**{"X-Client-IP": value})) == expected

    def test_an_ipv4_address_written_as_ipv6_is_read_as_ipv4(self) -> None:
        address = client_address(_trusted(**{"X-Client-IP": "::ffff:203.0.113.7"}))

        assert address == IPv4Address("203.0.113.7")

    def test_is_none_without_the_internal_token(self) -> None:
        assert client_address(_request(**{"X-Client-IP": "203.0.113.7"})) is None

    @pytest.mark.parametrize(
        "token",
        ["", "guess", TEST_INTERNAL_API_TOKEN[:-1], f"{TEST_INTERNAL_API_TOKEN}x"],
    )
    def test_is_none_with_a_wrong_internal_token(self, token: str) -> None:
        request = _request(**{"X-Internal-Token": token, "X-Client-IP": "203.0.113.7"})

        assert client_address(request) is None

    def test_is_none_when_the_header_is_missing(self) -> None:
        assert client_address(_trusted()) is None

    @pytest.mark.parametrize(
        "value",
        [
            "",
            " ",
            "not-an-ip",
            "999.1.1.1",
            "01.2.3.4",
            " 203.0.113.7",
            "203.0.113.7 ",
            "203.0.113.7, 198.51.100.1",
            "203.0.113.7:443",
            "203.0.113.0/24",
            "fe80::1%eth0",
            "<script>alert(1)</script>",
            "'; DROP TABLE staff_staff; --",
            "1" * 1000,
            "2001:db8::" + "1" * 200,
        ],
    )
    def test_a_malformed_value_is_none(self, value: str) -> None:
        assert client_address(_trusted(**{"X-Client-IP": value})) is None

    @pytest.mark.parametrize(
        "header",
        ["X-Forwarded-For", "X-Real-IP", "CF-Connecting-IP", "Forwarded", "True-Client-IP"],
    )
    def test_never_reads_another_proxy_header(self, header: str) -> None:
        assert client_address(_trusted(**{header: "203.0.113.7"})) is None

    def test_never_reads_the_socket_address(self) -> None:
        request = _trusted()
        request.META["REMOTE_ADDR"] = "203.0.113.7"

        assert client_address(request) is None


def _backend_modules() -> list[Path]:
    return [
        path
        for path in BACKEND.rglob("*.py")
        if not SKIPPED_DIRECTORIES.intersection(path.relative_to(BACKEND).parts)
    ]


class TestOnlyOneReader:
    def test_nothing_else_reads_a_client_address_header(self) -> None:
        readers = [
            str(path.relative_to(BACKEND))
            for path in _backend_modules()
            if CLIENT_ADDRESS_HEADERS.search(path.read_text(encoding="utf-8"))
        ]

        assert readers == ["tenancy/client_address.py"]

    @pytest.mark.parametrize(
        "code",
        [
            'request.headers.get("X-Forwarded-For")',
            'request.META["HTTP_X_REAL_IP"]',
            'request.META.get("REMOTE_ADDR")',
            'request.headers["CF-Connecting-IP"]',
            'request.headers.get("Forwarded")',
            "request.META['HTTP_FORWARDED']",
            'request.headers.get("x-client-ip")',
        ],
    )
    def test_the_search_finds_a_header_read(self, code: str) -> None:
        assert CLIENT_ADDRESS_HEADERS.search(code)

    def test_the_search_sees_the_backend_code(self) -> None:
        assert READER in _backend_modules()
        assert len(_backend_modules()) > 50
