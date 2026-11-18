"""Configuration → General → Office details (spec stories 11-16).

An Admin (or a Platform Admin in Support Access) sets the office's contact email, phone, address
(two lines) and website. Each is checked; empty is allowed. Every Role reads them, and whether the
contact email is set, from the context, so screens can show "add the contact email first".
"""

from __future__ import annotations

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status
from rest_framework.test import APIClient

from staff.models import Role, Staff, StaffStatus
from tests.conftest import ConsultancyFactory, JsonLogs, PlatformContext
from tests.support_access import open_as_platform_admin

CONTEXT_URL = "/api/context/"
OFFICE_DETAILS_URL = "/api/configuration/office-details/"

EMPTY = {
    "contact_email": "",
    "phone": "",
    "address_line1": "",
    "address_line2": "",
    "website": "",
}
FULL = {
    "contact_email": "info@himal-education.com",
    "phone": "+977 (01) 441-2345",
    "address_line1": "Putalisadak, Kathmandu 44600",
    "address_line2": "Second floor, Sunrise Plaza",
    "website": "https://himal-education.com",
}
ALL_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER)


def _context_details(client: APIClient) -> dict[str, object]:
    consultancy: dict[str, object] = client.get(CONTEXT_URL).json()["consultancy"]
    return consultancy


