"""Django is called only by the Next.js server: internal token, Consultancy from the forwarded host.

See ADR 0002 (the Next.js server forwards the Consultancy's host).
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from tenancy.middleware import InternalTokenMiddleware
from tests.conftest import (
    TEST_INTERNAL_API_TOKEN,
    ConsultancyFactory,
    PlatformContext,
    api_client,
    build_host,
)

CURRENT_CONSULTANCY_URL = "/api/consultancy/"
PLATFORM_ME_URL = "/api/platform/me/"
# The address the Next.js server uses to reach Django on the private network.
INTERNAL_HOST = "backend:8000"
REFUSED_BODY = {"detail": "Forbidden."}
WRONG_TOKEN = "guess"  # noqa: S105  # test-only value


def _respond(_request: HttpRequest) -> HttpResponse:
    return HttpResponse()


class TestInternalToken:
    def test_request_without_the_internal_token_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        client = APIClient(HTTP_HOST=abc.host)

        response = client.get(CURRENT_CONSULTANCY_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == REFUSED_BODY

    @pytest.mark.parametrize(
        "token",
        [
            "wrong-token-of-the-same-kind-and-length-xx",
            TEST_INTERNAL_API_TOKEN[:-1],
            f"{TEST_INTERNAL_API_TOKEN}x",
            f" {TEST_INTERNAL_API_TOKEN}",
            "",
            "<script>alert(1)</script>",
            "' OR '1'='1",
        ],
    )
    def test_request_with_a_wrong_internal_token_is_refused(
        self, make_consultancy: ConsultancyFactory, token: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        client = APIClient(HTTP_HOST=abc.host, HTTP_X_INTERNAL_TOKEN=token)

        response = client.get(CURRENT_CONSULTANCY_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == REFUSED_BODY

    def test_refusal_reveals_nothing_about_the_token(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        client = APIClient(HTTP_HOST=abc.host, HTTP_X_INTERNAL_TOKEN=WRONG_TOKEN)

        response = client.get(CURRENT_CONSULTANCY_URL)

        assert TEST_INTERNAL_API_TOKEN not in response.content.decode()

    def test_unknown_host_without_the_token_is_refused_before_routing(self, db: None) -> None:
        client = APIClient(HTTP_HOST=build_host("unknown"))

        response = client.get(CURRENT_CONSULTANCY_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "options", "head"])
    def test_every_method_needs_the_token(
        self, make_consultancy: ConsultancyFactory, method: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        client = APIClient(HTTP_HOST=abc.host)

        response = getattr(client, method)(CURRENT_CONSULTANCY_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_request_with_the_right_internal_token_is_served(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = api_client(abc.host).get(CURRENT_CONSULTANCY_URL)

        assert response.status_code == status.HTTP_200_OK


class TestInternalTokenSetting:
    @pytest.mark.parametrize("token", ["", "x" * 31])
    def test_startup_fails_without_a_long_enough_token(self, token: str) -> None:
        with (
            override_settings(INTERNAL_API_TOKEN=token),
            pytest.raises(ImproperlyConfigured, match="INTERNAL_API_TOKEN"),
        ):
            InternalTokenMiddleware(_respond)

    def test_startup_accepts_a_32_character_token(self) -> None:
        with override_settings(INTERNAL_API_TOKEN="x" * 32):
            InternalTokenMiddleware(_respond)

    def test_allowed_hosts_is_only_the_product_domain(self) -> None:
        from django.conf import settings  # noqa: PLC0415  # read the live settings object

        # Django's test setup adds "testserver"; the rest is what settings.py sets.
        assert [f".{settings.PRODUCT_DOMAIN}", "testserver"] == settings.ALLOWED_HOSTS

    def test_the_internal_token_check_runs_before_anything_but_the_request_log(self) -> None:
        from django.conf import settings  # noqa: PLC0415  # read the live settings object

        assert settings.MIDDLEWARE[0] == "config.middleware.RequestLogMiddleware"
        assert settings.MIDDLEWARE[1] == "tenancy.middleware.InternalTokenMiddleware"


class TestForwardedHost:
    def test_forwarded_host_picks_its_consultancy(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        client = api_client(INTERNAL_HOST, HTTP_X_FORWARDED_HOST=xyz.host)

        response = client.get(CURRENT_CONSULTANCY_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == "XYZ Overseas"

    def test_forwarded_host_wins_over_the_host_header(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        client = api_client(abc.host, HTTP_X_FORWARDED_HOST=xyz.host)

        response = client.get(CURRENT_CONSULTANCY_URL)

        assert response.json()["name"] == "XYZ Overseas"

    def test_forwarded_host_with_a_port_picks_its_consultancy(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        client = api_client(INTERNAL_HOST, HTTP_X_FORWARDED_HOST=f"{abc.host}:8080")

        response = client.get(CURRENT_CONSULTANCY_URL)

        assert response.json()["name"] == "ABC Education"

    def test_unknown_forwarded_host_is_not_found(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        make_consultancy("ABC Education", "abcedu")
        client = api_client(INTERNAL_HOST, HTTP_X_FORWARDED_HOST=build_host("unknown"))

        response = client.get(CURRENT_CONSULTANCY_URL)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_terminated_forwarded_host_is_not_found(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.consultancy.status = "terminated"
        abc.consultancy.save(update_fields=["status"])
        client = api_client(INTERNAL_HOST, HTTP_X_FORWARDED_HOST=abc.host)

        response = client.get(CURRENT_CONSULTANCY_URL)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        "host",
        [
            "evil.example.com",
            f"{build_host('abcedu')}.evil.example.com",
            f"{build_host('abcedu')}@evil.example.com",
            f"<script>.{settings.PRODUCT_DOMAIN}",
            f"{build_host('abcedu')}'; DROP SCHEMA abcedu; --",
        ],
    )
    def test_forwarded_host_outside_the_product_domain_is_refused(
        self, make_consultancy: ConsultancyFactory, host: str
    ) -> None:
        make_consultancy("ABC Education", "abcedu")
        client = api_client(INTERNAL_HOST, HTTP_X_FORWARDED_HOST=host)

        response = client.get(CURRENT_CONSULTANCY_URL)

        # ALLOWED_HOSTS refuses it; django-tenants answers a disallowed host with 404.
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_host_outside_the_product_domain_is_refused_without_a_forwarded_host(
        self, db: None
    ) -> None:
        response = api_client("evil.example.com").get(CURRENT_CONSULTANCY_URL)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_platform_endpoints_answer_on_the_forwarded_platform_host(
        self, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()
        platform_site.client.defaults["HTTP_HOST"] = INTERNAL_HOST
        platform_site.client.defaults["HTTP_X_FORWARDED_HOST"] = platform_site.host

        response = platform_site.client.get(PLATFORM_ME_URL)

        assert response.status_code == status.HTTP_200_OK

    def test_platform_endpoints_are_not_found_on_a_forwarded_consultancy_host(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        client = platform_site.new_client()
        client.defaults["HTTP_X_FORWARDED_HOST"] = abc.host

        response = client.get(PLATFORM_ME_URL)

        assert response.status_code == status.HTTP_404_NOT_FOUND
