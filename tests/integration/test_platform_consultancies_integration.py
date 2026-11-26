"""Platform Admins create, list and terminate Consultancies (other Status moves: issue 42).

Creating answers at once; the background job that builds the office is tested in
test_consultancy_setup_integration (issue 60).
"""

from __future__ import annotations

from typing import Any

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status
from rest_framework.test import APIClient

from staff.models import Role, Staff
from tenancy.models import Consultancy
from tests.conftest import (
    TEST_PASSWORD,
    ConsultancyFactory,
    PlatformContext,
    api_client,
    api_datetime,
    build_host,
    run_queued_jobs,
    use_access_token,
)

CONSULTANCIES_URL = "/api/platform/consultancies/"
STAFF_LOGIN_URL = "/api/auth/login/"
STAFF_CONTEXT_URL = "/api/context/"
# What the office's login page reads: open to anyone, on a ready office's host.
OFFICE_URL = "/api/consultancy/"
ALWAYS_ON_MODULES = [
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


TERMINATE = {"status": "terminated", "reason": "Contract ended"}
CONTACT_EMAIL = "info@everest.test"
CONTACT_EMAIL_MESSAGE = ["Enter a real email address, like info@youroffice.com."]


def status_url(consultancy_id: object) -> str:
    """Return the URL that changes a Consultancy's Status."""
    return f"{CONSULTANCIES_URL}{consultancy_id}/status/"


def terminate(client: APIClient, consultancy_id: object) -> Any:  # noqa: ANN401  # the response
    """Terminate a Consultancy through the API."""
    return client.post(status_url(consultancy_id), TERMINATE, format="json")


def new_consultancy(**overrides: str) -> dict[str, str]:
    """Return a valid create-Consultancy request body, with some fields replaced."""
    return {
        "name": "Everest Education",
        "subdomain": "everest",
        "contact_email": CONTACT_EMAIL,
        **overrides,
    }


def create(client: APIClient, **overrides: str) -> Any:  # noqa: ANN401  # JSON response body
    """Create a Consultancy through the API and return the response."""
    return client.post(CONSULTANCIES_URL, new_consultancy(**overrides), format="json")


def staff_login(host: str, email: str) -> int:
    """Log in as Staff on a Consultancy host and return the response status code."""
    client = api_client(host)
    response = client.post(
        STAFF_LOGIN_URL, {"email": email, "password": TEST_PASSWORD}, format="json"
    )
    return response.status_code


class TestCreateConsultancy:
    def test_answers_at_once_and_the_office_is_ready_once_its_job_has_run(
        self, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        response = create(platform_site.client)

        assert response.status_code == status.HTTP_202_ACCEPTED
        body = response.json()
        assert body["name"] == "Everest Education"
        assert body["subdomain"] == "everest"
        assert body["host"] == build_host("everest")
        assert body["setup_state"] == "setting_up"
        assert run_queued_jobs() == 1
        listed = platform_site.client.get(CONSULTANCIES_URL).json()[0]
        assert (listed["status"], listed["setup_state"]) == ("evaluation", "ready")

    def test_a_new_office_has_no_staff_until_an_admin_is_added(
        self, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        consultancy_id = create(platform_site.client).json()["id"]
        run_queued_jobs()

        with tenant_context(Consultancy.objects.get(public_id=consultancy_id)):
            assert not Staff.objects.exists()

    def test_letters_and_numbers_subdomain_is_reachable(
        self, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        response = create(platform_site.client, subdomain="everest2026")
        run_queued_jobs()

        assert response.status_code == status.HTTP_202_ACCEPTED
        reached = api_client(build_host("everest2026")).get(OFFICE_URL)
        assert reached.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("subdomain", ["everest-edu", "everest_edu", "2everest", "Everest"])
    def test_hyphens_underscores_a_leading_number_and_capitals_are_refused(
        self, platform_site: PlatformContext, subdomain: str
    ) -> None:
        platform_site.log_in()

        response = create(platform_site.client, subdomain=subdomain)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "subdomain": [
                "Use only lowercase letters and numbers, starting with a letter (at most 63)."
            ]
        }
        assert not Consultancy.objects.filter(name="Everest Education").exists()

    def test_rejects_a_subdomain_already_in_use(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        make_consultancy("ABC Education", "abcedu")
        platform_site.log_in()

        response = create(platform_site.client, subdomain="abcedu")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"subdomain": ["This subdomain is already taken."]}

    @pytest.mark.parametrize(
        ("subdomain", "message"),
        [
            ("admin", "This subdomain is reserved."),
            ("platform", "This subdomain is reserved."),
            ("Everest!", None),
        ],
    )
    def test_rejects_invalid_or_reserved_subdomains(
        self, platform_site: PlatformContext, subdomain: str, message: str | None
    ) -> None:
        platform_site.log_in()

        response = create(platform_site.client, subdomain=subdomain)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.json()) == {"subdomain"}
        if message is not None:
            assert response.json()["subdomain"] == [message]

    def test_refuses_an_admin_password_as_an_unknown_field(
        self, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        response = create(platform_site.client, admin_password=TEST_PASSWORD)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert not Consultancy.objects.filter(name="Everest Education").exists()

    def test_rejects_missing_fields(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        response = platform_site.client.post(CONSULTANCIES_URL, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.json()) == {"name", "subdomain", "contact_email"}

    def test_nothing_is_created_when_the_request_is_invalid(
        self, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        create(platform_site.client, subdomain=build_host("everest"))

        assert not Consultancy.objects.filter(name="Everest Education").exists()


class TestContactEmailWhenOpening:
    """The office's contact email is asked when it is opened (ticket 20, spec 119)."""

    def test_it_is_saved_as_the_offices_contact_email(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        response = create(platform_site.client, contact_email="  info@everest.test ")

        assert response.status_code == status.HTTP_202_ACCEPTED, response.content
        office = Consultancy.objects.get(public_id=response.json()["id"])
        assert office.contact_email == CONTACT_EMAIL

    def test_it_is_required(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()
        body = new_consultancy()
        del body["contact_email"]

        response = platform_site.client.post(CONSULTANCIES_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"contact_email": CONTACT_EMAIL_MESSAGE}
        assert not Consultancy.objects.filter(name="Everest Education").exists()

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "info@a.test, sales@a.test",
            "Everest <info@everest.test>",
            "<script>alert(1)</script>@x.test",
            "'; DROP TABLE tenancy_consultancy; --",
            "info@everest.test\nBcc: x@y.test",
            "not-an-email",
            f"{'x' * 250}@everest.test",
            42,
            None,
        ],
    )
    def test_anything_but_one_real_address_is_refused(
        self, platform_site: PlatformContext, bad: object
    ) -> None:
        platform_site.log_in()

        response = platform_site.client.post(
            CONSULTANCIES_URL, {**new_consultancy(), "contact_email": bad}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"contact_email": CONTACT_EMAIL_MESSAGE}
        assert not Consultancy.objects.filter(name="Everest Education").exists()

    def test_other_offices_keep_their_contact_email(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        Consultancy.objects.filter(pk=abc.consultancy.pk).update(contact_email="info@abc.test")
        platform_site.log_in()

        create(platform_site.client)

        assert Consultancy.objects.get(pk=abc.consultancy.pk).contact_email == "info@abc.test"


class TestListConsultancies:
    def test_lists_each_consultancy_with_subdomain_status_and_modules(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc = make_consultancy("ABC Education", "abcedu")
        platform_site.log_in()
        abc_ends_at, xyz_ends_at = (
            api_datetime(office.consultancy.evaluation_ends_at) for office in (abc, xyz)
        )

        response = platform_site.client.get(CONSULTANCIES_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == [
            {
                "id": str(abc.consultancy.public_id),
                "name": "ABC Education",
                "subdomain": "abcedu",
                "host": abc.host,
                "status": "evaluation",
                "status_reason": "",
                "evaluation_ends_at": abc_ends_at,
                "paid_until": None,
                "modules": ALWAYS_ON_MODULES,
                "setup_state": "ready",
            },
            {
                "id": str(xyz.consultancy.public_id),
                "name": "XYZ Overseas",
                "subdomain": "xyzoverseas",
                "host": xyz.host,
                "status": "evaluation",
                "status_reason": "",
                "evaluation_ends_at": xyz_ends_at,
                "paid_until": None,
                "modules": ALWAYS_ON_MODULES,
                "setup_state": "ready",
            },
        ]


class TestTerminateConsultancy:
    def test_terminated_consultancy_is_listed_as_terminated(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        platform_site.log_in()

        response = terminate(platform_site.client, abc.consultancy.public_id)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "terminated"
        listed = platform_site.client.get(CONSULTANCIES_URL).json()[0]
        assert (listed["status"], listed["status_reason"]) == ("terminated", "Contract ended")

    def test_staff_can_no_longer_log_in(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        platform_site.log_in()

        terminate(platform_site.client, abc.consultancy.public_id)

        assert staff_login(abc.host, counsellor.email) == status.HTTP_404_NOT_FOUND

    def test_open_staff_logins_stop_working(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        platform_site.log_in()

        terminate(platform_site.client, abc.consultancy.public_id)

        assert abc.client.get(STAFF_CONTEXT_URL).status_code == status.HTTP_404_NOT_FOUND

    def test_data_is_kept(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        platform_site.log_in()

        terminate(platform_site.client, abc.consultancy.public_id)

        with tenant_context(abc.consultancy):
            assert Staff.objects.filter(email=counsellor.email).exists()

    def test_other_consultancies_keep_working(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz_counsellor = xyz.add_staff(Role.COUNSELLOR)
        platform_site.log_in()

        terminate(platform_site.client, abc.consultancy.public_id)

        assert staff_login(xyz.host, xyz_counsellor.email) == status.HTTP_200_OK

    def test_unknown_consultancy_is_not_found(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        response = terminate(platform_site.client, 999_999)

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestPlatformEndpointAccess:
    def test_anonymous_requests_are_refused(self, platform_site: PlatformContext) -> None:
        client = platform_site.new_client()

        assert client.get(CONSULTANCIES_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert create(client).status_code == status.HTTP_401_UNAUTHORIZED

    def test_staff_token_cannot_reach_platform_endpoints(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        tokens = abc.tokens_for(abc.add_staff(Role.ADMIN))
        client = platform_site.new_client()
        use_access_token(client, tokens.access)

        response = terminate(client, abc.consultancy.public_id)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert Consultancy.objects.get(pk=abc.consultancy.pk).status == "evaluation"

    def test_platform_endpoints_do_not_exist_on_a_consultancy_subdomain(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(CONSULTANCIES_URL).status_code == status.HTTP_404_NOT_FOUND
        assert create(abc.client).status_code == status.HTTP_404_NOT_FOUND
