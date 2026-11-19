"""Code and Central login emails in the new design (spec 2026-11-18 stories 23-27; issue 05).

Staff Invite and password reset Codes come in the office's look (its name as sender, logo,
Theme colour and Office details); the Central login list and the Platform Admin Invite and
reset Codes come in the Platform's look (the product's name and logo, emerald). Every one is
HTML with a plain-text copy, never needs the contact email, and never puts the Code in a link.
Sent email is read from Django's test mailbox.
"""

from __future__ import annotations

import dataclasses
import re
from email.utils import parseaddr
from typing import TYPE_CHECKING

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django_tenants.utils import schema_context
from rest_framework import status

from emails.looks import PLATFORM_ACCENT, PRODUCT_NAME
from emails.types import OfficeDetails
from platform_admin.brand import set_product_logo
from staff.models import Role
from tenancy.logo import PNG, SVG, CheckedLogo
from tenancy.services import set_logo, set_office_details, set_theme
from tenancy.subdomains import get_central_login_host, get_platform_host
from tenancy.theme import DEFAULT_THEME
from tests.conftest import (
    PUBLIC_SCHEMA,
    ConsultancyContext,
    ConsultancyFactory,
    PlatformContext,
    api_client,
    run_queued_jobs,
)
from tests.support_access import open_as_platform_admin

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pytest_django.fixtures import Settings
    from rest_framework.test import APIClient

STAFF_URL = "/api/staff/"
RESET_URL = "/api/auth/password-reset/"
CENTRAL_URL = "/api/central-login/"
PLATFORM_ADMINS_URL = "/api/platform/admins/"
PLATFORM_RESET_URL = "/api/platform/auth/password-reset/"
OFFICE_BLUE = "#1d4ed8"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
CONTACT_EMAIL = "info@abcedu.example"
DETAILS = OfficeDetails(
    contact_email=CONTACT_EMAIL,
    phone="01-4412345",
    address_line1="Putalisadak",
    address_line2="Kathmandu",
    website="https://abcedu.example",
)
HREF = re.compile(r'href="([^"]*)"')


def outbox() -> list[EmailMultiAlternatives]:
    """Every email Django's test mailbox received, each with its HTML part."""
    sent: list[EmailMultiAlternatives] = []
    for message in mail.outbox:
        assert isinstance(message, EmailMultiAlternatives)
        sent.append(message)
    return sent


def last() -> EmailMultiAlternatives:
    """The last email sent."""
    return outbox()[-1]


def html_of(message: EmailMultiAlternatives) -> str:
    """The email's HTML part."""
    [(html, kind)] = message.alternatives
    assert kind == "text/html"
    return str(html)


def code_in(message: EmailMultiAlternatives) -> str:
    """The 6-digit Code in the plain-text copy."""
    found = re.search(r"\b(\d{6})\b", str(message.body))
    assert found is not None
    return found.group(1)


def assert_code_outside_links(message: EmailMultiAlternatives) -> None:
    """The Code shows in both copies and is never part of any link."""
    code = code_in(message)
    html = html_of(message)
    assert code in html
    links = HREF.findall(html)
    assert all(code not in link for link in links)
    assert all(code not in link for link in re.findall(r"https?://\S+", str(message.body)))


def inline_logo(message: EmailMultiAlternatives) -> list[bytes]:
    """The bytes of every image carried inside the email as the logo."""
    found: list[bytes] = []
    for part in message.message().walk():
        if part.get("Content-ID") == "<logo>":
            payload = part.get_payload(decode=True)
            assert isinstance(payload, bytes)
            found.append(payload)
    return found


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """An office in blue, with no Office details yet."""
    office = make_consultancy("ABC Education", "abcedu")
    set_theme(office.consultancy, dataclasses.replace(DEFAULT_THEME, accent=OFFICE_BLUE))
    return office


@pytest.fixture
def central(db: None) -> Iterator[APIClient]:
    """An API client on the Central login address."""
    yield api_client(get_central_login_host(), HTTP_X_FORWARDED_PROTO="https")


def invite_staff(abc: ConsultancyContext, name: str = "Sita Sharma") -> EmailMultiAlternatives:
    """An Admin adds an employee; return the Invite email."""
    abc.log_in_as(Role.ADMIN)
    body = {
        "name": name,
        "mobile": "9812345670",
        "email": "sita@abcedu.test",
        "job_title": "Counsellor",
        "role": "counsellor",
        "joined_on": "2026-11-01",
    }
    response = abc.client.post(STAFF_URL, body, format="json")
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return last()


