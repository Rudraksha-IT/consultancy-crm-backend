"""Platform Admins log in on the Platform subdomain only, and are not Staff of any Consultancy."""

from __future__ import annotations

import pytest
from django_tenants.utils import schema_context
from rest_framework import status
from rest_framework.test import APIClient

from platform_admin.models import PlatformAdmin
from staff.models import Role
from tests.conftest import (
    PLATFORM_TEST_PASSWORD,
    PUBLIC_SCHEMA,
    TEST_PASSWORD,
    ConsultancyFactory,
    PlatformContext,
    use_access_token,
)

PLATFORM_LOGIN_URL = "/api/platform/auth/login/"
PLATFORM_ME_URL = "/api/platform/me/"
STAFF_LOGIN_URL = "/api/auth/login/"
WRONG_PASSWORD = "not-the-password"  # noqa: S105  # test-only credential


def post_platform_login(
    client: APIClient, email: str, password: str = PLATFORM_TEST_PASSWORD
) -> int:
    """Submit the Platform login form and return the response status code."""
    response = client.post(
        PLATFORM_LOGIN_URL, {"email": email, "password": password}, format="json"
    )
    return response.status_code


class TestPlatformLogin:
    def test_platform_admin_logs_in_on_the_platform_subdomain(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()

        response = platform_site.client.post(
            PLATFORM_LOGIN_URL,
            {"email": admin.email, "password": PLATFORM_TEST_PASSWORD},
            format="json",
        )

        me = {
            "platform_admin": {"id": str(admin.public_id), "name": admin.name, "email": admin.email}
        }
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["platform_admin"] == me["platform_admin"]
        use_access_token(platform_site.client, response.json()["access"])
        assert platform_site.client.get(PLATFORM_ME_URL).json() == me

    def test_email_is_matched_regardless_of_case(self, platform_site: PlatformContext) -> None:
        platform_site.add_admin(email="ops@platform.test")

        status_code = post_platform_login(platform_site.client, " Ops@Platform.TEST ")

        assert status_code == status.HTTP_200_OK

    def test_wrong_password_is_rejected(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()

        response = platform_site.client.post(
            PLATFORM_LOGIN_URL, {"email": admin.email, "password": WRONG_PASSWORD}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Invalid email or password."}

    def test_unknown_email_is_rejected(self, platform_site: PlatformContext) -> None:
        status_code = post_platform_login(platform_site.client, "nobody@platform.test")

        assert status_code == status.HTTP_400_BAD_REQUEST

    def test_inactive_platform_admin_cannot_log_in(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        with schema_context(PUBLIC_SCHEMA):
            PlatformAdmin.objects.filter(pk=admin.pk).update(is_active=False)

        assert post_platform_login(platform_site.client, admin.email) == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"email": "not-an-email", "password": TEST_PASSWORD},
            {"email": f"{'a' * 250}@platform.test", "password": TEST_PASSWORD},
            {"email": "ops@platform.test", "password": "x" * 129},
            {"email": "ops@platform.test", "password": {"a": 1}},
        ],
    )
    def test_refused_input_is_400(
        self, platform_site: PlatformContext, body: dict[str, object]
    ) -> None:
        response = platform_site.client.post(PLATFORM_LOGIN_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_nobody_logged_in_is_401(self, platform_site: PlatformContext) -> None:
        response = platform_site.client.get(PLATFORM_ME_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestPlatformIsSeparateFromConsultancies:
    def test_platform_login_does_not_exist_on_a_consultancy_subdomain(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        admin = platform_site.add_admin()

        assert post_platform_login(abc.client, admin.email) == status.HTTP_404_NOT_FOUND

    def test_staff_login_does_not_exist_on_the_platform_subdomain(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        response = platform_site.client.post(
            STAFF_LOGIN_URL, {"email": counsellor.email, "password": TEST_PASSWORD}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_platform_admin_cannot_log_in_as_staff_of_a_consultancy(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        admin = platform_site.add_admin()

        response = abc.client.post(
            STAFF_LOGIN_URL, {"email": admin.email, "password": TEST_PASSWORD}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_platform_admin_token_is_rejected_on_a_consultancy_subdomain(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.add_staff(Role.ADMIN)  # Same primary key as the Platform Admin.
        tokens = platform_site.tokens_for(platform_site.add_admin())
        use_access_token(abc.client, tokens.access)

        response = abc.client.get("/api/context/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_staff_token_is_rejected_on_the_platform_subdomain(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        platform_site.add_admin()  # Same primary key as the Staff member.
        tokens = abc.tokens_for(abc.add_staff(Role.ADMIN))
        use_access_token(platform_site.client, tokens.access)

        response = platform_site.client.get(PLATFORM_ME_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
