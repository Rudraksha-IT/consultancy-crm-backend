"""Support Access (spec 9-12, 11a, 11b; issue 27): a Platform Admin opens an office as self."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone
from django_tenants.utils import schema_context, tenant_context
from freezegun import freeze_time
from rest_framework import status
from rest_framework.test import APIClient

from auth_tokens.services import sign_out_everywhere
from history.models import HistoryEntry
from platform_admin.models import PlatformAdmin, SupportVisit
from platform_admin.support_access import ENTRY_CODE_LIFETIME, VISIT_LENGTH
from staff.models import PLATFORM_VISITOR_ID, Role, Staff
from students.models import Source, Stage, Student
from tenancy.status import ConsultancyStatus
from tests.conftest import (
    TEST_PASSWORD,
    ConsultancyContext,
    ConsultancyFactory,
    PlatformContext,
    use_access_token,
)
from tests.support_access import (
    ENTER_URL,
    LOGOUT_URL,
    open_as_platform_admin,
    start,
    start_url,
)

VISITS_URL = "/api/platform/support-visits/"
CONTEXT_URL = "/api/context/"
EMPLOYEES_URL = "/api/staff/"
MODULES_URL = "/api/support-access/modules/"
EXPORT_URL = "/api/support-access/export/"
REASON = "Bikash couldn't see a Student's documents"
ALL_ON = {
    "batches": True,
    "communication": True,
    "accounts": True,
    "reports": True,
    "clock_in": True,
}
STAFF_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER)
REFUSED = "Not allowed."


def reset_code_url(staff: Staff) -> str:
    """Send `staff` a reset Code (Platform Admins only)."""
    return f"/api/support-access/staff/{staff.pk}/reset-code/"


def sign_out_url(staff: Staff) -> str:
    """Sign `staff` out of every device (Platform Admins only)."""
    return f"/api/support-access/staff/{staff.pk}/sign-out/"


def note_url(student: Student) -> str:
    """Replace a Student's Note."""
    return f"/api/students/{student.public_id}/note/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office the Platform Admin opens."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def xyz(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """Another office, which must never be reachable through a visit to ABC."""
    return make_consultancy("XYZ Overseas", "xyzoverseas")


@pytest.fixture
def ops(platform_site: PlatformContext) -> PlatformAdmin:
    """The Platform Admin, logged in on the Platform."""
    return platform_site.log_in()


def make_student(office: ConsultancyContext, counsellor: Staff | None = None) -> Student:
    """Store a Student directly."""
    with tenant_context(office.consultancy):
        return Student.objects.create(
            name="Sita Gurung",
            phone="9841000001",
            country="australia",
            source=Source.WALK_IN,
            highest_qualification="plus_two",
            note="Wants Melbourne only.",
            assigned_counsellor=counsellor,
            stage=Stage.COUNSELLING,
        )


def visits() -> list[SupportVisit]:
    """Every stored visit, oldest first."""
    with schema_context("public"):
        return list(SupportVisit.objects.order_by("pk"))


def set_status(office: ConsultancyContext, value: ConsultancyStatus) -> None:
    """Put the office in a Status directly."""
    with schema_context("public"):
        type(office.consultancy).objects.filter(pk=office.consultancy.pk).update(status=value.value)


def post(client: APIClient, url: str, body: object = None) -> Any:  # noqa: ANN401  # the response
    """POST JSON."""
    return client.post(url, body, format="json")


class TestStartingAVisit:
    def test_records_who_which_office_why_the_status_and_when(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        before = timezone.now()
        answer = start(platform_site.client, abc, REASON)
        [visit] = visits()
        assert visit.platform_admin_id == ops.pk
        assert visit.consultancy_id == abc.consultancy.pk
        assert visit.reason == REASON
        assert visit.consultancy_status == "evaluation"
        assert visit.started_at >= before
        assert visit.ended_at is None
        assert answer["host"] == abc.host
        assert isinstance(answer["entry_code"], str)
        assert len(str(answer["entry_code"])) >= 40
        assert answer["visit"] == {
            "id": visit.pk,
            "platform_admin": {"name": "Platform Ops"},
            "consultancy": {"id": abc.consultancy.pk, "name": "ABC Education"},
            "reason": REASON,
            "consultancy_status": "evaluation",
            "started_at": visit.started_at.isoformat(),
            "ended_at": None,
        }

    def test_the_code_itself_is_never_stored(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        answer = start(platform_site.client, abc, REASON)
        [visit] = visits()
        assert str(answer["entry_code"]) not in visit.entry_code_hash

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"reason": ""},
            {"reason": "   "},
            {"reason": None},
            {"reason": 12},
            {"reason": ["help"]},
            {"reason": "x" * 501},
            {"reason": "Help", "consultancy": 2},
        ],
    )
    def test_a_reason_is_required_and_checked(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        abc: ConsultancyContext,
        body: dict[str, object],
    ) -> None:
        response = post(platform_site.client, start_url(abc.consultancy.pk), body)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert visits() == []

    def test_a_reason_shaped_like_code_is_kept_as_text(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        reason = "<script>alert(1)</script>'; DROP TABLE x;--"
        start(platform_site.client, abc, reason)
        assert visits()[0].reason == reason

    def test_a_form_body_is_refused(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        response = platform_site.client.post(start_url(abc.consultancy.pk), {"reason": REASON})
        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_only_post_is_allowed(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        url = start_url(abc.consultancy.pk)
        for method in ("get", "put", "patch", "delete"):
            response = getattr(platform_site.client, method)(url)
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_an_unknown_office_is_not_found(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        response = post(platform_site.client, start_url(999_999), {"reason": REASON})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_terminated_office_cant_be_opened(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        set_status(abc, ConsultancyStatus.TERMINATED)
        response = post(platform_site.client, start_url(abc.consultancy.pk), {"reason": REASON})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": "This office's account has been closed, so it can't be opened."
        }
        assert visits() == []

    @pytest.mark.parametrize(
        "value", [ConsultancyStatus.PAST_DUE, ConsultancyStatus.SUSPENDED, ConsultancyStatus.ACTIVE]
    )
    def test_any_other_status_can_be_opened_and_is_noted(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        abc: ConsultancyContext,
        value: ConsultancyStatus,
    ) -> None:
        set_status(abc, value)
        start(platform_site.client, abc, REASON)
        assert visits()[0].consultancy_status == value.value

    def test_logged_out_is_refused(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        response = post(platform_site.new_client(), start_url(abc.consultancy.pk), {"reason": "x"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("role", STAFF_ROLES)
    def test_staff_cant_start_a_visit(
        self, platform_site: PlatformContext, abc: ConsultancyContext, role: Role
    ) -> None:
        staff = abc.add_staff(role)
        access = abc.tokens_for(staff).access
        on_platform = platform_site.new_client()
        use_access_token(on_platform, access)
        response = post(on_platform, start_url(abc.consultancy.pk), {"reason": REASON})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        use_access_token(abc.client, access)
        response = post(abc.client, start_url(abc.consultancy.pk), {"reason": REASON})
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert visits() == []


class TestEnteringTheOffice:
    def test_the_code_logs_the_platform_admin_in_as_themselves(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        answer = start(platform_site.client, abc, REASON)
        response = post(abc.new_client(), ENTER_URL, {"code": answer["entry_code"]})
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert set(body) == {"access", "refresh", "context"}
        assert body["context"]["staff"]["name"] == "Platform Ops"
        assert body["context"]["staff"]["role"] == "admin"
        assert body["context"]["support_access"] == {
            "platform_admin_name": "Platform Ops",
            "platform_host": platform_site.host,
        }
        assert visits()[0].entered_at is not None

    def test_the_context_says_it_is_a_platform_admin(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        context = visit.client.get(CONTEXT_URL).json()
        assert context["support_access"] == {
            "platform_admin_name": "Platform Ops",
            "platform_host": platform_site.host,
        }
        assert context["staff"]["id"] == PLATFORM_VISITOR_ID

    def test_staff_context_has_no_support_access(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        assert abc.client.get(CONTEXT_URL).json()["support_access"] is None

    def test_the_code_works_only_once(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        code = start(platform_site.client, abc, REASON)["entry_code"]
        assert post(abc.new_client(), ENTER_URL, {"code": code}).status_code == 200
        again = post(abc.new_client(), ENTER_URL, {"code": code})
        assert again.status_code == status.HTTP_400_BAD_REQUEST
        assert again.json() == {
            "detail": "This link has expired. Open the office again from the Platform."
        }

    def test_the_code_works_only_on_its_own_office(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        abc: ConsultancyContext,
        xyz: ConsultancyContext,
    ) -> None:
        code = start(platform_site.client, abc, REASON)["entry_code"]
        response = post(xyz.new_client(), ENTER_URL, {"code": code})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert post(abc.new_client(), ENTER_URL, {"code": code}).status_code == 200

    def test_the_code_runs_out(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        code = start(platform_site.client, abc, REASON)["entry_code"]
        later = timezone.now() + ENTRY_CODE_LIFETIME + timedelta(seconds=1)
        with freeze_time(later):
            response = post(abc.new_client(), ENTER_URL, {"code": code})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_code_is_refused_on_the_platform(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        code = start(platform_site.client, abc, REASON)["entry_code"]
        response = post(platform_site.new_client(), ENTER_URL, {"code": code})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_deactivated_platform_admin_cant_enter(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        code = start(platform_site.client, abc, REASON)["entry_code"]
        with schema_context("public"):
            PlatformAdmin.objects.filter(pk=ops.pk).update(is_active=False)
        assert post(abc.new_client(), ENTER_URL, {"code": code}).status_code == 400

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"code": ""},
            {"code": None},
            {"code": 123},
            {"code": "x" * 101},
            {"code": "<script>"},
            {"code": "' OR 1=1 --"},
        ],
    )
    def test_bad_codes_are_refused(self, abc: ConsultancyContext, body: dict[str, object]) -> None:
        response = post(abc.new_client(), ENTER_URL, body)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "access" not in response.json()

    def test_unknown_fields_are_refused(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        code = start(platform_site.client, abc, REASON)["entry_code"]
        response = post(abc.new_client(), ENTER_URL, {"code": code, "role": "admin"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert visits()[0].entered_at is None

    def test_only_post_is_allowed(self, abc: ConsultancyContext) -> None:
        for method in ("get", "put", "patch", "delete"):
            response = getattr(abc.new_client(), method)(ENTER_URL)
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestTheVisitsTokens:
    def test_work_only_on_that_office(
        self, platform_site: PlatformContext, abc: ConsultancyContext, xyz: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        elsewhere = xyz.new_client()
        use_access_token(elsewhere, visit.access)
        assert elsewhere.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED
        on_platform = platform_site.new_client()
        use_access_token(on_platform, visit.access)
        assert on_platform.get(VISITS_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_platform_login_token_is_refused_in_an_office(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        client = abc.new_client()
        use_access_token(client, platform_site.tokens_for(ops).access)
        assert client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_renew_during_the_visit(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        response = post(abc.new_client(), "/api/auth/refresh/", {"refresh": visit.refresh})
        assert response.status_code == status.HTTP_200_OK
        renewed = abc.new_client()
        use_access_token(renewed, response.json()["access"])
        assert renewed.get(CONTEXT_URL).json()["support_access"] is not None

    def test_leaving_ends_the_visit_and_its_tokens(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        response = post(abc.new_client(), LOGOUT_URL, {"refresh": visit.refresh})
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert visits()[0].ended_at is not None
        assert visit.client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED
        renew = post(abc.new_client(), "/api/auth/refresh/", {"refresh": visit.refresh})
        assert renew.status_code == status.HTTP_401_UNAUTHORIZED

    def test_stop_working_when_the_visit_runs_out(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        with schema_context("public"):
            SupportVisit.objects.filter(pk=visit.visit_id).update(
                ends_by=timezone.now() - timedelta(seconds=1)
            )
        assert visit.client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_stop_working_when_the_platform_admin_is_signed_out(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc, admin=ops)
        with schema_context("public"):
            sign_out_everywhere(ops)
        assert visit.client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_stop_working_when_the_office_is_terminated(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        set_status(abc, ConsultancyStatus.TERMINATED)
        assert visit.client.get(CONTEXT_URL).status_code == status.HTTP_404_NOT_FOUND

    def test_lasts_at_most_the_visit_length(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        open_as_platform_admin(platform_site, abc)
        [visit] = visits()
        assert visit.ends_by - visit.started_at == VISIT_LENGTH


class TestVisitsOnThePlatform:
    def test_listed_newest_first_with_when_they_ended(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        first = open_as_platform_admin(platform_site, abc, admin=ops, reason="First")
        post(abc.new_client(), LOGOUT_URL, {"refresh": first.refresh})
        open_as_platform_admin(platform_site, abc, admin=ops, reason="Second")
        response = platform_site.client.get(VISITS_URL)
        assert response.status_code == status.HTTP_200_OK
        rows = response.json()
        assert [row["reason"] for row in rows] == ["Second", "First"]
        assert rows[0]["ended_at"] is None
        assert rows[1]["ended_at"] is not None

    def test_a_visit_that_ran_out_shows_when(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        open_as_platform_admin(platform_site, abc, admin=ops)
        ends_by = timezone.now() - timedelta(minutes=1)
        with schema_context("public"):
            SupportVisit.objects.update(ends_by=ends_by)
        [row] = platform_site.client.get(VISITS_URL).json()
        assert row["ended_at"] == ends_by.isoformat()

    def test_logged_out_is_refused(self, platform_site: PlatformContext) -> None:
        assert platform_site.new_client().get(VISITS_URL).status_code == 401

    def test_only_get(self, platform_site: PlatformContext, ops: PlatformAdmin) -> None:
        assert post(platform_site.client, VISITS_URL, {}).status_code == 405

    @pytest.mark.parametrize("role", STAFF_ROLES)
    def test_never_shown_to_the_office(
        self, platform_site: PlatformContext, abc: ConsultancyContext, role: Role
    ) -> None:
        open_as_platform_admin(platform_site, abc)
        abc.log_in_as(role)
        assert abc.client.get(VISITS_URL).status_code == status.HTTP_404_NOT_FOUND
        assert "support_access" not in str(abc.client.get(CONTEXT_URL).json()["staff"])


class TestInsideTheOffice:
    def test_can_do_what_an_admin_can(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        assert visit.client.get(EMPLOYEES_URL).status_code == status.HTTP_200_OK
        response = visit.client.put(
            "/api/configuration/dates/", {"date_display": "BS"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK

    def test_never_appears_in_the_employees_list(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        visit = open_as_platform_admin(platform_site, abc)
        for client in (abc.client, visit.client):
            names = [row["name"] for row in client.get(EMPLOYEES_URL).json()]
            assert names == [admin.name]
        with tenant_context(abc.consultancy):
            assert Staff.objects.count() == 1

    def test_changes_are_recorded_under_the_platform_admins_own_name(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        student = make_student(abc)
        visit = open_as_platform_admin(platform_site, abc)
        response = visit.client.put(note_url(student), {"note": "Fixed"}, format="json")
        assert response.status_code == status.HTTP_200_OK
        with tenant_context(abc.consultancy):
            entry = HistoryEntry.objects.get(student=student)
        assert entry.by_staff_id is None
        assert entry.by_name == "Platform Ops"
        assert entry.by_platform_admin is True
        history = visit.client.get(f"/api/students/{student.public_id}/history/").json()
        assert history["results"][0]["by"] == {"name": "Platform Ops", "is_platform_admin": True}

    def test_can_change_a_past_due_or_suspended_office_that_staff_cant(
        self, platform_site: PlatformContext, ops: PlatformAdmin, abc: ConsultancyContext
    ) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.ADMIN)
        for value in (ConsultancyStatus.PAST_DUE, ConsultancyStatus.SUSPENDED):
            set_status(abc, value)
            visit = open_as_platform_admin(platform_site, abc, admin=ops, reason=f"Help {value}")
            refused = abc.client.put(note_url(student), {"note": "Staff"}, format="json")
            assert refused.status_code == status.HTTP_403_FORBIDDEN
            done = visit.client.put(note_url(student), {"note": value.value}, format="json")
            assert done.status_code == status.HTTP_200_OK

    def test_an_ended_visits_token_doesnt_lift_the_restriction(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        set_status(abc, ConsultancyStatus.SUSPENDED)
        visit = open_as_platform_admin(platform_site, abc)
        post(abc.new_client(), LOGOUT_URL, {"refresh": visit.refresh})
        response = post(visit.client, "/api/auth/password-reset/", {"email": "a@b.test"})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_cant_change_my_settings_there_is_no_staff_account(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        response = visit.client.patch("/api/me/", {"name": "Hacker"}, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        response = post(
            visit.client,
            "/api/me/password/",
            {"current_password": TEST_PASSWORD, "new_password": "Kathmandu!2026x"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert visit.client.get("/api/me/devices/").status_code == status.HTTP_403_FORBIDDEN
        response = post(visit.client, "/api/me/devices/sign-out-others/")
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestPlatformOnlyActions:
    def test_switch_the_offices_features(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        response = visit.client.put(MODULES_URL, ALL_ON, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert {"batches", "communication", "accounts", "reports"} <= set(
            response.json()["modules"]
        )
        assert "reports" in visit.client.get(CONTEXT_URL).json()["modules"]

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {**ALL_ON, "students": False},
            {**ALL_ON, "magic": True},
            {**ALL_ON, "batches": "yes"},
            {**ALL_ON, "batches": None},
        ],
    )
    def test_bad_feature_switches_are_refused(
        self, platform_site: PlatformContext, abc: ConsultancyContext, body: dict[str, object]
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        response = visit.client.put(MODULES_URL, body, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_send_a_reset_code_to_anyone_admins_included(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        admin = abc.add_staff(Role.ADMIN)
        visit = open_as_platform_admin(platform_site, abc)
        response = post(visit.client, reset_code_url(admin))
        assert response.status_code == status.HTTP_200_OK
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [admin.email]
        assert re.search(r"\b\d{6}\b", str(mail.outbox[0].body))

    def test_no_reset_code_before_they_join(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        invited = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=invited.pk).update(status="invited")
        visit = open_as_platform_admin(platform_site, abc)
        response = post(visit.client, reset_code_url(invited))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert mail.outbox == []

    def test_sign_anyone_out_admins_included(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        visit = open_as_platform_admin(platform_site, abc)
        response = post(visit.client, sign_out_url(admin))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_unknown_staff_is_not_found(
        self, platform_site: PlatformContext, abc: ConsultancyContext, xyz: ConsultancyContext
    ) -> None:
        theirs = xyz.add_staff(Role.ADMIN, email="admin@xyz.test")
        visit = open_as_platform_admin(platform_site, abc)
        assert post(visit.client, sign_out_url(theirs)).status_code == 404
        assert post(visit.client, reset_code_url(theirs)).status_code == 404
        assert mail.outbox == []

    def test_download_all_of_the_offices_data(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        make_student(abc)
        with tenant_context(abc.consultancy):
            Student.objects.update(note="=HYPERLINK(1)")
        abc.add_staff(Role.ADMIN)
        visit = open_as_platform_admin(platform_site, abc)
        response = post(visit.client, EXPORT_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/zip"
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = set(archive.namelist())
        assert {"students_student.csv", "staff_staff.csv", "history_historyentry.csv"} <= names
        assert not any(name.startswith(("codes_", "auth_tokens_")) for name in names)
        staff_rows = list(csv.reader(io.StringIO(archive.read("staff_staff.csv").decode())))
        assert "password" not in staff_rows[0]
        assert len(staff_rows) == 2
        students = list(csv.DictReader(io.StringIO(archive.read("students_student.csv").decode())))
        assert students[0]["note"] == "'=HYPERLINK(1)"
        assert "communication_providersettings.csv" not in names

    @pytest.mark.parametrize("role", STAFF_ROLES)
    def test_refused_for_every_staff_role(self, abc: ConsultancyContext, role: Role) -> None:
        other = abc.add_staff(Role.ADMIN, email="other@abc.test")
        abc.log_in_as(role)
        for response in (
            abc.client.put(MODULES_URL, ALL_ON, format="json"),
            post(abc.client, reset_code_url(other)),
            post(abc.client, sign_out_url(other)),
            post(abc.client, EXPORT_URL),
        ):
            assert response.status_code == status.HTTP_403_FORBIDDEN
            assert response.json() == {"detail": "Only a Platform Admin can do this."}
        assert mail.outbox == []

    def test_refused_when_logged_out(self, abc: ConsultancyContext) -> None:
        assert abc.new_client().put(MODULES_URL, ALL_ON, format="json").status_code == 401
        assert post(abc.new_client(), EXPORT_URL).status_code == 401

    def test_only_the_methods_they_need(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        assert visit.client.get(MODULES_URL).status_code == 405
        assert visit.client.get(EXPORT_URL).status_code == 405
        staff = abc.add_staff(Role.COUNSELLOR)
        assert visit.client.get(sign_out_url(staff)).status_code == 405
        assert visit.client.delete(reset_code_url(staff)).status_code == 405
