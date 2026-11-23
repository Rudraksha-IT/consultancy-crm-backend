"""A product name the Platform can change (first live fixes, ticket 15).

One saved name, "Rudraksha CRM" until a Platform Admin changes it in Platform → Settings. Anyone
reads it on the Platform's and the Central login's addresses (the login screens show it before
anyone logs in); only Platform Admins change it, and each change is recorded with who and when.
Platform emails and a new two-step login setup use the saved name.
"""

from __future__ import annotations

from email.utils import parseaddr
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from django.conf import settings
from django.core import mail
from django_tenants.utils import schema_context
from rest_framework import status

from platform_admin.brand import DEFAULT_PRODUCT_NAME, PRODUCT_NAME_MAX_LENGTH
from platform_admin.models import PlatformAdmin, ProductNameChange
from staff.models import Role
from tenancy.subdomains import get_central_login_host
from tests.conftest import (
    PLATFORM_TEST_PASSWORD,
    PUBLIC_SCHEMA,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    use_access_token,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

BRAND_URL = "/api/platform/brand/"
NAME_URL = "/api/platform/brand/name/"
EMAIL_URL = "/api/platform/email/"
TEST_EMAIL_URL = "/api/platform/email/test/"
LOGIN_URL = "/api/platform/auth/login/"
SETUP_URL = "/api/platform/auth/two-factor/setup/"
BLANK_MESSAGE = "Enter the product's name."
LONG_MESSAGE = f"Use at most {PRODUCT_NAME_MAX_LENGTH} characters."
KIND_MESSAGE = "Use letters, numbers, spaces and . - & ' ( ) + only."


def rename(client: APIClient, name: object) -> _MonkeyPatchedResponse:
    """Change the product's name."""
    return client.put(NAME_URL, {"name": name}, format="json")


def brand(client: APIClient) -> Any:  # noqa: ANN401  # JSON
    """What the login screens and every page get."""
    return client.get(BRAND_URL).json()


def changes() -> list[ProductNameChange]:
    """Every change of the name, oldest first."""
    with schema_context(PUBLIC_SCHEMA):
        return list(ProductNameChange.objects.select_related("by").order_by("pk"))


@pytest.fixture
def ops(platform_site: PlatformContext) -> PlatformContext:
    """The Platform, with a Platform Admin logged in on its client."""
    platform_site.log_in()
    return platform_site


class TestRead:
    def test_the_name_is_rudraksha_crm_until_changed(self, platform_site: PlatformContext) -> None:
        assert DEFAULT_PRODUCT_NAME == "Rudraksha CRM"
        assert brand(platform_site.new_client()) == {"logo": None, "name": DEFAULT_PRODUCT_NAME}

    def test_the_central_login_reads_the_same_name(
        self, ops: PlatformContext, make_consultancy: ConsultancyFactory
    ) -> None:
        make_consultancy("ABC Education", "abcedu")
        rename(ops.client, "Northwind CRM")

        central = api_client(get_central_login_host())

        assert brand(central)["name"] == "Northwind CRM"


class TestChange:
    def test_a_platform_admin_changes_it_and_everyone_reads_it(self, ops: PlatformContext) -> None:
        response = rename(ops.client, "  Acme   Office Suite  ")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"name": "Acme Office Suite"}
        assert brand(ops.new_client())["name"] == "Acme Office Suite"

    def test_each_change_is_recorded_with_who_and_when(self, ops: PlatformContext) -> None:
        rename(ops.client, "First Name")
        rename(ops.client, "Second Name")

        first, second = changes()
        assert (first.before, first.after) == (DEFAULT_PRODUCT_NAME, "First Name")
        assert (second.before, second.after) == ("First Name", "Second Name")
        assert second.by is not None
        assert second.by.email == "ops@platform.test"
        assert second.changed_at >= first.changed_at

    def test_the_same_name_again_records_nothing(self, ops: PlatformContext) -> None:
        response = rename(ops.client, DEFAULT_PRODUCT_NAME)

        assert response.status_code == status.HTTP_200_OK
        assert changes() == []

    def test_the_longest_name_is_kept(self, ops: PlatformContext) -> None:
        name = "A" * PRODUCT_NAME_MAX_LENGTH

        assert rename(ops.client, name).json() == {"name": name}

    def test_letters_of_any_language_and_safe_marks_are_kept(self, ops: PlatformContext) -> None:
        name = "Shiksha (Nepal) & Co. - CRM+ 2 नेपाल's"[:PRODUCT_NAME_MAX_LENGTH]

        assert rename(ops.client, name).json() == {"name": name}

    def test_the_change_is_logged_without_the_name(
        self, ops: PlatformContext, json_logs: JsonLogs
    ) -> None:
        rename(ops.client, "Secret Launch Name")

        assert json_logs.with_message("Product name changed: platform_admin_id=")
        assert "Secret Launch Name" not in json_logs.text()


