"""Configuration → Dates: an Admin chooses AD (the default) or BS; the context returns the choice.

Dates are always stored in AD; the setting only changes how the frontend shows and takes them.
"""

from __future__ import annotations

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from staff.models import Role, Staff, StaffStatus
from tests.conftest import ConsultancyFactory, PlatformContext
from tests.integration.test_stage_and_counsellor_integration import make_student

CONTEXT_URL = "/api/context/"
DATES_URL = "/api/configuration/dates/"
PAYMENTS_URL = "/api/students/{id}/payments/"


class TestDateDisplayInContext:
    def test_a_new_consultancy_shows_dates_in_ad(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.FRONT_DESK)

        assert abc.client.get(CONTEXT_URL).json()["date_display"] == "AD"


class TestAdminChangesDateDisplay:
    def test_admin_chooses_bs_and_every_role_gets_it_in_the_context(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(DATES_URL, {"date_display": "BS"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"date_display": "BS"}
        assert abc.client.get(CONTEXT_URL).json()["date_display"] == "BS"
        for role in (Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER):
            abc.log_in_as(role)
            assert abc.client.get(CONTEXT_URL).json()["date_display"] == "BS"

    def test_admin_goes_back_to_ad(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        abc.client.put(DATES_URL, {"date_display": "BS"}, format="json")

        response = abc.client.put(DATES_URL, {"date_display": "AD"}, format="json")

        assert response.json() == {"date_display": "AD"}
        assert abc.client.get(CONTEXT_URL).json()["date_display"] == "AD"

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_can_change_it(
        self, make_consultancy: ConsultancyFactory, role: Role
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(role)

        response = abc.client.put(DATES_URL, {"date_display": "BS"}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == {"detail": "Only an Admin can change the Configuration."}
        assert abc.client.get(CONTEXT_URL).json()["date_display"] == "AD"

    def test_requires_a_logged_in_staff_member(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.put(DATES_URL, {"date_display": "BS"}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_deactivated_admin_cannot_change_it(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        admin = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        response = abc.client.put(DATES_URL, {"date_display": "BS"}, format="json")

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
        assert abc.new_client().get("/api/consultancy/").status_code == status.HTTP_200_OK

    def test_not_on_the_platform_address(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        response = platform_site.client.put(DATES_URL, {"date_display": "BS"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_only_put_is_allowed(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        body = {"date_display": "BS"}

        assert abc.client.get(DATES_URL).status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert abc.client.delete(DATES_URL).status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        for send in (abc.client.post, abc.client.patch):
            response = send(DATES_URL, body, format="json")
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert abc.client.get(CONTEXT_URL).json()["date_display"] == "AD"


class TestDateDisplayInputIsSafe:
    @pytest.mark.parametrize(
        "value",
        [
            "bs",
            "Bs",
            " BS",
            "BS\n",
            "NS",
            "",
            None,
            1,
            True,
            ["BS"],
            {"value": "BS"},
            pytest.param("B" * 5000, id="over-long"),
            "<script>alert(1)</script>",
            "BS'; DROP TABLE tenancy_consultancy; --",
            "AD; color: red",
        ],
    )
    def test_refuses_anything_but_ad_or_bs(
        self, make_consultancy: ConsultancyFactory, value: object
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(DATES_URL, {"date_display": value}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["date_display"]
        assert abc.client.get(CONTEXT_URL).json()["date_display"] == "AD"

    def test_refuses_a_missing_value(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(DATES_URL, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"date_display": ["This field is required."]}

    def test_refuses_unknown_fields_and_stores_nothing(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        body = {"date_display": "BS", "theme": "<script>alert(1)</script>"}

        response = abc.client.put(DATES_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert abc.client.get(CONTEXT_URL).json()["date_display"] == "AD"

    @pytest.mark.parametrize("body", [["BS"], "BS", 7])
    def test_refuses_a_body_that_is_not_an_object(
        self, make_consultancy: ConsultancyFactory, body: object
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(DATES_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert abc.client.get(CONTEXT_URL).json()["date_display"] == "AD"

    def test_refuses_a_body_that_is_not_json(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(DATES_URL, "date_display=BS", content_type="text/plain")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


class TestDatesStayInAd:
    def test_a_payment_date_is_taken_and_given_in_ad_whatever_the_setting(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        abc.client.put(DATES_URL, {"date_display": "BS"}, format="json")
        student = make_student(abc)
        body = {"amount": 5000, "paid_on": "2026-10-20", "method": "cash", "purpose": "PTE"}

        response = abc.client.post(PAYMENTS_URL.format(id=student.pk), body, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["paid_on"] == "2026-10-20"

    def test_a_bs_date_is_refused_where_a_date_is_taken(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        abc.client.put(DATES_URL, {"date_display": "BS"}, format="json")
        student = make_student(abc)
        body = {"amount": 5000, "paid_on": "2083-07-03", "method": "cash", "purpose": "PTE"}

        response = abc.client.post(PAYMENTS_URL.format(id=student.pk), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "paid_on" in response.json()
