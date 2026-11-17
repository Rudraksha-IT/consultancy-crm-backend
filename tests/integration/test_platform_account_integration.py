"""The Platform Admin's own Account page: name, email and password (issue 59).

Every change goes through `save_platform_admin`, the same function the deploy uses.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from django.core.management import call_command
from django_tenants.utils import schema_context
from rest_framework import status
from rest_framework.test import APIClient

from auth_tokens.services import issue_tokens
from platform_admin.models import PlatformAdmin, PlatformAdminChange
from platform_admin.services import WRONG_PASSWORD_TRIES_ALLOWED
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

ME_URL = "/api/platform/me/"
PASSWORD_URL = "/api/platform/me/password/"  # noqa: S105  # a path, not a secret
LOGIN_URL = "/api/platform/auth/login/"
CONSULTANCIES_URL = "/api/platform/consultancies/"
NEW_PASSWORD = "Fresh-Start-2027!"  # noqa: S105  # test-only credential
WRONG_PASSWORD = "Not-The-One-1!"  # noqa: S105  # test-only credential
CENTRAL_HOST = get_central_login_host()

INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "Robert'); DROP TABLE platform_admin;--",
    "1 OR 1=1",
    "x{color:red}",
]


def stored(admin: PlatformAdmin) -> PlatformAdmin:
    """The Platform Admin as saved now."""
    with schema_context(PUBLIC_SCHEMA):
        return PlatformAdmin.objects.get(pk=admin.pk)


def changes_of(admin: PlatformAdmin) -> list[PlatformAdminChange]:
    """The changes made to `admin` after it was created."""
    with schema_context(PUBLIC_SCHEMA):
        found = PlatformAdminChange.objects.filter(platform_admin=admin, created=False)
        return list(found.order_by("pk"))


def logged_in_client(platform: PlatformContext, admin: PlatformAdmin) -> APIClient:
    """A new client on the Platform, logged in as `admin` (another session)."""
    client = platform.new_client()
    use_access_token(client, platform.tokens_for(admin).access)
    return client


def patch_me(client: APIClient, body: object) -> Any:  # noqa: ANN401  # the test client's answer
    """PATCH the Account."""
    return client.patch(ME_URL, body, format="json")


def change_password(
    client: APIClient, current: str = PLATFORM_TEST_PASSWORD, new: str = NEW_PASSWORD
) -> Any:  # noqa: ANN401  # the test client's answer
    """POST a password change."""
    return client.post(
        PASSWORD_URL, {"current_password": current, "new_password": new}, format="json"
    )


def login_status(platform: PlatformContext, email: str, password: str) -> int:
    """Log in on the Platform and return the status code."""
    response = platform.new_client().post(
        LOGIN_URL, {"email": email, "password": password}, format="json"
    )
    return response.status_code


def still_works(platform: PlatformContext, access: str) -> bool:
    """Whether this access token still opens the Account."""
    client = platform.new_client()
    use_access_token(client, access)
    return client.get(ME_URL).status_code == status.HTTP_200_OK


@pytest.fixture
def admin(platform_site: PlatformContext) -> PlatformAdmin:
    """A Platform Admin, logged in on `platform_site.client`."""
    admin = platform_site.add_admin()
    use_access_token(platform_site.client, platform_site.tokens_for(admin).access)
    return admin


class TestSeeMyAccount:
    def test_shows_my_name_and_email(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        response = platform_site.client.get(ME_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "platform_admin": {"id": admin.pk, "name": admin.name, "email": admin.email}
        }


class TestChangeName:
    """The name changes without the password (issue 65); the email can't be changed here."""

    def test_a_name_change_needs_no_password_and_is_recorded(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        other_session = platform_site.tokens_for(admin).access

        response = patch_me(platform_site.client, {"name": "  Ramesh  "})

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["platform_admin"] == {"id": admin.pk, "name": "Ramesh", "email": admin.email}
        assert {"access", "refresh"} <= body.keys()
        assert stored(admin).name == "Ramesh"
        # A name change signs nobody out.
        assert still_works(platform_site, other_session)
        [change] = changes_of(admin)
        assert change.fields == ["name"]
        assert change.by_id == admin.pk

    def test_the_same_name_changes_nothing(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        response = patch_me(platform_site.client, {"name": admin.name})

        assert response.status_code == status.HTTP_200_OK
        assert changes_of(admin) == []

    @pytest.mark.parametrize(
        "body",
        [
            {"email": "new@platform.test"},
            {"name": "Ramesh", "email": "new@platform.test"},
            {"name": "Ramesh", "current_password": PLATFORM_TEST_PASSWORD},
            {"name": "Ramesh", "code": "123456"},
        ],
    )
    def test_the_email_and_the_old_proof_fields_are_unknown(
        self, platform_site: PlatformContext, admin: PlatformAdmin, body: dict[str, str]
    ) -> None:
        response = patch_me(platform_site.client, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        kept = stored(admin)
        assert (kept.name, kept.email) == (admin.name, admin.email)
        assert changes_of(admin) == []

    def test_two_step_login_set_up_needs_no_number_for_the_name(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        with schema_context(PUBLIC_SCHEMA):
            PlatformAdmin.objects.filter(pk=admin.pk).update(two_factor_required=True)

        response = patch_me(platform_site.client, {"name": "Ramesh"})

        assert response.status_code == status.HTTP_200_OK
        assert stored(admin).name == "Ramesh"

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"name": "Ramesh", "new_password": "X"},
            {"name": "Ramesh", "is_active": False},
            {"name": "Ramesh", "id": 99},
            {"name": 5},
            {"name": ["x"]},
            {"name": None},
            {"name": ""},
            {"name": "   "},
            {"name": "N" * 201},
            {"name": "Ops\nAdmin"},
            {"x": "<script>"},
            [],
        ],
    )
    def test_bad_values_and_unknown_fields_are_refused(
        self, platform_site: PlatformContext, admin: PlatformAdmin, body: object
    ) -> None:
        response = patch_me(platform_site.client, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        kept = stored(admin)
        assert (kept.name, kept.email, kept.is_active) == (admin.name, admin.email, True)
        assert kept.check_password(PLATFORM_TEST_PASSWORD)

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_html_and_sql_shaped_names_are_refused(
        self, platform_site: PlatformContext, admin: PlatformAdmin, text: str
    ) -> None:
        response = patch_me(platform_site.client, {"name": text})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.json()
        assert text not in response.content.decode()
        assert stored(admin).name == admin.name

    def test_nobody_logged_in_is_refused(self, platform_site: PlatformContext) -> None:
        response = patch_me(platform_site.new_client(), {"name": "Ramesh"})

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_deploy_afterwards_does_not_undo_the_change(
        self,
        platform_site: PlatformContext,
        admin: PlatformAdmin,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        patch_me(platform_site.client, {"name": "Renamed"})
        monkeypatch.setenv("PLATFORM_ADMIN_NAME", "Platform Ops")
        monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", admin.email)
        monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "Deploy-Secret-2026!")

        call_command("ensure_platform_admin", stdout=io.StringIO())

        kept = stored(admin)
        assert kept.name == "Renamed"
        assert kept.check_password(PLATFORM_TEST_PASSWORD)


class TestChangePassword:
    def test_works_and_keeps_this_session_while_signing_out_the_others(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        other_session = platform_site.tokens_for(admin).access

        response = change_password(platform_site.client)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert {"access", "refresh"} <= body.keys()
        assert NEW_PASSWORD not in response.content.decode()
        assert not still_works(platform_site, other_session)
        assert still_works(platform_site, body["access"])
        assert login_status(platform_site, admin.email, PLATFORM_TEST_PASSWORD) == 400
        assert login_status(platform_site, admin.email, NEW_PASSWORD) == 200
        assert changes_of(admin)[-1].fields == ["password"]

    @pytest.mark.parametrize(
        ("new", "rules"),
        [
            ("weakpass", ["capital", "number", "symbol"]),
            ("Aa1!", ["min_length"]),
        ],
    )
    def test_a_weak_new_password_is_refused_with_the_unmet_rules(
        self, platform_site: PlatformContext, admin: PlatformAdmin, new: str, rules: list[str]
    ) -> None:
        response = change_password(platform_site.client, new=new)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["unmet_rules"] == rules
        assert stored(admin).check_password(PLATFORM_TEST_PASSWORD)

    def test_the_same_password_as_now_is_refused(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        response = change_password(platform_site.client, new=PLATFORM_TEST_PASSWORD)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "new_password" in response.json()

    def test_a_wrong_current_password_is_refused_and_logged_without_it(
        self, platform_site: PlatformContext, admin: PlatformAdmin, json_logs: JsonLogs
    ) -> None:
        response = change_password(platform_site.client, current=WRONG_PASSWORD)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "current_password" in response.json()
        assert stored(admin).check_password(PLATFORM_TEST_PASSWORD)
        line = json_logs.only(f"platform admin current password refused: id={admin.pk}")
        assert line["level"] == "WARNING"
        for secret in (WRONG_PASSWORD, NEW_PASSWORD, PLATFORM_TEST_PASSWORD, admin.email):
            assert secret not in json_logs.text()

    @pytest.mark.parametrize(
        "body",
        [
            {"current_password": PLATFORM_TEST_PASSWORD},
            {"new_password": NEW_PASSWORD},
            {"current_password": PLATFORM_TEST_PASSWORD, "new_password": None},
            {"current_password": PLATFORM_TEST_PASSWORD, "new_password": 12345678},
            {"current_password": PLATFORM_TEST_PASSWORD, "new_password": "A1!" + "a" * 126},
            {"current_password": PLATFORM_TEST_PASSWORD, "new_password": NEW_PASSWORD, "x": 1},
        ],
    )
    def test_missing_wrong_type_long_and_unknown_values_are_refused(
        self, platform_site: PlatformContext, admin: PlatformAdmin, body: dict[str, object]
    ) -> None:
        response = platform_site.client.post(PASSWORD_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(admin).check_password(PLATFORM_TEST_PASSWORD)

    def test_injection_shaped_passwords_are_only_ever_hashed(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        new = "Robert'); DROP TABLE x;--1A <script>"

        response = change_password(platform_site.client, new=new)

        assert response.status_code == status.HTTP_200_OK
        kept = stored(admin)
        assert kept.check_password(new)
        assert new not in kept.password

    def test_too_many_wrong_current_passwords_are_rate_limited(
        self, platform_site: PlatformContext, admin: PlatformAdmin, json_logs: JsonLogs
    ) -> None:
        for _ in range(WRONG_PASSWORD_TRIES_ALLOWED):
            assert change_password(platform_site.client, current=WRONG_PASSWORD).status_code == 400

        refused = change_password(platform_site.client)

        assert refused.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert stored(admin).check_password(PLATFORM_TEST_PASSWORD)
        assert json_logs.with_message("platform admin password tries limit reached")

    def test_a_right_current_password_starts_the_count_again(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        for _ in range(WRONG_PASSWORD_TRIES_ALLOWED - 1):
            change_password(platform_site.client, current=WRONG_PASSWORD)
        response = change_password(platform_site.client)
        assert response.status_code == status.HTTP_200_OK
        use_access_token(platform_site.client, response.json()["access"])
        for _ in range(WRONG_PASSWORD_TRIES_ALLOWED - 1):
            change_password(platform_site.client, current=WRONG_PASSWORD)

        again = change_password(
            platform_site.client, current=NEW_PASSWORD, new="Another-Pass-2027!"
        )
        assert again.status_code == status.HTTP_200_OK


class TestOnlyMyOwnAccount:
    def test_a_platform_admin_cannot_change_another_platform_admins_account(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        other = platform_site.add_admin(email="other@platform.test")

        patch_me(platform_site.client, {"name": "Changed"})
        change_password(platform_site.client)

        kept = stored(other)
        assert kept.name == other.name
        assert kept.check_password(PLATFORM_TEST_PASSWORD)
        assert changes_of(other) == []

    def test_nobody_logged_in_is_refused(self, platform_site: PlatformContext) -> None:
        client = platform_site.new_client()

        assert client.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert patch_me(client, {"name": "X"}).status_code == 401
        assert change_password(client).status_code == status.HTTP_401_UNAUTHORIZED

    def test_staff_tokens_on_an_office_and_the_platform_are_refused(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        staff = abc.log_in_as(Role.ADMIN)
        staff_access = abc.tokens_for(staff).access
        on_platform = platform_site.new_client()
        use_access_token(on_platform, staff_access)

        for client in (abc.client, on_platform):
            assert client.get(ME_URL).status_code in {401, 403, 404}
            assert patch_me(client, {"name": "X"}).status_code in {401, 403, 404}
            assert change_password(client).status_code in {401, 403, 404}

    def test_the_office_host_and_the_central_login_host_never_serve_it(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        admin = platform_site.add_admin()
        with schema_context(PUBLIC_SCHEMA):
            access = issue_tokens(admin).access
        for client in (abc.new_client(), api_client(CENTRAL_HOST)):
            use_access_token(client, access)
            assert client.get(ME_URL).status_code in {401, 403, 404}
            response = patch_me(client, {"name": "X"})
            assert response.status_code in {401, 403, 404}
            assert change_password(client).status_code in {401, 403, 404}
        assert stored(admin).name == admin.name

    def test_only_patch_and_get_on_me_and_only_post_on_password(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        client = platform_site.client

        assert client.put(ME_URL, {}, format="json").status_code == 405
        assert client.delete(ME_URL).status_code == 405
        assert client.post(ME_URL, {}, format="json").status_code == 405
        assert client.get(PASSWORD_URL).status_code == 405
        assert client.patch(PASSWORD_URL, {}, format="json").status_code == 405


class TestNoSecretsInLogs:
    def test_changes_log_ids_only(
        self, platform_site: PlatformContext, admin: PlatformAdmin, json_logs: JsonLogs
    ) -> None:
        response = patch_me(platform_site.client, {"name": "Ops Renamed"})
        use_access_token(platform_site.client, response.json()["access"])
        change_password(platform_site.client)

        text = json_logs.text()
        for secret in ("Ops Renamed", admin.email, PLATFORM_TEST_PASSWORD, NEW_PASSWORD):
            assert secret not in text
        assert json_logs.with_message(f"platform admin changed: id={admin.pk}")


class TestAfterAChange:
    def test_the_new_tokens_open_the_rest_of_the_platform(
        self, platform_site: PlatformContext, admin: PlatformAdmin
    ) -> None:
        response = patch_me(platform_site.client, {"name": "Ops Two"})
        use_access_token(platform_site.client, response.json()["access"])

        assert platform_site.client.get(CONSULTANCIES_URL).status_code == status.HTTP_200_OK
