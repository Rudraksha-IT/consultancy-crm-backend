"""Platform Admins add Admins to a Consultancy once it is set up (issue 64).

Creating a Consultancy invites nobody; "Add admin" on the Platform list invites an Admin by
Code into that office only, as often as needed. The page shows the product domain from the
backend's environment after the address field.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from django.core import mail
from django.utils import timezone
from django_tenants.utils import schema_context, tenant_context
from pytest_django.fixtures import Settings
from rest_framework import status
from rest_framework.test import APIClient

from staff.models import Role, Staff, StaffStatus
from tenancy.models import Consultancy, SetupState
from tests.conftest import (
    PUBLIC_SCHEMA,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    build_host,
    run_queued_jobs,
    use_access_token,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

CONSULTANCIES_URL = "/api/platform/consultancies/"
PRODUCT_DOMAIN_URL = "/api/platform/product-domain/"
JOIN_COMPLETE_URL = "/api/auth/join/complete/"
LOGIN_URL = "/api/auth/login/"
EMAIL = "sita@everest.test"
NAME = "Sita Sharma"
NEW_PASSWORD = "Kathmandu!2026"  # noqa: S105  # test-only credential
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE staff_staff; --",
    "}body{background:url(x)}",
]


def admins_url(consultancy_id: int) -> str:
    """The Add admin endpoint of one Consultancy."""
    return f"{CONSULTANCIES_URL}{consultancy_id}/admins/"


def add_admin(
    client: APIClient, consultancy_id: int, body: object = None
) -> _MonkeyPatchedResponse:
    """POST an Admin to add; a valid one unless `body` is given."""
    sent = {"name": NAME, "email": EMAIL} if body is None else body
    return client.post(admins_url(consultancy_id), sent, format="json")


def emailed_code() -> str:
    """The 6-digit Code in the last email."""
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[-1].body))
    assert found is not None
    return found.group(1)


def staff_of(consultancy: Consultancy) -> list[tuple[str, str, str, str]]:
    """Each Staff member of an office as (name, email, role, status)."""
    with tenant_context(consultancy):
        return [
            (str(s.name), str(s.email), str(s.role), str(s.status))
            for s in Staff.objects.order_by("pk")
        ]


@pytest.fixture
def everest(platform_site: PlatformContext) -> Consultancy:
    """A Consultancy created on the Platform and set up, with nobody in it yet."""
    platform_site.log_in()
    created = platform_site.client.post(
        CONSULTANCIES_URL, {"name": "Everest Education", "subdomain": "everest"}, format="json"
    )
    assert created.status_code == status.HTTP_202_ACCEPTED, created.content
    run_queued_jobs()
    with schema_context(PUBLIC_SCHEMA):
        office: Consultancy = Consultancy.objects.get(pk=created.json()["id"])
    return office


class TestProductDomain:
    def test_platform_admins_get_the_product_domain_from_the_environment(
        self, platform_site: PlatformContext, settings: Settings
    ) -> None:
        platform_site.log_in()

        response = platform_site.client.get(PRODUCT_DOMAIN_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"product_domain": settings.PRODUCT_DOMAIN}

    def test_nobody_else_gets_it(
        self, platform_site: PlatformContext, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        assert platform_site.new_client().get(PRODUCT_DOMAIN_URL).status_code == 401
        use_access_token(abc.client, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)
        assert abc.client.get(PRODUCT_DOMAIN_URL).status_code == status.HTTP_404_NOT_FOUND

    def test_only_get(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()
        for method in ("post", "put", "patch", "delete"):
            response = getattr(platform_site.client, method)(PRODUCT_DOMAIN_URL)
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestAddAdmin:
    def test_invites_an_admin_by_code_into_the_office(
        self, platform_site: PlatformContext, everest: Consultancy
    ) -> None:
        response = add_admin(platform_site.client, everest.pk)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {"name": NAME, "email": EMAIL, "status": "invited"}
        assert staff_of(everest) == [(NAME, EMAIL, Role.ADMIN.value, StaffStatus.INVITED.value)]
        with tenant_context(everest):
            assert not Staff.objects.get().has_usable_password()
        [email] = mail.outbox
        assert email.to == [EMAIL]
        assert "Everest Education" in email.subject
        assert f"http://{build_host('everest')}/join" in str(email.body)

    def test_the_admin_joins_with_the_code_and_logs_in(
        self, platform_site: PlatformContext, everest: Consultancy
    ) -> None:
        add_admin(platform_site.client, everest.pk)
        office = api_client(build_host("everest"))

        joined = office.post(
            JOIN_COMPLETE_URL,
            {"email": EMAIL, "code": emailed_code(), "password": NEW_PASSWORD},
            format="json",
        )

        assert joined.status_code == status.HTTP_200_OK
        assert joined.json()["context"]["staff"]["role"] == "admin"
        login = office.post(LOGIN_URL, {"email": EMAIL, "password": NEW_PASSWORD}, format="json")
        assert login.status_code == status.HTTP_200_OK

    def test_more_admins_can_be_added(
        self, platform_site: PlatformContext, everest: Consultancy
    ) -> None:
        add_admin(platform_site.client, everest.pk)

        response = add_admin(
            platform_site.client, everest.pk, {"name": "Ram Thapa", "email": "Ram@Everest.test"}
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert [row[1] for row in staff_of(everest)] == [EMAIL, "ram@everest.test"]
        assert len(mail.outbox) == 2

    def test_an_email_already_used_in_the_office_is_refused(
        self, platform_site: PlatformContext, everest: Consultancy
    ) -> None:
        add_admin(platform_site.client, everest.pk)

        response = add_admin(
            platform_site.client, everest.pk, {"name": "Other", "email": " SITA@everest.test "}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"email": ["Someone here already uses this email."]}
        assert len(staff_of(everest)) == 1

    @pytest.mark.parametrize("office_status", ["past_due", "suspended"])
    def test_allowed_for_a_read_only_office(
        self, platform_site: PlatformContext, everest: Consultancy, office_status: str
    ) -> None:
        with schema_context(PUBLIC_SCHEMA):
            Consultancy.objects.filter(pk=everest.pk).update(status=office_status)

        assert add_admin(platform_site.client, everest.pk).status_code == status.HTTP_201_CREATED

    def test_refused_for_a_terminated_office(
        self, platform_site: PlatformContext, everest: Consultancy
    ) -> None:
        with schema_context(PUBLIC_SCHEMA):
            Consultancy.objects.filter(pk=everest.pk).update(status="terminated")

        response = add_admin(platform_site.client, everest.pk)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "A Terminated Consultancy can't be changed."}
        assert staff_of(everest) == []
        assert mail.outbox == []

    @pytest.mark.parametrize("state", [SetupState.SETTING_UP, SetupState.FAILED])
    def test_refused_while_the_office_is_not_set_up(
        self, platform_site: PlatformContext, everest: Consultancy, state: SetupState
    ) -> None:
        with schema_context(PUBLIC_SCHEMA):
            Consultancy.objects.filter(pk=everest.pk).update(setup_state=state)

        response = add_admin(platform_site.client, everest.pk)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": "This office is still being set up, or its setup failed."
        }
        assert mail.outbox == []

    def test_an_unknown_office_is_not_found(
        self, platform_site: PlatformContext, everest: Consultancy
    ) -> None:
        assert add_admin(platform_site.client, 99_999).status_code == status.HTTP_404_NOT_FOUND

    def test_logged_with_ids_only(
        self, platform_site: PlatformContext, everest: Consultancy, json_logs: JsonLogs
    ) -> None:
        add_admin(platform_site.client, everest.pk)

        with tenant_context(everest):
            staff_id = Staff.objects.get().pk
        [line] = json_logs.with_message(
            f"Admin invited from the Platform: staff_id={staff_id}, consultancy_id={everest.pk}, "
            "platform_admin_id="
        )
        assert line["level"] == "INFO"
        assert EMAIL not in json_logs.text()
        assert NAME not in json_logs.text()
        assert emailed_code() not in json_logs.text()

    def test_the_invite_is_marked_as_made_by_today(
        self, platform_site: PlatformContext, everest: Consultancy
    ) -> None:
        add_admin(platform_site.client, everest.pk)

        with tenant_context(everest):
            assert Staff.objects.get().joined_on == timezone.localdate()


class TestWhoCanAdd:
    def test_nobody_logged_in_is_refused(
        self, platform_site: PlatformContext, everest: Consultancy
    ) -> None:
        response = add_admin(platform_site.new_client(), everest.pk)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert staff_of(everest) == []

    def test_staff_can_never_add_from_their_office_or_the_platform(
        self,
        platform_site: PlatformContext,
        everest: Consultancy,
        make_consultancy: ConsultancyFactory,
    ) -> None:
        abc: ConsultancyContext = make_consultancy("ABC Education", "abcedu")
        access = abc.tokens_for(abc.add_staff(Role.ADMIN)).access

        for client in (abc.new_client(), platform_site.new_client()):
            use_access_token(client, access)
            assert add_admin(client, everest.pk).status_code in {401, 403, 404}
            assert add_admin(client, abc.consultancy.pk).status_code in {401, 403, 404}

        assert staff_of(everest) == []
        assert len(staff_of(abc.consultancy)) == 1

    def test_only_post(self, platform_site: PlatformContext, everest: Consultancy) -> None:
        for method in ("get", "put", "patch", "delete"):
            response = getattr(platform_site.client, method)(admins_url(everest.pk))
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestIsolation:
    def test_the_admin_lands_only_in_the_chosen_office(
        self,
        platform_site: PlatformContext,
        everest: Consultancy,
        make_consultancy: ConsultancyFactory,
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc_admin = abc.add_staff(Role.ADMIN)

        add_admin(platform_site.client, everest.pk, {"name": NAME, "email": abc_admin.email})

        assert [row[1] for row in staff_of(everest)] == [abc_admin.email]
        assert [row[1] for row in staff_of(abc.consultancy)] == [abc_admin.email]
        with tenant_context(abc.consultancy):
            assert Staff.objects.get().status == StaffStatus.ACTIVE.value


class TestRefusedInput:
    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"name": NAME},
            {"email": EMAIL},
            {"name": NAME, "email": EMAIL, "role": "counsellor"},
            {"name": NAME, "email": EMAIL, "password": NEW_PASSWORD},
            {"name": None, "email": EMAIL},
            {"name": 42, "email": EMAIL},
            {"name": [NAME], "email": EMAIL},
            {"name": NAME, "email": 12345},
            {"name": NAME, "email": "not-an-email"},
            {"name": "x" * 201, "email": EMAIL},
            {"name": NAME, "email": f"{'a' * 250}@everest.test"},
            {"name": "", "email": EMAIL},
            [],
            EMAIL,
        ],
    )
    def test_bad_bodies_add_nobody(
        self, platform_site: PlatformContext, everest: Consultancy, body: object
    ) -> None:
        response = add_admin(platform_site.client, everest.pk, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert staff_of(everest) == []
        assert mail.outbox == []

    @pytest.mark.parametrize("value", INJECTION_STRINGS)
    def test_injection_shaped_values_are_refused(
        self, platform_site: PlatformContext, everest: Consultancy, value: str
    ) -> None:
        for body in ({"name": value, "email": EMAIL}, {"name": NAME, "email": value}):
            response = add_admin(platform_site.client, everest.pk, body)
            assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert staff_of(everest) == []

    def test_only_json_is_taken(self, platform_site: PlatformContext, everest: Consultancy) -> None:
        response = platform_site.client.post(admins_url(everest.pk), {"name": NAME, "email": EMAIL})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_does_not_exist_on_an_office_host(
        self, platform_site: PlatformContext, everest: Consultancy
    ) -> None:
        office = api_client(build_host("everest"))

        assert add_admin(office, everest.pk).status_code == status.HTTP_404_NOT_FOUND
