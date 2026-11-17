"""Settings → Swipe between pages: each person's own choice, off until they switch it on.

Pulling past the end of a page to open the next one caused pages to change by accident. It was
first an office-wide switch in Configuration (2026-11-16); it is now in each Staff member's own
Settings (asked 2026-11-17), saved through My details like light/dark. The context tells the
screens whether the person logged in uses it.
"""

from __future__ import annotations

import pytest
from rest_framework import status

from staff.models import Role
from tests.conftest import ConsultancyFactory, JsonLogs, PlatformContext, use_access_token

CONTEXT_URL = "/api/context/"
ME_URL = "/api/me/"
OLD_CONFIGURATION_URL = "/api/configuration/page-swipe/"
NOT_A_BOOLEAN = "Must be true or false."
EVERY_ROLE = [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER]


class TestMyPageSwipe:
    def test_off_for_someone_new(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.COUNSELLOR)

        assert abc.client.get(CONTEXT_URL).json()["page_swipe"] is False
        assert abc.client.get(ME_URL).json()["page_swipe"] is False

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_anyone_switches_it_on_for_themselves_and_off_again(
        self, make_consultancy: ConsultancyFactory, role: Role
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(role)

        switched_on = abc.client.patch(ME_URL, {"page_swipe": True}, format="json")

        assert switched_on.status_code == status.HTTP_200_OK
        assert switched_on.json()["page_swipe"] is True
        assert abc.client.get(CONTEXT_URL).json()["page_swipe"] is True
        switched_off = abc.client.patch(ME_URL, {"page_swipe": False}, format="json")
        assert switched_off.json()["page_swipe"] is False
        assert abc.client.get(CONTEXT_URL).json()["page_swipe"] is False

    def test_my_choice_never_changes_a_colleagues(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        colleague = abc.new_client()
        use_access_token(colleague, abc.tokens_for(abc.add_staff(Role.COUNSELLOR)).access)

        abc.client.patch(ME_URL, {"page_swipe": True}, format="json")

        assert abc.client.get(CONTEXT_URL).json()["page_swipe"] is True
        assert colleague.get(CONTEXT_URL).json()["page_swipe"] is False

    def test_other_details_are_left_as_they_are(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.TEACHER)
        abc.client.patch(ME_URL, {"colour_mode": "dark"}, format="json")

        abc.client.patch(ME_URL, {"page_swipe": True}, format="json")

        me = abc.client.get(ME_URL).json()
        assert me["colour_mode"] == "dark"
        assert me["page_swipe"] is True

    def test_requires_a_logged_in_staff_member(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.patch(ME_URL, {"page_swipe": True}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_not_on_the_platform_address(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        response = platform_site.client.patch(ME_URL, {"page_swipe": True}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_office_wide_switch_in_configuration_is_gone(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(OLD_CONFIGURATION_URL, {"page_swipe": True}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert abc.client.get(CONTEXT_URL).json()["page_swipe"] is False

    def test_logged_without_anything_typed(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        me = abc.log_in_as(Role.COUNSELLOR)

        abc.client.patch(ME_URL, {"page_swipe": True}, format="json")

        messages = [entry["message"] for entry in json_logs.entries()]
        assert f"My details saved: staff_id={me.pk}" in messages


class TestMyPageSwipeInputIsSafe:
    @pytest.mark.parametrize(
        "value",
        [
            "true",
            "on",
            1,
            0,
            None,
            "",
            [True],
            {"on": True},
            pytest.param("t" * 5000, id="over-long"),
            "<script>alert(1)</script>",
            "true'; DROP TABLE staff_staff; --",
        ],
    )
    def test_refuses_anything_but_true_or_false(
        self, make_consultancy: ConsultancyFactory, value: object
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.patch(ME_URL, {"page_swipe": value}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"page_swipe": [NOT_A_BOOLEAN]}
        assert abc.client.get(CONTEXT_URL).json()["page_swipe"] is False

    def test_a_form_body_is_refused(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.patch(ME_URL, {"page_swipe": "true"})

        assert response.status_code in {
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        }
        assert abc.client.get(CONTEXT_URL).json()["page_swipe"] is False


class TestMyPageSwipeIsolation:
    """One office's people never change another office's, even with the same Role."""

    def test_switching_it_on_in_one_office_leaves_another_off(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)

        abc.client.patch(ME_URL, {"page_swipe": True}, format="json")

        assert abc.client.get(CONTEXT_URL).json()["page_swipe"] is True
        assert xyz.client.get(CONTEXT_URL).json()["page_swipe"] is False