class TestOfficeDetailsInContext:
    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_a_new_consultancy_has_none_and_no_contact_email(
        self, make_consultancy: ConsultancyFactory, role: Role
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(role)

        consultancy = _context_details(abc.client)

        assert consultancy["office_details"] == EMPTY
        assert consultancy["has_contact_email"] is False

    def test_the_public_consultancy_answer_never_shows_them(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        abc.client.put(OFFICE_DETAILS_URL, FULL, format="json")

        answer = abc.new_client().get("/api/consultancy/").json()

        assert "office_details" not in answer
        assert FULL["contact_email"] not in str(answer)


class TestAdminSavesOfficeDetails:
    def test_saves_every_field_and_every_role_reads_them(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(OFFICE_DETAILS_URL, FULL, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"office_details": FULL, "has_contact_email": True}
        assert _context_details(abc.client)["office_details"] == FULL
        for role in (Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER):
            abc.log_in_as(role)
            consultancy = _context_details(abc.client)
            assert consultancy["office_details"] == FULL
            assert consultancy["has_contact_email"] is True

    def test_empty_fields_are_allowed_and_clear_what_was_there(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        abc.client.put(OFFICE_DETAILS_URL, FULL, format="json")

        response = abc.client.put(OFFICE_DETAILS_URL, EMPTY, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"office_details": EMPTY, "has_contact_email": False}
        assert _context_details(abc.client)["office_details"] == EMPTY

    def test_only_some_fields_set(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        body = {**EMPTY, "phone": "01-4412345", "website": "http://himal.com.np/contact"}

        response = abc.client.put(OFFICE_DETAILS_URL, body, format="json")

        assert response.json() == {"office_details": body, "has_contact_email": False}

    def test_text_is_trimmed_and_address_spaces_are_tidied(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        body = {
            "contact_email": "  info@himal.com  ",
            "phone": " 01-4412345 ",
            "address_line1": "  Putalisadak,    Kathmandu  ",
            "address_line2": "  Second   floor ",
            "website": " https://himal.com ",
        }

        response = abc.client.put(OFFICE_DETAILS_URL, body, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["office_details"] == {
            "contact_email": "info@himal.com",
            "phone": "01-4412345",
            "address_line1": "Putalisadak, Kathmandu",
            "address_line2": "Second floor",
            "website": "https://himal.com",
        }

    def test_an_address_keeps_html_shaped_text_as_words(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        # An address is free text; screens and emails escape it, so it is stored exactly.
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        body = {**EMPTY, "address_line1": '<b>Tower</b> & Sons\' "Plaza"'}

        response = abc.client.put(OFFICE_DETAILS_URL, body, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["office_details"]["address_line1"] == body["address_line1"]

    def test_the_log_line_never_carries_the_details(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        abc.client.put(OFFICE_DETAILS_URL, FULL, format="json")

        assert json_logs.with_message("Office details changed")
        for value in FULL.values():
            assert value not in json_logs.text()

    def test_a_platform_admin_in_support_access_can_change_them(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        visit = open_as_platform_admin(platform_site, abc)

        response = visit.client.put(OFFICE_DETAILS_URL, FULL, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert _context_details(visit.client)["office_details"] == FULL
        abc.log_in_as(Role.COUNSELLOR)
        assert _context_details(abc.client)["office_details"] == FULL


class TestWhoCanChangeThem:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_can_change_them(
        self, make_consultancy: ConsultancyFactory, role: Role
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(role)

        response = abc.client.put(OFFICE_DETAILS_URL, FULL, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == {"detail": "Only an Admin can change the Configuration."}
        assert _context_details(abc.client)["office_details"] == EMPTY

    def test_requires_a_logged_in_staff_member(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.put(OFFICE_DETAILS_URL, FULL, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_deactivated_admin_cannot_change_them(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        admin = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        response = abc.client.put(OFFICE_DETAILS_URL, FULL, format="json")

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}

    def test_not_on_the_platform_address(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        response = platform_site.client.put(OFFICE_DETAILS_URL, FULL, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_only_put_is_allowed(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(OFFICE_DETAILS_URL).status_code == 405
        assert abc.client.delete(OFFICE_DETAILS_URL).status_code == 405
        for send in (abc.client.post, abc.client.patch):
            assert send(OFFICE_DETAILS_URL, FULL, format="json").status_code == 405
        assert _context_details(abc.client)["office_details"] == EMPTY


EMAIL_MESSAGE = "Enter a real email address, like info@youroffice.com."
PHONE_MESSAGE = "A phone number can have digits, spaces, + ( ) and -."
WEBSITE_MESSAGE = "The website must start with https:// or http://."
SCRIPT = "<script>alert(1)</script>"
SQL = "'; DROP TABLE tenancy_consultancy; --"
CSS = "red; background: url(javascript:alert(1))"


class TestOfficeDetailsInputIsSafe:
    @pytest.mark.parametrize(
        "value",
        [
            "info",
            "info@",
            "@himal.com",
            "info@himal",
            "info @himal.com",
            "info@himal.com\nBcc: everyone@example.com",
            "info@himal.com\r\n",
            '"Himal" <info@himal.com>',
            "info@himal.com, other@himal.com",
            SCRIPT,
            SQL,
            CSS,
            pytest.param("a" * 250 + "@himal.com", id="over-long"),
            None,
            7,
            True,
            ["info@himal.com"],
            {"email": "info@himal.com"},
        ],
    )
    def test_refuses_a_contact_email_that_isnt_one_address(
        self, make_consultancy: ConsultancyFactory, value: object
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(
            OFFICE_DETAILS_URL, {**FULL, "contact_email": value}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"contact_email": [EMAIL_MESSAGE]}
        assert _context_details(abc.client)["office_details"] == EMPTY

    @pytest.mark.parametrize(
        "value",
        [
            "phone me",
            "01.4412345",
            "12345",
            "+977 01 441 2345 ext 7",
            "01-4412345\n",
            SCRIPT,
            SQL,
            CSS,
            pytest.param("1" * 31, id="over-long"),
            None,
            14412345,
            ["01-4412345"],
        ],
    )
    def test_refuses_a_phone_that_isnt_a_number(
        self, make_consultancy: ConsultancyFactory, value: object
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(OFFICE_DETAILS_URL, {**FULL, "phone": value}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"phone": [PHONE_MESSAGE]}

    @pytest.mark.parametrize(
        "value",
        [
            "himal.com",
            "www.himal.com",
            "ftp://himal.com",
            "javascript:alert(1)",
            "https://",
            "https://himal",
            "https://himal .com",
            'https://himal.com/"><script>alert(1)</script>',
            "https://himal.com/<b>",
            "https://himal.com\n",
            "data:text/html,<script>alert(1)</script>",
            SCRIPT,
            SQL,
            pytest.param("https://himal.com/" + "a" * 200, id="over-long"),
            None,
            1,
            {"url": "https://himal.com"},
        ],
    )
    def test_refuses_a_website_that_isnt_a_web_address(
        self, make_consultancy: ConsultancyFactory, value: object
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(OFFICE_DETAILS_URL, {**FULL, "website": value}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"website": [WEBSITE_MESSAGE]}

    @pytest.mark.parametrize("field", ["address_line1", "address_line2"])
    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ("Putalisadak\nKathmandu", "Use one line, with no line breaks."),
            ("Putalisadak\x00", "Use one line, with no line breaks."),
            ("a" * 101, "Use at most 100 characters."),
            (None, "Enter the address as text."),
            (44600, "Enter the address as text."),
            (["Putalisadak"], "Enter the address as text."),
        ],
    )
    def test_refuses_an_address_line_that_isnt_one_short_line(
        self, make_consultancy: ConsultancyFactory, field: str, value: object, message: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(OFFICE_DETAILS_URL, {**FULL, field: value}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {field: [message]}

    @pytest.mark.parametrize("missing", list(EMPTY))
    def test_every_field_must_be_sent(
        self, make_consultancy: ConsultancyFactory, missing: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        body = {key: value for key, value in FULL.items() if key != missing}

        response = abc.client.put(OFFICE_DETAILS_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == [missing]
        assert _context_details(abc.client)["office_details"] == EMPTY

    def test_refuses_unknown_fields_and_stores_nothing(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(
            OFFICE_DETAILS_URL, {**FULL, "smtp_password": "secret"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert _context_details(abc.client)["office_details"] == EMPTY

    @pytest.mark.parametrize("body", [["info@himal.com"], "info@himal.com", 7])
    def test_refuses_a_body_that_is_not_an_object(
        self, make_consultancy: ConsultancyFactory, body: object
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(OFFICE_DETAILS_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_refuses_a_body_that_is_not_json(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(
            OFFICE_DETAILS_URL, "contact_email=info@himal.com", content_type="text/plain"
        )

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_nothing_is_saved_when_one_field_is_wrong(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        abc.client.put(OFFICE_DETAILS_URL, FULL, format="json")
        body = {**EMPTY, "contact_email": "new@himal.com", "website": "himal.com"}

        response = abc.client.put(OFFICE_DETAILS_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert _context_details(abc.client)["office_details"] == FULL
