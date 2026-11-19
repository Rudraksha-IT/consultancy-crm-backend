"""Central login (spec 13e, issue 44): type your email, get your offices' login links by email.

The page always answers the same, so it can't be used to find out who works where.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.core.management import call_command
from django.db import connection
from django.utils import timezone
from django_tenants.utils import schema_context, tenant_context
from pytest_django.fixtures import Settings
from rest_framework import status
from rest_framework.test import APIClient

from central_login.directory import add_to_directory, email_hash
from central_login.models import CentralLoginRequest, StaffDirectoryEntry
from central_login.services import MAX_EMAILS_PER_HOUR
from staff.employees import cancel_invite, deactivate_employee, invite_employee
from staff.models import Role, Staff
from staff.types import NewStaff, Office
from tenancy.context import activate_public_schema
from tenancy.models import Consultancy
from tenancy.services import change_status
from tenancy.status import ConsultancyStatus
from tenancy.subdomains import get_central_login_host
from tenancy.types import StatusChange
from tests.conftest import (
    PUBLIC_SCHEMA,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    run_queued_jobs,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

URL = "/api/central-login/"
CENTRAL_HOST = get_central_login_host()
SAME_ANSWER = {"detail": "If this email belongs to an office, we've sent it a link."}
SHARED_EMAIL = "sita@example.com"
INJECTION_STRINGS = [
    "<script>alert(1)</script>@example.com",
    "'; DROP TABLE staff_staff; --",
    "}body{background:url(x)}",
    "a@b.com\r\nBcc: everyone@example.com",
]


@pytest.fixture
def central(db: None) -> Iterator[APIClient]:
    """An API client on the central login address, as the Next.js server calls it."""
    yield api_client(CENTRAL_HOST, HTTP_X_FORWARDED_PROTO="https")
    activate_public_schema()


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The first office."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def xyz(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """A second office."""
    return make_consultancy("XYZ Overseas", "xyzoverseas")


def ask(client: APIClient, body: object) -> _MonkeyPatchedResponse:
    """POST `body` as JSON to the central login."""
    return client.post(URL, body, format="json")


def entries() -> list[tuple[str, str]]:
    """Every directory entry as (email hash, schema), read from the public schema."""
    with schema_context(PUBLIC_SCHEMA):
        return sorted(
            StaffDirectoryEntry.objects.values_list("email_hash", "consultancy__schema_name")
        )


def office(context: ConsultancyContext) -> Office:
    """What an invite email names: the office and its sign-up page."""
    return Office(consultancy=context.consultancy, join_link=f"https://{context.host}/join")


def move_to(
    context: ConsultancyContext, platform_site: PlatformContext, *statuses: ConsultancyStatus
) -> None:
    """Make a Platform Admin move the office through `statuses`, in order."""
    admin = platform_site.add_admin()
    paid_until = timezone.localdate() + timedelta(days=30)
    with schema_context(PUBLIC_SCHEMA):
        for target in statuses:
            change = StatusChange(target, "Checked by a test", paid_until=paid_until)
            change_status(context.consultancy, change, by=admin)


class TestSameAnswer:
    """Known and unknown emails get exactly the same answer."""

    def test_known_email_gets_the_same_answer(
        self, central: APIClient, abc: ConsultancyContext
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        response = ask(central, {"email": SHARED_EMAIL})
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SAME_ANSWER

    def test_unknown_email_gets_the_same_answer(
        self, central: APIClient, abc: ConsultancyContext
    ) -> None:
        response = ask(central, {"email": "nobody@example.com"})
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SAME_ANSWER
        assert mail.outbox == []

    def test_answers_are_byte_for_byte_the_same(
        self, central: APIClient, abc: ConsultancyContext
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        known = ask(central, {"email": SHARED_EMAIL})
        unknown = ask(central, {"email": "nobody@example.com"})
        assert known.content == unknown.content
        assert sorted(known.headers) == sorted(unknown.headers)


class TestEmailedLinks:
    """The email lists every office the email belongs to, with a link to its login page."""

    def test_lists_each_office_with_its_login_link(
        self, central: APIClient, abc: ConsultancyContext, xyz: ConsultancyContext
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        xyz.add_staff(Role.FRONT_DESK, email=SHARED_EMAIL)
        ask(central, {"email": "  Sita@Example.COM "})
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.to == [SHARED_EMAIL]
        assert "ABC Education" in sent.body
        assert "XYZ Overseas" in sent.body
        assert f"https://{abc.host}/login?email=sita%40example.com" in sent.body
        assert f"https://{xyz.host}/login?email=sita%40example.com" in sent.body

    def test_only_the_offices_the_email_belongs_to(
        self, central: APIClient, abc: ConsultancyContext, xyz: ConsultancyContext
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        xyz.add_staff(Role.COUNSELLOR, email="someone.else@example.com")
        ask(central, {"email": SHARED_EMAIL})
        body = mail.outbox[0].body
        assert "ABC Education" in body
        assert "XYZ Overseas" not in body
        assert xyz.host not in body

    def test_terminated_offices_are_left_out(
        self,
        central: APIClient,
        abc: ConsultancyContext,
        xyz: ConsultancyContext,
        platform_site: PlatformContext,
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        xyz.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        move_to(xyz, platform_site, ConsultancyStatus.TERMINATED)
        ask(central, {"email": SHARED_EMAIL})
        body = mail.outbox[0].body
        assert "ABC Education" in body
        assert "XYZ Overseas" not in body

    def test_nothing_is_sent_when_only_terminated_offices_match(
        self, central: APIClient, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        move_to(abc, platform_site, ConsultancyStatus.TERMINATED)
        response = ask(central, {"email": SHARED_EMAIL})
        assert response.json() == SAME_ANSWER
        assert mail.outbox == []

    def test_restricted_offices_are_still_listed(
        self, central: APIClient, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        move_to(abc, platform_site, ConsultancyStatus.ACTIVE, ConsultancyStatus.PAST_DUE)
        ask(central, {"email": SHARED_EMAIL})
        assert "ABC Education" in mail.outbox[0].body

    def test_link_keeps_the_port_and_scheme_the_page_was_opened_on(
        self, abc: ConsultancyContext, db: None
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        local = api_client(f"{CENTRAL_HOST}:8080", HTTP_X_FORWARDED_PROTO="http")
        ask(local, {"email": SHARED_EMAIL})
        assert f"http://{abc.host}:8080/login?email=sita%40example.com" in mail.outbox[0].body

    def test_email_names_no_one_else(self, central: APIClient, abc: ConsultancyContext) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        abc.add_staff(Role.ADMIN, email="boss@example.com")
        ask(central, {"email": SHARED_EMAIL})
        sent = mail.outbox[0]
        assert isinstance(sent, EmailMultiAlternatives)
        [(html, kind)] = sent.alternatives
        assert kind == "text/html"
        assert "boss@example.com" not in sent.body
        assert "boss@example.com" not in str(html)

    def test_a_mail_failure_still_answers_the_same(
        self, central: APIClient, abc: ConsultancyContext, settings: Settings, json_logs: JsonLogs
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        settings.EMAIL_BACKEND = "tests.fakes.BrokenEmailBackend"
        response = ask(central, {"email": SHARED_EMAIL})
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SAME_ANSWER
        [failure] = json_logs.with_message("outside call failed: provider=email ")
        assert failure["level"] == "ERROR"
        assert "send_office_links" in str(failure["message"])
        [recovered] = json_logs.with_message("central login email not sent")
        assert recovered["level"] == "WARNING"
        assert "exception" in recovered
        assert SHARED_EMAIL not in json_logs.text()


class TestRateLimit:
    """Per email: a few emails an hour, then nothing more is sent (the answer stays the same)."""

    def test_stops_sending_after_the_hourly_limit(
        self, central: APIClient, abc: ConsultancyContext
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        for _ in range(MAX_EMAILS_PER_HOUR):
            ask(central, {"email": SHARED_EMAIL})
        response = ask(central, {"email": SHARED_EMAIL})
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == SAME_ANSWER
        assert len(mail.outbox) == MAX_EMAILS_PER_HOUR

    def test_unknown_emails_count_too(self, central: APIClient, abc: ConsultancyContext) -> None:
        for _ in range(MAX_EMAILS_PER_HOUR + 1):
            ask(central, {"email": "nobody@example.com"})
        with schema_context(PUBLIC_SCHEMA):
            assert CentralLoginRequest.objects.count() == MAX_EMAILS_PER_HOUR

    def test_other_emails_are_not_held_back(
        self, central: APIClient, abc: ConsultancyContext
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        abc.add_staff(Role.FRONT_DESK, email="hari@example.com")
        for _ in range(MAX_EMAILS_PER_HOUR + 1):
            ask(central, {"email": SHARED_EMAIL})
        ask(central, {"email": "hari@example.com"})
        assert mail.outbox[-1].to == ["hari@example.com"]

    def test_counted_by_hash_with_no_raw_email(
        self, central: APIClient, abc: ConsultancyContext
    ) -> None:
        ask(central, {"email": SHARED_EMAIL})
        with schema_context(PUBLIC_SCHEMA):
            stored = list(CentralLoginRequest.objects.values_list("email_hash", flat=True))
        assert stored == [email_hash(SHARED_EMAIL)]


class TestRefusedInput:
    """The server never trusts what it is sent."""

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"email": ""},
            {"email": None},
            {"email": 123},
            {"email": ["a@b.com"]},
            {"email": "not-an-email"},
            {"email": f"{'a' * 250}@example.com"},
            {"email": SHARED_EMAIL, "office": "abcedu"},
        ],
    )
    def test_bad_body_is_refused(self, central: APIClient, body: object) -> None:
        response = ask(central, body)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert mail.outbox == []

    @pytest.mark.parametrize("value", INJECTION_STRINGS)
    def test_injection_shaped_email_is_refused(self, central: APIClient, value: str) -> None:
        response = ask(central, {"email": value})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert mail.outbox == []
        with schema_context(PUBLIC_SCHEMA):
            assert not CentralLoginRequest.objects.exists()

    def test_form_body_is_refused(self, central: APIClient) -> None:
        response = central.post(URL, {"email": SHARED_EMAIL})
        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post(self, central: APIClient, method: str) -> None:
        response = getattr(central, method)(URL)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestWhereItWorks:
    """Only on the central login address, only through the Next.js server."""

    def test_not_on_an_office_address(self, abc: ConsultancyContext) -> None:
        response = ask(abc.new_client(), {"email": SHARED_EMAIL})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_not_on_the_platform_address(self, platform_site: PlatformContext) -> None:
        response = ask(platform_site.new_client(), {"email": SHARED_EMAIL})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_office_endpoints_are_not_on_the_central_address(self, central: APIClient) -> None:
        assert central.get("/api/context/").status_code == status.HTTP_404_NOT_FOUND
        assert central.get("/api/platform/consultancies/").status_code == 404

    def test_refused_without_the_internal_token(self, db: None) -> None:
        client = APIClient()
        client.defaults["HTTP_HOST"] = CENTRAL_HOST
        response = ask(client, {"email": SHARED_EMAIL})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_no_office_can_take_the_central_address(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()
        response = platform_site.client.post(
            "/api/platform/consultancies/",
            {
                "name": "Login Ltd",
                "subdomain": "login",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "subdomain" in response.json()


class TestDirectoryKeptInStep:
    """The Staff services keep the index of email hashes in step; no raw email is stored."""

    def test_adding_staff_adds_an_entry(self, abc: ConsultancyContext) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        assert (email_hash(SHARED_EMAIL), "abcedu") in entries()

    def test_inviting_adds_an_entry(self, abc: ConsultancyContext) -> None:
        admin = abc.add_staff(Role.ADMIN)
        with tenant_context(abc.consultancy):
            invite_employee(
                NewStaff(name="New Person", email="New@Example.com", role=Role.COUNSELLOR),
                abc.consultancy,
                office(abc),
                by=admin,
            )
        assert (email_hash("new@example.com"), "abcedu") in entries()

    def test_cancelling_an_invite_removes_it(self, abc: ConsultancyContext) -> None:
        admin = abc.add_staff(Role.ADMIN)
        with tenant_context(abc.consultancy):
            invited = invite_employee(
                NewStaff(name="New Person", email="new@example.com", role=Role.COUNSELLOR),
                abc.consultancy,
                office(abc),
                by=admin,
            )
            cancel_invite(invited, by=admin)
        assert (email_hash("new@example.com"), "abcedu") not in entries()

    def test_deactivating_removes_it(self, central: APIClient, abc: ConsultancyContext) -> None:
        admin = abc.add_staff(Role.ADMIN)
        leaver = abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        with tenant_context(abc.consultancy):
            deactivate_employee(leaver, by=admin)
        assert (email_hash(SHARED_EMAIL), "abcedu") not in entries()
        ask(central, {"email": SHARED_EMAIL})
        assert mail.outbox == []

    def test_deactivating_through_the_api_removes_it(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        leaver = abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        response = abc.client.post(f"/api/staff/{leaver.pk}/deactivate/", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert (email_hash(SHARED_EMAIL), "abcedu") not in entries()

    def test_changing_an_email_moves_the_entry(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        with tenant_context(abc.consultancy):
            from staff.services import change_email  # noqa: PLC0415  # the service under test

            change_email(staff, "Sita.New@Example.com")
            staff.refresh_from_db()
        assert staff.email == "sita.new@example.com"
        assert (email_hash(SHARED_EMAIL), "abcedu") not in entries()
        assert (email_hash("sita.new@example.com"), "abcedu") in entries()

    def test_same_email_in_two_offices_has_two_entries(
        self, abc: ConsultancyContext, xyz: ConsultancyContext
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        xyz.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        hashed = email_hash(SHARED_EMAIL)
        assert [entry for entry in entries() if entry[0] == hashed] == [
            (hashed, "abcedu"),
            (hashed, "xyzoverseas"),
        ]

    def test_an_admin_added_from_the_platform_is_indexed(
        self, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()
        response = platform_site.client.post(
            "/api/platform/consultancies/",
            {"name": "Delta Study", "subdomain": "beta"},
            format="json",
        )
        assert response.status_code == status.HTTP_202_ACCEPTED
        run_queued_jobs()
        assert (email_hash("ram@example.com"), "beta") not in entries()

        added = platform_site.client.post(
            f"/api/platform/consultancies/{response.json()['id']}/admins/",
            {"name": "Ram", "email": "Ram@Example.com"},
            format="json",
        )

        assert added.status_code == status.HTTP_201_CREATED
        assert (email_hash("ram@example.com"), "beta") in entries()

    def test_no_raw_email_in_the_public_schema(self, abc: ConsultancyContext) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        with schema_context(PUBLIC_SCHEMA), connection.cursor() as cursor:
            for table in ("central_login_staffdirectoryentry", "central_login_centralloginrequest"):
                cursor.execute(f"SELECT * FROM {table}")  # noqa: S608  # fixed table names
                assert SHARED_EMAIL not in str(cursor.fetchall())

    def test_hash_is_keyed_with_its_own_secret(self, settings: Settings) -> None:
        first = email_hash(SHARED_EMAIL)
        settings.STAFF_DIRECTORY_KEY = "another-staff-directory-key-0123456789abc"
        assert email_hash(SHARED_EMAIL) != first
        assert len(first) == 64


class TestRebuildCommand:
    """`rebuild_staff_directory` builds the index again from every office's Staff."""

    def test_rebuilds_from_every_schema(
        self, abc: ConsultancyContext, xyz: ConsultancyContext
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        xyz.add_staff(Role.ADMIN, email="boss@example.com")
        leaver = xyz.add_staff(Role.COUNSELLOR, email="leaver@example.com")
        with tenant_context(xyz.consultancy):
            Staff.objects.filter(pk=leaver.pk).update(status="deactivated")
        with schema_context(PUBLIC_SCHEMA):
            StaffDirectoryEntry.objects.all().delete()
            StaffDirectoryEntry.objects.create(
                email_hash=email_hash("stale@example.com"),
                consultancy=Consultancy.objects.get(schema_name="abcedu"),
            )
        call_command("rebuild_staff_directory")
        assert entries() == sorted(
            [
                (email_hash(SHARED_EMAIL), "abcedu"),
                (email_hash("boss@example.com"), "xyzoverseas"),
            ]
        )


class TestLogging:
    """No raw email is ever logged; each lookup logs one line."""

    def test_no_email_in_logs(
        self, central: APIClient, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        json_logs.clear()
        ask(central, {"email": SHARED_EMAIL})
        ask(central, {"email": "nobody@example.com"})
        text = json_logs.text()
        assert SHARED_EMAIL not in text
        assert "nobody@example.com" not in text
        assert email_hash(SHARED_EMAIL) not in text
        assert len(json_logs.with_message("Central login request handled")) == 2


class TestDirectoryGuards:
    """The index is only changed for the office whose schema is active."""

    def test_refuses_a_change_outside_an_office(self, db: None) -> None:
        activate_public_schema()
        with pytest.raises(RuntimeError, match="inside an office"):
            add_to_directory(SHARED_EMAIL)

    def test_rows_never_print_a_hash_or_email(self, abc: ConsultancyContext) -> None:
        abc.add_staff(Role.COUNSELLOR, email=SHARED_EMAIL)
        with schema_context(PUBLIC_SCHEMA):
            entry = StaffDirectoryEntry.objects.get()
            request = CentralLoginRequest.objects.create(
                email_hash=email_hash(SHARED_EMAIL), requested_at=timezone.now()
            )
        assert str(entry) == f"Directory entry {entry.pk}"
        assert str(request) == f"Central login request {request.pk}"
