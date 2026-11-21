"""Each office's Theme: sent with the context, chosen only by a Platform Admin."""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from platform_admin.models import PlatformAdmin
from staff.models import Role
from tests.conftest import (
    ConsultancyContext,
    ConsultancyFactory,
    PlatformContext,
    use_access_token,
)
from tests.support_access import open_as_platform_admin

CONTEXT_URL = "/api/context/"
CURRENT_CONSULTANCY_URL = "/api/consultancy/"
OFFICE_THEME_URL = "/api/configuration/theme/"
PLATFORM_CONSULTANCIES_URL = "/api/platform/consultancies/"
DEFAULT_THEME = {
    "page": "stone",
    "sidebar": "#ffffff",
    "accent": "#047857",
    "dark": {"base": "soft", "sidebar": "darker", "accent": "#047857"},
}
GLACIER_DARK = {"base": "teal", "sidebar": "brand", "accent": "auto"}
GLACIER = {"page": "ice", "sidebar": "#0F3440", "accent": "#0b6e8e", "dark": GLACIER_DARK}
GLACIER_STORED = {**GLACIER, "sidebar": "#0f3440"}


def theme_with(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401  # JSON test bodies
    """Return a valid Theme with some fields replaced."""
    return {**GLACIER, **overrides}


def theme_url(office: ConsultancyContext) -> str:
    """The Platform endpoint that sets an office's Theme."""
    return f"{PLATFORM_CONSULTANCIES_URL}{office.consultancy.public_id}/theme/"


def put_theme(client: APIClient, office: ConsultancyContext, body: object) -> Any:  # noqa: ANN401
    """PUT a Theme for `office` on the Platform."""
    return client.put(theme_url(office), body, format="json")


def theme_of(office: ConsultancyContext) -> Any:  # noqa: ANN401  # JSON
    """The office's Theme as its login screen gets it."""
    return office.new_client().get(CURRENT_CONSULTANCY_URL).json()["theme"]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office whose colours are chosen."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def ops(platform_site: PlatformContext) -> PlatformAdmin:
    """A Platform Admin logged in on the Platform."""
    return platform_site.log_in()


class TestThemeInContext:
    def test_a_new_consultancy_has_the_default_theme(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(CONTEXT_URL)

        assert response.json()["theme"] == DEFAULT_THEME

    def test_the_login_screen_gets_the_theme_without_logging_in(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.get(CURRENT_CONSULTANCY_URL)

        assert response.json() == {
            "name": "ABC Education",
            "theme": DEFAULT_THEME,
            "logo": None,
            "status": "evaluation",
            "status_message": None,
        }


class TestPlatformAdminChoosesTheme:
    def test_saves_a_theme_and_the_office_gets_it(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        response = put_theme(platform_site.client, abc, GLACIER)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["theme"] == GLACIER_STORED
        assert response.json()["id"] == str(abc.consultancy.public_id)
        assert theme_of(abc) == GLACIER_STORED
        abc.log_in_as(Role.TEACHER)
        assert abc.client.get(CONTEXT_URL).json()["theme"] == GLACIER_STORED

    def test_dark_settings_are_optional(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        body = {"page": "sky", "sidebar": "#ffffff", "accent": "#2546c4"}

        response = put_theme(platform_site.client, abc, body)

        assert response.json()["theme"]["dark"] == {
            "base": "soft",
            "sidebar": "darker",
            "accent": "auto",
        }

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            (theme_with(page="purple"), "page"),
            (theme_with(sidebar="white"), "sidebar"),
            (theme_with(accent="#04785"), "accent"),
            (theme_with(dark={"base": "navy", "sidebar": "darker", "accent": "auto"}), "dark"),
            (theme_with(dark={"base": "soft", "sidebar": "glow", "accent": "auto"}), "dark"),
            (theme_with(dark={"base": "soft", "sidebar": "darker", "accent": "blue"}), "dark"),
        ],
    )
    def test_refuses_an_invalid_theme(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        abc: ConsultancyContext,
        body: dict[str, Any],
        field: str,
    ) -> None:
        response = put_theme(platform_site.client, abc, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        assert theme_of(abc) == DEFAULT_THEME

    def test_an_unknown_office_is_not_found(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        response = platform_site.client.put(
            f"{PLATFORM_CONSULTANCIES_URL}999999/theme/", GLACIER, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_requires_a_logged_in_platform_admin(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        response = put_theme(platform_site.new_client(), abc, GLACIER)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert theme_of(abc) == DEFAULT_THEME

    @pytest.mark.parametrize("role", list(Role))
    def test_no_staff_member_can_choose_it_admins_included(
        self, platform_site: PlatformContext, abc: ConsultancyContext, role: Role
    ) -> None:
        staff = abc.log_in_as(role)
        on_platform = platform_site.new_client()
        use_access_token(on_platform, abc.tokens_for(staff).access)

        assert put_theme(on_platform, abc, GLACIER).status_code == status.HTTP_401_UNAUTHORIZED
        office = abc.client.put(OFFICE_THEME_URL, GLACIER, format="json")
        assert office.status_code == status.HTTP_404_NOT_FOUND
        assert put_theme(abc.client, abc, GLACIER).status_code == status.HTTP_404_NOT_FOUND
        assert theme_of(abc) == DEFAULT_THEME

    def test_not_even_from_inside_the_office_during_support_access(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)

        response = visit.client.put(OFFICE_THEME_URL, GLACIER, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert theme_of(abc) == DEFAULT_THEME

    def test_only_put_is_allowed(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        url = theme_url(abc)
        for method in ("get", "post", "patch", "delete"):
            response = getattr(platform_site.client, method)(url)
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_a_form_body_is_refused(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        response = platform_site.client.put(theme_url(abc), "page=ice", content_type="text/plain")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


class TestPlatformSetsTheme:
    def new_consultancy(self, **extra: Any) -> dict[str, Any]:  # noqa: ANN401  # JSON body
        return {
            "name": "Everest Education",
            "subdomain": "everest",
            "contact_email": "info@everest.test",
            **extra,
        }

    def test_creates_a_consultancy_with_the_chosen_theme(
        self, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        response = platform_site.client.post(
            PLATFORM_CONSULTANCIES_URL, self.new_consultancy(theme=GLACIER), format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json()["theme"] == GLACIER_STORED

    def test_without_a_theme_it_gets_the_default(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        response = platform_site.client.post(
            PLATFORM_CONSULTANCIES_URL, self.new_consultancy(), format="json"
        )

        assert response.json()["theme"] == DEFAULT_THEME

    def test_refuses_an_invalid_theme(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        response = platform_site.client.post(
            PLATFORM_CONSULTANCIES_URL,
            self.new_consultancy(theme=theme_with(page="purple")),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "page" in response.json()["theme"]


INJECTIONS = [
    "#047857; background: url(https://evil.example/x)",
    "#047857\n",
    "#04785g",
    "var(--bad)",
    "expression(alert(1))",
    "#" + "0" * 5000,
]


class TestThemeInputIsSafe:
    """A Theme ends up in CSS on every screen, so only the documented values may be stored."""

    @pytest.mark.parametrize("value", INJECTIONS)
    @pytest.mark.parametrize("field", ["sidebar", "accent"])
    def test_refuses_anything_but_a_hex_colour(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        abc: ConsultancyContext,
        field: str,
        value: str,
    ) -> None:
        response = put_theme(platform_site.client, abc, theme_with(**{field: value}))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert theme_of(abc) == DEFAULT_THEME

    @pytest.mark.parametrize("value", INJECTIONS)
    def test_refuses_anything_but_auto_or_hex_for_the_dark_accent(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        abc: ConsultancyContext,
        value: str,
    ) -> None:
        dark = {"base": "soft", "sidebar": "darker", "accent": value}

        response = put_theme(platform_site.client, abc, theme_with(dark=dark))

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_never_stores_keys_it_does_not_know(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        body = theme_with(script="<script>alert(1)</script>")
        body["dark"] = {**GLACIER_DARK, "extra": "x"}

        response = put_theme(platform_site.client, abc, body)

        assert response.json()["theme"] == GLACIER_STORED
        assert theme_of(abc) == GLACIER_STORED

    @pytest.mark.parametrize("body", [[GLACIER], "stone", 7])
    def test_refuses_a_body_that_is_not_a_theme(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        abc: ConsultancyContext,
        body: object,
    ) -> None:
        response = put_theme(platform_site.client, abc, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
