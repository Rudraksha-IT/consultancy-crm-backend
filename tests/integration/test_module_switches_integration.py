"""Platform Admins switch a Consultancy's Modules; switched-off Modules answer as not found."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from rest_framework import status

from staff.models import Role
from tests.conftest import (
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    use_access_token,
)

CONSULTANCIES_URL = "/api/platform/consultancies/"
CONTEXT_URL = "/api/context/"
ALWAYS_ON = [
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
SWITCHABLE = ["batches", "communication", "accounts", "reports", "clock_in"]
ALL_OFF = dict.fromkeys(SWITCHABLE, False)
ALL_ON = dict.fromkeys(SWITCHABLE, True)
# One of each switchable Module's endpoints that an Admin can read. Each Module's own tests check
# every endpoint and Role (test_batches_integration, test_communication_integration,
# test_fees_integration, test_reports_integration and test_office_networks_integration).
MODULE_URLS = {
    "batches": "/api/batches/",
    "communication": "/api/messages/",
    "accounts": "/api/accounts/student-fees/",
    "reports": "/api/reports/students/?months=6",
    "clock_in": "/api/office-networks/",
}
MODULES = list(MODULE_URLS)


def modules_url(consultancy_id: object) -> str:
    """Return the URL that saves a Consultancy's Module switches."""
    return f"{CONSULTANCIES_URL}{consultancy_id}/modules/"


def save(platform_site: PlatformContext, office: ConsultancyContext, body: object) -> Any:  # noqa: ANN401  # JSON response
    """Save Module switches for `office` as the logged-in Platform Admin."""
    return platform_site.client.put(modules_url(office.consultancy.public_id), body, format="json")


