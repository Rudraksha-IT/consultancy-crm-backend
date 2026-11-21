"""The Platform knows offices and Platform Admins only by UUID (ADR 0027, spec 111; issue 19).

Every Platform answer names them by UUID; an address with a number, a malformed id or an
unknown UUID finds nothing (404), never a server error.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from rest_framework import status

from tests.record_ids import assert_no_record_numbers, is_uuid

if TYPE_CHECKING:
    from tests.conftest import ConsultancyContext, ConsultancyFactory, PlatformContext

CONSULTANCIES_URL = "/api/platform/consultancies/"
ADMINS_URL = "/api/platform/admins/"
# Each office address on the Platform, the method it takes, and a body it accepts.
OFFICE_ADDRESSES = [
    ("status/", "post", {"status": "terminated", "reason": "Contract ended"}),
    ("modules/", "put", {"modules": []}),
    ("theme/", "put", {"preset": "glacier"}),
    ("support-visits/", "post", {"reason": "Admin asked for help"}),
    ("admins/", "post", {"name": "Sita Sharma", "email": "sita@office.com"}),
    ("retry-setup/", "post", {}),
]
ADMIN_ADDRESSES = ["resend/", "deactivate/", "reactivate/"]
BAD_IDS = ["not-a-uuid", "16abc", "3f8a2c1e9b7d4c2a8e1f0a1b2c3d4e5f", "%27%20OR%201%3D1", "-1"]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office the Platform works on."""
    return make_consultancy("ABC Education", "abcedu")


class TestAnswers:
    """The Platform's lists name offices and Platform Admins by UUID."""

    def test_the_office_list(self, platform_site: PlatformContext, abc: ConsultancyContext) -> None:
        platform_site.log_in()

        answer = platform_site.client.get(CONSULTANCIES_URL).json()

        assert_no_record_numbers(answer)
        assert answer[0]["id"] == str(abc.consultancy.public_id)

    def test_the_platform_admins(self, platform_site: PlatformContext) -> None:
        me = platform_site.log_in()

        answer = platform_site.client.get(ADMINS_URL).json()

        assert_no_record_numbers(answer)
        assert answer[0]["id"] == str(me.public_id)

    def test_a_support_visit(self, platform_site: PlatformContext, abc: ConsultancyContext) -> None:
        platform_site.log_in()
        url = f"{CONSULTANCIES_URL}{abc.consultancy.public_id}/support-visits/"

        started = platform_site.client.post(url, {"reason": "Help"}, format="json").json()

        assert_no_record_numbers(started)
        assert is_uuid(started["visit"]["id"])
        assert started["visit"]["consultancy"]["id"] == str(abc.consultancy.public_id)


class TestRefusedAddresses:
    """Only an office's or Platform Admin's own UUID finds them."""

    @pytest.mark.parametrize(("suffix", "method", "body"), OFFICE_ADDRESSES)
    def test_an_office_by_number_is_not_found(
        self,
        platform_site: PlatformContext,
        abc: ConsultancyContext,
        suffix: str,
        method: str,
        body: dict[str, object],
    ) -> None:
        platform_site.log_in()
        url = f"{CONSULTANCIES_URL}{abc.consultancy.pk}/{suffix}"

        response = getattr(platform_site.client, method)(url, body, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("address", OFFICE_ADDRESSES)
    @pytest.mark.parametrize("bad", [*BAD_IDS, "00000000-0000-4000-8000-000000000000"])
    def test_a_malformed_or_unknown_office_is_not_found(
        self,
        platform_site: PlatformContext,
        abc: ConsultancyContext,
        address: tuple[str, str, dict[str, object]],
        bad: str,
    ) -> None:
        platform_site.log_in()
        suffix, method, body = address

        response = getattr(platform_site.client, method)(
            f"{CONSULTANCIES_URL}{bad}/{suffix}", body, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("suffix", ADMIN_ADDRESSES)
    def test_a_platform_admin_by_number_is_not_found(
        self, platform_site: PlatformContext, suffix: str
    ) -> None:
        platform_site.log_in()
        other = platform_site.add_admin(email="other@platform.com")

        response = platform_site.client.post(f"{ADMINS_URL}{other.pk}/{suffix}", format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("suffix", ADMIN_ADDRESSES)
    @pytest.mark.parametrize("bad", BAD_IDS)
    def test_a_malformed_platform_admin_is_not_found(
        self, platform_site: PlatformContext, suffix: str, bad: str
    ) -> None:
        platform_site.log_in()

        response = platform_site.client.post(f"{ADMINS_URL}{bad}/{suffix}", format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("suffix", ADMIN_ADDRESSES)
    def test_an_unknown_platform_admin_is_not_found(
        self, platform_site: PlatformContext, suffix: str
    ) -> None:
        platform_site.log_in()

        response = platform_site.client.post(f"{ADMINS_URL}{uuid.uuid4()}/{suffix}", format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_offices_uuid_is_not_a_platform_admin(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        platform_site.log_in()

        response = platform_site.client.post(
            f"{ADMINS_URL}{abc.consultancy.public_id}/deactivate/", format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
