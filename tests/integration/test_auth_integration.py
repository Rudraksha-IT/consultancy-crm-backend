"""Staff log in to their own Consultancy with email and password (tokens: ADR 0003)."""

from __future__ import annotations

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status
from rest_framework.test import APIClient

from staff.models import Role, Staff, StaffStatus
from tests.conftest import TEST_PASSWORD, ConsultancyContext, ConsultancyFactory, use_access_token

LOGIN_URL = "/api/auth/login/"
CONTEXT_URL = "/api/context/"
WRONG_PASSWORD = "not-the-password"  # noqa: S105  # test-only credential


def post_login(client: APIClient, email: str, password: str = TEST_PASSWORD) -> int:
    """Submit the login form and return the response status code."""
    response = client.post(LOGIN_URL, {"email": email, "password": password}, format="json")
    return response.status_code


def deactivate(consultancy: ConsultancyContext, staff: Staff) -> None:
    """Mark a Staff member inactive, as an Admin deactivating them would."""
    with tenant_context(consultancy.consultancy):
        Staff.objects.filter(pk=staff.pk).update(status=StaffStatus.DEACTIVATED)


class TestLogin:
    def test_staff_logs_in_with_email_and_password(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.post(
            LOGIN_URL, {"email": counsellor.email, "password": TEST_PASSWORD}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["context"]["staff"]["email"] == counsellor.email

    def test_logged_in_staff_can_fetch_their_context(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        response = abc.client.post(
            LOGIN_URL, {"email": counsellor.email, "password": TEST_PASSWORD}, format="json"
        )
        use_access_token(abc.client, response.json()["access"])

        response = abc.client.get(CONTEXT_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["staff"]["email"] == counsellor.email

    def test_email_is_matched_regardless_of_case(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.add_staff(Role.ADMIN, email="sita@abcedu.com")

        assert post_login(abc.client, " Sita@ABCedu.com ") == status.HTTP_200_OK

    def test_wrong_password_is_rejected(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.post(
            LOGIN_URL, {"email": counsellor.email, "password": WRONG_PASSWORD}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Invalid email or password."}

    def test_unknown_email_is_rejected(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        assert post_login(abc.client, "nobody@abcedu.com") == status.HTTP_400_BAD_REQUEST

    def test_missing_fields_are_rejected(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.post(LOGIN_URL, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.json()) == {"email", "password"}

    def test_inactive_staff_cannot_log_in(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        deactivate(abc, counsellor)

        assert post_login(abc.client, counsellor.email) == status.HTTP_400_BAD_REQUEST

    def test_deactivated_staff_loses_their_open_login(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.log_in_as(Role.COUNSELLOR)
        deactivate(abc, counsellor)

        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize(
        "body",
        [
            {"email": 12, "password": TEST_PASSWORD},
            {"email": "not-an-email", "password": TEST_PASSWORD},
            {"email": f"{'a' * 250}@abcedu.com", "password": TEST_PASSWORD},
            {"email": "<script>alert(1)</script>@x.com", "password": TEST_PASSWORD},
            {"email": "sita@abcedu.com", "password": ["a"]},
            {"email": "sita@abcedu.com", "password": "x" * 129},
        ],
    )
    def test_refused_input_is_400(
        self, make_consultancy: ConsultancyFactory, body: dict[str, object]
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.post(LOGIN_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize(
        "password", ["' OR '1'='1", "<script>alert(1)</script>", "body{color:red}"]
    )
    def test_injection_shaped_passwords_are_only_wrong_passwords(
        self, make_consultancy: ConsultancyFactory, password: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.post(
            LOGIN_URL, {"email": counsellor.email, "password": password}, format="json"
        )

        assert response.json() == {"detail": "Invalid email or password."}

    def test_unknown_fields_are_ignored(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.post(
            LOGIN_URL,
            {"email": counsellor.email, "password": TEST_PASSWORD, "role": "admin"},
            format="json",
        )

        assert response.json()["context"]["staff"]["role"] == "counsellor"