@pytest.fixture
def office(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """A Consultancy with its Admin logged in."""
    context = make_consultancy("ABC Education", "abcedu")
    context.log_in_as(Role.ADMIN)
    return context


@pytest.fixture
def platform(platform_site: PlatformContext) -> PlatformContext:
    """The Platform with a Platform Admin logged in."""
    platform_site.log_in()
    return platform_site


class TestDefaults:
    def test_a_new_consultancy_has_only_the_always_on_modules(
        self, office: ConsultancyContext
    ) -> None:
        response = office.client.get(CONTEXT_URL)

        assert response.json()["modules"] == ALWAYS_ON

    @pytest.mark.parametrize("module", MODULES)
    def test_switched_off_module_endpoint_is_not_found(
        self, office: ConsultancyContext, module: str
    ) -> None:
        response = office.client.get(MODULE_URLS[module])

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestSaveFeatures:
    def test_switching_on_shows_in_the_context_and_the_platform_list(
        self, platform: PlatformContext, office: ConsultancyContext
    ) -> None:
        response = save(platform, office, {**ALL_OFF, "batches": True, "reports": True})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["modules"] == [*ALWAYS_ON, "batches", "reports"]
        assert office.client.get(CONTEXT_URL).json()["modules"] == [
            *ALWAYS_ON,
            "batches",
            "reports",
        ]
        listed = platform.client.get(CONSULTANCIES_URL).json()
        assert listed[0]["modules"] == [*ALWAYS_ON, "batches", "reports"]

    @pytest.mark.parametrize("module", MODULES)
    def test_switched_on_module_endpoint_answers(
        self, platform: PlatformContext, office: ConsultancyContext, module: str
    ) -> None:
        save(platform, office, {**ALL_OFF, module: True})

        response = office.client.get(MODULE_URLS[module])

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("module", MODULES)
    def test_switching_off_again_hides_it_and_on_again_restores_it(
        self, platform: PlatformContext, office: ConsultancyContext, module: str
    ) -> None:
        save(platform, office, ALL_ON)
        save(platform, office, ALL_OFF)

        assert office.client.get(MODULE_URLS[module]).status_code == 404
        assert module not in office.client.get(CONTEXT_URL).json()["modules"]

        save(platform, office, ALL_ON)

        assert office.client.get(MODULE_URLS[module]).status_code == 200

    def test_unknown_consultancy_is_not_found(self, platform: PlatformContext) -> None:
        response = platform.client.put(modules_url(uuid.uuid4()), ALL_ON, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logs_each_change_with_ids_only(
        self, platform: PlatformContext, office: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        save(platform, office, {**ALL_OFF, "accounts": True})
        save(platform, office, {**ALL_OFF, "accounts": True})

        lines = json_logs.with_message("Module switched")
        assert [line["message"] for line in lines] == [
            f"Module switched: consultancy_id={office.consultancy.pk}, module=accounts, on=True"
        ]


class TestRefusedInput:
    @pytest.mark.parametrize("module", ALWAYS_ON)
    def test_always_on_modules_cannot_be_switched_off(
        self, platform: PlatformContext, office: ConsultancyContext, module: str
    ) -> None:
        response = save(platform, office, {**ALL_ON, module: False})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {module: ["This is always on and can't be switched off."]}
        assert office.client.get(CONTEXT_URL).json()["modules"] == ALWAYS_ON

    @pytest.mark.parametrize(
        "name",
        ["unknown", "Batches", "batches ", "<script>alert(1)</script>", "'; DROP TABLE x;--"],
    )
    def test_unknown_modules_are_refused_and_never_stored(
        self, platform: PlatformContext, office: ConsultancyContext, name: str
    ) -> None:
        response = save(platform, office, {**ALL_ON, name: True})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown Module."]}
        assert name not in response.content.decode()
        assert office.client.get(CONTEXT_URL).json()["modules"] == ALWAYS_ON

    @pytest.mark.parametrize("value", ["true", 1, 0, None, [], {}, "on"])
    def test_switches_must_be_true_or_false(
        self, platform: PlatformContext, office: ConsultancyContext, value: object
    ) -> None:
        response = save(platform, office, {**ALL_OFF, "batches": value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"batches": ["Must be true or false."]}

    def test_every_switchable_module_is_required(
        self, platform: PlatformContext, office: ConsultancyContext
    ) -> None:
        response = save(platform, office, {"batches": True})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.json()) == {"communication", "accounts", "reports", "clock_in"}
        assert office.client.get(CONTEXT_URL).json()["modules"] == ALWAYS_ON

    @pytest.mark.parametrize("body", [["batches"], "batches", 1, None])
    def test_body_must_be_an_object(
        self, platform: PlatformContext, office: ConsultancyContext, body: object
    ) -> None:
        response = save(platform, office, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_only_put_is_allowed(
        self, platform: PlatformContext, office: ConsultancyContext, method: str
    ) -> None:
        call = getattr(platform.client, method)

        response = call(modules_url(office.consultancy.public_id), ALL_ON, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestWhoCanSwitch:
    def test_anonymous_is_refused(
        self, platform_site: PlatformContext, office: ConsultancyContext
    ) -> None:
        response = save(platform_site, office, ALL_ON)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert office.client.get(CONTEXT_URL).json()["modules"] == ALWAYS_ON

    @pytest.mark.parametrize("role", list(Role))
    def test_staff_of_any_role_are_refused(
        self, platform_site: PlatformContext, office: ConsultancyContext, role: Role
    ) -> None:
        staff = office.add_staff(role, email=f"switcher-{role.value}@abc.test")
        client = platform_site.new_client()
        use_access_token(client, office.tokens_for(staff).access)

        response = client.put(modules_url(office.consultancy.public_id), ALL_ON, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert office.client.get(CONTEXT_URL).json()["modules"] == ALWAYS_ON

    def test_the_endpoint_does_not_exist_on_a_consultancy_subdomain(
        self, office: ConsultancyContext
    ) -> None:
        response = office.client.put(
            modules_url(office.consultancy.public_id), ALL_ON, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestModuleAccess:
    @pytest.mark.parametrize("role", list(Role))
    @pytest.mark.parametrize("module", MODULES)
    def test_no_role_reaches_a_switched_off_module(
        self, make_consultancy: ConsultancyFactory, role: Role, module: str
    ) -> None:
        office = make_consultancy("ABC Education", "abcedu")
        office.log_in_as(role)

        assert office.client.get(MODULE_URLS[module]).status_code == 404

    @pytest.mark.parametrize("module", MODULES)
    def test_logged_out_is_refused_whether_on_or_off(
        self, platform: PlatformContext, office: ConsultancyContext, module: str
    ) -> None:
        logged_out = office.new_client()
        before = logged_out.get(MODULE_URLS[module]).status_code
        save(platform, office, ALL_ON)
        after = logged_out.get(MODULE_URLS[module]).status_code

        assert (before, after) == (401, 401)
