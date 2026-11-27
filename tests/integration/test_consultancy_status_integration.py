"""Consultancy Status (issue 42, spec 13-13d): Platform Admins change it, every request obeys it."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone
from django_tenants.utils import schema_context, tenant_context
from rest_framework import status
from rest_framework.test import APIClient

from platform_admin.models import PlatformAdmin
from platform_admin.setup import start_consultancy_setup
from platform_admin.types import ConsultancyOnboarding
from staff.models import Role, Staff
from tenancy.exceptions import InvalidStatusChangeError, StatusRecordIsAppendOnlyError
from tenancy.models import Consultancy, ConsultancyStatusChange, Domain
from tenancy.services import change_status, start_active, start_evaluation, terminate
from tenancy.status import ConsultancyStatus
from tenancy.types import StatusChange
from tests.conftest import (
    TEST_PASSWORD,
    ConsultancyContext,
    ConsultancyFactory,
    PlatformContext,
    api_client,
    build_host,
    run_queued_jobs,
    use_access_token,
)

CONSULTANCIES_URL = "/api/platform/consultancies/"
LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/refresh/"
LOGOUT_URL = "/api/auth/logout/"
CONTEXT_URL = "/api/context/"
CONSULTANCY_URL = "/api/consultancy/"
ME_URL = "/api/me/"
STUDENTS_URL = "/api/students/"
PUBLIC_SCHEMA = "public"
DAY = timedelta(days=1)
INJECTION = "<script>alert(1)</script>'; DROP TABLE tenancy_consultancy; --"


def status_url(consultancy_id: object) -> str:
    """Return the URL that changes a Consultancy's Status."""
    return f"{CONSULTANCIES_URL}{consultancy_id}/status/"


def days_ahead(days: int) -> str:
    """Return the date `days` after today, in the Consultancy's time zone, as YYYY-MM-DD."""
    return (timezone.localdate() + timedelta(days=days)).isoformat()


def set_status(office: ConsultancyContext, value: str, **fields: Any) -> None:  # noqa: ANN401
    """Put the Consultancy straight into a Status, as if a Platform Admin had."""
    with schema_context(PUBLIC_SCHEMA):
        Consultancy.objects.filter(pk=office.consultancy.pk).update(status=value, **fields)


def reload(office: ConsultancyContext) -> Consultancy:
    """Return the Consultancy as stored now."""
    with schema_context(PUBLIC_SCHEMA):
        stored: Consultancy = Consultancy.objects.get(pk=office.consultancy.pk)
    return stored


def changes(office: ConsultancyContext) -> list[ConsultancyStatusChange]:
    """Return the recorded Status changes, oldest first."""
    with schema_context(PUBLIC_SCHEMA):
        return list(
            ConsultancyStatusChange.objects.filter(consultancy=office.consultancy).order_by("pk")
        )


