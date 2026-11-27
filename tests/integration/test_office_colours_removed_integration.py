"""Office colours are gone (UI revamp, ticket 04): no answer carries a Theme and none is taken."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import status

from platform_admin.models import PlatformAdmin
from staff.models import Role
from tenancy.models import Consultancy
from tests.conftest import ConsultancyContext, ConsultancyFactory, PlatformContext
from tests.support_access import open_as_platform_admin

CONTEXT_URL = "/api/context/"
CURRENT_CONSULTANCY_URL = "/api/consultancy/"
OFFICE_THEME_URL = "/api/configuration/theme/"
OFFICE_DATES_URL = "/api/configuration/dates/"
PLATFORM_CONSULTANCIES_URL = "/api/platform/consultancies/"
OLD_THEME = {
    "page": "ice",
    "sidebar": "#0f3440",
    "accent": "#0b6e8e",
    "dark": {"base": "teal", "sidebar": "brand", "accent": "auto"},
}
# What an old client, or someone trying their luck, might still send as a Theme.
THEMES_SENT = [
    OLD_THEME,
    {"accent": "#047857; background: url(https://evil.example/x)"},
    "<script>alert(1)</script>",
    "'; DROP TABLE tenancy_consultancy; --",
    "#" + "0" * 5000,
    None,
    7,
]
UNKNOWN_FIELD = {"detail": ["Unknown field."]}


def theme_url(office: ConsultancyContext) -> str:
    """Where the Platform used to set an office's Theme."""
    return f"{PLATFORM_CONSULTANCIES_URL}{office.consultancy.public_id}/theme/"


def new_consultancy(**extra: Any) -> dict[str, Any]:  # noqa: ANN401  # JSON body
    """What the Platform's New Consultancy screen sends."""
    return {
        "name": "Everest Education",
        "subdomain": "everest",
        "contact_email": "info@everest.test",
        **extra,
    }


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """An office."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def ops(platform_site: PlatformContext) -> PlatformAdmin:
    """A Platform Admin logged in on the Platform."""
    return platform_site.log_in()


class TestNoAnswerCarriesATheme:
    @pytest.mark.parametrize("role", list(Role))
    def test_the_context_has_no_theme(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = abc.client.get(CONTEXT_URL)

        assert response.status_code == status.HTTP_200_OK
        assert "theme" not in response.json()

    def test_the_login_screen_gets_no_theme(self, abc: ConsultancyContext) -> None:
        response = abc.new_client().get(CURRENT_CONSULTANCY_URL)

        assert response.json() == {
            "name": "ABC Education",
            "logo": None,
            "status": "evaluation",
            "status_message": None,
        }

    def test_the_platforms_list_has_no_theme(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        rows = platform_site.client.get(PLATFORM_CONSULTANCIES_URL).json()

        assert rows
        assert all("theme" not in row for row in rows)

    def test_a_new_consultancy_answers_without_a_theme(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        response = platform_site.client.post(
            PLATFORM_CONSULTANCIES_URL, new_consultancy(), format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert "theme" not in response.json()

    def test_a_consultancy_stores_no_theme(self) -> None:
        assert "theme" not in {field.name for field in Consultancy._meta.get_fields()}


class TestAThemeIsRefused:
    @pytest.mark.parametrize("theme", THEMES_SENT)
    def test_a_new_consultancy_with_a_theme_is_refused_and_nothing_is_made(
        self, platform_site: PlatformContext, ops: PlatformAdmin, theme: object
    ) -> None:
        response = platform_site.client.post(
            PLATFORM_CONSULTANCIES_URL, new_consultancy(theme=theme), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == UNKNOWN_FIELD
        assert not Consultancy.objects.filter(name="Everest Education").exists()

    @pytest.mark.parametrize("method", ["get", "put", "post", "patch", "delete"])
    def test_the_platforms_theme_address_is_gone(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        abc: ConsultancyContext,
        method: str,
    ) -> None:
        response = getattr(platform_site.client, method)(theme_url(abc), OLD_THEME, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_features_of_a_consultancy_refuse_a_theme(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        url = f"{PLATFORM_CONSULTANCIES_URL}{abc.consultancy.public_id}/modules/"

        response = platform_site.client.put(url, {"theme": OLD_THEME}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("role", list(Role))
    def test_the_office_has_no_theme_address(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        for method in ("get", "put", "post", "patch", "delete"):
            response = getattr(abc.client, method)(OFFICE_THEME_URL, OLD_THEME, format="json")
            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_nor_during_support_access(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)

        response = visit.client.put(OFFICE_THEME_URL, OLD_THEME, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("theme", THEMES_SENT)
    def test_configuration_refuses_a_theme_and_saves_nothing(
        self, abc: ConsultancyContext, theme: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(
            OFFICE_DATES_URL, {"date_display": "BS", "theme": theme}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == UNKNOWN_FIELD
        assert abc.client.get(CONTEXT_URL).json()["date_display"] == "AD"