class TestRefused:
    @pytest.mark.parametrize(
        ("name", "message"),
        [
            ("", BLANK_MESSAGE),
            ("    ", BLANK_MESSAGE),
            ("A" * (PRODUCT_NAME_MAX_LENGTH + 1), LONG_MESSAGE),
            ("<script>alert(1)</script>", KIND_MESSAGE),
            ('Acme" onmouseover="x', KIND_MESSAGE),
            ("Acme\r\nBcc: someone@x.test", KIND_MESSAGE),
            ("Acme\x00CRM", "Null characters are not allowed."),
            ("Acme; DROP TABLE", KIND_MESSAGE),
            ("{{7*7}}", KIND_MESSAGE),
            ("-Acme", KIND_MESSAGE),
            ("Acme\u202eMRC", KIND_MESSAGE),
        ],
    )
    def test_a_bad_name_is_refused_and_nothing_changes(
        self, ops: PlatformContext, name: str, message: str
    ) -> None:
        response = rename(ops.client, name)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"name": [message]}
        assert brand(ops.new_client())["name"] == DEFAULT_PRODUCT_NAME
        assert changes() == []

    @pytest.mark.parametrize("body", [{}, {"name": 5}, {"name": None}, {"name": ["Acme"]}])
    def test_a_missing_or_non_text_name_is_refused(
        self, ops: PlatformContext, body: dict[str, object]
    ) -> None:
        response = ops.client.put(NAME_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert brand(ops.new_client())["name"] == DEFAULT_PRODUCT_NAME

    def test_unknown_fields_are_refused(self, ops: PlatformContext) -> None:
        response = ops.client.put(NAME_URL, {"name": "Acme", "logo": None}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert brand(ops.new_client())["name"] == DEFAULT_PRODUCT_NAME

    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_only_put_changes_it(self, ops: PlatformContext, method: str) -> None:
        response = getattr(ops.client, method)(NAME_URL, {"name": "Acme"}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_nobody_logged_out_changes_it(self, platform_site: PlatformContext) -> None:
        response = rename(platform_site.new_client(), "Intruder CRM")

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
        assert brand(platform_site.new_client())["name"] == DEFAULT_PRODUCT_NAME

    def test_an_offices_staff_never_change_it(
        self, platform_site: PlatformContext, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        access = abc.tokens_for(abc.add_staff(Role.ADMIN)).access

        for client in (abc.new_client(), platform_site.new_client()):
            use_access_token(client, access)
            response = rename(client, "Intruder CRM")
            assert response.status_code in {401, 403, 404}

        assert brand(platform_site.new_client())["name"] == DEFAULT_PRODUCT_NAME
        assert changes() == []

    def test_an_offices_address_has_neither_the_name_nor_the_change(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        assert abc.new_client().get(BRAND_URL).status_code == status.HTTP_404_NOT_FOUND
        assert rename(abc.new_client(), "Intruder CRM").status_code == status.HTTP_404_NOT_FOUND


class TestUsedEverywhere:
    def test_platform_emails_come_from_the_saved_name(self, ops: PlatformContext) -> None:
        rename(ops.client, "Northwind CRM")

        ops.client.post(TEST_EMAIL_URL, {}, format="json")

        sent = mail.outbox[-1]
        assert parseaddr(sent.from_email) == ("Northwind CRM", settings.DEFAULT_FROM_EMAIL)
        assert sent.subject == "Test email from Northwind CRM"

    def test_email_settings_show_the_saved_name(self, ops: PlatformContext) -> None:
        rename(ops.client, "Northwind CRM")

        assert ops.client.get(EMAIL_URL).json()["product_name"] == "Northwind CRM"

    def test_a_new_two_step_setup_carries_the_saved_name(self, ops: PlatformContext) -> None:
        rename(ops.client, "Northwind CRM")
        other = ops.add_admin("second@platform.test")
        with schema_context(PUBLIC_SCHEMA):
            PlatformAdmin.objects.filter(pk=other.pk).update(two_factor_required=True)
        client = ops.new_client()
        login = client.post(
            LOGIN_URL, {"email": other.email, "password": PLATFORM_TEST_PASSWORD}, format="json"
        )

        setup = client.post(SETUP_URL, {"challenge": login.json()["challenge"]}, format="json")

        uri = urlparse(setup.json()["uri"])
        assert parse_qs(uri.query)["issuer"] == ["Northwind CRM"]
        assert unquote(uri.path) == f"/Northwind CRM:{other.email}"