def change(platform: PlatformContext, office: ConsultancyContext, **body: object) -> Any:  # noqa: ANN401
    """Ask the Platform API to change the Consultancy's Status."""
    return platform.client.post(status_url(office.consultancy.public_id), body, format="json")


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """ABC Education, as created by the test helper (Evaluation, 14 days)."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def ops(platform_site: PlatformContext) -> PlatformAdmin:
    """The logged-in Platform Admin."""
    return platform_site.log_in()


def create_and_set_up(client: APIClient, **overrides: object) -> dict[str, Any]:
    """Create a Consultancy through the API, run its setup job, and return its listed row."""
    response = client.post(CONSULTANCIES_URL, new_consultancy(**overrides), format="json")
    assert response.status_code == status.HTTP_202_ACCEPTED
    run_queued_jobs()
    listed: list[dict[str, Any]] = client.get(CONSULTANCIES_URL).json()
    return next(row for row in listed if row["id"] == response.json()["id"])


class TestNewConsultancy:
    def test_starts_in_evaluation_for_14_days_recorded_under_the_platform_admin(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        before = timezone.now()

        body = create_and_set_up(platform_site.client)

        assert body["status"] == "evaluation"
        assert body["paid_until"] is None
        ends_at = datetime.fromisoformat(body["evaluation_ends_at"])
        assert before + timedelta(days=14) <= ends_at <= timezone.now() + timedelta(days=14)
        with schema_context(PUBLIC_SCHEMA):
            record = ConsultancyStatusChange.objects.get(consultancy__public_id=body["id"])
        assert (record.from_status, record.to_status, record.changed_by_id) == (
            "",
            "evaluation",
            ops.pk,
        )

    def test_can_start_active_with_a_paid_until_date(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        body = create_and_set_up(platform_site.client, status="active", paid_until=days_ahead(30))

        assert (body["status"], body["paid_until"], body["evaluation_ends_at"]) == (
            "active",
            days_ahead(30),
            None,
        )

    @pytest.mark.parametrize(
        ("extra", "field"),
        [
            ({"status": "active"}, "paid_until"),
            ({"status": "active", "paid_until": days_ahead(0)}, "paid_until"),
            ({"status": "active", "paid_until": "12/11/2026"}, "paid_until"),
            ({"status": "active", "paid_until": 20261112}, "paid_until"),
            ({"paid_until": days_ahead(5)}, "paid_until"),
            ({"status": "suspended"}, "status"),
            ({"status": "terminated"}, "status"),
            ({"status": ["active"]}, "status"),
        ],
    )
    def test_refuses_a_bad_starting_status(
        self, platform_site: PlatformContext, ops: PlatformAdmin, extra: dict[str, Any], field: str
    ) -> None:
        response = platform_site.client.post(
            CONSULTANCIES_URL, new_consultancy(**extra), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        assert not Consultancy.objects.filter(name="Everest Education").exists()

    def test_refuses_a_paid_until_date_too_far_ahead_and_creates_nothing(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        response = platform_site.client.post(
            CONSULTANCIES_URL,
            new_consultancy(status="active", paid_until=days_ahead(4000)),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Choose a date within 10 years."}
        assert not Consultancy.objects.filter(name="Everest Education").exists()
        assert not Domain.objects.filter(domain=build_host("everest")).exists()

    def test_refuses_unknown_fields(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        response = platform_site.client.post(
            CONSULTANCIES_URL, new_consultancy(is_active=False), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}


def new_consultancy(**overrides: object) -> dict[str, object]:
    """Return a valid create-Consultancy body, with some fields replaced."""
    return {
        "name": "Everest Education",
        "subdomain": "everest",
        "contact_email": "info@everest.test",
        **overrides,
    }


class TestMoves:
    def test_evaluation_to_active_with_paid_until(
        self, abc: ConsultancyContext, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        response = change(platform_site, abc, status="active", paid_until=days_ahead(31))

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert (body["status"], body["paid_until"], body["evaluation_ends_at"]) == (
            "active",
            days_ahead(31),
            None,
        )

    @pytest.mark.usefixtures("ops")
    @pytest.mark.parametrize(
        ("start", "body", "expected"),
        [
            ("active", {"status": "past_due", "reason": "Invoice 12 unpaid"}, "past_due"),
            ("active", {"status": "suspended", "reason": "Unpaid for 60 days"}, "suspended"),
            ("past_due", {"status": "suspended", "reason": "Still unpaid"}, "suspended"),
            ("suspended", {"status": "past_due", "reason": "Part paid"}, "past_due"),
            ("past_due", {"status": "active", "paid_until": days_ahead(30)}, "active"),
            ("suspended", {"status": "active", "paid_until": days_ahead(30)}, "active"),
            ("active", {"status": "active", "paid_until": days_ahead(365)}, "active"),
            ("evaluation", {"status": "terminated", "reason": "Didn't go ahead"}, "terminated"),
            ("suspended", {"status": "terminated", "reason": "Left us"}, "terminated"),
        ],
    )
    def test_allowed_moves(
        self,
        abc: ConsultancyContext,
        platform_site: PlatformContext,
        start: str,
        body: dict[str, str],
        expected: str,
    ) -> None:
        set_status(abc, start)

        response = change(platform_site, abc, **body)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == expected
        assert reload(abc).status == expected

    @pytest.mark.usefixtures("ops")
    @pytest.mark.parametrize(
        ("start", "body", "message"),
        [
            (
                "evaluation",
                {"status": "past_due", "reason": "x"},
                "Evaluation can't change to Past due.",
            ),
            (
                "evaluation",
                {"status": "suspended", "reason": "x"},
                "Evaluation can't change to Suspended.",
            ),
            (
                "past_due",
                {"status": "past_due", "reason": "x"},
                "Past due can't change to Past due.",
            ),
            (
                "active",
                {"status": "evaluation", "evaluation_ends_at": days_ahead(3)},
                "Only a Consultancy in Evaluation can have its evaluation extended.",
            ),
            (
                "terminated",
                {"status": "active", "paid_until": days_ahead(3)},
                "A Terminated Consultancy can't be changed.",
            ),
            (
                "terminated",
                {"status": "terminated", "reason": "again"},
                "A Terminated Consultancy can't be changed.",
            ),
        ],
    )
    def test_refused_moves_change_nothing(
        self,
        abc: ConsultancyContext,
        platform_site: PlatformContext,
        start: str,
        body: dict[str, str],
        message: str,
    ) -> None:
        set_status(abc, start)

        response = change(platform_site, abc, **body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": message}
        assert reload(abc).status == start
        assert changes(abc) == []

    def test_extending_an_evaluation_keeps_the_status(
        self, abc: ConsultancyContext, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        set_status(abc, "evaluation", evaluation_ends_at=timezone.now() - timedelta(days=1))

        response = change(
            platform_site,
            abc,
            status="evaluation",
            evaluation_ends_at=days_ahead(10),
            reason="Waiting for their owner",
        )

        assert response.status_code == status.HTTP_200_OK
        ends_at = timezone.localtime(datetime.fromisoformat(response.json()["evaluation_ends_at"]))
        assert ends_at.date() == date.fromisoformat(days_ahead(11))
        assert (ends_at.hour, ends_at.minute) == (0, 0)
        assert reload(abc).status == "evaluation"
        assert abc.new_client().get(CONSULTANCY_URL).json()["status_message"] is None

    def test_terminating_switches_off_every_address(
        self, abc: ConsultancyContext, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        with schema_context(PUBLIC_SCHEMA):
            Domain.objects.create(domain=build_host("abc-old"), tenant=abc.consultancy)

        change(platform_site, abc, status="terminated", reason="Contract ended")

        with schema_context(PUBLIC_SCHEMA):
            assert not Domain.objects.filter(tenant=abc.consultancy, is_active=True).exists()
        assert reload(abc).terminated_at is not None
        for host in (abc.host, build_host("abc-old")):
            assert api_client(host).get(CONSULTANCY_URL).status_code == status.HTTP_404_NOT_FOUND

    def test_terminated_data_is_kept(
        self, abc: ConsultancyContext, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)

        change(platform_site, abc, status="terminated", reason="Contract ended")

        with tenant_context(abc.consultancy):
            assert Staff.objects.filter(pk=counsellor.pk).exists()

    def test_suspending_records_when(
        self, abc: ConsultancyContext, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        set_status(abc, "active")

        change(platform_site, abc, status="suspended", reason="Unpaid")

        assert reload(abc).suspended_at is not None
        change(platform_site, abc, status="active", paid_until=days_ahead(30))
        assert reload(abc).suspended_at is None


class TestStatusRecord:
    def test_every_change_is_recorded_with_who_from_to_reason_and_when(
        self, abc: ConsultancyContext, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        before = timezone.now()

        change(platform_site, abc, status="active", paid_until=days_ahead(30))
        change(platform_site, abc, status="past_due", reason="  Invoice 12 unpaid  ")

        recorded = changes(abc)
        assert [(r.from_status, r.to_status, r.reason, r.changed_by_id) for r in recorded] == [
            ("evaluation", "active", "", ops.pk),
            ("active", "past_due", "Invoice 12 unpaid", ops.pk),
        ]
        assert recorded[0].paid_until == date.fromisoformat(days_ahead(30))
        assert all(before <= r.changed_at <= timezone.now() for r in recorded)
        assert reload(abc).status_reason == "Invoice 12 unpaid"

    def test_the_record_can_never_be_changed_or_deleted(
        self, abc: ConsultancyContext, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        change(platform_site, abc, status="active", paid_until=days_ahead(30))
        record = changes(abc)[0]

        with schema_context(PUBLIC_SCHEMA):
            record.reason = "rewritten"
            with pytest.raises(StatusRecordIsAppendOnlyError):
                record.save()
            with pytest.raises(StatusRecordIsAppendOnlyError):
                record.delete()
            with pytest.raises(StatusRecordIsAppendOnlyError):
                ConsultancyStatusChange.objects.all().update(reason="x")
            with pytest.raises(StatusRecordIsAppendOnlyError):
                ConsultancyStatusChange.objects.all().delete()

    def test_the_service_refuses_terminating_twice(
        self, abc: ConsultancyContext, ops: PlatformAdmin
    ) -> None:
        with schema_context(PUBLIC_SCHEMA):
            terminate(abc.consultancy, reason="Left", by=ops)
            with pytest.raises(InvalidStatusChangeError):
                terminate(abc.consultancy, reason="Left again", by=ops)
        assert len(changes(abc)) == 1


class TestRefusedInput:
    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({}, "status"),
            ({"status": "closed"}, "status"),
            ({"status": 3}, "status"),
            ({"status": None}, "status"),
            ({"status": "past_due"}, "detail"),
            ({"status": "past_due", "reason": "   "}, "detail"),
            ({"status": "suspended", "reason": ""}, "detail"),
            ({"status": "terminated"}, "detail"),
            ({"status": "past_due", "reason": 12}, "reason"),
            ({"status": "past_due", "reason": ["x"]}, "reason"),
            ({"status": "past_due", "reason": "x" * 501}, "reason"),
            ({"status": "active"}, "paid_until"),
            ({"status": "active", "paid_until": None}, "paid_until"),
            ({"status": "active", "paid_until": "2026-02-30"}, "paid_until"),
            ({"status": "active", "paid_until": "2026-11-12T10:00:00"}, "paid_until"),
            ({"status": "active", "paid_until": "2020-01-01"}, "paid_until"),
            ({"status": "active", "paid_until": days_ahead(0)}, "paid_until"),
            ({"status": "active", "paid_until": days_ahead(4000)}, "detail"),
            ({"status": "active", "paid_until": INJECTION}, "paid_until"),
            ({"status": "evaluation"}, "evaluation_ends_at"),
            ({"status": "evaluation", "evaluation_ends_at": days_ahead(-1)}, "evaluation_ends_at"),
            ({"status": "evaluation", "evaluation_ends_at": days_ahead(4000)}, "detail"),
            (
                {"status": "past_due", "reason": "x", "paid_until": days_ahead(3)},
                "paid_until",
            ),
            (
                {"status": "active", "paid_until": days_ahead(3), "evaluation_ends_at": "x"},
                "evaluation_ends_at",
            ),
            ({"status": "past_due", "reason": "x", "is_active": False}, "detail"),
        ],
    )
    def test_refused_and_nothing_changes(
        self,
        abc: ConsultancyContext,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        body: dict[str, Any],
        field: str,
    ) -> None:
        set_status(abc, "active")

        response = platform_site.client.post(
            status_url(abc.consultancy.public_id), body, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        assert reload(abc).status == "active"
        assert changes(abc) == []

    def test_a_reason_shaped_like_code_is_kept_as_text(
        self, abc: ConsultancyContext, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        set_status(abc, "active")

        response = change(platform_site, abc, status="past_due", reason=INJECTION)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status_reason"] == INJECTION
        assert Consultancy.objects.filter(pk=abc.consultancy.pk).exists()

    def test_a_form_body_is_refused(
        self, abc: ConsultancyContext, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        response = platform_site.client.post(
            status_url(abc.consultancy.public_id),
            "status=terminated&reason=x",
            content_type="text/plain",
        )

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        assert reload(abc).status == "evaluation"

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(
        self,
        abc: ConsultancyContext,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        method: str,
    ) -> None:
        response = getattr(platform_site.client, method)(status_url(abc.consultancy.public_id))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_unknown_consultancy_is_not_found(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        response = platform_site.client.post(
            status_url(uuid.uuid4()), {"status": "terminated", "reason": "x"}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestWhoCanChangeIt:
    def test_logged_out_is_refused(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        response = platform_site.new_client().post(
            status_url(abc.consultancy.public_id),
            {"status": "terminated", "reason": "x"},
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert reload(abc).status == "evaluation"

    @pytest.mark.parametrize("role", list(Role))
    def test_no_staff_member_can_change_it(
        self, abc: ConsultancyContext, platform_site: PlatformContext, role: Role
    ) -> None:
        tokens = abc.tokens_for(abc.add_staff(role))
        client = platform_site.new_client()
        use_access_token(client, tokens.access)

        response = client.post(
            status_url(abc.consultancy.public_id),
            {"status": "terminated", "reason": "x"},
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert reload(abc).status == "evaluation"

    def test_not_reachable_on_a_consultancy_address(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            status_url(abc.consultancy.public_id),
            {"status": "terminated", "reason": "x"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert reload(abc).status == "evaluation"


def log_in(client: APIClient, staff: Staff) -> Any:  # noqa: ANN401  # the response
    """Log a Staff member in with their password."""
    return client.post(LOGIN_URL, {"email": staff.email, "password": TEST_PASSWORD}, format="json")


def write(client: APIClient) -> Any:  # noqa: ANN401  # the response
    """Save a change: the Staff member's own name."""
    return client.patch(ME_URL, {"name": "Renamed"}, format="json")


