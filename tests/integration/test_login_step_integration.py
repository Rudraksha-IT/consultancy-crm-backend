"""Email-first login (issue 66): the first step only says what comes next.

Someone invited who hasn't joined yet goes to the Join page (a fresh Code is emailed). Everyone
else, including an email nobody has, goes to the password step, so strangers can't tell who has
an account.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.core import mail
from django_tenants.utils import schema_context, tenant_context
from rest_framework import status
from rest_framework.test import APIClient

from platform_admin.models import PlatformAdmin
from staff.models import Role, Staff, StaffStatus
from tenancy.models import Consultancy
from tests.conftest import (
    PUBLIC_SCHEMA,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
)

STAFF_STEP_URL = "/api/auth/login/next/"
PLATFORM_STEP_URL = "/api/platform/auth/login/next/"
STAFF_INVITE_URL = "/api/staff/"
PLATFORM_ADMINS_URL = "/api/platform/admins/"
PASSWORD = {"next": "password"}
JOIN = {"next": "join"}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office every Staff test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def step(client: APIClient, url: str, body: object) -> Any:  # noqa: ANN401  # the answer
    """Send the first step."""
    return client.post(url, body, format="json")


def invited_staff(office: ConsultancyContext, email: str) -> Staff:
    """A Staff member added as invited, without a password."""
    staff = office.add_staff(Role.COUNSELLOR, email=email)
    with tenant_context(office.consultancy):
        Staff.objects.filter(pk=staff.pk).update(status=StaffStatus.INVITED)
    return staff


class TestOfficeLogin:
    def test_someone_who_has_joined_gets_the_password_step(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")

        response = step(abc.new_client(), STAFF_STEP_URL, {"email": staff.email})

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == PASSWORD
        assert mail.outbox == []

    @pytest.mark.parametrize("email", ["nobody@abcedu.test", "NOBODY@ABCedu.test"])
    def test_an_unknown_email_gets_the_password_step_too(
        self, abc: ConsultancyContext, email: str
    ) -> None:
        response = step(abc.new_client(), STAFF_STEP_URL, {"email": email})

        assert response.json() == PASSWORD
        assert mail.outbox == []

    def test_someone_who_left_gets_the_password_step(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email="ram@abcedu.test")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=staff.pk).update(status=StaffStatus.DEACTIVATED)

        assert step(abc.new_client(), STAFF_STEP_URL, {"email": staff.email}).json() == PASSWORD

    def test_someone_invited_goes_to_join_and_gets_a_fresh_code(
        self, abc: ConsultancyContext
    ) -> None:
        staff = invited_staff(abc, "new@abcedu.test")

        response = step(abc.new_client(), STAFF_STEP_URL, {"email": " New@ABCedu.test "})

        assert response.json() == JOIN
        [email] = mail.outbox
        assert email.to == [staff.email]
        assert re.search(r"\b\d{6}\b", str(email.body))

    def test_an_email_invited_in_another_office_gets_the_password_step(
        self, abc: ConsultancyContext, make_consultancy: ConsultancyFactory
    ) -> None:
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        invited_staff(xyz, "both@x.test")

        assert step(abc.new_client(), STAFF_STEP_URL, {"email": "both@x.test"}).json() == PASSWORD
        assert mail.outbox == []

    def test_works_on_a_read_only_office(self, abc: ConsultancyContext) -> None:
        abc.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")
        with schema_context(PUBLIC_SCHEMA):
            Consultancy.objects.filter(pk=abc.consultancy.pk).update(status="suspended")

        response = step(abc.new_client(), STAFF_STEP_URL, {"email": "sita@abcedu.test"})

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"email": None},
            {"email": 7},
            {"email": "not-an-email"},
            {"email": "<script>alert(1)</script>"},
            {"email": f"{'a' * 250}@abcedu.test"},
            {"email": "sita@abcedu.test", "password": "x"},
            [],
        ],
    )
    def test_bad_input_is_refused(self, abc: ConsultancyContext, body: object) -> None:
        assert step(abc.new_client(), STAFF_STEP_URL, body).status_code == 400

    def test_only_post(self, abc: ConsultancyContext) -> None:
        assert abc.new_client().get(STAFF_STEP_URL).status_code == 405

    def test_logs_no_email(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        invited_staff(abc, "new@abcedu.test")

        step(abc.new_client(), STAFF_STEP_URL, {"email": "new@abcedu.test"})
        step(abc.new_client(), STAFF_STEP_URL, {"email": "nobody@abcedu.test"})

        assert "new@abcedu.test" not in json_logs.text()
        assert "nobody@abcedu.test" not in json_logs.text()


class TestPlatformLogin:
    def test_a_platform_admin_who_has_joined_gets_the_password_step(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()

        response = step(platform_site.new_client(), PLATFORM_STEP_URL, {"email": admin.email})

        assert response.json() == PASSWORD

    def test_an_unknown_email_gets_the_password_step(self, platform_site: PlatformContext) -> None:
        response = step(
            platform_site.new_client(), PLATFORM_STEP_URL, {"email": "nobody@platform.test"}
        )

        assert response.json() == PASSWORD
        assert mail.outbox == []

    def test_an_invited_platform_admin_goes_to_join_with_a_fresh_code(
        self, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()
        platform_site.client.post(
            PLATFORM_ADMINS_URL, {"name": "Rita", "email": "rita@platform.test"}, format="json"
        )
        mail.outbox.clear()

        response = step(
            platform_site.new_client(), PLATFORM_STEP_URL, {"email": "Rita@Platform.test"}
        )

        assert response.json() == JOIN
        [email] = mail.outbox
        assert email.to == ["rita@platform.test"]
        assert "/platform/join" in str(email.body)

    def test_a_deactivated_platform_admin_gets_the_password_step(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin(email="old@platform.test")
        with schema_context(PUBLIC_SCHEMA):
            PlatformAdmin.objects.filter(pk=admin.pk).update(is_active=False, joined_at=None)

        assert (
            step(platform_site.new_client(), PLATFORM_STEP_URL, {"email": admin.email}).json()
            == PASSWORD
        )
        assert mail.outbox == []

    def test_staff_emails_never_lead_to_the_platform_join(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        invited_staff(abc, "new@abcedu.test")

        assert (
            step(platform_site.new_client(), PLATFORM_STEP_URL, {"email": "new@abcedu.test"}).json()
            == PASSWORD
        )

    def test_each_step_exists_only_on_its_own_host(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        body = {"email": "x@x.test"}
        assert step(abc.new_client(), PLATFORM_STEP_URL, body).status_code == 404
        assert step(platform_site.new_client(), STAFF_STEP_URL, body).status_code == 404

    @pytest.mark.parametrize("body", [{}, {"email": "nope"}, {"email": "a@b.test", "x": 1}])
    def test_bad_input_is_refused(self, platform_site: PlatformContext, body: object) -> None:
        assert step(platform_site.new_client(), PLATFORM_STEP_URL, body).status_code == 400
