"""Rules for the subdomain a new Consultancy is reached at."""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from pytest_django.fixtures import Settings

from tenancy.subdomains import (
    build_host,
    find_subdomain_problem,
    schema_name_for,
    subdomain_from_host,
)


class TestFindSubdomainProblem:
    @pytest.mark.parametrize("subdomain", ["abcedu", "a1", "x", "edu2025", "a" * 63])
    def test_accepts_lowercase_letters_and_numbers_starting_with_a_letter(
        self, subdomain: str
    ) -> None:
        assert find_subdomain_problem(subdomain) is None

    @pytest.mark.parametrize(
        "subdomain",
        [
            "",
            "ABC",
            "abc-edu",
            "abc_edu",
            "abc.edu",
            "-abc",
            "abc-",
            "1abc",
            "abc edu",
            "a" * 64,
            "abç",
            "abc!",
            "<b>x</b>",
        ],
    )
    def test_rejects_anything_but_lowercase_letters_and_numbers(self, subdomain: str) -> None:
        assert find_subdomain_problem(subdomain) == (
            "Use only lowercase letters and numbers, starting with a letter (at most 63)."
        )

    @pytest.mark.parametrize("subdomain", ["admin", "platform", "www", "api", "public", "login"])
    def test_rejects_reserved_subdomains(self, subdomain: str) -> None:
        assert find_subdomain_problem(subdomain) == "This subdomain is reserved."

    def test_rejects_the_configured_central_login_subdomain(self, settings: Settings) -> None:
        settings.CENTRAL_LOGIN_SUBDOMAIN = "find"

        assert find_subdomain_problem("find") == "This subdomain is reserved."

    def test_rejects_the_configured_platform_subdomain(self, settings: Settings) -> None:
        settings.PLATFORM_SUBDOMAIN = "console"

        assert find_subdomain_problem("console") == "This subdomain is reserved."


class TestSchemaNameFor:
    def test_replaces_hyphens_because_schema_names_cannot_contain_them(self) -> None:
        assert schema_name_for("abc-edu") == "abc_edu"


class TestHosts:
    def test_builds_host_under_the_product_domain(self) -> None:
        assert build_host("abcedu") == f"abcedu.{django_settings.PRODUCT_DOMAIN}"

    def test_reads_subdomain_back_from_host(self) -> None:
        assert subdomain_from_host(build_host("abc-edu")) == "abc-edu"