class TestWhatEachStatusLetsThrough:
    @pytest.mark.parametrize(
        ("value", "fields"),
        [
            ("active", {}),
            ("evaluation", {"evaluation_ends_at": timezone.now() + timedelta(days=2)}),
        ],
    )
    def test_working_statuses_let_everything_through(
        self, abc: ConsultancyContext, value: str, fields: dict[str, Any]
    ) -> None:
        staff = abc.log_in_as(Role.ADMIN)
        set_status(abc, value, **fields)

        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_200_OK
        assert write(abc.client).status_code == status.HTTP_200_OK
        assert log_in(abc.new_client(), staff).status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("value", ["past_due", "suspended"])
    def test_read_only_statuses_let_reads_and_login_through(
        self, abc: ConsultancyContext, value: str
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        set_status(abc, value)
        client = abc.new_client()

        logged_in = log_in(client, staff)
        assert logged_in.status_code == status.HTTP_200_OK
        tokens = logged_in.json()
        use_access_token(client, tokens["access"])
        assert client.get(CONTEXT_URL).status_code == status.HTTP_200_OK
        assert client.get(STUDENTS_URL).status_code == status.HTTP_200_OK
        refreshed = client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
        assert refreshed.status_code == status.HTTP_200_OK
        logged_out = client.post(
            LOGOUT_URL, {"refresh": refreshed.json()["refresh"]}, format="json"
        )
        assert logged_out.status_code == status.HTTP_204_NO_CONTENT

    @pytest.mark.parametrize(
        ("value", "word"), [("past_due", "past due"), ("suspended", "suspended")]
    )
    def test_read_only_statuses_refuse_every_change_and_say_why(
        self, abc: ConsultancyContext, value: str, word: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        set_status(abc, value)

        responses = [
            write(abc.client),
            abc.client.post(STUDENTS_URL, {"name": "Sita"}, format="json"),
            abc.client.put(ME_URL, {}, format="json"),
            abc.client.delete(ME_URL),
            abc.client.post("/api/auth/password-reset/", {"email": "a@b.test"}, format="json"),
        ]

        for response in responses:
            assert response.status_code == status.HTTP_403_FORBIDDEN
            body = response.json()
            assert body["status"] == value
            assert word in body["detail"]
            assert "not make changes" in body["detail"]
        with tenant_context(abc.consultancy):
            assert not Staff.objects.filter(name="Renamed").exists()

    def test_an_ended_evaluation_blocks_everything_but_the_login_screen_and_logout(
        self, abc: ConsultancyContext
    ) -> None:
        staff = abc.add_staff(Role.ADMIN)
        tokens = abc.tokens_for(staff)
        use_access_token(abc.client, tokens.access)
        set_status(abc, "evaluation", evaluation_ends_at=timezone.now() - timedelta(minutes=1))

        for response in (
            abc.client.get(CONTEXT_URL),
            abc.client.get(STUDENTS_URL),
            write(abc.client),
            log_in(abc.new_client(), staff),
        ):
            assert response.status_code == status.HTTP_403_FORBIDDEN
            assert response.json()["status"] == "evaluation"
            assert "trial has ended" in response.json()["detail"]
        public = abc.new_client().get(CONSULTANCY_URL)
        assert public.status_code == status.HTTP_200_OK
        assert "trial has ended" in public.json()["status_message"]
        logged_out = abc.client.post(LOGOUT_URL, {"refresh": tokens.refresh}, format="json")
        assert logged_out.status_code == status.HTTP_204_NO_CONTENT

    def test_terminated_is_not_found_for_everything(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.ADMIN)
        tokens = abc.tokens_for(staff)
        use_access_token(abc.client, tokens.access)
        set_status(abc, "terminated")

        for response in (
            abc.client.get(CONTEXT_URL),
            abc.client.get(CONSULTANCY_URL),
            write(abc.client),
            log_in(abc.new_client(), staff),
            abc.client.post(LOGOUT_URL, {"refresh": tokens.refresh}, format="json"),
        ):
            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "closed" not in response.content.decode()

    def test_a_switched_off_address_is_not_found(self, abc: ConsultancyContext) -> None:
        with schema_context(PUBLIC_SCHEMA):
            Domain.objects.filter(tenant=abc.consultancy).update(is_active=False)

        assert abc.new_client().get(CONSULTANCY_URL).status_code == status.HTTP_404_NOT_FOUND

    def test_a_damaged_status_is_read_only(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        set_status(abc, "unknown")

        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_200_OK
        assert write(abc.client).status_code == status.HTTP_403_FORBIDDEN

    def test_the_platform_keeps_working_whatever_a_consultancy_s_status(
        self, abc: ConsultancyContext, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        set_status(abc, "suspended")

        assert platform_site.client.get(CONSULTANCIES_URL).status_code == status.HTTP_200_OK
        response = change(platform_site, abc, status="active", paid_until=days_ahead(30))
        assert response.status_code == status.HTTP_200_OK


class TestStaffSeeTheStatus:
    def test_context_has_the_status_and_no_message_when_active(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        set_status(abc, "active", evaluation_ends_at=None)

        consultancy = abc.client.get(CONTEXT_URL).json()["consultancy"]

        assert (
            consultancy["status"],
            consultancy["status_message"],
            consultancy["evaluation_ends_at"],
        ) == ("active", None, None)

    def test_context_gives_the_evaluation_end_so_the_shell_can_warn(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        ends_at = timezone.now() + timedelta(days=2)
        set_status(abc, "evaluation", evaluation_ends_at=ends_at)

        consultancy = abc.client.get(CONTEXT_URL).json()["consultancy"]

        assert consultancy["status"] == "evaluation"
        assert consultancy["status_message"] is None
        assert datetime.fromisoformat(consultancy["evaluation_ends_at"]) == ends_at

    @pytest.mark.parametrize("value", ["past_due", "suspended"])
    def test_context_and_login_screen_say_why_staff_can_only_read(
        self, abc: ConsultancyContext, value: str
    ) -> None:
        abc.log_in_as(Role.TEACHER)
        set_status(abc, value)

        context = abc.client.get(CONTEXT_URL).json()["consultancy"]
        public = abc.new_client().get(CONSULTANCY_URL).json()

        assert context["status"] == public["status"] == value
        assert context["status_message"] == public["status_message"]
        assert "look things up but not make changes" in context["status_message"]
        assert "evaluation_ends_at" not in public
        assert "status_reason" not in public
        assert "status_reason" not in context


class TestStatusIsolation:
    def test_changing_one_consultancy_never_touches_another(
        self,
        make_consultancy: ConsultancyFactory,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)

        change(platform_site, abc, status="terminated", reason="Left")

        assert reload(xyz).status == "evaluation"
        with schema_context(PUBLIC_SCHEMA):
            assert Domain.objects.get(tenant=xyz.consultancy).is_active
        assert changes(xyz) == []
        assert write(xyz.client).status_code == status.HTTP_200_OK


class TestServicesGuardThemselves:
    """The services refuse bad changes even when called without the API's checks."""

    def test_a_consultancy_starts_only_once(
        self, abc: ConsultancyContext, ops: PlatformAdmin
    ) -> None:
        with schema_context(PUBLIC_SCHEMA):
            start_evaluation(abc.consultancy, by=ops)
            with pytest.raises(InvalidStatusChangeError, match="already has a Status"):
                start_active(abc.consultancy, paid_until=timezone.localdate() + DAY, by=ops)

    @pytest.mark.parametrize(
        "change",
        [
            StatusChange(ConsultancyStatus.ACTIVE, ""),
            StatusChange(ConsultancyStatus.EVALUATION, ""),
            StatusChange(ConsultancyStatus.TERMINATED, "x" * 501),
            StatusChange(ConsultancyStatus.ACTIVE, "", paid_until=date(2020, 1, 1)),
            StatusChange(ConsultancyStatus.EVALUATION, "", evaluation_ends_at=timezone.now() - DAY),
        ],
    )
    def test_bad_changes_are_refused(
        self, abc: ConsultancyContext, ops: PlatformAdmin, change: StatusChange
    ) -> None:
        with schema_context(PUBLIC_SCHEMA), pytest.raises(InvalidStatusChangeError):
            change_status(abc.consultancy, change, by=ops)
        assert changes(abc) == []

    def test_a_new_consultancy_started_active_without_a_date_is_refused(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        onboarding = ConsultancyOnboarding(
            name="Everest Education",
            subdomain="everest",
            contact_email="info@everest.test",
            status=ConsultancyStatus.ACTIVE,
        )

        with schema_context(PUBLIC_SCHEMA), pytest.raises(InvalidStatusChangeError):
            start_consultancy_setup(onboarding, by=ops)
        assert not Consultancy.objects.filter(name="Everest Education").exists()
