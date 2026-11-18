"""The current Staff and Consultancy context the frontend builds its navigation from."""

from __future__ import annotations

import pytest
from rest_framework import status

from staff.models import Role
from tests.conftest import ConsultancyFactory, api_datetime

CONTEXT_URL = "/api/context/"
ALWAYS_ON_MODULES = [
    "students",
    "staff",
    "visits",
    "tasks",
    "history",
    "catalogue",
    "applications",
    "visas",
    "documents",
    "payments",
    "configuration",
]
DEFAULT_THEME = {
    "page": "stone",
    "sidebar": "#ffffff",
    "accent": "#047857",
    "dark": {"base": "soft", "sidebar": "darker", "accent": "#047857"},
}


class TestStaffContext:
    @pytest.mark.parametrize(
        ("role", "expected_role"),
        [
            (Role.ADMIN, "admin"),
            (Role.COUNSELLOR, "counsellor"),
            (Role.FRONT_DESK, "front_desk"),
            (Role.TEACHER, "teacher"),
        ],
    )
    def test_returns_staff_role_consultancy_modules_and_date_display(
        self, make_consultancy: ConsultancyFactory, role: Role, expected_role: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        staff = abc.log_in_as(role)
        evaluation_ends_at = api_datetime(abc.consultancy.evaluation_ends_at)

        response = abc.client.get(CONTEXT_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "staff": {
                "id": staff.pk,
                "name": staff.name,
                "email": staff.email,
                "role": expected_role,
                "colour_mode": "device",
            },
            "consultancy": {
                "name": "ABC Education",
                "logo": None,
                "status": "evaluation",
                "status_message": None,
                "evaluation_ends_at": evaluation_ends_at,
                "office_details": {
                    "contact_email": "",
                    "phone": "",
                    "address_line1": "",
                    "address_line2": "",
                    "website": "",
                },
                "has_contact_email": False,
            },
            "modules": ALWAYS_ON_MODULES,
            "date_display": "AD",
            "page_swipe": False,
            "theme": DEFAULT_THEME,
            "support_access": None,
        }

    def test_requires_a_logged_in_staff_member(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.get(CONTEXT_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
