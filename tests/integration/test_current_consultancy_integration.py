"""The current Consultancy is resolved from the request host."""

from __future__ import annotations

from rest_framework import status

from tests.conftest import ConsultancyFactory, api_client, build_host

CURRENT_CONSULTANCY_URL = "/api/consultancy/"

DEFAULT_THEME = {
    "page": "stone",
    "sidebar": "#ffffff",
    "accent": "#047857",
    "dark": {"base": "soft", "sidebar": "darker", "accent": "#047857"},
}


class TestCurrentConsultancy:
    def test_returns_name_of_consultancy_owning_the_host(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.get(CURRENT_CONSULTANCY_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "name": "ABC Education",
            "theme": DEFAULT_THEME,
            "logo": None,
            "status": "evaluation",
            "status_message": None,
        }

    def test_each_consultancy_sees_its_own_name(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")

        abc_response = abc.client.get(CURRENT_CONSULTANCY_URL)
        xyz_response = xyz.client.get(CURRENT_CONSULTANCY_URL)

        assert abc_response.json() == {
            "name": "ABC Education",
            "theme": DEFAULT_THEME,
            "logo": None,
            "status": "evaluation",
            "status_message": None,
        }
        assert xyz_response.json() == {
            "name": "XYZ Overseas",
            "theme": DEFAULT_THEME,
            "logo": None,
            "status": "evaluation",
            "status_message": None,
        }

    def test_unknown_host_returns_not_found(self, make_consultancy: ConsultancyFactory) -> None:
        make_consultancy("ABC Education", "abcedu")
        client = api_client(build_host("unknown"))

        response = client.get(CURRENT_CONSULTANCY_URL)

        assert response.status_code == status.HTTP_404_NOT_FOUND