def reset_staff(abc: ConsultancyContext) -> EmailMultiAlternatives:
    """A Counsellor asks for a password reset Code; return the email."""
    staff = abc.add_staff(Role.COUNSELLOR)
    response = abc.new_client().post(RESET_URL, {"email": str(staff.email)}, format="json")
    assert response.status_code == status.HTTP_202_ACCEPTED
    return last()


class TestStaffCodeEmailsInTheOfficesLook:
    @pytest.mark.parametrize("send", [invite_staff, reset_staff])
    def test_html_and_plain_text_from_the_office(
        self, abc: ConsultancyContext, settings: Settings, send: object
    ) -> None:
        assert callable(send)
        sent = send(abc)

        assert parseaddr(sent.from_email) == ("ABC Education", settings.DEFAULT_FROM_EMAIL)
        assert OFFICE_BLUE in html_of(sent)
        assert PLATFORM_ACCENT not in html_of(sent)
        assert "ABC Education" in html_of(sent)
        assert str(sent.body).startswith("ABC Education\n")
        assert_code_outside_links(sent)

    @pytest.mark.parametrize("send", [invite_staff, reset_staff])
    def test_no_contact_email_is_needed_and_empty_details_are_left_out(
        self, abc: ConsultancyContext, send: object
    ) -> None:
        assert callable(send)
        sent = send(abc)

        assert sent.reply_to == []
        assert "Phone" not in html_of(sent)
        assert "Phone" not in str(sent.body)
        assert "To answer, write to" not in str(sent.body)

    @pytest.mark.parametrize("send", [invite_staff, reset_staff])
    def test_the_office_details_and_reply_to_when_set(
        self, abc: ConsultancyContext, send: object
    ) -> None:
        set_office_details(abc.consultancy, DETAILS)
        assert callable(send)
        sent = send(abc)

        assert sent.reply_to == [CONTACT_EMAIL]
        for value in ("Putalisadak", "Kathmandu", "Phone 01-4412345", "https://abcedu.example"):
            assert value in html_of(sent)
            assert value in str(sent.body)
        assert f"To answer, write to {CONTACT_EMAIL}." in str(sent.body)

    @pytest.mark.parametrize("send", [invite_staff, reset_staff])
    def test_a_png_logo_is_carried_inside(self, abc: ConsultancyContext, send: object) -> None:
        set_logo(abc.consultancy, CheckedLogo(PNG, PNG_BYTES))
        assert callable(send)
        sent = send(abc)

        assert 'src="cid:logo"' in html_of(sent)
        assert inline_logo(sent) == [PNG_BYTES]

    def test_an_svg_logo_becomes_the_office_name(self, abc: ConsultancyContext) -> None:
        set_logo(abc.consultancy, CheckedLogo(SVG, SVG_BYTES))

        sent = reset_staff(abc)

        assert "cid:logo" not in html_of(sent)
        assert inline_logo(sent) == []

    def test_the_invite_has_the_sign_up_page_as_a_button_and_names_the_role(
        self, abc: ConsultancyContext
    ) -> None:
        sent = invite_staff(abc)

        assert sent.subject == "Your code to join ABC Education"
        assert "as Counsellor" in str(sent.body)
        links = HREF.findall(html_of(sent))
        assert any(link.endswith("/join") for link in links)

    def test_the_reset_names_the_office(self, abc: ConsultancyContext) -> None:
        sent = reset_staff(abc)

        assert sent.subject == "Your code to reset your password at ABC Education"
        assert "It works for 30 minutes, once." in str(sent.body)
        assert "ignore this email" in str(sent.body)

    def test_a_name_with_markup_is_escaped(self, abc: ConsultancyContext) -> None:
        sent = invite_staff(abc, name="<b>Sita</b> <script>alert(1)</script>")

        assert "<script>alert(1)</script>" not in html_of(sent)
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_of(sent)
        assert "Hello <b>Sita</b> <script>alert(1)</script>," in str(sent.body)

    def test_an_office_name_with_markup_is_escaped(self, abc: ConsultancyContext) -> None:
        abc.consultancy.name = 'ABC <img src=x onerror="alert(1)">'
        abc.consultancy.save(update_fields=["name"])

        sent = reset_staff(abc)

        assert "<img src=x" not in html_of(sent)
        assert "&lt;img src=x" in html_of(sent)

    def test_a_reset_sent_in_support_access_uses_the_offices_look(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        visit = open_as_platform_admin(platform_site, abc)

        response = visit.client.post(f"/api/support-access/staff/{staff.pk}/reset-code/")

        assert response.status_code == status.HTTP_200_OK, response.content
        sent = last()
        assert parseaddr(sent.from_email)[0] == "ABC Education"
        assert OFFICE_BLUE in html_of(sent)
        assert_code_outside_links(sent)


class TestPlatformCodeEmailsInThePlatformsLook:
    def test_the_platform_invite(self, platform_site: PlatformContext, settings: Settings) -> None:
        platform_site.log_in()

        response = platform_site.client.post(
            PLATFORM_ADMINS_URL, {"name": "Mina Rai", "email": "mina@platform.test"}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.content
        sent = last()
        assert parseaddr(sent.from_email) == (PRODUCT_NAME, settings.DEFAULT_FROM_EMAIL)
        assert sent.reply_to == []
        assert sent.subject == "Your code to join the Platform"
        assert PLATFORM_ACCENT in html_of(sent)
        assert PRODUCT_NAME in html_of(sent)
        assert any("/join" in link for link in HREF.findall(html_of(sent)))
        assert_code_outside_links(sent)

    def test_the_platform_reset_names_the_platform_address(
        self, platform_site: PlatformContext, settings: Settings
    ) -> None:
        admin = platform_site.add_admin()

        response = platform_site.new_client().post(
            PLATFORM_RESET_URL, {"email": str(admin.email)}, format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        run_queued_jobs()
        sent = last()
        assert parseaddr(sent.from_email) == (PRODUCT_NAME, settings.DEFAULT_FROM_EMAIL)
        assert sent.subject == "Your code to reset your Platform password"
        assert get_platform_host() in html_of(sent)
        assert get_platform_host() in str(sent.body)
        assert PLATFORM_ACCENT in html_of(sent)
        assert_code_outside_links(sent)

    def test_the_product_logo_is_carried_inside_when_it_is_a_png(
        self, platform_site: PlatformContext
    ) -> None:
        with schema_context(PUBLIC_SCHEMA):
            set_product_logo(CheckedLogo(PNG, PNG_BYTES))
        platform_site.log_in()

        platform_site.client.post(
            PLATFORM_ADMINS_URL, {"name": "Mina Rai", "email": "mina@platform.test"}, format="json"
        )

        assert 'src="cid:logo"' in html_of(last())
        assert inline_logo(last()) == [PNG_BYTES]

    def test_an_svg_product_logo_becomes_the_product_name(
        self, platform_site: PlatformContext
    ) -> None:
        with schema_context(PUBLIC_SCHEMA):
            set_product_logo(CheckedLogo(SVG, SVG_BYTES))
        platform_site.log_in()

        platform_site.client.post(
            PLATFORM_ADMINS_URL, {"name": "Mina Rai", "email": "mina@platform.test"}, format="json"
        )

        assert "cid:logo" not in html_of(last())
        assert inline_logo(last()) == []


class TestCentralLoginEmail:
    def test_in_the_platforms_look_with_a_button_per_office(
        self,
        abc: ConsultancyContext,
        make_consultancy: ConsultancyFactory,
        central: APIClient,
        settings: Settings,
    ) -> None:
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.add_staff(Role.COUNSELLOR, email="sita@example.com")
        xyz.add_staff(Role.ADMIN, email="sita@example.com")

        response = central.post(CENTRAL_URL, {"email": "sita@example.com"}, format="json")

        assert response.status_code == status.HTTP_202_ACCEPTED
        run_queued_jobs()
        [sent] = outbox()
        assert parseaddr(sent.from_email) == (PRODUCT_NAME, settings.DEFAULT_FROM_EMAIL)
        assert sent.reply_to == []
        assert PLATFORM_ACCENT in html_of(sent)
        assert OFFICE_BLUE not in html_of(sent)
        links = HREF.findall(html_of(sent))
        for office in (abc, xyz):
            assert any(link.startswith(f"https://{office.host}") for link in links)
            assert str(office.consultancy.name) in str(sent.body)
        assert "ABC Education" in html_of(sent)
        assert "XYZ Overseas" in html_of(sent)

    def test_an_office_name_with_markup_is_escaped(
        self, abc: ConsultancyContext, central: APIClient
    ) -> None:
        abc.consultancy.name = "ABC <script>alert(1)</script>"
        abc.consultancy.save(update_fields=["name"])
        abc.add_staff(Role.COUNSELLOR, email="sita@example.com")

        central.post(CENTRAL_URL, {"email": "sita@example.com"}, format="json")
        run_queued_jobs()

        [sent] = outbox()
        assert "<script>alert(1)</script>" not in html_of(sent)
        assert "ABC &lt;script&gt;" in html_of(sent)
        assert "ABC <script>alert(1)</script>" in str(sent.body)
