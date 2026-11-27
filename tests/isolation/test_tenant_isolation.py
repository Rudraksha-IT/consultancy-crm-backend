"""Tenant isolation: nothing belonging to one Consultancy is visible from another's host.

Every later endpoint adds its own case here (see the spec's Testing Decisions).
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application, Visa
from auth_tokens.models import WrongPasswordCount
from auth_tokens.password_tries import WRONG_PASSWORDS_ALLOWED
from auth_tokens.services import sign_out_everywhere
from batches.models import Attendance, Batch, BatchMembership, Mark
from catalogue.models import Course, Intake, University
from central_login.directory import offices_for
from clock_in.models import (
    Holiday,
    WorkFromHome,
    WorkingDayChange,
    WorkSession,
    WorkSessionChange,
)
from commission.models import Commission
from communication.models import Message
from documents.models import Document, DocumentFile, DocumentRequirement, DocumentType
from emails.types import OfficeDetails
from expenses.models import Expense
from exports.models import ExportRecord
from fee_types.models import FeeType
from fees.models import Fee
from history.models import HistoryEntry, HistoryKind
from inbox.models import InboxMessage
from modules.services import switch_modules
from modules.types import Module
from notifications.models import Notification
from office_networks.models import OfficeNetwork
from payments.models import Payment
from platform_admin.models import PlatformAdmin
from staff.models import Role, Staff, StaffStatus
from students.models import Student, WorkExperience
from tasks.models import Task
from tenancy.context import activate_public_schema
from tenancy.models import Consultancy
from tenancy.services import set_office_details
from tests.conftest import (
    PLATFORM_TEST_PASSWORD,
    TEST_PASSWORD,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    run_queued_jobs,
    use_access_token,
)
from tests.fakes import FakeDocumentStorage, StoredFile
from tests.integration.test_record_ids_office_integration import (
    office_with_every_record,
    record_addresses,
)
from tests.support_access import open_as_platform_admin
from tests.support_access import start as start_visit
from transfer_requests.models import TransferRequest, TransferRequestStatus
from visits.models import Visit

# A UUID no record has, fixed so every test worker collects the same tests.
NO_RECORD_ID = "00000000-0000-4000-8000-000000000000"
LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/refresh/"
LOGOUT_URL = "/api/auth/logout/"
CONTEXT_URL = "/api/context/"


class TestTenantIsolation:
    def test_data_created_in_one_consultancy_is_not_visible_in_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_admin = abc.add_staff(Role.ADMIN)

        with tenant_context(xyz.consultancy):
            is_visible_from_xyz = Staff.objects.filter(email=abc_admin.email).exists()

        assert not is_visible_from_xyz


class TestLoginIsolation:
    def test_staff_cannot_log_in_on_another_consultancys_subdomain(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_counsellor = abc.add_staff(Role.COUNSELLOR)

        response = xyz.client.post(
            LOGIN_URL, {"email": abc_counsellor.email, "password": TEST_PASSWORD}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_access_token_from_one_consultancy_is_rejected_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.add_staff(Role.ADMIN)  # Same primary key as ABC's Staff, so only the schema differs.
        tokens = abc.tokens_for(abc.add_staff(Role.ADMIN))
        use_access_token(abc.client, tokens.access)
        use_access_token(xyz.client, tokens.access)

        response = xyz.client.get(CONTEXT_URL)

        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_200_OK
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_access_token_is_rejected_when_another_host_is_forwarded(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.add_staff(Role.ADMIN)  # Same primary key as ABC's Staff, so only the schema differs.
        tokens = abc.tokens_for(abc.add_staff(Role.ADMIN))
        client = api_client(abc.host, HTTP_X_FORWARDED_HOST=xyz.host)
        use_access_token(client, tokens.access)

        response = client.get(CONTEXT_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_refresh_token_from_one_consultancy_is_rejected_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz_tokens = xyz.tokens_for(xyz.add_staff(Role.ADMIN))
        abc_tokens = abc.tokens_for(abc.add_staff(Role.ADMIN))

        refreshed = xyz.client.post(REFRESH_URL, {"refresh": abc_tokens.refresh}, format="json")
        logged_out = xyz.client.post(LOGOUT_URL, {"refresh": abc_tokens.refresh}, format="json")

        assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED
        assert logged_out.status_code == status.HTTP_401_UNAUTHORIZED
        # Neither touched A's own token, nor B's record with the same id.
        assert (
            abc.client.post(REFRESH_URL, {"refresh": abc_tokens.refresh}, format="json").status_code
            == status.HTTP_200_OK
        )
        assert (
            xyz.client.post(REFRESH_URL, {"refresh": xyz_tokens.refresh}, format="json").status_code
            == status.HTTP_200_OK
        )

    def test_signing_out_everywhere_on_one_consultancy_leaves_the_same_id_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_admin = abc.add_staff(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)  # Same primary key as ABC's Admin.

        with tenant_context(abc.consultancy):
            sign_out_everywhere(abc_admin)

        assert xyz.client.get(CONTEXT_URL).status_code == status.HTTP_200_OK

    def test_wrong_passwords_on_one_consultancy_never_count_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        email = "sita@shared.test"
        abc.add_staff(Role.COUNSELLOR, email=email)
        xyz.add_staff(Role.COUNSELLOR, email=email)
        wrong = {"email": email, "password": "not-the-password"}
        for _ in range(WRONG_PASSWORDS_ALLOWED):
            abc.client.post(LOGIN_URL, wrong, format="json")
        refused = abc.client.post(LOGIN_URL, {**wrong, "password": TEST_PASSWORD}, format="json")
        assert refused.status_code == status.HTTP_429_TOO_MANY_REQUESTS

        right = xyz.client.post(LOGIN_URL, {**wrong, "password": TEST_PASSWORD}, format="json")

        assert right.status_code == status.HTTP_200_OK
        with tenant_context(xyz.consultancy):
            assert not WrongPasswordCount.objects.exists()


RESET_URL = "/api/auth/password-reset/"
RESET_CODE_URL = "/api/auth/password-reset/code/"
RESET_COMPLETE_URL = "/api/auth/password-reset/complete/"
NEW_PASSWORD = "Kathmandu!2026"  # noqa: S105  # test-only credential


def _emailed_code() -> str:
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[-1].body))
    assert found is not None
    return found.group(1)


class TestCodeIsolation:
    def test_a_code_from_one_consultancy_does_not_work_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        # The same person works at both (same email, same primary key).
        abc.add_staff(Role.COUNSELLOR, email="sita@both.test")
        xyz_staff = xyz.add_staff(Role.COUNSELLOR, email="sita@both.test")
        abc.client.post(RESET_URL, {"email": "sita@both.test"}, format="json")
        code = _emailed_code()

        checked = xyz.client.post(
            RESET_CODE_URL, {"email": "sita@both.test", "code": code}, format="json"
        )
        completed = xyz.client.post(
            RESET_COMPLETE_URL,
            {"email": "sita@both.test", "code": code, "password": NEW_PASSWORD},
            format="json",
        )

        assert checked.status_code == status.HTTP_400_BAD_REQUEST
        assert completed.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(xyz.consultancy):
            assert Staff.objects.get(pk=xyz_staff.pk).check_password(TEST_PASSWORD)

    def test_a_reset_on_one_consultancy_leaves_the_same_person_on_another_logged_in(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.add_staff(Role.COUNSELLOR, email="sita@both.test")
        xyz_staff = xyz.add_staff(Role.COUNSELLOR, email="sita@both.test")
        use_access_token(xyz.client, xyz.tokens_for(xyz_staff).access)
        abc.client.post(RESET_URL, {"email": "sita@both.test"}, format="json")

        abc.client.post(
            RESET_COMPLETE_URL,
            {"email": "sita@both.test", "code": _emailed_code(), "password": NEW_PASSWORD},
            format="json",
        )

        assert xyz.client.get(CONTEXT_URL).status_code == status.HTTP_200_OK
        with tenant_context(xyz.consultancy):
            assert Staff.objects.get(pk=xyz_staff.pk).check_password(TEST_PASSWORD)

    def test_a_request_on_one_consultancy_emails_only_its_own_staff(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.add_staff(Role.COUNSELLOR, email="ram@xyz.test")

        abc.client.post(RESET_URL, {"email": "ram@xyz.test"}, format="json")

        assert mail.outbox == []


PLATFORM_RESET_URL = "/api/platform/auth/password-reset/"
PLATFORM_RESET_VERIFY_URL = "/api/platform/auth/password-reset/verify/"
PLATFORM_RESET_CONFIRM_URL = "/api/platform/auth/password-reset/confirm/"


class TestPlatformResetIsolation:
    """The Platform's forgot password (issue 63) never touches an office, nor an office it."""

    def test_an_office_reset_code_never_resets_the_platform_admin_with_that_email(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.add_staff(Role.ADMIN, email="ops@both.test")
        admin = platform_site.add_admin(email="ops@both.test")
        abc.client.post(RESET_URL, {"email": "ops@both.test"}, format="json")
        body = {"email": "ops@both.test", "code": _emailed_code(), "new_password": NEW_PASSWORD}

        response = platform_site.client.post(PLATFORM_RESET_CONFIRM_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        activate_public_schema()
        assert PlatformAdmin.objects.get(pk=admin.pk).check_password(PLATFORM_TEST_PASSWORD)

    def test_a_platform_reset_never_changes_an_office_staff_member_with_that_email(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        staff = abc.add_staff(Role.ADMIN, email="ops@both.test")
        platform_site.add_admin(email="ops@both.test")
        use_access_token(abc.client, abc.tokens_for(staff).access)
        platform_site.client.post(PLATFORM_RESET_URL, {"email": "ops@both.test"}, format="json")
        code = _emailed_code()

        refused = abc.client.post(
            RESET_COMPLETE_URL,
            {"email": "ops@both.test", "code": code, "password": NEW_PASSWORD},
            format="json",
        )
        platform_site.client.post(
            PLATFORM_RESET_CONFIRM_URL,
            {"email": "ops@both.test", "code": code, "new_password": NEW_PASSWORD},
            format="json",
        )

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_200_OK
        with tenant_context(abc.consultancy):
            assert Staff.objects.get(pk=staff.pk).check_password(TEST_PASSWORD)

    @pytest.mark.parametrize(
        "url", [PLATFORM_RESET_URL, PLATFORM_RESET_VERIFY_URL, PLATFORM_RESET_CONFIRM_URL]
    )
    def test_the_platform_reset_does_not_exist_on_an_office_host(
        self, make_consultancy: ConsultancyFactory, url: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.post(url, {"email": "ops@both.test"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert mail.outbox == []


class TestPlatformTeamAndBrandIsolation:
    """The Platform Admins tab and the product logo (issue 65) are the Platform's alone."""

    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("get", "/api/platform/admins/"),
            ("post", "/api/platform/admins/"),
            ("post", f"/api/platform/admins/{NO_RECORD_ID}/deactivate/"),
            ("post", f"/api/platform/admins/{NO_RECORD_ID}/resend/"),
            ("post", f"/api/platform/admins/{NO_RECORD_ID}/reactivate/"),
            ("post", "/api/platform/brand/logo/"),
            ("get", "/api/platform/brand/logos/"),
            ("put", "/api/platform/brand/in-use/"),
            ("delete", f"/api/platform/brand/logos/{NO_RECORD_ID}/"),
            ("get", f"/api/platform/brand/logos/{NO_RECORD_ID}/file/"),
            ("patch", "/api/platform/me/"),
        ],
    )
    def test_staff_of_any_office_never_reach_them(
        self,
        make_consultancy: ConsultancyFactory,
        platform_site: PlatformContext,
        method: str,
        url: str,
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        admin = platform_site.add_admin()
        access = abc.tokens_for(abc.add_staff(Role.ADMIN)).access

        for client in (abc.new_client(), xyz.new_client(), platform_site.new_client()):
            use_access_token(client, access)
            body = {"name": "Intruder", "email": "intruder@x.test"}
            response = getattr(client, method)(url, body, format="json")
            assert response.status_code in {401, 403, 404, 415}

        activate_public_schema()
        assert PlatformAdmin.objects.count() == 1
        assert PlatformAdmin.objects.get(pk=admin.pk).is_active


LOGO_URL = "/api/configuration/logo/"
LOGO_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
LOGO_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _logo_upload(content: bytes = LOGO_PNG) -> dict[str, object]:
    return {"file": SimpleUploadedFile("logo.png", content, content_type="image/png")}


class TestOfficeLogoIsolation:
    def test_each_office_shows_only_its_own_logo(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)

        abc.client.post(LOGO_URL, _logo_upload(LOGO_PNG), format="multipart")

        assert xyz.client.get(CONTEXT_URL).json()["consultancy"]["logo"] is None
        assert xyz.new_client().get("/api/consultancy/").json()["logo"] is None
        # The same address on the other office's host finds nothing.
        assert xyz.new_client().get("/api/consultancy/logo/1/").status_code == 404
        xyz.client.post(LOGO_URL, _logo_upload(LOGO_JPEG), format="multipart")
        assert abc.new_client().get("/api/consultancy/logo/1/").content == LOGO_PNG
        assert xyz.new_client().get("/api/consultancy/logo/1/").content == LOGO_JPEG

    def test_files_are_kept_under_each_offices_own_prefix(
        self, make_consultancy: ConsultancyFactory, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)

        abc.client.post(LOGO_URL, _logo_upload(), format="multipart")
        xyz.client.post(LOGO_URL, _logo_upload(), format="multipart")
        xyz.client.delete(LOGO_URL)

        [key] = fake_document_storage.files
        assert key.startswith("abcedu/office-logo/")
        assert abc.new_client().get("/api/consultancy/").json()["logo"] is not None

    @pytest.mark.parametrize("method", ["post", "patch", "delete"])
    def test_an_admins_token_cannot_change_another_offices_logo(
        self, make_consultancy: ConsultancyFactory, method: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)  # Same primary key as ABC's Admin, so only the schema differs.
        xyz.client.post(LOGO_URL, _logo_upload(), format="multipart")
        intruder = xyz.new_client()
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        if method == "post":
            response = intruder.post(LOGO_URL, _logo_upload(LOGO_JPEG), format="multipart")
        elif method == "patch":
            response = intruder.patch(LOGO_URL, {"show_name": False}, format="json")
        else:
            response = intruder.delete(LOGO_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert xyz.new_client().get("/api/consultancy/").json()["logo"] == {
            "url": "/api/consultancy/logo/1/",
            "show_name": True,
        }
        assert xyz.new_client().get("/api/consultancy/logo/1/").content == LOGO_PNG


OFFICE_DETAILS_URL = "/api/configuration/office-details/"
ABC_DETAILS = {
    "contact_email": "info@abc.com",
    "phone": "01-4412345",
    "address_line1": "Putalisadak, Kathmandu",
    "address_line2": "",
    "website": "https://abc.com",
}


class TestOfficeDetailsIsolation:
    def test_each_office_reads_only_its_own_details(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.COUNSELLOR)

        abc.client.put(OFFICE_DETAILS_URL, ABC_DETAILS, format="json")

        assert abc.client.get("/api/context/").json()["consultancy"]["office_details"] == (
            ABC_DETAILS
        )
        theirs = xyz.client.get("/api/context/").json()["consultancy"]
        assert theirs["has_contact_email"] is False
        assert "info@abc.com" not in str(theirs)

    def test_an_admins_token_cannot_change_another_offices_details(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)  # Same primary key as ABC's Admin, so only the schema differs.
        intruder = xyz.new_client()
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        response = intruder.put(OFFICE_DETAILS_URL, ABC_DETAILS, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert xyz.client.get("/api/context/").json()["consultancy"]["has_contact_email"] is False


class TestAccountEmailIsolation:
    def test_each_offices_code_email_carries_only_its_own_look(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        set_office_details(abc.consultancy, OfficeDetails(contact_email="info@abc.com"))
        staff = xyz.add_staff(Role.COUNSELLOR)

        xyz.new_client().post("/api/auth/password-reset/", {"email": str(staff.email)})

        [sent] = mail.outbox
        assert sent.from_email.startswith("XYZ Overseas")
        assert sent.reply_to == []
        assert "ABC Education" not in str(sent.body)
        assert "info@abc.com" not in str(sent.body)

    def test_an_offices_admin_cannot_reach_the_platform_test_email(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        on_platform = platform_site.new_client()
        use_access_token(on_platform, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        assert on_platform.post("/api/platform/email/test/").status_code in {401, 403}
        assert on_platform.get("/api/platform/email/").status_code in {401, 403}
        assert mail.outbox == []


class TestModuleIsolation:
    def test_switching_one_consultancys_modules_leaves_another_alone(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        platform_site.log_in()

        platform_site.client.put(
            f"/api/platform/consultancies/{abc.consultancy.public_id}/modules/",
            {
                "batches": True,
                "communication": True,
                "accounts": True,
                "reports": True,
                "clock_in": True,
            },
            format="json",
        )

        assert abc.client.get("/api/batches/").status_code == status.HTTP_200_OK
        assert xyz.client.get("/api/batches/").status_code == status.HTTP_404_NOT_FOUND
        assert "batches" not in xyz.client.get(CONTEXT_URL).json()["modules"]

    def test_a_token_from_a_consultancy_with_the_module_on_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.add_staff(Role.ADMIN)  # Same primary key as ABC's Admin, so only the schema differs.
        platform_site.log_in()
        for office in (abc, xyz):
            platform_site.client.put(
                f"/api/platform/consultancies/{office.consultancy.public_id}/modules/",
                {
                    "batches": True,
                    "communication": True,
                    "accounts": True,
                    "reports": True,
                    "clock_in": True,
                },
                format="json",
            )
        use_access_token(xyz.client, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        assert (
            xyz.client.get("/api/accounts/student-fees/").status_code
            == status.HTTP_401_UNAUTHORIZED
        )


class TestLogIsolation:
    def test_one_consultancys_log_lines_never_carry_anothers_host(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_admin = abc.add_staff(Role.ADMIN)

        abc.client.post(
            LOGIN_URL, {"email": abc_admin.email, "password": TEST_PASSWORD}, format="json"
        )
        xyz.client.post(
            LOGIN_URL, {"email": abc_admin.email, "password": TEST_PASSWORD}, format="json"
        )

        assert json_logs.only(f"Staff logged in: staff_id={abc_admin.pk}")["tenant"] == abc.host
        assert json_logs.only("Staff login failed")["tenant"] == xyz.host
        assert {entry["tenant"] for entry in json_logs.with_message("POST /api/auth/login/")} == {
            abc.host,
            xyz.host,
        }


STAFF_URL = "/api/staff/"
JOIN_COMPLETE_URL = "/api/auth/join/complete/"


def _invite_body(email: str) -> dict[str, object]:
    return {
        "name": "Sita Sharma",
        "mobile": "9812345670",
        "email": email,
        "job_title": "Counsellor",
        "role": "counsellor",
        "joined_on": "2026-11-01",
    }


class TestStaffManagementIsolation:
    def test_an_admin_sees_only_their_own_consultancys_staff(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz_counsellor = xyz.add_staff(Role.COUNSELLOR, email="ram@xyz.test")

        emails = [row["email"] for row in abc.client.get(STAFF_URL).json()]

        assert xyz_counsellor.email not in emails

    def test_an_admin_cannot_change_another_consultancys_staff_by_id(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.add_staff(Role.ADMIN, email="boss@xyz.test")
        target = xyz.add_staff(Role.COUNSELLOR, email="ram@xyz.test")  # id 2, absent on ABC

        changed = abc.client.patch(
            f"{STAFF_URL}{target.public_id}/", {"role": "admin"}, format="json"
        )
        deactivated = abc.client.post(f"{STAFF_URL}{target.public_id}/deactivate/")
        resent = abc.client.post(f"{STAFF_URL}{target.public_id}/invite/")
        cancelled = abc.client.delete(f"{STAFF_URL}{target.public_id}/invite/")
        invited_back = abc.client.post(f"{STAFF_URL}{target.public_id}/invite-back/")

        assert {r.status_code for r in (changed, deactivated, resent, cancelled, invited_back)} == {
            status.HTTP_404_NOT_FOUND
        }
        with tenant_context(xyz.consultancy):
            kept = Staff.objects.get(pk=target.pk)
        assert kept.role == Role.COUNSELLOR
        assert kept.is_active
        assert mail.outbox == []

    def test_an_admin_cannot_invite_back_another_consultancys_leaver(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_admin = abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        leaver = xyz.add_staff(Role.COUNSELLOR, email="ram@xyz.test")
        xyz.client.post(f"{STAFF_URL}{leaver.public_id}/deactivate/")
        on_xyz = xyz.new_client()
        use_access_token(on_xyz, abc.tokens_for(abc_admin).access)

        by_id = abc.client.post(f"{STAFF_URL}{leaver.public_id}/invite-back/")
        by_token = on_xyz.post(f"{STAFF_URL}{leaver.public_id}/invite-back/")

        assert by_id.status_code == status.HTTP_404_NOT_FOUND
        assert by_token.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            kept = Staff.objects.get(pk=leaver.pk)
        assert kept.status == StaffStatus.DEACTIVATED
        assert kept.invited_back is False
        assert mail.outbox == []

    def test_inviting_back_in_one_consultancy_leaves_the_same_email_elsewhere_alone(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        email = "ram@shared.test"
        abc_leaver = abc.add_staff(Role.COUNSELLOR, email=email)
        xyz_leaver = xyz.add_staff(Role.COUNSELLOR, email=email)
        with tenant_context(xyz.consultancy):
            Staff.objects.filter(pk=xyz_leaver.pk).update(status=StaffStatus.DEACTIVATED)
        abc.client.post(f"{STAFF_URL}{abc_leaver.public_id}/deactivate/")

        invited = abc.client.post(f"{STAFF_URL}{abc_leaver.public_id}/invite-back/")

        assert invited.status_code == status.HTTP_202_ACCEPTED
        with tenant_context(xyz.consultancy):
            other = Staff.objects.get(pk=xyz_leaver.pk)
        assert other.status == StaffStatus.DEACTIVATED
        assert other.invited_back is False
        assert len(mail.outbox) == 1

    def test_an_admins_token_is_refused_on_another_consultancys_staff_list(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.add_staff(Role.ADMIN)  # Same primary key as ABC's Admin.
        tokens = abc.tokens_for(abc.add_staff(Role.ADMIN))
        use_access_token(xyz.client, tokens.access)

        listed = xyz.client.get(STAFF_URL)
        invited = xyz.client.post(STAFF_URL, _invite_body("sita@x.test"), format="json")

        assert listed.status_code == status.HTTP_401_UNAUTHORIZED
        assert invited.status_code == status.HTTP_401_UNAUTHORIZED
        assert mail.outbox == []

    def test_an_invite_code_from_one_consultancy_does_not_work_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        email = "sita@both.test"
        abc.client.post(STAFF_URL, _invite_body(email), format="json")
        abc_code = re.findall(r"\b\d{6}\b", str(mail.outbox[-1].body))[0]
        xyz.client.post(STAFF_URL, _invite_body(email), format="json")
        xyz_code = re.findall(r"\b\d{6}\b", str(mail.outbox[-1].body))[0]
        body = {"email": email, "code": abc_code, "password": "Kathmandu!2026"}

        response = xyz.new_client().post(JOIN_COMPLETE_URL, body, format="json")

        if abc_code != xyz_code:
            assert response.status_code == status.HTTP_400_BAD_REQUEST
        abc_joined = abc.new_client().post(JOIN_COMPLETE_URL, body, format="json")
        assert abc_joined.status_code == status.HTTP_200_OK
        with tenant_context(xyz.consultancy):
            assert not Staff.objects.get(email=email).is_active


STUDENTS_URL = "/api/students/"
APPLICATIONS_URL = "/api/applications/"
VISAS_URL = "/api/visas/"


def _lead_body(name: str, **changes: object) -> dict[str, object]:
    return {
        "name": name,
        "phone": "9819988776",
        "email": "same@gmail.test",
        "country": "canada",
        "source": "walk_in",
        "highest_qualification": "plus_two",
        **changes,
    }


class TestStudentIsolation:
    def test_students_of_one_consultancy_never_appear_in_anothers_list_or_search(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.FRONT_DESK)
        xyz.log_in_as(Role.FRONT_DESK)
        abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json")
        xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json")

        listed = xyz.client.get(STUDENTS_URL).json()
        by_name = xyz.client.get(STUDENTS_URL, {"q": "sita"}).json()
        by_phone = xyz.client.get(STUDENTS_URL, {"q": "9819988776"}).json()
        by_email = xyz.client.get(STUDENTS_URL, {"q": "same@gmail"}).json()

        assert [row["name"] for row in listed["results"]] == ["Ram Thapa"]
        assert by_name["results"] == []
        assert [row["name"] for row in by_phone["results"]] == ["Ram Thapa"]
        assert [row["name"] for row in by_email["results"]] == ["Ram Thapa"]

    def test_a_counsellor_of_another_consultancy_cannot_be_assigned(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.FRONT_DESK)
        xyz.add_staff(Role.FRONT_DESK)
        xyz_counsellor = xyz.add_staff(Role.COUNSELLOR)  # id 2, absent on ABC

        response = abc.client.post(
            STUDENTS_URL,
            _lead_body("Sita Karki", assigned_counsellor=xyz_counsellor.pk),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_token_from_one_consultancy_cannot_list_or_add_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.add_staff(Role.FRONT_DESK)  # Same primary key as ABC's Front Desk.
        tokens = abc.tokens_for(abc.add_staff(Role.FRONT_DESK))
        use_access_token(xyz.client, tokens.access)

        listed = xyz.client.get(STUDENTS_URL)
        added = xyz.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json")

        assert listed.status_code == status.HTTP_401_UNAUTHORIZED
        assert added.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            assert not Student.objects.exists()


class TestStudentProfileIsolation:
    """Issue 10: one Consultancy's Students, their Notes and History stay its own."""

    def test_the_same_id_opens_only_this_consultancys_student(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita_id = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()[
            "id"
        ]
        ram_id = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        assert sita_id != ram_id  # Every Student has their own UUID, whichever office.

        theirs = xyz.client.get(f"{STUDENTS_URL}{sita_id}/")
        own = xyz.client.get(f"{STUDENTS_URL}{ram_id}/").json()
        with tenant_context(abc.consultancy):
            sita_number = Student.objects.get(public_id=sita_id).pk
        by_number = xyz.client.get(f"{STUDENTS_URL}{sita_number}/")

        assert theirs.status_code == status.HTTP_404_NOT_FOUND
        assert own["name"] == "Ram Thapa"
        assert by_number.status_code == status.HTTP_404_NOT_FOUND

    def test_a_student_only_in_another_consultancy_cannot_be_read_or_changed(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json")
        only_abc = abc.client.post(STUDENTS_URL, _lead_body("Gita Rai"), format="json").json()["id"]
        url = f"{STUDENTS_URL}{only_abc}/"

        responses = [
            xyz.client.get(url),
            xyz.client.patch(url, {"father_name": "Stolen"}, format="json"),
            xyz.client.put(f"{url}note/", {"note": "Stolen"}, format="json"),
            xyz.client.get(f"{url}history/"),
            xyz.client.post(
                f"{url}academic-records/", {"level": "see", "institution": "X"}, format="json"
            ),
            xyz.client.post(f"{url}test-scores/", {"test": "pte", "overall": 50}, format="json"),
        ]

        assert [response.status_code for response in responses] == [status.HTTP_404_NOT_FOUND] * 6
        with tenant_context(abc.consultancy):
            gita = Student.objects.get(public_id=only_abc)
            assert gita.father_name == ""
            assert gita.note == ""

    def test_one_consultancys_history_never_shows_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita_id = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()[
            "id"
        ]
        ram_id = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        abc.client.put(f"{STUDENTS_URL}{sita_id}/note/", {"note": "ABC secret"}, format="json")

        history = xyz.client.get(f"{STUDENTS_URL}{ram_id}/history/").json()

        assert history["count"] == 0

    def test_a_token_from_one_consultancy_cannot_open_or_change_a_profile_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)  # Same primary key as ABC's Admin below.
        ram_id = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        tokens = abc.tokens_for(abc.add_staff(Role.ADMIN))
        intruder = api_client(xyz.host)
        use_access_token(intruder, tokens.access)
        url = f"{STUDENTS_URL}{ram_id}/"

        responses = [
            intruder.get(url),
            intruder.patch(url, {"father_name": "Stolen"}, format="json"),
            intruder.put(f"{url}note/", {"note": "Stolen"}, format="json"),
            intruder.get(f"{url}history/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 4
        with tenant_context(xyz.consultancy):
            assert Student.objects.get(public_id=ram_id).note != "Stolen"


class TestEditDetailsIsolation:
    """Issue 81: one save on the Edit details page never reaches another Consultancy."""

    @staticmethod
    def _save(details: str) -> dict[str, object]:
        return {
            "version": 1,
            "name": details,
            "academic_records": [{"level": "see", "institution": details}],
            "test_scores": [],
            "work_experience": [],
        }

    def test_another_consultancys_student_cannot_be_saved_from_here(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json")
        abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json")
        only_abc = abc.client.post(STUDENTS_URL, _lead_body("Gita Rai"), format="json").json()["id"]

        response = xyz.client.put(
            f"{STUDENTS_URL}{only_abc}/details/", self._save("Stolen"), format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            gita = Student.objects.get(public_id=only_abc)
            assert gita.name == "Gita Rai"
            assert not gita.academic_records.exists()

    def test_the_same_id_saves_only_this_consultancys_student_and_rows(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()["id"]
        ram = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        abc_row = abc.client.post(
            f"{STUDENTS_URL}{sita}/academic-records/",
            {"level": "see", "institution": "ABC school"},
            format="json",
        ).json()["id"]
        stolen = {
            **self._save("Ram Thapa"),
            "version": 1,
            "academic_records": [{"id": abc_row, "level": "see", "institution": "Stolen"}],
        }

        response = xyz.client.put(f"{STUDENTS_URL}{ram}/details/", stolen, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(abc.consultancy):
            sita_rows = Student.objects.get(public_id=sita).academic_records
            assert sita_rows.get().institution == "ABC school"
        with tenant_context(xyz.consultancy):
            assert not Student.objects.get(public_id=ram).academic_records.exists()

    def test_a_token_from_another_consultancy_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)  # Same primary key as ABC's Admin below.
        ram_id = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        response = intruder.put(
            f"{STUDENTS_URL}{ram_id}/details/", self._save("Stolen"), format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            assert Student.objects.get(public_id=ram_id).name == "Ram Thapa"


class TestWorkExperienceIsolation:
    """Issue 46: one Consultancy's Students' jobs stay its own."""

    def test_another_consultancys_jobs_cannot_be_read_added_changed_or_removed(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        ram = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        sita = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()["id"]
        gita = abc.client.post(STUDENTS_URL, _lead_body("Gita Rai"), format="json").json()["id"]
        job = {"employer": "Bir Hospital", "job_title": "Nurse", "started_on": "2024-04"}
        job_id = abc.client.post(
            f"{STUDENTS_URL}{gita}/work-experience/", job, format="json"
        ).json()["id"]
        url = f"{STUDENTS_URL}{gita}/work-experience/"

        responses = [
            xyz.client.post(url, job, format="json"),
            xyz.client.patch(f"{url}{job_id}/", {"employer": "Stolen"}, format="json"),
            xyz.client.delete(f"{url}{job_id}/"),
        ]
        abc_sita_on_xyz = xyz.client.get(f"{STUDENTS_URL}{sita}/")
        on_xyz = xyz.client.get(f"{STUDENTS_URL}{ram}/").json()

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 3
        assert abc_sita_on_xyz.status_code == status.HTTP_404_NOT_FOUND
        assert on_xyz["name"] == "Ram Thapa"
        assert on_xyz["work_experience"] == []
        with tenant_context(abc.consultancy):
            assert WorkExperience.objects.get(public_id=job_id).employer == "Bir Hospital"

    def test_a_token_from_one_consultancy_cannot_touch_jobs_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        ram = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        job = {"employer": "Bir Hospital", "job_title": "Nurse", "started_on": "2024-04"}
        url = f"{STUDENTS_URL}{ram}/work-experience/"
        job_id = xyz.client.post(url, job, format="json").json()["id"]
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        responses = [
            intruder.post(url, job, format="json"),
            intruder.patch(f"{url}{job_id}/", {"employer": "Stolen"}, format="json"),
            intruder.delete(f"{url}{job_id}/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 3
        with tenant_context(xyz.consultancy):
            assert WorkExperience.objects.filter(student__public_id=ram).count() == 1


class TestStageAndCounsellorIsolation:
    """Issue 11: Stage, assignment and Transfer never reach another Consultancy."""

    def test_another_consultancys_student_cannot_be_moved_assigned_or_transferred(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz_admin = xyz.log_in_as(Role.ADMIN)
        abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json")
        only_abc = abc.client.post(STUDENTS_URL, _lead_body("Gita Rai"), format="json").json()["id"]
        url = f"{STUDENTS_URL}{only_abc}/"

        responses = [
            xyz.client.post(f"{url}stage/", {"stage": "lost", "lost_reason": "x"}, format="json"),
            xyz.client.post(
                f"{url}assign/", {"counsellor": str(xyz_admin.public_id)}, format="json"
            ),
            xyz.client.post(
                f"{url}transfer/", {"counsellor": str(xyz_admin.public_id)}, format="json"
            ),
        ]

        assert [response.status_code for response in responses] == [status.HTTP_404_NOT_FOUND] * 3
        with tenant_context(abc.consultancy):
            gita = Student.objects.get(public_id=only_abc)
            assert (gita.stage, gita.assigned_counsellor_id) == ("lead", None)

    def test_a_counsellor_of_another_consultancy_cannot_be_chosen(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.add_staff(Role.ADMIN)
        abc_counsellor = abc.add_staff(Role.COUNSELLOR)  # Its id doesn't exist at XYZ.
        xyz.log_in_as(Role.ADMIN)
        ram_id = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]

        response = xyz.client.post(
            f"{STUDENTS_URL}{ram_id}/assign/",
            {"counsellor": str(abc_counsellor.public_id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(xyz.consultancy):
            assert Student.objects.get(public_id=ram_id).assigned_counsellor_id is None

    def test_the_assigned_students_list_shows_only_this_consultancys_students(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_admin = abc.log_in_as(Role.ADMIN)
        xyz_admin = xyz.log_in_as(Role.ADMIN)
        assert abc_admin.pk == xyz_admin.pk  # Each schema numbers its own Staff.
        abc.client.post(
            STUDENTS_URL,
            _lead_body("Sita Karki", assigned_counsellor=abc_admin.pk),
            format="json",
        )

        response = xyz.client.get(f"/api/staff/{xyz_admin.public_id}/assigned-students/")

        assert response.json() == []

    def test_a_token_from_one_consultancy_cannot_move_assign_or_transfer_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz_admin = xyz.log_in_as(Role.ADMIN)
        ram_id = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)
        url = f"{STUDENTS_URL}{ram_id}/"

        responses = [
            intruder.post(f"{url}stage/", {"stage": "counselling"}, format="json"),
            intruder.post(f"{url}assign/", {"counsellor": str(xyz_admin.public_id)}, format="json"),
            intruder.post(
                f"{url}transfer/", {"counsellor": str(xyz_admin.public_id)}, format="json"
            ),
            intruder.get(f"/api/staff/{xyz_admin.public_id}/assigned-students/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 4
        with tenant_context(xyz.consultancy):
            assert Student.objects.get(public_id=ram_id).stage == "lead"


CATALOGUE_URL = "/api/catalogue/universities/"


def _university_url(university_id: object) -> str:
    return f"{CATALOGUE_URL}{university_id}/"


def _courses_url(university_id: object) -> str:
    return f"{CATALOGUE_URL}{university_id}/courses/"


def _course_url(course_id: object) -> str:
    return f"/api/catalogue/courses/{course_id}/"


_COURSE_BODY = {
    "name": "Bachelor of Nursing",
    "level": "bachelor",
    "intakes": [f"{timezone.localdate().year + 1}-02"],
}
_IMPORT_URL = "/api/catalogue/import/"
_IMPORT_CSV = (
    "university,country,course,level,intakes\nDeakin University,Australia,MIT,Master,Feb 2027\n"
)


class TestCatalogueIsolation:
    def test_one_consultancys_catalogue_is_not_visible_from_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        added = abc.client.post(
            CATALOGUE_URL, {"name": "Deakin University", "country": "australia"}, format="json"
        ).json()
        abc.client.post(_courses_url(added["id"]), _COURSE_BODY, format="json")

        listed = xyz.client.get(CATALOGUE_URL, {"include_archived": "true"}).json()
        searched = xyz.client.get(CATALOGUE_URL, {"q": "deakin"}).json()

        assert listed["results"] == []
        assert searched["results"] == []

    def test_the_same_name_can_be_used_in_two_consultancies(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        body = {"name": "Deakin University", "country": "australia"}

        first = abc.client.post(CATALOGUE_URL, body, format="json")
        second = xyz.client.post(CATALOGUE_URL, body, format="json")

        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_201_CREATED

    def test_another_consultancys_university_or_course_cannot_be_changed_by_id(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        university = abc.client.post(
            CATALOGUE_URL, {"name": "Deakin University", "country": "australia"}, format="json"
        ).json()
        course = abc.client.post(_courses_url(university["id"]), _COURSE_BODY, format="json").json()

        renamed = xyz.client.patch(
            _university_url(university["id"]), {"name": "Taken"}, format="json"
        )
        course_added = xyz.client.post(_courses_url(university["id"]), _COURSE_BODY, format="json")
        archived = xyz.client.patch(_course_url(course["id"]), {"archived": True}, format="json")

        assert renamed.status_code == status.HTTP_404_NOT_FOUND
        assert course_added.status_code == status.HTTP_404_NOT_FOUND
        assert archived.status_code == status.HTTP_404_NOT_FOUND
        listed = abc.client.get(CATALOGUE_URL).json()["results"]
        assert listed[0]["name"] == "Deakin University"
        assert [c["archived"] for c in listed[0]["courses"]] == [False]

    def test_another_consultancys_university_cannot_be_read_saved_or_deleted_by_id(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        university = abc.client.post(
            CATALOGUE_URL, {"name": "Deakin University", "country": "australia"}, format="json"
        ).json()
        course = abc.client.post(_courses_url(university["id"]), _COURSE_BODY, format="json").json()
        abc.client.patch(_university_url(university["id"]), {"archived": True}, format="json")
        page = {
            "name": "Taken",
            "country": "canada",
            "archived": False,
            "courses": [{**_COURSE_BODY, "id": course["id"], "archived": True, "documents": []}],
            "deleted_courses": [],
        }

        read = xyz.client.get(_university_url(university["id"]))
        saved = xyz.client.put(_university_url(university["id"]), page, format="json")
        deleted = xyz.client.delete(_university_url(university["id"]))

        assert read.status_code == status.HTTP_404_NOT_FOUND
        assert saved.status_code == status.HTTP_404_NOT_FOUND
        assert deleted.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            stored = University.objects.get(public_id=university["id"])
            assert stored.name == "Deakin University"
            assert not stored.courses.get().archived

    def test_upcoming_intakes_come_only_from_the_consultancys_own_catalogue(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.COUNSELLOR)
        university = abc.client.post(
            CATALOGUE_URL, {"name": "Deakin University", "country": "australia"}, format="json"
        ).json()
        abc.client.post(_courses_url(university["id"]), _COURSE_BODY, format="json")

        mine = abc.client.get("/api/catalogue/intakes/").json()
        theirs = xyz.client.get("/api/catalogue/intakes/").json()

        assert mine == {"intakes": _COURSE_BODY["intakes"]}
        assert theirs == {"intakes": []}

    def test_an_admins_token_cannot_save_or_delete_in_another_consultancy(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        with tenant_context(xyz.consultancy):
            target = University.objects.create(name="Monash", country="australia", archived=True)
        xyz.add_staff(Role.ADMIN)  # Same primary key as ABC's Admin.
        tokens = abc.tokens_for(abc.add_staff(Role.ADMIN))
        use_access_token(xyz.client, tokens.access)
        page = {
            "name": "Taken",
            "country": "canada",
            "archived": False,
            "courses": [],
            "deleted_courses": [],
        }

        saved = xyz.client.put(_university_url(target.public_id), page, format="json")
        deleted = xyz.client.delete(_university_url(target.public_id))

        assert saved.status_code == status.HTTP_401_UNAUTHORIZED
        assert deleted.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            assert University.objects.get(pk=target.pk).name == "Monash"

    def test_an_admins_token_cannot_read_or_change_another_consultancys_catalogue(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.add_staff(Role.ADMIN)  # Same primary key as ABC's Admin.
        tokens = abc.tokens_for(abc.add_staff(Role.ADMIN))
        use_access_token(xyz.client, tokens.access)

        listed = xyz.client.get(CATALOGUE_URL)
        added = xyz.client.post(
            CATALOGUE_URL, {"name": "Deakin University", "country": "australia"}, format="json"
        )

        assert listed.status_code == status.HTTP_401_UNAUTHORIZED
        assert added.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            assert not University.objects.exists()

    def test_an_import_lands_only_in_its_own_consultancy(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)

        first = abc.client.post(_IMPORT_URL, {"csv": _IMPORT_CSV}, format="json").json()
        listed = xyz.client.get(CATALOGUE_URL, {"include_archived": "true"}).json()
        second = xyz.client.post(_IMPORT_URL, {"csv": _IMPORT_CSV}, format="json").json()

        assert first["imported"] == 1
        assert listed["results"] == []
        # Matching by name only looks inside the importing Consultancy.
        assert second["imported"] == 1
        assert second["already_there"] == 0
        with tenant_context(abc.consultancy):
            assert University.objects.count() == 1

    def test_an_admins_token_cannot_import_into_another_consultancy(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.add_staff(Role.ADMIN)  # Same primary key as ABC's Admin.
        tokens = abc.tokens_for(abc.add_staff(Role.ADMIN))
        use_access_token(xyz.client, tokens.access)

        response = xyz.client.post(_IMPORT_URL, {"csv": _IMPORT_CSV}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            assert not University.objects.exists()


def _course_in(office: ConsultancyContext, name: str = "Deakin University") -> Course:
    """An active Course (Intake Mar 2027) in `office`'s Catalogue."""
    with tenant_context(office.consultancy):
        university = University.objects.create(name=name, country="australia")
        course = Course.objects.create(university=university, name="Master of IT", level="master")
        Intake.objects.create(course=course, month="2027-03")
        return course


def _applications_url(student_id: str) -> str:
    return f"{STUDENTS_URL}{student_id}/applications/"


class TestApplicationIsolation:
    """Issue 14: Applications never reach, or use, another Consultancy's data."""

    def test_another_consultancys_student_cannot_get_or_change_an_application(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json")
        only_abc = abc.client.post(STUDENTS_URL, _lead_body("Gita Rai"), format="json").json()["id"]
        abc_course = _course_in(abc)
        added = abc.client.post(
            _applications_url(only_abc),
            {"course": str(abc_course.public_id), "intake": "2027-03"},
            format="json",
        ).json()["applications"][0]
        xyz_course = _course_in(xyz)  # Same id as ABC's Course.

        responses = [
            xyz.client.post(
                _applications_url(only_abc),
                {"course": str(xyz_course.public_id), "intake": "2027-03"},
                format="json",
            ),
            xyz.client.patch(
                f"{_applications_url(only_abc)}{added['id']}/",
                {"status": "rejected"},
                format="json",
            ),
        ]

        assert abc_course.pk == xyz_course.pk
        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2
        with tenant_context(abc.consultancy):
            [stored] = Application.objects.all()
            assert stored.status == "applied"
            assert Student.objects.get(public_id=only_abc).stage == "applying"

    def test_an_application_id_of_another_consultancy_is_not_found_on_its_own_student(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_student = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()
        abc.client.post(
            _applications_url(abc_student["id"]),
            {"course": str(_course_in(abc).public_id), "intake": "2027-03"},
            format="json",
        )
        xyz_student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()

        response = xyz.client.patch(
            f"{_applications_url(xyz_student['id'])}1/", {"status": "rejected"}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert Application.objects.get().status == "applied"

    def test_a_course_of_another_consultancy_cannot_be_picked(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_course = _course_in(abc)  # Its id doesn't exist at XYZ.
        xyz.log_in_as(Role.ADMIN)
        ram_id = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]

        response = xyz.client.post(
            _applications_url(ram_id),
            {"course": str(abc_course.public_id), "intake": "2027-03"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"course": ["Choose a course from the Catalogue."]}
        with tenant_context(xyz.consultancy):
            assert not Application.objects.exists()

    def test_one_consultancys_applications_never_show_on_anothers_profile(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_id = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()["id"]
        abc.client.post(
            _applications_url(abc_id),
            {"course": str(_course_in(abc).public_id), "intake": "2027-03"},
            format="json",
        )
        xyz_id = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]

        profile = xyz.client.get(f"{STUDENTS_URL}{xyz_id}/").json()

        assert abc_id != xyz_id
        assert profile["applications"] == []

    def test_a_token_from_one_consultancy_cannot_add_or_change_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        ram_id = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        course = _course_in(xyz)
        added = xyz.client.post(
            _applications_url(ram_id),
            {"course": str(course.public_id), "intake": "2027-03"},
            format="json",
        ).json()["applications"][0]
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        responses = [
            intruder.post(
                _applications_url(ram_id),
                {"course": str(course.public_id), "intake": "2027-03"},
                format="json",
            ),
            intruder.patch(
                f"{_applications_url(ram_id)}{added['id']}/", {"status": "rejected"}, format="json"
            ),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 2
        with tenant_context(xyz.consultancy):
            assert Application.objects.get().status == "applied"


class TestApplicationListIsolation:
    """Issue 47: the office-wide Applications list shows only the office's own Applications."""

    def test_each_office_lists_and_offers_filters_from_only_its_own_applications(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()["id"]
        abc.client.post(
            _applications_url(sita),
            {"course": str(_course_in(abc).public_id), "intake": "2027-03"},
            format="json",
        )
        xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json")

        everyone = xyz.client.get(APPLICATIONS_URL).json()
        searched = xyz.client.get(APPLICATIONS_URL, {"q": "Deakin"}).json()
        choices = xyz.client.get(f"{APPLICATIONS_URL}filter-choices/").json()

        assert (everyone["count"], everyone["results"]) == (0, [])
        assert searched["results"] == []
        assert choices == {"intakes": [], "years": [], "countries": []}
        assert abc.client.get(APPLICATIONS_URL).json()["count"] == 1

    def test_a_token_from_one_office_is_refused_on_anothers_list(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        ram = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        xyz.client.post(
            _applications_url(ram),
            {"course": str(_course_in(xyz).public_id), "intake": "2027-03"},
            format="json",
        )
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        responses = [
            intruder.get(APPLICATIONS_URL),
            intruder.get(f"{APPLICATIONS_URL}filter-choices/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 2


def _student_with_coe(office: ConsultancyContext, name: str) -> tuple[str, str]:
    """Add a Student through the API with an Application at Offer, chosen; return both ids."""
    student_id = office.client.post(STUDENTS_URL, _lead_body(name), format="json").json()["id"]
    added = office.client.post(
        _applications_url(student_id),
        {"course": str(_course_in(office).public_id), "intake": "2027-03"},
        format="json",
    ).json()["applications"][0]
    with tenant_context(office.consultancy):
        application = Application.objects.get(public_id=added["id"])
        Application.objects.filter(pk=application.pk).update(status="offer")
        Student.objects.filter(public_id=student_id).update(chosen_offer=application)
    return student_id, added["id"]


def _visas_url(student_id: str) -> str:
    return f"{STUDENTS_URL}{student_id}/visas/"


def _student_applying(office: ConsultancyContext, name: str) -> tuple[str, str]:
    """Add a Student through the API with an Application waiting for an offer; both ids."""
    student_id = office.client.post(STUDENTS_URL, _lead_body(name), format="json").json()["id"]
    added = office.client.post(
        _applications_url(student_id),
        {"course": str(_course_in(office, f"{name} University").public_id), "intake": "2027-03"},
        format="json",
    ).json()["applications"][0]
    return student_id, added["id"]


def _offer_letter_form(application_id: str) -> dict[str, object]:
    return {
        "application": str(application_id),
        "kind": "conditional",
        "file": SimpleUploadedFile("offer.pdf", b"%PDF-1.7\n%%EOF\n"),
    }


class TestOfferLetterAndChosenOfferIsolation:
    """Issue 76: Offer Letters and the Chosen Offer never reach another Consultancy's data."""

    def test_another_office_cannot_add_a_letter_or_choose_an_offer(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita, waiting = _student_applying(abc, "Sita Karki")
        gita, offered = _student_with_coe(abc, "Gita Rai")
        with tenant_context(abc.consultancy):
            Student.objects.filter(public_id=gita).update(chosen_offer=None)

        responses = [
            xyz.client.post(
                f"{STUDENTS_URL}{sita}/offer-letters/",
                _offer_letter_form(waiting),
                format="multipart",
            ),
            xyz.client.put(
                f"{STUDENTS_URL}{gita}/chosen-offer/", {"application": offered}, format="json"
            ),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2
        with tenant_context(abc.consultancy):
            assert Application.objects.get(public_id=waiting).status == "applied"
            assert not Document.objects.exists()
            assert Student.objects.get(public_id=gita).chosen_offer_id is None

    def test_ids_of_another_office_are_refused_on_its_own_student(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        _student_applying(abc, "Hari Shah")  # so ABC's ids below don't exist in XYZ
        _, abc_waiting = _student_applying(abc, "Sita Karki")
        _, abc_offered = _student_with_coe(abc, "Gita Rai")
        ram, _ = _student_applying(xyz, "Ram Thapa")

        responses = [
            xyz.client.post(
                f"{STUDENTS_URL}{ram}/offer-letters/",
                _offer_letter_form(abc_waiting),
                format="multipart",
            ),
            xyz.client.put(
                f"{STUDENTS_URL}{ram}/chosen-offer/", {"application": abc_offered}, format="json"
            ),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_400_BAD_REQUEST] * 2
        with tenant_context(xyz.consultancy):
            assert not Document.objects.exists()
            assert Student.objects.get(public_id=ram).chosen_offer_id is None
        with tenant_context(abc.consultancy):
            assert Application.objects.get(public_id=abc_waiting).status == "applied"

    def test_a_token_from_one_office_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        ram, waiting = _student_applying(xyz, "Ram Thapa")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        responses = [
            intruder.post(
                f"{STUDENTS_URL}{ram}/offer-letters/",
                _offer_letter_form(waiting),
                format="multipart",
            ),
            intruder.put(
                f"{STUDENTS_URL}{ram}/chosen-offer/", {"application": waiting}, format="json"
            ),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 2
        with tenant_context(xyz.consultancy):
            assert Application.objects.get(public_id=waiting).status == "applied"


def _visa_letter_form() -> dict[str, object]:
    return {"result": "granted", "file": SimpleUploadedFile("visa.pdf", b"%PDF-1.7\n%%EOF\n")}


class TestVisaLetterIsolation:
    """Issue 77: a visa letter never reaches, or changes, another Consultancy's Visa."""

    def test_another_office_cannot_upload_a_letter(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita, application_id = _student_with_coe(abc, "Sita Karki")
        visa_id = abc.client.post(
            _visas_url(sita), {"application": application_id}, format="json"
        ).json()["visas"][0]["id"]

        response = xyz.client.post(
            f"{_visas_url(sita)}{visa_id}/letter/", _visa_letter_form(), format="multipart"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert Visa.objects.get(public_id=visa_id).status == "applied"
            assert not Document.objects.exists()

    def test_a_visa_id_of_another_office_is_not_found_on_its_own_student(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        _student_applying(abc, "Hari Shah")  # so ABC's ids below don't exist in XYZ
        sita, application_id = _student_with_coe(abc, "Sita Karki")
        abc_visa = abc.client.post(
            _visas_url(sita), {"application": application_id}, format="json"
        ).json()["visas"][0]["id"]
        ram, _ = _student_with_coe(xyz, "Ram Thapa")

        response = xyz.client.post(
            f"{_visas_url(ram)}{abc_visa}/letter/", _visa_letter_form(), format="multipart"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(xyz.consultancy):
            assert not Document.objects.exists()
        with tenant_context(abc.consultancy):
            assert Visa.objects.get(public_id=abc_visa).status == "applied"

    def test_a_token_from_one_office_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        ram, application_id = _student_with_coe(xyz, "Ram Thapa")
        visa_id = xyz.client.post(
            _visas_url(ram), {"application": application_id}, format="json"
        ).json()["visas"][0]["id"]
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        response = intruder.post(
            f"{_visas_url(ram)}{visa_id}/letter/", _visa_letter_form(), format="multipart"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            assert Visa.objects.get(public_id=visa_id).status == "applied"
            assert not Document.objects.exists()


class TestDecidedVisaIsolation:
    """UI revamp ticket 10: a Visa's Status is never reached from another Consultancy."""

    def test_another_office_gets_not_found_for_a_waiting_or_a_decided_visa(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita, application_id = _student_with_coe(abc, "Sita Karki")
        visa_id = abc.client.post(
            _visas_url(sita), {"application": application_id}, format="json"
        ).json()["visas"][0]["id"]
        url = f"{_visas_url(sita)}{visa_id}/"

        while_waiting = xyz.client.patch(url, {"status": "granted"}, format="json")
        abc.client.patch(url, {"status": "refused"}, format="json")
        once_decided = xyz.client.patch(url, {"status": "granted"}, format="json")

        assert while_waiting.status_code == status.HTTP_404_NOT_FOUND
        assert once_decided.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert Visa.objects.get(public_id=visa_id).status == "refused"

    def test_a_visa_id_of_another_office_is_not_found_on_its_own_student(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        _student_applying(abc, "Hari Shah")  # so ABC's ids below don't exist in XYZ
        sita, application_id = _student_with_coe(abc, "Sita Karki")
        abc_visa = abc.client.post(
            _visas_url(sita), {"application": application_id}, format="json"
        ).json()["visas"][0]["id"]
        abc.client.patch(f"{_visas_url(sita)}{abc_visa}/", {"status": "granted"}, format="json")
        ram, _ = _student_with_coe(xyz, "Ram Thapa")

        response = xyz.client.patch(
            f"{_visas_url(ram)}{abc_visa}/", {"status": "refused"}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert Visa.objects.get(public_id=abc_visa).status == "granted"

    def test_a_token_from_one_office_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        ram, application_id = _student_with_coe(xyz, "Ram Thapa")
        visa_id = xyz.client.post(
            _visas_url(ram), {"application": application_id}, format="json"
        ).json()["visas"][0]["id"]
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        response = intruder.patch(
            f"{_visas_url(ram)}{visa_id}/", {"status": "granted"}, format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            assert Visa.objects.get(public_id=visa_id).status == "applied"


class TestVisaListIsolation:
    """Issue 48: the Visas list and a Visa's interview stay inside their own Consultancy."""

    def test_each_office_lists_and_offers_filters_from_only_its_own_visas(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita, application_id = _student_with_coe(abc, "Sita Karki")
        abc.client.post(_visas_url(sita), {"application": application_id}, format="json")
        xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json")

        everyone = xyz.client.get(VISAS_URL).json()
        choices = xyz.client.get(f"{VISAS_URL}filter-choices/").json()

        assert (everyone["count"], everyone["results"]) == (0, [])
        assert choices == {"intakes": [], "countries": []}
        assert abc.client.get(VISAS_URL).json()["count"] == 1

    def test_a_search_never_finds_another_offices_visas(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        """UI revamp ticket 24: `q` on the Visas list and its export."""
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita, application_id = _student_with_coe(abc, "Sita Karki")
        abc.client.post(_visas_url(sita), {"application": application_id}, format="json")
        search = {"q": "sita"}

        found = xyz.client.get(VISAS_URL, search).json()
        exported = xyz.client.post(
            "/api/exports/", {"list": "visas", "filters": search}, format="json"
        )

        assert (found["count"], found["results"]) == (0, [])
        assert exported.status_code == status.HTTP_200_OK
        assert "Sita" not in exported.content.decode("utf-8")
        assert abc.client.get(VISAS_URL, search).json()["count"] == 1

    def test_another_office_cannot_set_visa_dates(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita, application_id = _student_with_coe(abc, "Sita Karki")
        visa_id = abc.client.post(
            _visas_url(sita), {"application": application_id}, format="json"
        ).json()["visas"][0]["id"]
        ram = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        when = (timezone.localdate() + timedelta(days=2)).isoformat() + "T10:00"

        responses = [
            xyz.client.patch(
                f"{_visas_url(sita)}{visa_id}/dates/", {"biometrics_at": when}, format="json"
            ),
            xyz.client.patch(
                f"{_visas_url(ram)}{visa_id}/dates/",
                {"medical_at": when, "interview_at": when},
                format="json",
            ),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2
        with tenant_context(abc.consultancy):
            stored = Visa.objects.get()
            assert (stored.biometrics_at, stored.medical_at, stored.interview_at) == (None,) * 3

    def test_a_token_from_one_office_is_refused_on_anothers_list(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        responses = [intruder.get(VISAS_URL), intruder.get(f"{VISAS_URL}filter-choices/")]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 2


class TestVisaIsolation:
    """Issue 15: Visas never reach, or use, another Consultancy's data."""

    def test_another_consultancy_cannot_start_or_change_a_visa(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json")
        only_abc, application_id = _student_with_coe(abc, "Gita Rai")
        visa_id = abc.client.post(
            _visas_url(only_abc), {"application": application_id}, format="json"
        ).json()["visas"][0]["id"]

        responses = [
            xyz.client.post(_visas_url(only_abc), {"application": application_id}, format="json"),
            xyz.client.patch(
                f"{_visas_url(only_abc)}{visa_id}/", {"status": "granted"}, format="json"
            ),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2
        with tenant_context(abc.consultancy):
            [stored] = Visa.objects.all()
            assert stored.status == "applied"
            assert Student.objects.get(public_id=only_abc).stage == "visa"

    def test_ids_of_another_consultancy_are_not_found_on_its_own_student(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_student, abc_application = _student_with_coe(abc, "Sita Karki")
        abc_visa = abc.client.post(
            _visas_url(abc_student), {"application": abc_application}, format="json"
        ).json()["visas"][0]["id"]
        xyz_student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()

        responses = [
            xyz.client.post(
                _visas_url(xyz_student["id"]), {"application": abc_application}, format="json"
            ),
            xyz.client.patch(
                f"{_visas_url(xyz_student['id'])}{abc_visa}/", {"status": "granted"}, format="json"
            ),
        ]

        assert [r.status_code for r in responses] == [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]
        with tenant_context(xyz.consultancy):
            assert not Visa.objects.exists()
        with tenant_context(abc.consultancy):
            assert Visa.objects.get().status == "applied"

    def test_one_consultancys_visas_never_show_on_anothers_profile(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_id, application_id = _student_with_coe(abc, "Sita Karki")
        abc.client.post(_visas_url(abc_id), {"application": application_id}, format="json")
        xyz_id = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]

        profile = xyz.client.get(f"{STUDENTS_URL}{xyz_id}/").json()

        assert abc_id != xyz_id
        assert profile["visas"] == []

    def test_a_token_from_one_consultancy_cannot_start_or_change_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        ram_id, application_id = _student_with_coe(xyz, "Ram Thapa")
        visa_id = xyz.client.post(
            _visas_url(ram_id), {"application": application_id}, format="json"
        ).json()["visas"][0]["id"]
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        responses = [
            intruder.post(_visas_url(ram_id), {"application": application_id}, format="json"),
            intruder.patch(f"{_visas_url(ram_id)}{visa_id}/", {"status": "granted"}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 2
        with tenant_context(xyz.consultancy):
            assert Visa.objects.get().status == "applied"
            assert Student.objects.get(public_id=ram_id).stage == "visa"


_PDF = b"%PDF-1.7\n%%EOF\n"
_TYPES_URL = "/api/configuration/document-types/"
_PAPERWORK_URL = "/api/documents/students/"


def _documents_url(student_id: str) -> str:
    return f"{STUDENTS_URL}{student_id}/documents/"


def _passport_id(office: ConsultancyContext) -> str:
    with tenant_context(office.consultancy):
        return str(DocumentType.objects.get(name="Passport").public_id)


def _student_with_passport(office: ConsultancyContext, name: str) -> tuple[str, str]:
    """Add a counselled Student through the API with an uploaded Passport; return both ids."""
    student_id, document_id, _ = _student_with_passport_file(office, name)
    return student_id, document_id


def _file_link_url(student_id: str, document_id: str, file_id: str) -> str:
    return f"{_documents_url(student_id)}{document_id}/files/{file_id}/link/"


def _student_with_passport_file(office: ConsultancyContext, name: str) -> tuple[str, str, str]:
    """Like `_student_with_passport`, also returning the Passport's one file's id."""
    student_id = office.client.post(STUDENTS_URL, _lead_body(name), format="json").json()["id"]
    office.client.post(
        f"{STUDENTS_URL}{student_id}/stage/", {"stage": "counselling"}, format="json"
    )
    body = {
        "document_type": str(_passport_id(office)),
        "file": SimpleUploadedFile("passport.pdf", _PDF, content_type="application/pdf"),
    }
    answer = office.client.post(_documents_url(student_id), body, format="multipart").json()
    document = answer["items"][0]["document"]
    return student_id, document["id"], document["files"][0]["id"]


class TestDocumentIsolation:
    """Issue 16: Documents, their files and Document Types never cross Consultancies."""

    def test_another_consultancy_gets_no_link_to_a_document(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_student, abc_document, abc_file = _student_with_passport_file(abc, "Sita Karki")
        xyz_student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()

        responses = [
            xyz.client.get(_file_link_url(abc_student, abc_document, abc_file)),
            xyz.client.get(_file_link_url(xyz_student["id"], abc_document, abc_file)),
        ]

        assert abc_student != xyz_student["id"]
        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2
        assert all("url" not in r.json() for r in responses)

    def test_the_same_ids_on_another_consultancy_reach_only_its_own_files(
        self, make_consultancy: ConsultancyFactory, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_student, abc_document, abc_file = _student_with_passport_file(abc, "Sita Karki")
        xyz_student, xyz_document, _ = _student_with_passport_file(xyz, "Ram Thapa")
        stored_before = set(fake_document_storage.files)

        page = {"file": SimpleUploadedFile("x.pdf", _PDF, content_type="application/pdf")}
        into_abc = xyz.client.post(
            f"{_documents_url(abc_student)}{abc_document}/files/", page, format="multipart"
        )
        page = {"file": SimpleUploadedFile("x.pdf", _PDF, content_type="application/pdf")}
        abc_document_on_xyz = xyz.client.post(
            f"{_documents_url(xyz_student)}{abc_document}/files/", page, format="multipart"
        )
        page = {"file": SimpleUploadedFile("x.pdf", _PDF, content_type="application/pdf")}
        own = xyz.client.post(
            f"{_documents_url(xyz_student)}{xyz_document}/files/", page, format="multipart"
        )
        # XYZ's own Document with ABC's file UUID: never ABC's file.
        link = xyz.client.get(_file_link_url(xyz_student, xyz_document, abc_file))

        assert (abc_student, abc_document) != (xyz_student, xyz_document)
        assert into_abc.status_code == status.HTTP_404_NOT_FOUND
        assert abc_document_on_xyz.status_code == status.HTTP_404_NOT_FOUND
        assert own.status_code == status.HTTP_201_CREATED
        with tenant_context(abc.consultancy):
            assert DocumentFile.objects.count() == 1
        with tenant_context(xyz.consultancy):
            assert DocumentFile.objects.count() == 2
            xyz_keys = set(DocumentFile.objects.values_list("storage_key", flat=True))
        assert all(key.startswith("xyzoverseas/") for key in xyz_keys)
        assert set(fake_document_storage.files) - stored_before <= xyz_keys
        assert link.status_code == status.HTTP_404_NOT_FOUND

    def test_a_token_from_one_consultancy_gets_no_link_or_upload_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        ram, document, file = _student_with_passport_file(xyz, "Ram Thapa")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)
        body = {
            "document_type": str(_passport_id(xyz)),
            "file": SimpleUploadedFile("x.pdf", _PDF, content_type="application/pdf"),
        }
        page = {"file": SimpleUploadedFile("p.pdf", _PDF, content_type="application/pdf")}

        responses = [
            intruder.get(_file_link_url(ram, document, file)),
            intruder.get(_documents_url(ram)),
            intruder.post(_documents_url(ram), body, format="multipart"),
            intruder.post(f"{_documents_url(ram)}{document}/files/", page, format="multipart"),
            intruder.get(_TYPES_URL),
            intruder.get(_PAPERWORK_URL),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 6
        with tenant_context(xyz.consultancy):
            assert Document.objects.count() == 1
            assert DocumentFile.objects.count() == 1

    def test_checklists_and_uploads_stay_in_their_own_consultancy(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_student, _ = _student_with_passport(abc, "Sita Karki")
        xyz_student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()

        checklist = xyz.client.get(_documents_url(xyz_student["id"])).json()
        missing = xyz.client.get(_documents_url(str(uuid.uuid4())))

        assert abc_student != xyz_student["id"]
        assert checklist["items"][0]["state"] == "not_received"
        assert checklist["done"] == 0
        assert missing.status_code == status.HTTP_404_NOT_FOUND

    def test_files_are_kept_under_each_consultancys_own_prefix(
        self,
        make_consultancy: ConsultancyFactory,
        fake_document_storage: type[FakeDocumentStorage],
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)

        _student_with_passport(abc, "Sita Karki")
        _student_with_passport(xyz, "Ram Thapa")

        prefixes = sorted(key.split("/", 1)[0] for key in fake_document_storage.files)
        assert prefixes == ["abcedu", "xyzoverseas"]

    def test_a_document_type_of_one_consultancy_cannot_be_used_or_changed_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        police = abc.client.post(
            _TYPES_URL, {"name": "Police report", "needed_by_everyone": True}, format="json"
        ).json()
        xyz_student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()

        listed = xyz.client.get(_TYPES_URL).json()["results"]
        moved = xyz.client.patch(
            f"{_TYPES_URL}{police['id']}/", {"needed_by_everyone": False}, format="json"
        )
        used = xyz.client.post(
            _documents_url(xyz_student["id"]),
            {
                "document_type": str(police["id"]),
                "file": SimpleUploadedFile("p.pdf", _PDF, content_type="application/pdf"),
            },
            format="multipart",
        )

        assert "Police report" not in [t["name"] for t in listed]
        assert moved.status_code == status.HTTP_404_NOT_FOUND
        assert used.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(abc.consultancy):
            assert DocumentType.objects.get(name="Police report").needed_by_everyone is True

    def test_the_documents_page_lists_only_its_own_students(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        _student_with_passport(abc, "Sita Karki")

        page = xyz.client.get(_PAPERWORK_URL).json()

        assert page["count"] == 0
        assert page["results"] == []

    def test_the_documents_page_names_only_its_own_offices_documents(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        """UI revamp 12: each row's `items` come from this office's checklist and uploads only."""
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc.client.post(
            _TYPES_URL, {"name": "ABC only paper", "needed_by_everyone": True}, format="json"
        )
        _student_with_passport(abc, "Sita Karki")
        xyz_student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()
        xyz.client.post(
            f"{STUDENTS_URL}{xyz_student['id']}/stage/", {"stage": "counselling"}, format="json"
        )

        abc_items = abc.client.get(_PAPERWORK_URL).json()["results"][0]["items"]
        xyz_items = xyz.client.get(_PAPERWORK_URL).json()["results"][0]["items"]

        assert {"name": "ABC only paper", "state": "missing"} in abc_items
        assert {"name": "Passport", "state": "waiting"} in abc_items
        assert "ABC only paper" not in [item["name"] for item in xyz_items]
        assert {item["state"] for item in xyz_items} == {"missing"}

    def test_another_consultancy_cannot_check_a_document(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        """Issue 17: B's Admin with A's Student and Document UUIDs reaches nothing of A's."""
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_student, abc_document = _student_with_passport(abc, "Sita Karki")
        xyz_student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()
        verification = f"{_documents_url(abc_student)}{abc_document}/verification/"

        response = xyz.client.post(
            verification, {"verification": "rejected", "reason": "x"}, format="json"
        )

        assert abc_student != xyz_student["id"]
        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert Document.objects.get(public_id=abc_document).verification == "pending"

    def test_a_token_from_one_consultancy_cannot_check_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        ram, document = _student_with_passport(xyz, "Ram Thapa")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        response = intruder.post(
            f"{_documents_url(ram)}{document}/verification/",
            {"verification": "verified"},
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            assert Document.objects.get().verification == "pending"

    def test_a_document_cannot_be_linked_to_another_consultancys_application_or_visa(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_student, abc_application = _student_with_coe(abc, "Sita Karki")
        abc_visa = abc.client.post(
            _visas_url(abc_student), {"application": abc_application}, format="json"
        ).json()["visas"][0]["id"]
        xyz_student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()

        def linked_upload(field: str, value: str) -> int:
            body = {
                "document_type": str(_passport_id(xyz)),
                "file": SimpleUploadedFile("p.pdf", _PDF, content_type="application/pdf"),
                field: str(value),
            }
            response = xyz.client.post(_documents_url(xyz_student["id"]), body, format="multipart")
            return response.status_code

        codes = [linked_upload("application", abc_application), linked_upload("visa", abc_visa)]

        assert codes == [status.HTTP_400_BAD_REQUEST] * 2
        with tenant_context(xyz.consultancy):
            assert Document.objects.count() == 0


_PICKER_URL = "/api/documents/types/"


def _optional_type(office: ConsultancyContext, name: str) -> str:
    """Add a type asked for only when needed through the picker; return its id."""
    return str(office.client.post(_PICKER_URL, {"name": name}, format="json").json()["id"])


def _requirement_count(office: ConsultancyContext) -> int:
    with tenant_context(office.consultancy):
        return DocumentRequirement.objects.count()


def _bin_a_passport(office: ConsultancyContext, name: str) -> tuple[str, str, str]:
    """A Student whose two-page Passport a Counsellor put in the Bin; the client is an Admin."""
    admin = office.log_in_as(Role.ADMIN)
    student_id = office.client.post(STUDENTS_URL, _lead_body(name), format="json").json()["id"]
    office.client.post(
        f"{STUDENTS_URL}{student_id}/stage/", {"stage": "counselling"}, format="json"
    )
    counsellor = office.add_staff(Role.COUNSELLOR)
    with tenant_context(office.consultancy):
        Student.objects.filter(public_id=student_id).update(assigned_counsellor=counsellor)
    use_access_token(office.client, office.tokens_for(counsellor).access)
    pages = [SimpleUploadedFile(f"p{n}.pdf", _PDF, content_type="application/pdf") for n in (1, 2)]
    body = {"document_type": str(_passport_id(office)), "file": pages}
    answer = office.client.post(_documents_url(student_id), body, format="multipart").json()
    document = answer["items"][0]["document"]
    office.client.post(
        f"{_documents_url(student_id)}{document['id']}/bin/", {"reason": "Blurry"}, format="json"
    )
    use_access_token(office.client, office.tokens_for(admin).access)
    return student_id, document["id"], document["files"][0]["id"]


class TestDocumentBinIsolation:
    """Issue 79: the Bin, its actions and its file links never cross Consultancies."""

    def test_another_consultancy_cannot_bin_restore_take_out_or_delete(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_student, abc_document, abc_file = _bin_a_passport(abc, "Sita Karki")
        xyz_admin = xyz.log_in_as(Role.ADMIN)
        xyz_counsellor = xyz.add_staff(Role.COUNSELLOR)
        url = f"{_documents_url(abc_student)}{abc_document}/"

        responses = [
            xyz.client.post(f"{url}restore/", {}, format="json"),
            xyz.client.post(f"{url}take-out/", {}, format="json"),
            xyz.client.delete(url),
            xyz.client.delete(f"{url}files/{abc_file}/"),
            xyz.client.get(f"/api/documents/bin/files/{abc_file}/link/"),
        ]
        use_access_token(xyz.client, xyz.tokens_for(xyz_counsellor).access)
        binned = xyz.client.post(f"{url}bin/", {"reason": "x"}, format="json")

        assert xyz_admin.role == Role.ADMIN
        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 5
        assert binned.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert Document.objects.get(public_id=abc_document).binned_at is not None
            assert DocumentFile.objects.count() == 2

    def test_the_bin_tab_lists_only_its_own_items(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        _bin_a_passport(abc, "Sita Karki")
        xyz.log_in_as(Role.ADMIN)

        page = xyz.client.get("/api/documents/bin/").json()
        home = xyz.client.get("/api/home/waiting/").json()

        assert (page["count"], page["results"]) == (0, [])
        assert home["documents_in_bin"] == 0

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        ram, document, file = _bin_a_passport(xyz, "Ram Thapa")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)
        url = f"{_documents_url(ram)}{document}/"

        responses = [
            intruder.get("/api/documents/bin/"),
            intruder.get(f"/api/documents/bin/files/{file}/link/"),
            intruder.post(f"{url}restore/", {}, format="json"),
            intruder.delete(url),
            intruder.post(f"{url}files/{file}/bin/", {"reason": "x"}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 5
        with tenant_context(xyz.consultancy):
            assert Document.objects.get(public_id=document).binned_at is not None


class TestDocumentRequirementIsolation:
    """Issues 40, 80: what a Country, Course or Student asks for never crosses Consultancies."""

    def test_another_consultancy_cannot_set_or_drop_anything_with_the_same_ids(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        course = _course_in(abc)
        abc_student = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()
        cv = _optional_type(abc, "CV")
        catalogue = "/api/catalogue/"
        abc.client.put(
            f"{catalogue}courses/{course.public_id}/documents/",
            {"document_types": [cv]},
            format="json",
        )
        abc.client.put(
            f"{_documents_url(abc_student['id'])}asked/", {"document_types": [cv]}, format="json"
        )

        responses = [
            xyz.client.put(
                f"{catalogue}courses/{course.public_id}/documents/",
                {"document_types": []},
                format="json",
            ),
            xyz.client.put(
                f"{_documents_url(abc_student['id'])}asked/", {"document_types": []}, format="json"
            ),
            xyz.client.delete(f"{_documents_url(abc_student['id'])}asked/{cv}/"),
            xyz.client.delete(f"{_TYPES_URL}{cv}/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 4
        assert _requirement_count(abc) == 2
        with tenant_context(abc.consultancy):
            assert DocumentType.objects.filter(public_id=cv).exists()

    def test_a_type_of_one_consultancy_cannot_be_asked_for_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_only = [_optional_type(abc, f"ABC type {n}") for n in range(12)][-1]
        xyz_course = _course_in(xyz)
        xyz_student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()

        responses = [
            xyz.client.put(
                f"/api/catalogue/courses/{xyz_course.public_id}/documents/",
                {"document_types": [abc_only]},
                format="json",
            ),
            xyz.client.put(
                f"{_documents_url(xyz_student['id'])}asked/",
                {"document_types": [abc_only]},
                format="json",
            ),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_400_BAD_REQUEST] * 2
        assert _requirement_count(xyz) == 0

    def test_the_picker_lists_and_adds_only_its_own_types(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.COUNSELLOR)
        xyz.log_in_as(Role.COUNSELLOR)
        _optional_type(abc, "Police report")

        listed = xyz.client.get(_PICKER_URL).json()["results"]
        added = xyz.client.post(_PICKER_URL, {"name": "Police report"}, format="json")

        assert "Police report" not in [t["name"] for t in listed]
        assert added.status_code == status.HTTP_201_CREATED
        with tenant_context(abc.consultancy):
            assert DocumentType.objects.filter(name="Police report").count() == 1

    def test_a_checklist_never_asks_for_another_consultancys_requirements(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_course = _course_in(abc)
        cv = _optional_type(abc, "CV")
        abc.client.put(
            f"/api/catalogue/courses/{abc_course.public_id}/documents/",
            {"document_types": [cv]},
            format="json",
        )
        xyz_student, _ = _student_with_coe(xyz, "Ram Thapa")

        checklist = xyz.client.get(_documents_url(xyz_student)).json()

        assert "CV" not in [item["document_type"]["name"] for item in checklist["items"]]
        assert checklist["total"] == 8

    def test_a_token_from_one_consultancy_reaches_none_of_them_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        course = _course_in(xyz)
        ram = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        cv = _optional_type(xyz, "CV")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)
        body = {"document_types": [cv]}

        responses = [
            intruder.get(_PICKER_URL),
            intruder.post(_PICKER_URL, {"name": "Intruder"}, format="json"),
            intruder.get(_LISTS_URL),
            intruder.put(
                f"{_LISTS_URL}australia/",
                {"documents": [{"document_type": cv, "group": "visa"}]},
                format="json",
            ),
            intruder.put(
                f"/api/catalogue/courses/{course.public_id}/documents/", body, format="json"
            ),
            intruder.put(f"{_documents_url(ram)}asked/", body, format="json"),
            intruder.delete(f"{_documents_url(ram)}asked/{cv}/"),
            intruder.delete(f"{_TYPES_URL}{cv}/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 8
        assert _requirement_count(xyz) == 0
        with tenant_context(xyz.consultancy):
            assert not DocumentType.objects.filter(name="Intruder").exists()
            assert DocumentType.objects.filter(public_id=cv).exists()


_LISTS_URL = "/api/configuration/document-lists/"


def _country_list(office: ConsultancyContext, country: str, rows: list[tuple[str, str]]) -> int:
    """PUT a Country's list as the logged-in Admin; return the status code."""
    body = {"documents": [{"document_type": pk, "group": group} for pk, group in rows]}
    response = office.client.put(f"{_LISTS_URL}{country}/", body, format="json")
    return int(response.status_code)


class TestCountryListIsolation:
    """Issue 80: a Consultancy's Country document lists never cross into another's."""

    def test_another_consultancy_sees_and_changes_only_its_own_lists(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_gs = _optional_type(abc, "Genuine Student statement")
        _country_list(abc, "australia", [(abc_gs, "statements")])

        seen = xyz.client.get(_LISTS_URL).json()["results"]
        cleared = _country_list(xyz, "australia", [])
        foreign = _country_list(xyz, "australia", [(abc_gs, "visa")])

        assert all(one["documents"] == [] for one in seen)
        assert (cleared, foreign) == (status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST)
        assert _requirement_count(abc) == 1
        assert _requirement_count(xyz) == 0

    def test_a_new_document_from_a_list_stays_in_its_own_consultancy(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        _optional_type(abc, "Police clearance")
        url = f"{_LISTS_URL}uk/new-document/"

        made = xyz.client.post(url, {"name": "Police clearance", "group": "visa"}, format="json")
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)
        replayed = intruder.post(url, {"name": "Sponsor letter", "group": "visa"}, format="json")

        assert made.status_code == status.HTTP_201_CREATED
        assert replayed.status_code == status.HTTP_401_UNAUTHORIZED
        assert _requirement_count(abc) == 0
        assert _requirement_count(xyz) == 1
        with tenant_context(xyz.consultancy):
            assert not DocumentType.objects.filter(name="Sponsor letter").exists()
        with tenant_context(abc.consultancy):
            assert DocumentType.objects.filter(name="Police clearance").count() == 1

    def test_a_checklist_never_uses_another_consultancys_country_list(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        _country_list(abc, "australia", [(_optional_type(abc, "Police report"), "visa")])
        body = _lead_body("Ram Thapa", country="australia")
        ram = xyz.client.post(STUDENTS_URL, body, format="json").json()["id"]
        xyz.client.post(f"{STUDENTS_URL}{ram}/stage/", {"stage": "counselling"}, format="json")

        checklist = xyz.client.get(_documents_url(ram)).json()

        assert "Police report" not in [item["document_type"]["name"] for item in checklist["items"]]
        assert checklist["total"] == 8


def _fee_type_id(office: ConsultancyContext, name: str = "Visa fee") -> str:
    """The id of the office's Fee Type with this name."""
    with tenant_context(office.consultancy):
        return str(FeeType.objects.get(name=name).public_id)


def _payment_body(office: ConsultancyContext) -> dict[str, object]:
    """One Payment's new details, as an Admin's edit sends them."""
    return {
        "amount": 25000,
        "paid_on": timezone.localdate().isoformat(),
        "method": "cash",
        "fee_type": _fee_type_id(office),
    }


def _visit_body(office: ConsultancyContext, *fee_types: str) -> dict[str, object]:
    """One visit's Payments, NPR 25,000 a line, for these Fee Types (the Visa fee if none)."""
    lines = [{"amount": 25000, "fee_type": one} for one in fee_types or (_fee_type_id(office),)]
    return {"paid_on": timezone.localdate().isoformat(), "method": "cash", "lines": lines}


def _student_with_payment(office: ConsultancyContext, name: str) -> tuple[str, str]:
    """A new Student of the office's logged-in Admin, with one Payment: (student id, payment id)."""
    student = office.client.post(STUDENTS_URL, _lead_body(name), format="json").json()
    payment = office.client.post(
        f"/api/students/{student['id']}/payments/", _visit_body(office), format="json"
    ).json()
    return student["id"], payment["id"]


class TestPaymentIsolation:
    """Issue 18: Payments and their receipts never cross Consultancies."""

    def test_another_consultancy_cannot_see_a_receipt_with_the_same_id(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        _, abc_payment = _student_with_payment(abc, "Sita Karki")

        response = xyz.client.get(f"/api/payments/{abc_payment}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Sita" not in response.content.decode()

    def test_a_payment_cannot_be_recorded_for_another_consultancys_student(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.FRONT_DESK)
        abc_student = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()

        response = xyz.client.post(
            f"/api/students/{abc_student['id']}/payments/", _visit_body(xyz), format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert Payment.objects.count() == 0
        with tenant_context(xyz.consultancy):
            assert Payment.objects.count() == 0

    def test_the_same_ids_reach_only_this_consultancys_own_records(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        abc_student, abc_payment = _student_with_payment(abc, "Sita Karki")
        xyz_student, xyz_payment = _student_with_payment(xyz, "Ram Thapa")

        receipt = xyz.client.get(f"/api/payments/{xyz_payment}/").json()
        history = xyz.client.get(f"/api/students/{xyz_student}/history/").json()["results"]
        abc_receipt = xyz.client.get(f"/api/payments/{abc_payment}/")

        assert (abc_student, abc_payment) != (xyz_student, xyz_payment)
        assert receipt["student"]["name"] == "Ram Thapa"
        assert abc_receipt.status_code == status.HTTP_404_NOT_FOUND
        assert [entry["kind"] for entry in history] == ["payment_recorded"]

    def test_a_token_from_one_consultancy_cannot_record_or_read_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        ram, payment = _student_with_payment(xyz, "Ram Thapa")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        responses = [
            intruder.get(f"/api/payments/{payment}/"),
            intruder.post(f"/api/students/{ram}/payments/", _visit_body(xyz), format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 2
        with tenant_context(xyz.consultancy):
            assert Payment.objects.count() == 1


class TestReceiptIsolation:
    """Ticket 07: one receipt's lines never cross Consultancies, even with the same numbers."""

    def test_a_receipt_shows_only_this_offices_lines(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            office.log_in_as(Role.ADMIN)
        sita = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()
        ram = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()
        three = (
            _fee_type_id(abc),
            _fee_type_id(abc, "IELTS class"),
            _fee_type_id(abc, "PTE class"),
        )
        abc_made = abc.client.post(
            f"/api/students/{sita['id']}/payments/", _visit_body(abc, *three), format="json"
        ).json()
        xyz_made = xyz.client.post(
            f"/api/students/{ram['id']}/payments/", _visit_body(xyz), format="json"
        ).json()

        receipt = xyz.client.get(f"/api/payments/{xyz_made['id']}/").json()
        other_lines = [
            xyz.client.get(f"/api/payments/{line['id']}/") for line in abc_made["lines"][1:]
        ]

        assert abc_made["receipt_number"] == xyz_made["receipt_number"]
        assert (len(receipt["lines"]), receipt["total"]) == (1, 25000)
        assert receipt["student"]["name"] == "Ram Thapa"
        assert [r.status_code for r in other_lines] == [status.HTTP_404_NOT_FOUND] * 2
        assert "Sita" not in "".join(r.content.decode() for r in other_lines)

    def test_another_offices_fee_type_on_any_line_saves_nothing(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.FRONT_DESK)
        abc_only = abc.client.post("/api/fee-types/", {"name": "ABC only"}, format="json")
        assert abc_only.status_code == status.HTTP_201_CREATED
        ram = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()

        response = xyz.client.post(
            f"/api/students/{ram['id']}/payments/",
            _visit_body(xyz, abc_only.json()["id"], _fee_type_id(xyz)),
            format="json",
        )

        assert response.json() == {"lines": [{"fee_type": ["Choose a Fee Type."]}, {}]}
        with tenant_context(xyz.consultancy):
            assert Payment.objects.count() == 0


class TestAdminPaymentsIsolation:
    """Issue 19: the Payments list, edits and deletes never cross Consultancies."""

    def _two_offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext]:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            switch_modules(office.consultancy, {Module.ACCOUNTS})
            office.log_in_as(Role.ADMIN)
        return abc, xyz

    def test_the_list_shows_only_this_consultancys_payments(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _student_with_payment(abc, "Sita Karki")
        _student_with_payment(xyz, "Ram Thapa")

        body = xyz.client.get("/api/payments/").json()

        assert [row["student"]["name"] for row in body["results"]] == ["Ram Thapa"]
        assert body["total"] == 25000
        assert "Sita" not in xyz.client.get("/api/payments/", {"q": "Sita"}).content.decode()

    def test_another_consultancy_cannot_edit_or_delete_a_payment(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _, abc_payment = _student_with_payment(abc, "Sita Karki")
        body = {**_payment_body(xyz), "amount": 1}

        edited = xyz.client.put(f"/api/payments/{uuid.uuid4()}/", body, format="json")
        edited_same_id = xyz.client.put(f"/api/payments/{abc_payment}/", body, format="json")
        deleted = xyz.client.delete(f"/api/payments/{abc_payment}/")

        assert [r.status_code for r in (edited, edited_same_id, deleted)] == [
            status.HTTP_404_NOT_FOUND
        ] * 3
        with tenant_context(abc.consultancy):
            assert Payment.objects.get(public_id=abc_payment).amount == 25000

    def test_the_same_id_changes_only_this_consultancys_payment(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _, abc_payment = _student_with_payment(abc, "Sita Karki")
        _, xyz_payment = _student_with_payment(xyz, "Ram Thapa")

        xyz.client.put(
            f"/api/payments/{xyz_payment}/", {**_payment_body(xyz), "amount": 1}, format="json"
        )
        xyz.client.delete(f"/api/payments/{xyz_payment}/")

        assert abc_payment != xyz_payment
        with tenant_context(abc.consultancy):
            assert Payment.objects.get(public_id=abc_payment).amount == 25000
        with tenant_context(xyz.consultancy):
            assert Payment.objects.count() == 0

    def test_a_token_from_one_consultancy_cannot_list_edit_or_delete_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _, payment = _student_with_payment(xyz, "Ram Thapa")
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abcedu.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        responses = [
            intruder.get("/api/payments/"),
            intruder.put(f"/api/payments/{payment}/", _payment_body(xyz), format="json"),
            intruder.delete(f"/api/payments/{payment}/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 3
        with tenant_context(xyz.consultancy):
            assert Payment.objects.count() == 1

    def test_switching_accounts_off_in_one_leaves_the_other_alone(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        switch_modules(abc.consultancy, set())

        assert abc.client.get("/api/payments/").status_code == status.HTTP_404_NOT_FOUND
        assert xyz.client.get("/api/payments/").status_code == status.HTTP_200_OK


DATES_URL = "/api/configuration/dates/"


class TestDateDisplayIsolation:
    def test_choosing_bs_in_one_consultancy_leaves_another_in_ad(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)

        abc.client.put(DATES_URL, {"date_display": "BS"}, format="json")

        assert abc.client.get(CONTEXT_URL).json()["date_display"] == "BS"
        assert xyz.client.get(CONTEXT_URL).json()["date_display"] == "AD"

    def test_an_admins_token_cannot_change_another_consultancys_dates(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz_admin = xyz.add_staff(Role.ADMIN)  # Same primary key as ABC's Admin.
        use_access_token(xyz.client, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        response = xyz.client.put(DATES_URL, {"date_display": "BS"}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        use_access_token(xyz.client, xyz.tokens_for(xyz_admin).access)
        assert xyz.client.get(CONTEXT_URL).json()["date_display"] == "AD"


TASKS_URL = "/api/tasks/"


def _task_body(assignee_id: str, student_id: str | None = None) -> dict[str, object]:
    return {
        "title": "Call about the IELTS score",
        "due_on": timezone.localdate().isoformat(),
        "assignee": assignee_id,
        "student": student_id,
    }


class TestTaskIsolation:
    """Issue 21: Tasks, their assignees and a Student's Tasks never cross Consultancies."""

    def _two_offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, Staff, ConsultancyContext, Staff]:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        return abc, abc.log_in_as(Role.ADMIN), xyz, xyz.log_in_as(Role.ADMIN)

    def test_my_tasks_and_assignees_show_only_this_consultancys(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_admin, xyz, xyz_admin = self._two_offices(make_consultancy)
        abc.client.post(
            TASKS_URL,
            {**_task_body(str(abc_admin.public_id)), "title": "ABC secret"},
            format="json",
        )
        xyz.client.post(TASKS_URL, _task_body(str(xyz_admin.public_id)), format="json")

        mine = xyz.client.get(TASKS_URL)
        assignees = xyz.client.get(f"{TASKS_URL}assignees/").json()

        assert mine.json()["count"] == 1
        assert "ABC secret" not in mine.content.decode()
        assert [person["id"] for person in assignees] == [str(xyz_admin.public_id)]

    def test_another_consultancys_student_or_staff_cannot_be_linked(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _, xyz, xyz_admin = self._two_offices(make_consultancy)
        abc_student = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()
        abc_counsellor = abc.add_staff(Role.COUNSELLOR)

        linked = xyz.client.post(
            TASKS_URL, _task_body(str(xyz_admin.public_id), abc_student["id"]), format="json"
        )
        given = xyz.client.post(TASKS_URL, _task_body(str(abc_counsellor.public_id)), format="json")

        assert [linked.status_code, given.status_code] == [status.HTTP_400_BAD_REQUEST] * 2
        for office in (abc, xyz):
            with tenant_context(office.consultancy):
                assert Task.objects.count() == 0

    def test_another_consultancy_cannot_tick_a_task_or_read_a_students_tasks(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_admin, xyz, _ = self._two_offices(make_consultancy)
        student = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()
        task = abc.client.post(
            TASKS_URL, _task_body(str(abc_admin.public_id), student["id"]), format="json"
        ).json()

        ticked = xyz.client.patch(f"{TASKS_URL}{task['id']}/", {"done": True}, format="json")
        listed = xyz.client.get(f"/api/students/{student['id']}/tasks/")

        assert [ticked.status_code, listed.status_code] == [status.HTTP_404_NOT_FOUND] * 2
        with tenant_context(abc.consultancy):
            assert Task.objects.get(public_id=task["id"]).is_done is False

    def test_the_same_ids_reach_only_this_consultancys_own_tasks(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_admin, xyz, xyz_admin = self._two_offices(make_consultancy)
        abc_student = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()
        xyz_student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()
        abc_task = abc.client.post(
            TASKS_URL, _task_body(str(abc_admin.public_id), abc_student["id"]), format="json"
        ).json()
        xyz_task = xyz.client.post(
            TASKS_URL, _task_body(str(xyz_admin.public_id), xyz_student["id"]), format="json"
        ).json()

        xyz.client.patch(f"{TASKS_URL}{xyz_task['id']}/", {"done": True}, format="json")
        theirs = xyz.client.patch(f"{TASKS_URL}{abc_task['id']}/", {"done": True}, format="json")
        profile = xyz.client.get(f"/api/students/{xyz_student['id']}/tasks/").json()

        assert abc_task["id"] != xyz_task["id"]  # Each Task has its own UUID (ADR 0027).
        assert theirs.status_code == status.HTTP_404_NOT_FOUND
        assert abc_student["id"] != xyz_student["id"]
        assert xyz_task["student"]["name"] == "Ram Thapa"
        assert profile["count"] == 0
        with tenant_context(abc.consultancy):
            assert Task.objects.get(public_id=abc_task["id"]).is_done is False

    def test_a_token_from_one_consultancy_is_refused_on_every_task_endpoint(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _, xyz, xyz_admin = self._two_offices(make_consultancy)
        student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()
        task = xyz.client.post(
            TASKS_URL, _task_body(str(xyz_admin.public_id)), format="json"
        ).json()
        intruder = api_client(xyz.host)
        second = abc.add_staff(Role.ADMIN, email="second-admin@abcedu.test")
        use_access_token(intruder, abc.tokens_for(second).access)

        responses = [
            intruder.get(TASKS_URL),
            intruder.post(TASKS_URL, _task_body(str(xyz_admin.public_id)), format="json"),
            intruder.patch(f"{TASKS_URL}{task['id']}/", {"done": True}, format="json"),
            intruder.get(f"{TASKS_URL}assignees/"),
            intruder.get(f"/api/students/{student['id']}/tasks/"),
            intruder.get(f"{TASKS_URL}count/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 6
        with tenant_context(xyz.consultancy):
            assert list(Task.objects.values_list("is_done", flat=True)) == [False]

    def test_the_tasks_count_counts_only_this_consultancys(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        """Issue 50: the menu count, and "Only me" Tasks, stay in their own office."""
        abc, abc_admin, xyz, xyz_admin = self._two_offices(make_consultancy)
        for _ in range(3):
            abc.client.post(TASKS_URL, _task_body(str(abc_admin.public_id)), format="json")
        abc_private = abc.client.post(
            TASKS_URL, {**_task_body(str(abc_admin.public_id)), "only_me": True}, format="json"
        ).json()
        xyz.client.post(TASKS_URL, _task_body(str(xyz_admin.public_id)), format="json")

        counts = [
            abc.client.get(f"{TASKS_URL}count/").json(),
            xyz.client.get(f"{TASKS_URL}count/").json(),
        ]
        # ABC's private Task is unknown in XYZ and never touched.
        xyz.client.patch(f"{TASKS_URL}{abc_private['id']}/", {"done": True}, format="json")

        assert counts == [{"count": 4}, {"count": 1}]
        with tenant_context(abc.consultancy):
            assert Task.objects.get(public_id=abc_private["id"]).is_done is False


NOTIFICATIONS_URL = "/api/notifications/"


class TestNotificationIsolation:
    """Issue 22: notifications never cross Consultancies."""

    def _given_a_task(self, office: ConsultancyContext, title: str) -> tuple[Staff, Staff]:
        """An Admin gives a new Counsellor a Task due today; return (admin, counsellor)."""
        admin = office.log_in_as(Role.ADMIN)
        counsellor = office.add_staff(Role.COUNSELLOR)
        office.client.post(
            TASKS_URL, {**_task_body(str(counsellor.public_id)), "title": title}, format="json"
        )
        use_access_token(office.client, office.tokens_for(counsellor).access)
        return admin, counsellor

    def test_the_bell_shows_only_this_consultancys(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        self._given_a_task(abc, "ABC secret")
        self._given_a_task(xyz, "XYZ task")

        body = xyz.client.get(NOTIFICATIONS_URL)

        assert body.json()["count"] == 2  # Given, and due today.
        assert "ABC secret" not in body.content.decode()

    def test_the_same_ids_reach_only_this_consultancys_own(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        self._given_a_task(abc, "ABC secret")
        self._given_a_task(xyz, "XYZ task")
        with tenant_context(abc.consultancy):
            [abc_id] = Notification.objects.values_list("public_id", flat=True)

        theirs = xyz.client.patch(f"{NOTIFICATIONS_URL}{abc_id}/", {"read": True}, format="json")
        xyz.client.post(f"{NOTIFICATIONS_URL}read-all/", {}, format="json")

        assert theirs.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert list(Notification.objects.values_list("read_at", flat=True)) == [None]

    def test_a_token_from_one_consultancy_is_refused_on_every_notification_endpoint(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        self._given_a_task(xyz, "XYZ task")
        with tenant_context(xyz.consultancy):
            [xyz_id] = Notification.objects.values_list("pk", flat=True)
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        responses = [
            intruder.get(NOTIFICATIONS_URL),
            intruder.patch(f"{NOTIFICATIONS_URL}{xyz_id}/", {"read": True}, format="json"),
            intruder.post(f"{NOTIFICATIONS_URL}read-all/", {}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 3
        with tenant_context(xyz.consultancy):
            assert list(Notification.objects.values_list("read_at", flat=True)) == [None]


BATCHES_URL = "/api/batches/"


class TestBatchIsolation:
    """Issue 23: classes, their Students and Teachers never cross Consultancies."""

    def _office_with_a_class(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str, label: str
    ) -> tuple[ConsultancyContext, Batch, Student]:
        """A Consultancy with classes on, its Admin logged in, and one class with one Student."""
        office = make_consultancy(name, subdomain)
        switch_modules(office.consultancy, {Module.BATCHES})
        office.log_in_as(Role.ADMIN)
        teacher = office.add_staff(Role.TEACHER)
        with tenant_context(office.consultancy):
            student = Student.objects.create(
                name=f"{label} Student", phone="9841000001", country="australia", source="walk_in"
            )
            batch = Batch.objects.create(
                name=f"{label} class",
                test_type="ielts",
                teacher=teacher,
                time_slot="7 AM",
                starts_on=timezone.localdate(),
                ends_on=timezone.localdate(),
            )
            BatchMembership.objects.create(batch=batch, student=student)
        return office, batch, student

    def test_lists_show_only_this_consultancys_classes_students_and_teachers(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        self._office_with_a_class(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz, _, _ = self._office_with_a_class(
            make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ"
        )

        answers = [
            xyz.client.get(BATCHES_URL),
            xyz.client.get(f"{BATCHES_URL}students/"),
            xyz.client.get(f"{BATCHES_URL}teachers/"),
        ]

        assert [r.status_code for r in answers] == [status.HTTP_200_OK] * 3
        assert all("ABC" not in r.content.decode() for r in answers)
        assert answers[0].json()["results"][0]["name"] == "XYZ class"

    def test_the_same_ids_reach_only_this_consultancys_own(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_batch, abc_student = self._office_with_a_class(
            make_consultancy, "ABC Education", "abcedu", "ABC"
        )
        xyz, _, _ = self._office_with_a_class(
            make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ"
        )
        with tenant_context(abc.consultancy):
            abc_teacher = str(Staff.objects.get(pk=abc_batch.teacher_id).public_id)
        body = {
            "name": "Taken over",
            "test_type": "pte",
            "teacher": abc_teacher,
            "time_slot": "9 AM",
            "starts_on": timezone.localdate().isoformat(),
            "ends_on": timezone.localdate().isoformat(),
        }
        other_id = abc_batch.public_id

        responses = [
            xyz.client.get(f"{BATCHES_URL}{other_id}/"),
            xyz.client.put(f"{BATCHES_URL}{other_id}/", body, format="json"),
            xyz.client.post(
                f"{BATCHES_URL}{other_id}/students/",
                {"student": str(abc_student.public_id)},
                format="json",
            ),
            xyz.client.delete(f"{BATCHES_URL}{other_id}/students/{abc_student.public_id}/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 4
        with tenant_context(abc.consultancy):
            assert Batch.objects.get().name == "ABC class"
            assert BatchMembership.objects.count() == 1

    def test_a_token_from_one_consultancy_is_refused_on_every_batch_endpoint(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _, _ = self._office_with_a_class(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz, xyz_batch, xyz_student = self._office_with_a_class(
            make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ"
        )
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)
        one = f"{BATCHES_URL}{xyz_batch.public_id}/"

        responses = [
            intruder.get(BATCHES_URL),
            intruder.post(BATCHES_URL, {}, format="json"),
            intruder.get(one),
            intruder.put(one, {}, format="json"),
            intruder.post(
                f"{one}students/", {"student": str(xyz_student.public_id)}, format="json"
            ),
            intruder.delete(f"{one}students/{xyz_student.public_id}/"),
            intruder.get(f"{BATCHES_URL}students/"),
            intruder.get(f"{BATCHES_URL}teachers/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 8
        with tenant_context(xyz.consultancy):
            assert BatchMembership.objects.count() == 1

    def test_switching_classes_off_in_one_consultancy_leaves_another_alone(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _, _ = self._office_with_a_class(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz, _, _ = self._office_with_a_class(
            make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ"
        )

        switch_modules(abc.consultancy, set())

        assert abc.client.get(BATCHES_URL).status_code == status.HTTP_404_NOT_FOUND
        assert xyz.client.get(BATCHES_URL).status_code == status.HTTP_200_OK


class TestAttendanceIsolation:
    """Issue 24: a class's Attendance, and a Student's classes, never cross Consultancies."""

    def _office_with_a_marked_class(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str, label: str
    ) -> tuple[ConsultancyContext, Batch, Student]:
        """An office with classes on, its Admin in, and a class with a Student marked today."""
        office = make_consultancy(name, subdomain)
        switch_modules(office.consultancy, {Module.BATCHES})
        office.log_in_as(Role.ADMIN)
        teacher = office.add_staff(Role.TEACHER)
        today = timezone.localdate()
        with tenant_context(office.consultancy):
            student = Student.objects.create(
                name=f"{label} Student", phone="9841000001", country="australia", source="walk_in"
            )
            batch = Batch.objects.create(
                name=f"{label} class",
                test_type="ielts",
                teacher=teacher,
                time_slot="7 AM",
                starts_on=today,
                ends_on=today,
            )
            BatchMembership.objects.create(batch=batch, student=student)
            Attendance.objects.create(
                batch=batch, student=student, day=today, mark=Mark.PRESENT, marked_by=teacher
            )
        return office, batch, student

    def test_the_same_ids_reach_only_this_consultancys_own(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_batch, abc_student = self._office_with_a_marked_class(
            make_consultancy, "ABC Education", "abcedu", "ABC"
        )
        xyz, xyz_batch, xyz_student = self._office_with_a_marked_class(
            make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ"
        )
        today = timezone.localdate().isoformat()
        other = abc_batch.public_id
        mark = {"marks": [{"student": str(abc_student.public_id), "mark": "absent"}]}

        responses = [
            xyz.client.get(f"{BATCHES_URL}{other}/attendance/{today}/"),
            xyz.client.put(f"{BATCHES_URL}{other}/attendance/{today}/", mark, format="json"),
            xyz.client.get(f"/api/students/{uuid.uuid4()}/batches/"),
            xyz.client.get(f"/api/students/{abc_student.public_id}/batches/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 4
        # XYZ's own class refuses ABC's Student UUID.
        refused = xyz.client.put(
            f"{BATCHES_URL}{xyz_batch.public_id}/attendance/{today}/", mark, format="json"
        )
        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        classes = xyz.client.get(f"/api/students/{xyz_student.public_id}/batches/").json()
        assert [c["name"] for c in classes] == ["XYZ class"]
        own = {"marks": [{"student": str(xyz_student.public_id), "mark": "absent"}]}
        xyz.client.put(
            f"{BATCHES_URL}{xyz_batch.public_id}/attendance/{today}/", own, format="json"
        )
        with tenant_context(abc.consultancy):
            assert Attendance.objects.get().mark == Mark.PRESENT
        with tenant_context(xyz.consultancy):
            assert Attendance.objects.get().mark == Mark.ABSENT

    def test_a_token_from_one_consultancy_is_refused_on_every_attendance_endpoint(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _, _ = self._office_with_a_marked_class(
            make_consultancy, "ABC Education", "abcedu", "ABC"
        )
        xyz, xyz_batch, xyz_student = self._office_with_a_marked_class(
            make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ"
        )
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)
        one_day = (
            f"{BATCHES_URL}{xyz_batch.public_id}/attendance/{timezone.localdate().isoformat()}/"
        )

        responses = [
            intruder.get(one_day),
            intruder.put(one_day, {"marks": []}, format="json"),
            intruder.get(f"/api/students/{xyz_student.public_id}/batches/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 3
        with tenant_context(xyz.consultancy):
            assert Attendance.objects.get().mark == Mark.PRESENT


class TestCommunicationIsolation:
    """Issue 04: emails never cross Consultancies, and each goes out under its own office."""

    def _office_with_an_email(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str, label: str
    ) -> tuple[ConsultancyContext, Student]:
        """An office with a contact email, its Admin in, and one email sent to its Student."""
        office = make_consultancy(name, subdomain)
        switch_modules(office.consultancy, {Module.COMMUNICATION})
        set_office_details(
            office.consultancy, OfficeDetails(contact_email=f"info@{subdomain}.test")
        )
        office.log_in_as(Role.ADMIN)
        with tenant_context(office.consultancy):
            student = Student.objects.create(
                name=f"{label} Student",
                phone="9841000001",
                email=f"student@{subdomain}.test",
                country="australia",
                source="walk_in",
            )
        sent = office.client.post(
            f"/api/students/{student.public_id}/messages/",
            {"subject": f"{label} subject", "body": f"{label} message"},
            format="json",
        )
        assert sent.status_code == 201
        activate_public_schema()  # The request left the connection on this office's schema.
        return office, student

    def test_each_office_sees_only_its_own_emails_sent_under_its_own_name(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_student = self._office_with_an_email(
            make_consultancy, "ABC Education", "abcedu", "ABC"
        )
        xyz, xyz_student = self._office_with_an_email(
            make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ"
        )

        listed = xyz.client.get("/api/messages/").json()["results"]
        assert [m["subject"] for m in listed] == ["XYZ subject"]
        # ABC's Student UUID finds nobody at XYZ; XYZ's own UUID lists XYZ's emails only.
        theirs = xyz.client.get(f"/api/students/{abc_student.public_id}/messages/")
        assert theirs.status_code == status.HTTP_404_NOT_FOUND
        own = xyz.client.get(f"/api/students/{xyz_student.public_id}/messages/").json()["results"]
        assert [m["subject"] for m in own] == ["XYZ subject"]
        other = xyz.client.post(
            f"/api/students/{abc_student.public_id}/messages/",
            {"subject": "Hi", "body": "Hi"},
            format="json",
        )
        assert other.status_code == status.HTTP_404_NOT_FOUND
        # Each email went under its own office's name, with its own contact email.
        assert [(m.to, m.reply_to, m.from_email.split(" <")[0]) for m in mail.outbox] == [
            (["student@abcedu.test"], ["info@abcedu.test"], "ABC Education"),
            (["student@xyzoverseas.test"], ["info@xyzoverseas.test"], "XYZ Overseas"),
        ]
        with tenant_context(abc.consultancy):
            assert [m.subject for m in Message.objects.all()] == ["ABC subject"]

    def test_a_token_from_one_consultancy_is_refused_on_every_email_endpoint(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _ = self._office_with_an_email(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz, xyz_student = self._office_with_an_email(
            make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ"
        )
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)
        one_student = f"/api/students/{xyz_student.public_id}/messages/"

        responses = [
            intruder.get("/api/messages/"),
            intruder.get(one_student),
            intruder.post(one_student, {"subject": "Hi", "body": "Hi"}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 3
        with tenant_context(xyz.consultancy):
            assert Message.objects.count() == 1


class TestBulkMessageIsolation:
    """Issue 04: an email to many Students reaches only this Consultancy's Students."""

    def _office(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str, label: str
    ) -> tuple[ConsultancyContext, Batch, list[Student]]:
        """An office with a contact email, its Admin in, two Students and a class holding one."""
        office = make_consultancy(name, subdomain)
        switch_modules(office.consultancy, {Module.COMMUNICATION, Module.BATCHES})
        set_office_details(
            office.consultancy, OfficeDetails(contact_email=f"info@{subdomain}.test")
        )
        office.log_in_as(Role.ADMIN)
        teacher = office.add_staff(Role.TEACHER)
        with tenant_context(office.consultancy):
            people = [
                Student.objects.create(
                    name=f"{label} Student {n}",
                    phone=f"984100000{n}",
                    email=f"student{n}@{subdomain}.test",
                    country="australia",
                    source="walk_in",
                )
                for n in (1, 2)
            ]
            batch = Batch.objects.create(
                name=f"{label} class",
                test_type="ielts",
                teacher=teacher,
                time_slot="7 AM",
                starts_on=timezone.localdate(),
                ends_on=timezone.localdate(),
            )
            BatchMembership.objects.create(batch=batch, student=people[0])
        activate_public_schema()
        return office, batch, people

    def test_only_this_offices_students_can_be_chosen_or_pre_ticked(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_batch, abc_people = self._office(
            make_consultancy, "ABC Education", "abcedu", "ABC"
        )
        xyz, xyz_batch, xyz_people = self._office(
            make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ"
        )
        body = {"subject": "Hi", "body": "Hi"}

        mine = xyz.client.post(
            "/api/messages/bulk/",
            {**body, "students": [str(s.public_id) for s in xyz_people]},
            format="json",
        )
        foreign = xyz.client.post(
            "/api/messages/bulk/",
            {**body, "students": [str(s.public_id) for s in abc_people]},
            format="json",
        )
        other_class = xyz.client.get("/api/messages/bulk/", {"batch": str(abc_batch.public_id)})
        pre_ticked = xyz.client.get(
            "/api/messages/bulk/", {"batch": str(xyz_batch.public_id)}
        ).json()

        assert mine.json()["sent"] == 2
        assert foreign.status_code == status.HTTP_400_BAD_REQUEST
        assert other_class.status_code == status.HTTP_404_NOT_FOUND
        assert [s["name"] for s in pre_ticked["students"]] == ["XYZ Student 1"]
        everyone = xyz.client.get("/api/messages/bulk/").json()["students"]
        assert {s["name"] for s in everyone} == {"XYZ Student 1", "XYZ Student 2"}
        assert {m.to[0] for m in mail.outbox} == {
            "student1@xyzoverseas.test",
            "student2@xyzoverseas.test",
        }
        with tenant_context(abc.consultancy):
            assert Message.objects.count() == 0

    def test_a_token_from_one_consultancy_is_refused_on_bulk_send(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _, _ = self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz, xyz_batch, xyz_people = self._office(
            make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ"
        )
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)
        body = {"subject": "Hi", "body": "Hi", "students": [str(s.public_id) for s in xyz_people]}

        responses = [
            intruder.get("/api/messages/bulk/"),
            intruder.get("/api/messages/bulk/", {"batch": str(xyz_batch.public_id)}),
            intruder.post("/api/messages/bulk/", body, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 3
        assert mail.outbox == []


class TestReadyMadeEmailIsolation:
    """Ticket 09: a ready-made email is filled in only from this office's own records."""

    def _office(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str, records: int
    ) -> tuple[ConsultancyContext, Student, dict[str, list[int | str]]]:
        """An office (Admin in) with one Student who has `records` of each record kind."""
        office = make_consultancy(name, subdomain)
        switch_modules(office.consultancy, {Module.COMMUNICATION, Module.ACCOUNTS, Module.BATCHES})
        set_office_details(
            office.consultancy, OfficeDetails(contact_email=f"info@{subdomain}.test")
        )
        admin = office.log_in_as(Role.ADMIN)
        teacher = office.add_staff(Role.TEACHER)
        # Payments, Offers, Visas and classes, each by UUID (ADR 0027).
        ids: dict[str, list[int | str]] = {"payment": [], "offer": [], "visa": [], "batch": []}
        with tenant_context(office.consultancy):
            student = Student.objects.create(
                name=f"{name} Student",
                phone="9841000001",
                email=f"student@{subdomain}.test",
                country="australia",
                source="walk_in",
            )
            university = University.objects.create(name=f"{name} University", country="uk")
            for n in range(records):
                ids["payment"].append(
                    str(
                        Payment.objects.create(
                            student=student,
                            amount=1000,
                            paid_on=timezone.localdate(),
                            method="cash",
                            fee_type=FeeType.objects.get(name="Visa fee"),
                            recorded_by=admin,
                            recorded_by_name=admin.name,
                        ).public_id
                    )
                )
                course = Course.objects.create(university=university, name=f"{name} course {n}")
                offer = Application.objects.create(
                    student=student, course=course, intake="2027-02", status="offer"
                )
                ids["offer"].append(str(offer.public_id))
                ids["visa"].append(
                    str(
                        Visa.objects.create(
                            student=student, application=offer, status="granted"
                        ).public_id
                    )
                )
                batch = Batch.objects.create(
                    name=f"{name} class {n}",
                    test_type="ielts",
                    teacher=teacher,
                    time_slot="7 AM",
                    starts_on=timezone.localdate(),
                    ends_on=timezone.localdate(),
                )
                BatchMembership.objects.create(batch=batch, student=student)
                ids["batch"].append(str(batch.public_id))
        activate_public_schema()
        return office, student, ids

    def test_another_offices_records_are_never_offered_or_filled_in(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        _, _, abc_ids = self._office(make_consultancy, "ABC Education", "abcedu", records=3)
        xyz, xyz_student, xyz_ids = self._office(
            make_consultancy, "XYZ Overseas", "xyzoverseas", records=1
        )
        menu_url = f"/api/students/{xyz_student.public_id}/emails/ready-made/"
        draft_url = f"/api/students/{xyz_student.public_id}/emails/draft/"
        kinds = {
            "payment": "payment_received",
            "offer": "offer_received",
            "visa": "visa_result",
            "batch": "class_details",
        }

        menu = {entry["kind"]: entry for entry in xyz.client.get(menu_url).json()["kinds"]}

        for record, kind in kinds.items():
            assert [r["id"] for r in menu[kind]["records"]] == xyz_ids[record]
            assert "ABC" not in str(menu[kind]["records"])
            theirs = xyz.client.get(draft_url, {"kind": kind, "record": str(abc_ids[record][-1])})
            assert theirs.status_code == status.HTTP_400_BAD_REQUEST, kind
            assert list(theirs.json()) == ["record"]
        many = xyz.client.get("/api/messages/bulk/ready-made/").json()["kinds"][0]
        assert [r["id"] for r in many["records"]] == xyz_ids["batch"]
        other_class = xyz.client.get(
            "/api/messages/bulk/draft/",
            {"kind": "class_details", "record": str(abc_ids["batch"][-1])},
        )
        assert other_class.status_code == status.HTTP_400_BAD_REQUEST
        signed = xyz.client.get(draft_url, {"kind": "plain"}).json()["body"]
        assert signed.endswith("XYZ Overseas")
        unknown = xyz.client.get(f"/api/students/{uuid.uuid4()}/emails/ready-made/")
        assert unknown.status_code == status.HTTP_404_NOT_FOUND

    def test_a_token_from_one_consultancy_is_refused_on_every_ready_made_endpoint(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _, _ = self._office(make_consultancy, "ABC Education", "abcedu", records=1)
        xyz, xyz_student, _ = self._office(
            make_consultancy, "XYZ Overseas", "xyzoverseas", records=1
        )
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)
        student = f"/api/students/{xyz_student.public_id}/emails"

        responses = [
            intruder.get(f"{student}/ready-made/"),
            intruder.get(f"{student}/draft/", {"kind": "plain"}),
            intruder.get("/api/messages/bulk/ready-made/"),
            intruder.get("/api/messages/bulk/draft/", {"kind": "plain"}),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 4


class TestHomeIsolation:
    """Issue 33: the Homes' progress and pipeline count only this Consultancy's Students."""

    def _office(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str, label: str
    ) -> ConsultancyContext:
        """An office with its Admin logged in and two Students, one of them Lost."""
        office = make_consultancy(name, subdomain)
        office.log_in_as(Role.ADMIN)
        with tenant_context(office.consultancy):
            for number, stage in enumerate(("counselling", "lost"), start=1):
                Student.objects.create(
                    name=f"{label} Student {number}",
                    phone=f"984100000{number}",
                    country="australia",
                    source="walk_in",
                    stage=stage,
                    lost_reason="Moved" if stage == "lost" else "",
                )
        activate_public_schema()
        return office

    def test_progress_and_pipeline_count_only_this_offices_students(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ")

        progress = xyz.client.get("/api/home/progress/").json()
        counts = xyz.client.get("/api/home/pipeline/").json()["counts"]

        assert [row["student"]["name"] for row in progress["results"]] == ["XYZ Student 1"]
        assert (counts["counselling"], counts["lost"]) == (1, 1)
        assert sum(counts.values()) == 2

    def test_a_token_from_one_consultancy_is_refused_on_the_home(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ")
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        responses = [intruder.get("/api/home/progress/"), intruder.get("/api/home/pipeline/")]

        assert {response.status_code for response in responses} == {status.HTTP_401_UNAUTHORIZED}
        assert all("XYZ" not in response.content.decode() for response in responses)


class TestHomeWaitingIsolation:
    """Issue 52: what waits on me counts only this Consultancy's Documents, requests and Visas."""

    def _office(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str
    ) -> tuple[ConsultancyContext, Staff]:
        """An office whose counsellor has a pending Document, a waiting request and an interview."""
        office = make_consultancy(name, subdomain)
        counsellor = office.add_staff(Role.COUNSELLOR, email=f"mine@{office.host}")
        asker = office.add_staff(Role.COUNSELLOR, email=f"asker@{office.host}")
        with tenant_context(office.consultancy):
            student = Student.objects.create(
                name=f"{name} Student",
                phone="9841000001",
                stage="visa",
                assigned_counsellor=counsellor,
            )
            Document.objects.create(
                student=student,
                document_type=DocumentType.objects.get(name="Passport"),
            )
            TransferRequest.objects.create(student=student, asked_by=asker, reason="Family.")
            university = University.objects.create(name=f"{name} Uni", country="australia")
            course = Course.objects.create(
                university=university, name="Master of IT", level="master"
            )
            application = Application.objects.create(
                student=student, course=course, intake="2027-03", status="offer"
            )
            Visa.objects.create(
                student=student,
                application=application,
                status="applied",
                biometrics_at=timezone.now() + timedelta(days=1),
                interview_at=timezone.now() + timedelta(days=2),
            )
        activate_public_schema()
        return office, counsellor

    def test_each_office_counts_only_its_own(self, make_consultancy: ConsultancyFactory) -> None:
        self._office(make_consultancy, "ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.COUNSELLOR)
        admin_client = xyz.new_client()
        use_access_token(admin_client, xyz.tokens_for(xyz.add_staff(Role.ADMIN)).access)

        mine = xyz.client.get("/api/home/waiting/").json()
        everyone = admin_client.get("/api/home/waiting/").json()
        page = admin_client.get("/api/documents/students/", {"waiting": "true"}).json()

        for answer in (mine, everyone):
            assert answer["documents_waiting"] == 0
            assert answer["transfer_requests_waiting"] == 0
            assert answer["visa_appointments"] == {"count": 0, "results": []}
        assert page["count"] == 0

    def test_a_token_from_one_consultancy_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_counsellor = self._office(make_consultancy, "ABC Education", "abcedu")
        xyz, _ = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc_counsellor).access)

        response = intruder.get("/api/home/waiting/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "XYZ" not in response.content.decode()


class TestVisitIsolation:
    """Issue 34: Visits, today's timeline and progress reach only this Consultancy's Students."""

    def _office(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str, label: str
    ) -> tuple[ConsultancyContext, Student]:
        """An office with Front Desk logged in and one Student who visited today."""
        office = make_consultancy(name, subdomain)
        office.log_in_as(Role.FRONT_DESK)
        with tenant_context(office.consultancy):
            student = Student.objects.create(
                name=f"{label} Student",
                phone="9841000001",
                country="australia",
                source="walk_in",
                note=f"{label} note",
            )
            Visit.objects.create(
                student=student, kind="new", purpose=f"{label} first", recorded_by_name="Desk"
            )
        activate_public_schema()
        return office, student

    def test_today_and_a_students_visits_show_only_this_offices(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz, xyz_student = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ")

        today = xyz.client.get("/api/visits/today/").json()
        visits = xyz.client.get(f"/api/students/{xyz_student.public_id}/visits/").json()

        assert [row["student"]["name"] for row in today["results"]] == ["XYZ Student"]
        assert [row["purpose"] for row in visits["results"]] == ["XYZ first"]

    def test_the_same_ids_reach_only_this_offices_own(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _ = self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz, xyz_student = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ")
        with tenant_context(abc.consultancy):
            only_in_abc = Student.objects.create(
                name="ABC Second", phone="9841000002", country="uk", source="walk_in"
            )
            Visit.objects.create(student=only_in_abc, kind="new", recorded_by_name="Desk")
        activate_public_schema()

        responses = [
            xyz.client.get(f"/api/students/{only_in_abc.public_id}/visits/"),
            xyz.client.post(
                f"/api/students/{only_in_abc.public_id}/visits/", {"reason": "other"}, format="json"
            ),
            xyz.client.get(f"/api/students/{only_in_abc.public_id}/progress/"),
        ]
        own = xyz.client.get(f"/api/students/{xyz_student.public_id}/visits/").json()

        assert {response.status_code for response in responses} == {status.HTTP_404_NOT_FOUND}
        assert [row["purpose"] for row in own["results"]] == ["XYZ first"]
        with tenant_context(abc.consultancy):
            assert Visit.objects.count() == 2

    def test_a_token_from_one_consultancy_is_refused_on_every_visit_endpoint(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _ = self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz, xyz_student = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ")
        intruder = api_client(xyz.host)
        abc_desk = abc.add_staff(Role.FRONT_DESK, email="second-desk@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_desk).access)

        responses = [
            intruder.get("/api/visits/today/"),
            intruder.get(f"/api/students/{xyz_student.public_id}/visits/"),
            intruder.post(
                f"/api/students/{xyz_student.public_id}/visits/", {"reason": "other"}, format="json"
            ),
            intruder.get(f"/api/students/{xyz_student.public_id}/progress/"),
        ]

        assert {response.status_code for response in responses} == {status.HTTP_401_UNAUTHORIZED}
        assert all("XYZ" not in response.content.decode() for response in responses)
        with tenant_context(xyz.consultancy):
            assert Visit.objects.count() == 1


class TestReportsIsolation:
    """Issue 28: an office's Reports count only its own Visits, Students and History."""

    REPORTS: tuple[tuple[str, dict[str, str]], ...] = (
        ("/api/reports/students/", {}),
        ("/api/reports/sources/", {"from": "2000-01-01", "to": "2099-12-31"}),
        # Issue 85: Staff Progress takes at most one year, ending today.
        (
            "/api/reports/staff-progress/",
            {
                "from": (timezone.localdate() - timedelta(days=30)).isoformat(),
                "to": timezone.localdate().isoformat(),
            },
        ),
    )

    def _office(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str, label: str
    ) -> ConsultancyContext:
        """An office with Reports on, its Admin logged in, and one Student who signed up."""
        office = make_consultancy(name, subdomain)
        switch_modules(office.consultancy, {Module.REPORTS})
        admin = office.log_in_as(Role.ADMIN)
        with tenant_context(office.consultancy):
            Staff.objects.filter(pk=admin.pk).update(name=f"{label} Admin")
            student = Student.objects.create(
                name=f"{label} Student",
                phone="9841000001",
                country="australia",
                source="facebook" if label == "ABC" else "walk_in",
                assigned_counsellor=admin,
            )
            Visit.objects.create(student=student, kind="new", recorded_by_name="Desk")
            HistoryEntry.objects.create(
                student=student,
                kind=HistoryKind.STAGE_CHANGED,
                by_staff_id=admin.pk,
                by_name=f"{label} Admin",
                before="counselling",
                after="applying",
            )
        activate_public_schema()
        return office

    def test_each_office_counts_only_its_own(self, make_consultancy: ConsultancyFactory) -> None:
        self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ")

        months = xyz.client.get("/api/reports/students/").json()["months"]
        sources = xyz.client.get(*self.REPORTS[1]).json()
        staff_progress = xyz.client.get(*self.REPORTS[2]).json()
        counsellors = staff_progress["counsellors"]

        assert (months[-1]["new_visitors"], months[-1]["signed_up"]) == (1, 1)
        assert sources["total"] == 1
        assert sources["sources"][0] == {"source": "walk_in", "count": 1}
        assert [(row["name"], row["students"], row["moved_forward"]) for row in counsellors] == [
            ("XYZ Admin", 1, 1)
        ]
        assert "ABC" not in str(staff_progress)

    def test_a_token_from_another_office_cannot_read_staff_progress(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        """Issue 85: ABC's Admin token used on XYZ's address is refused, and shows nothing."""
        abc = self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ")
        with tenant_context(abc.consultancy):
            abc_admin = Staff.objects.get(name="ABC Admin")
        activate_public_schema()
        intruder = xyz.new_client()
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        response = intruder.get(*self.REPORTS[2])

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "XYZ" not in response.content.decode()

    def test_each_office_counts_only_its_own_countries(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        """Issue 53: Visas granted by country and the countries Students want."""
        offices = [
            self._office(make_consultancy, "ABC Education", "abcedu", "ABC"),
            self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ"),
        ]
        for office, country in zip(offices, ("uk", "canada"), strict=True):
            with tenant_context(office.consultancy):
                student = Student.objects.get()
                university = University.objects.create(name="Shared Name", country=country)
                course = Course.objects.create(university=university, name="MIT", level="master")
                application = Application.objects.create(
                    student=student, course=course, intake="2027-03", status="offer"
                )
                Visa.objects.create(
                    student=student,
                    application=application,
                    status="granted",
                    granted_on=timezone.localdate(),
                )
            activate_public_schema()

        body = offices[1].client.get("/api/reports/students/").json()

        assert body["visas_granted_by_country"] == [{"country": "canada", "count": 1}]
        assert body["countries_wanted"] == [{"country": "australia", "count": 1}]

    def test_a_token_from_one_consultancy_is_refused_on_every_report(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ")
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        responses = [intruder.get(url, query) for url, query in self.REPORTS]

        assert {response.status_code for response in responses} == {status.HTTP_401_UNAUTHORIZED}
        assert all("XYZ" not in response.content.decode() for response in responses)


# Every day the Reports accept.
EVERY_REPORT_DAY = {"from": "2000-01-01", "to": "2099-12-31"}


class TestClassReportIsolation:
    """Issue 30: an office's Classes report lists only its own classes and Attendance."""

    URL = "/api/reports/batches/"

    def _office(
        self,
        make_consultancy: ConsultancyFactory,
        name: str,
        subdomain: str,
        label: str,
        mark: Mark,
    ) -> ConsultancyContext:
        """An office with Reports and classes on, its Admin in, and one class marked today."""
        office = make_consultancy(name, subdomain)
        switch_modules(office.consultancy, {Module.REPORTS, Module.BATCHES})
        office.log_in_as(Role.ADMIN)
        teacher = office.add_staff(Role.TEACHER)
        today = timezone.localdate()
        with tenant_context(office.consultancy):
            student = Student.objects.create(
                name=f"{label} Student", phone="9841000001", country="australia", source="walk_in"
            )
            batch = Batch.objects.create(
                name=f"{label} class",
                test_type="ielts",
                teacher=teacher,
                time_slot="7 AM",
                starts_on=today,
                ends_on=today,
            )
            BatchMembership.objects.create(batch=batch, student=student)
            Attendance.objects.create(
                batch=batch, student=student, day=today, mark=mark, marked_by=teacher
            )
        activate_public_schema()
        return office

    def test_each_office_lists_only_its_own_classes(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        self._office(make_consultancy, "ABC Education", "abcedu", "ABC", Mark.PRESENT)
        xyz = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ", Mark.ABSENT)

        rows = xyz.client.get(self.URL, EVERY_REPORT_DAY).json()["batches"]

        assert [(row["name"], row["students"], row["attended_percent"]) for row in rows] == [
            ("XYZ class", 1, 0)
        ]

    def test_a_token_from_one_consultancy_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = self._office(make_consultancy, "ABC Education", "abcedu", "ABC", Mark.PRESENT)
        xyz = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ", Mark.PRESENT)
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        response = intruder.get(self.URL, EVERY_REPORT_DAY)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "XYZ" not in response.content.decode()


def _student_with_fee(office: ConsultancyContext, name: str) -> tuple[str, str]:
    """A new Student of the office's logged-in Admin with one NPR 40,000 Fee: (student, fee) ids."""
    student = office.client.post(STUDENTS_URL, _lead_body(name), format="json").json()
    fees = office.client.post(
        f"/api/students/{student['id']}/fees/",
        {"fee_type": _fee_type_id(office), "amount": 40000},
        format="json",
    ).json()
    return student["id"], fees["fees"][0]["id"]


class TestFeeIsolation:
    """Issue 35: Fees, Balances and the Student fees list never cross Consultancies."""

    def _two_offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext]:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            switch_modules(office.consultancy, {Module.ACCOUNTS})
            office.log_in_as(Role.ADMIN)
        return abc, xyz

    def test_the_list_and_totals_count_only_this_consultancys_fees(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _student_with_fee(abc, "Sita Karki")
        _student_with_fee(abc, "Hari Lama")
        _student_with_fee(xyz, "Ram Thapa")

        body = xyz.client.get("/api/accounts/student-fees/").json()

        assert [row["student"]["name"] for row in body["results"]] == ["Ram Thapa"]
        assert body["totals"]["agreed"] == 40000
        assert "Sita" not in xyz.client.get("/api/accounts/student-fees/").content.decode()

    def test_another_consultancy_cannot_read_add_change_or_delete_fees(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        abc_student, abc_fee = _student_with_fee(abc, "Sita Karki")
        body = {"fee_type": _fee_type_id(xyz), "amount": 1}

        responses = [
            xyz.client.get(f"/api/students/{abc_student}/fees/"),
            xyz.client.post(f"/api/students/{abc_student}/fees/", body, format="json"),
            xyz.client.put(f"/api/fees/{abc_fee}/", body, format="json"),
            xyz.client.delete(f"/api/fees/{abc_fee}/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 4
        with tenant_context(abc.consultancy):
            assert list(Fee.objects.values_list("fee_type__name", "amount")) == [
                ("Visa fee", 40000)
            ]

    def test_the_same_ids_change_only_this_consultancys_fee(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        abc_student, abc_fee = _student_with_fee(abc, "Sita Karki")
        xyz_student, xyz_fee = _student_with_fee(xyz, "Ram Thapa")

        body = {"fee_type": _fee_type_id(xyz), "amount": 1}
        xyz.client.put(f"/api/fees/{xyz_fee}/", body, format="json")
        xyz.client.delete(f"/api/fees/{xyz_fee}/")

        assert (abc_student, abc_fee) != (xyz_student, xyz_fee)
        theirs = xyz.client.put(f"/api/fees/{abc_fee}/", body, format="json")
        assert theirs.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert Fee.objects.get(public_id=abc_fee).amount == 40000
            assert not HistoryEntry.objects.filter(kind=HistoryKind.FEE_DELETED).exists()
        with tenant_context(xyz.consultancy):
            assert Fee.objects.count() == 0

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        student, fee = _student_with_fee(xyz, "Ram Thapa")
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abcedu.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)
        body = {"fee_type": _fee_type_id(xyz), "amount": 1}

        responses = [
            intruder.get("/api/accounts/student-fees/"),
            intruder.get(f"/api/students/{student}/fees/"),
            intruder.post(f"/api/students/{student}/fees/", body, format="json"),
            intruder.put(f"/api/fees/{fee}/", body, format="json"),
            intruder.delete(f"/api/fees/{fee}/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 5
        with tenant_context(xyz.consultancy):
            assert Fee.objects.get(public_id=fee).amount == 40000


class TestFeeTypeIsolation:
    """Issue 06: Fee Types never cross Consultancies; an id is always this office's own."""

    def _two_offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext, str]:
        """Both offices with an Admin in; ABC has one more Fee Type than XYZ: (abc, xyz, its id)."""
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            switch_modules(office.consultancy, {Module.ACCOUNTS})
            office.log_in_as(Role.ADMIN)
        added = abc.client.post("/api/fee-types/", {"name": "ABC only"}, format="json").json()
        return abc, xyz, str(added["id"])

    def test_each_office_lists_only_its_own(self, make_consultancy: ConsultancyFactory) -> None:
        abc, xyz, _ = self._two_offices(make_consultancy)

        names = [row["name"] for row in xyz.client.get("/api/fee-types/").json()["results"]]

        assert "ABC only" not in names
        assert "ABC only" in abc.client.get("/api/fee-types/").content.decode()

    def test_another_offices_fee_type_cannot_be_picked(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        _, xyz, abc_only = self._two_offices(make_consultancy)
        student = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()

        payment = xyz.client.post(
            f"/api/students/{student['id']}/payments/",
            _visit_body(xyz, _fee_type_id(xyz), abc_only),
            format="json",
        )
        fee = xyz.client.post(
            f"/api/students/{student['id']}/fees/",
            {"fee_type": abc_only, "amount": 1},
            format="json",
        )

        assert payment.json() == {"lines": [{}, {"fee_type": ["Choose a Fee Type."]}]}
        assert fee.json() == {"fee_type": ["Choose a Fee Type."]}
        with tenant_context(xyz.consultancy):
            assert not Payment.objects.exists()
            assert not Fee.objects.exists()

    def test_another_offices_fee_type_cannot_be_changed_deleted_or_ordered(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz, abc_only = self._two_offices(make_consultancy)
        xyz_ids = [row["id"] for row in xyz.client.get("/api/fee-types/").json()["results"]]

        renamed = xyz.client.patch(f"/api/fee-types/{abc_only}/", {"name": "X"}, format="json")
        deleted = xyz.client.delete(f"/api/fee-types/{abc_only}/")
        ordered = xyz.client.put(
            "/api/fee-types/order/", {"order": [abc_only, *xyz_ids]}, format="json"
        )

        assert (renamed.status_code, deleted.status_code, ordered.status_code) == (404, 404, 400)
        with tenant_context(abc.consultancy):
            assert FeeType.objects.filter(public_id=abc_only, name="ABC only").exists()

    def test_the_same_id_changes_only_this_offices_fee_type(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz, _ = self._two_offices(make_consultancy)
        visa = _fee_type_id(xyz)

        xyz.client.patch(f"/api/fee-types/{visa}/", {"name": "Visa (XYZ)"}, format="json")

        assert visa != _fee_type_id(abc)  # Each office's Fee Types have their own UUIDs.
        with tenant_context(abc.consultancy):
            assert FeeType.objects.filter(public_id=_fee_type_id(abc), name="Visa fee").exists()

    def test_a_token_from_one_office_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz, _ = self._two_offices(make_consultancy)
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abcedu.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)
        visa = _fee_type_id(xyz)

        responses = [
            intruder.get("/api/fee-types/"),
            intruder.post("/api/fee-types/", {"name": "Intruder"}, format="json"),
            intruder.patch(f"/api/fee-types/{visa}/", {"hidden": True}, format="json"),
            intruder.delete(f"/api/fee-types/{visa}/"),
            intruder.put("/api/fee-types/order/", {"order": [visa]}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 5
        with tenant_context(xyz.consultancy):
            assert not FeeType.objects.filter(name="Intruder").exists()
            assert FeeType.objects.get(public_id=visa).hidden is False


_EXPENSE_BODY = {
    "spent_on": "2025-10-01",
    "category": "office_rent",
    "paid_to": "Office landlord",
    "amount": 85000,
    "method": "bank_transfer",
}


def _expense(office: ConsultancyContext, paid_to: str) -> str:
    """A new Expense added by the office's logged-in Admin: its id."""
    body = {**_EXPENSE_BODY, "paid_to": paid_to}
    response = office.client.post("/api/accounts/expenses/", body, format="json")
    expense_id: str = response.json()["id"]
    return expense_id


class TestExpenseIsolation:
    """Issue 36: Expenses and their month totals never cross Consultancies."""

    def _two_offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext]:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            switch_modules(office.consultancy, {Module.ACCOUNTS})
            office.log_in_as(Role.ADMIN)
        return abc, xyz

    def test_the_month_lists_and_totals_only_this_consultancys_expenses(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _expense(abc, "ABC landlord")
        _expense(abc, "ABC printer")
        _expense(xyz, "XYZ landlord")

        response = xyz.client.get("/api/accounts/expenses/", {"month": "2025-10"})

        body = response.json()
        assert [row["paid_to"] for row in body["results"]] == ["XYZ landlord"]
        assert (body["total"], body["count"]) == (85000, 1)
        assert "ABC" not in response.content.decode()

    def test_another_consultancy_cannot_change_or_delete_an_expense(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        abc_expense = _expense(abc, "ABC landlord")

        responses = [
            xyz.client.put(f"/api/accounts/expenses/{abc_expense}/", _EXPENSE_BODY, format="json"),
            xyz.client.delete(f"/api/accounts/expenses/{abc_expense}/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2
        with tenant_context(abc.consultancy):
            assert list(Expense.objects.values_list("paid_to", flat=True)) == ["ABC landlord"]

    def test_the_same_ids_change_only_this_consultancys_expense(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        abc_expense = _expense(abc, "ABC landlord")
        xyz_expense = _expense(xyz, "XYZ landlord")
        changed = {**_EXPENSE_BODY, "paid_to": "Changed", "amount": 1}

        xyz.client.put(f"/api/accounts/expenses/{xyz_expense}/", changed, format="json")
        xyz.client.delete(f"/api/accounts/expenses/{xyz_expense}/")

        assert abc_expense != xyz_expense  # Each Expense has its own UUID (ADR 0027).
        with tenant_context(abc.consultancy):
            assert Expense.objects.get(public_id=abc_expense).amount == 85000
        with tenant_context(xyz.consultancy):
            assert Expense.objects.count() == 0

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        expense = _expense(xyz, "XYZ landlord")
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abcedu.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        responses = [
            intruder.get("/api/accounts/expenses/"),
            intruder.post("/api/accounts/expenses/", _EXPENSE_BODY, format="json"),
            intruder.put(f"/api/accounts/expenses/{expense}/", _EXPENSE_BODY, format="json"),
            intruder.delete(f"/api/accounts/expenses/{expense}/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 4
        with tenant_context(xyz.consultancy):
            assert Expense.objects.get(public_id=expense).paid_to == "XYZ landlord"


def _commission(office: ConsultancyContext, name: str) -> tuple[str, str, str]:
    """A Student (UUID), a University and a Commission added by the office's Admin: ids."""
    student = office.client.post(STUDENTS_URL, _lead_body(name), format="json").json()["id"]
    university = office.client.post(
        "/api/catalogue/universities/",
        {"name": f"{name} University", "country": "australia"},
        format="json",
    ).json()["id"]
    body = {"student": student, "university": university, "intake": "2027-02", "amount": 290000}
    commission = office.client.post("/api/accounts/commission/", body, format="json").json()
    return student, university, commission["id"]


class TestCommissionIsolation:
    """Issue 37: Commission and its totals never cross Consultancies."""

    def _two_offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext]:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            switch_modules(office.consultancy, {Module.ACCOUNTS})
            office.log_in_as(Role.ADMIN)
        return abc, xyz

    def test_the_list_totals_and_received_count_only_this_consultancy(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _, _, abc_commission = _commission(abc, "Sita Karki")
        abc.client.put(
            f"/api/accounts/commission/{abc_commission}/status/",
            {"status": "received"},
            format="json",
        )
        _commission(xyz, "Ram Thapa")

        response = xyz.client.get("/api/accounts/commission/")

        body = response.json()
        assert [row["student"]["name"] for row in body["results"]] == ["Ram Thapa"]
        assert body["totals"]["received"] == 0
        assert "Sita" not in response.content.decode()
        assert xyz.client.get("/api/accounts/commission/received/").json()["total"] == 0

    def test_another_consultancys_student_or_university_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        abc_student, abc_university, _ = _commission(abc, "Sita Karki")
        xyz_student, xyz_university, _ = _commission(xyz, "Ram Thapa")
        assert abc_student != xyz_student  # Every Student has their own UUID.
        assert abc_university != xyz_university  # Every University has its own UUID.
        hari, hari_university, _ = _commission(abc, "Hari Lama")

        response = xyz.client.post(
            "/api/accounts/commission/",
            {
                "student": hari,
                "university": hari_university,
                "intake": "2027-02",
                "amount": 1,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.json()) == {"student", "university"}

    def test_another_consultancy_cannot_change_a_status(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _, _, abc_commission = _commission(abc, "Sita Karki")
        _commission(abc, "Hari Lama")

        response = xyz.client.put(
            f"/api/accounts/commission/{abc_commission}/status/",
            {"status": "received"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert (
                Commission.objects.get(public_id=abc_commission).status == "waiting_for_enrolment"
            )

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        student, university, commission = _commission(xyz, "Ram Thapa")
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abcedu.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)
        body = {"student": student, "university": university, "intake": "2027-02", "amount": 1}

        responses = [
            intruder.get("/api/accounts/commission/"),
            intruder.post("/api/accounts/commission/", body, format="json"),
            intruder.put(
                f"/api/accounts/commission/{commission}/status/",
                {"status": "received"},
                format="json",
            ),
            intruder.get("/api/accounts/commission/received/"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 4
        with tenant_context(xyz.consultancy):
            assert Commission.objects.get(public_id=commission).status == "waiting_for_enrolment"


class TestMoneyReportIsolation:
    """Issue 29: the Money report adds up only this Consultancy's Payments, Commission, Expenses."""

    URL = "/api/reports/money/"

    def _office(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str, amount: int
    ) -> ConsultancyContext:
        """An office with Reports and Accounts on, its Admin in, and `amount` of each kind today."""
        office = make_consultancy(name, subdomain)
        switch_modules(office.consultancy, {Module.REPORTS, Module.ACCOUNTS})
        office.log_in_as(Role.ADMIN)
        admin = office.add_staff(Role.ADMIN, email=f"money@{subdomain}.test")
        today = timezone.localdate()
        with tenant_context(office.consultancy):
            student = Student.objects.create(
                name=f"{name} Student", phone="9841000001", country="australia", source="walk_in"
            )
            university = University.objects.create(name=f"{name} University", country="australia")
            recorded = {"recorded_by": admin, "recorded_by_name": admin.name, "amount": amount}
            Payment.objects.create(
                student=student,
                paid_on=today,
                method="cash",
                fee_type=FeeType.objects.get(name="Visa fee"),
                **recorded,
            )
            Commission.objects.create(
                student=student,
                university=university,
                intake="2027-02",
                status="received",
                received_on=today,
                **recorded,
            )
            Expense.objects.create(
                spent_on=today,
                category="office_rent",
                paid_to=f"{name} landlord",
                method="cash",
                **recorded,
            )
        activate_public_schema()
        return office

    def test_each_office_adds_up_only_its_own_money(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        self._office(make_consultancy, "ABC Education", "abcedu", 1000)
        xyz = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", 7)

        body = xyz.client.get(self.URL).json()

        assert body["months"][-1] == {
            "month": f"{timezone.localdate():%Y-%m}",
            "from_students": 7,
            "from_commission": 7,
            "money_out": 7,
        }
        assert sum(row["from_students"] for row in body["months"]) == 7
        assert body["money_out_by_category"] == [{"category": "office_rent", "amount": 7}]

    def test_a_token_from_one_consultancy_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = self._office(make_consultancy, "ABC Education", "abcedu", 1000)
        xyz = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", 7)
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        response = intruder.get(self.URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "money_out" not in response.content.decode()


ME_URL = "/api/me/"
MY_PW_URL = "/api/me/password/"
MY_DEVICES_URL = "/api/me/devices/"
SIGN_OUT_OTHERS_URL = "/api/me/devices/sign-out-others/"


MY_EMAIL_URL = "/api/me/email/"
MY_EMAIL_CONFIRM_URL = "/api/me/email/confirm/"


class TestWorkEmailChangeIsolation:
    """A Staff member changes their own email (2026-11-22 ticket 04)."""

    def test_my_token_cant_ask_or_confirm_on_another_consultancy(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz_staff = xyz.add_staff(Role.COUNSELLOR)  # Same primary key as ABC's below.
        tokens = abc.tokens_for(abc.add_staff(Role.COUNSELLOR))
        use_access_token(xyz.client, tokens.access)

        answers = [
            xyz.client.post(
                MY_EMAIL_URL,
                {"email": "taken.over@office.com", "password": TEST_PASSWORD},
                format="json",
            ),
            xyz.client.post(
                MY_EMAIL_CONFIRM_URL,
                {"email": "taken.over@office.com", "code": "123456"},
                format="json",
            ),
        ]

        assert {answer.status_code for answer in answers} == {status.HTTP_401_UNAUTHORIZED}
        assert mail.outbox == []
        with tenant_context(xyz.consultancy):
            assert Staff.objects.get(pk=xyz_staff.pk).email == xyz_staff.email

    def test_a_code_from_one_office_never_works_in_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_staff = abc.log_in_as(Role.COUNSELLOR)
        xyz_staff = xyz.log_in_as(Role.COUNSELLOR)
        assert abc_staff.pk == xyz_staff.pk
        new_email = "sita.new@office.com"
        abc.client.post(
            MY_EMAIL_URL, {"email": new_email, "password": TEST_PASSWORD}, format="json"
        )

        answer = xyz.client.post(
            MY_EMAIL_CONFIRM_URL, {"email": new_email, "code": _emailed_code()}, format="json"
        )

        assert answer.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(xyz.consultancy):
            assert Staff.objects.get(pk=xyz_staff.pk).email == xyz_staff.email
        with tenant_context(abc.consultancy):
            assert Staff.objects.get(pk=abc_staff.pk).email == abc_staff.email

    def test_the_same_person_in_two_offices_changes_only_one(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        shared = "sita@office.com"
        new_email = "sita.new@office.com"
        abc_staff = abc.add_staff(Role.COUNSELLOR, email=shared)
        xyz_staff = xyz.add_staff(Role.ADMIN, email=shared)
        use_access_token(abc.client, abc.tokens_for(abc_staff).access)
        use_access_token(xyz.client, xyz.tokens_for(xyz_staff).access)
        abc.client.post(
            MY_EMAIL_URL, {"email": new_email, "password": TEST_PASSWORD}, format="json"
        )

        answer = abc.client.post(
            MY_EMAIL_CONFIRM_URL, {"email": new_email, "code": _emailed_code()}, format="json"
        )

        assert answer.status_code == status.HTTP_200_OK
        with tenant_context(abc.consultancy):
            assert Staff.objects.get(pk=abc_staff.pk).email == new_email
        with tenant_context(xyz.consultancy):
            assert Staff.objects.get(pk=xyz_staff.pk).email == shared
        assert xyz.client.get(ME_URL).status_code == status.HTTP_200_OK
        assert [office.schema_name for office in offices_for(shared)] == ["xyzoverseas"]
        assert [office.schema_name for office in offices_for(new_email)] == ["abcedu"]


class TestMySettingsIsolation:
    def test_my_token_cant_read_or_change_the_same_id_on_another_consultancy(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz_staff = xyz.add_staff(Role.COUNSELLOR)  # Same primary key as ABC's below.
        tokens = abc.tokens_for(abc.add_staff(Role.COUNSELLOR))
        use_access_token(xyz.client, tokens.access)

        answers = [
            xyz.client.get(ME_URL),
            xyz.client.patch(ME_URL, {"name": "Taken Over"}, format="json"),
            xyz.client.post(
                MY_PW_URL,
                {"current_password": TEST_PASSWORD, "new_password": "Kathmandu!2027"},
                format="json",
            ),
            xyz.client.get(MY_DEVICES_URL),
            xyz.client.post(SIGN_OUT_OTHERS_URL),
        ]

        assert {answer.status_code for answer in answers} == {status.HTTP_401_UNAUTHORIZED}
        with tenant_context(xyz.consultancy):
            kept = Staff.objects.get(pk=xyz_staff.pk)
        assert kept.name == xyz_staff.name
        assert kept.check_password(TEST_PASSWORD)

    def test_changes_on_one_consultancy_leave_the_same_id_on_another_alone(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_staff = abc.log_in_as(Role.COUNSELLOR)
        xyz_staff = xyz.log_in_as(Role.COUNSELLOR)
        assert abc_staff.pk == xyz_staff.pk

        abc.client.patch(ME_URL, {"name": "Sita Sharma", "colour_mode": "dark"}, format="json")
        renewed = abc.client.post(SIGN_OUT_OTHERS_URL).json()
        use_access_token(abc.client, renewed["access"])
        answer = abc.client.post(
            MY_PW_URL,
            {"current_password": TEST_PASSWORD, "new_password": "Kathmandu!2027"},
            format="json",
        )

        assert answer.status_code == status.HTTP_200_OK
        assert xyz.client.get(ME_URL).status_code == status.HTTP_200_OK
        with tenant_context(xyz.consultancy):
            kept = Staff.objects.get(pk=xyz_staff.pk)
        assert (kept.name, kept.colour_mode) == (xyz_staff.name, "light")
        assert kept.check_password(TEST_PASSWORD)

    def test_signed_in_devices_never_include_another_consultancys(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.COUNSELLOR)
        xyz_staff = xyz.add_staff(Role.COUNSELLOR)  # Same primary key.
        for _ in range(3):
            xyz.tokens_for(xyz_staff)

        assert len(abc.client.get(MY_DEVICES_URL).json()) == 1

    def test_the_same_browser_on_two_consultancies_never_shares_devices(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        """Issue 71: one browser, same Staff id on A and B; each list shows only its own."""
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_staff = abc.add_staff(Role.COUNSELLOR)
        xyz_staff = xyz.add_staff(Role.COUNSELLOR)  # Same primary key.
        browser_key = "0123456789abcdef0123456789abcdef"
        firefox = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"
        for _ in range(2):
            xyz_answer = xyz.new_client().post(
                "/api/auth/login/",
                {"email": xyz_staff.email, "password": TEST_PASSWORD},
                format="json",
                HTTP_X_BROWSER_AGENT=firefox,
                HTTP_X_BROWSER_KEY=browser_key,
            )
        abc_answer = abc.new_client().post(
            "/api/auth/login/",
            {"email": abc_staff.email, "password": TEST_PASSWORD},
            format="json",
            HTTP_X_BROWSER_AGENT="Mozilla/5.0 (Windows NT 10.0) Chrome/130.0",
            HTTP_X_BROWSER_KEY=browser_key,
        )
        use_access_token(abc.client, abc_answer.json()["access"])
        use_access_token(xyz.client, xyz_answer.json()["access"])

        abc_devices = abc.client.get(MY_DEVICES_URL).json()
        xyz_devices = xyz.client.get(MY_DEVICES_URL).json()

        assert [device["name"] for device in abc_devices] == ["Chrome on Windows"]
        assert [device["name"] for device in xyz_devices] == ["Firefox on Linux"]
        # A's login on that browser never ended B's.
        assert xyz_devices[0]["logged_out_at"] is None


class TestConsultancyStatusIsolation:
    """One Consultancy's Status never limits, or reveals anything about, another (issue 42)."""

    @pytest.mark.parametrize("value", ["past_due", "suspended", "terminated"])
    def test_a_restricted_consultancy_never_restricts_another(
        self,
        make_consultancy: ConsultancyFactory,
        platform_site: PlatformContext,
        value: str,
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        platform_site.log_in()
        if value in {"past_due", "suspended"}:
            paid_until = (timezone.localdate() + timedelta(days=30)).isoformat()
            platform_site.client.post(
                f"/api/platform/consultancies/{abc.consultancy.public_id}/status/",
                {"status": "active", "paid_until": paid_until},
                format="json",
            )
        reply = platform_site.client.post(
            f"/api/platform/consultancies/{abc.consultancy.public_id}/status/",
            {"status": value, "reason": "Unpaid"},
            format="json",
        )
        assert reply.status_code == status.HTTP_200_OK

        renamed = xyz.client.patch(ME_URL, {"name": "Gita Rai"}, format="json")
        context = xyz.client.get(CONTEXT_URL).json()["consultancy"]

        assert renamed.status_code == status.HTTP_200_OK
        assert (context["status"], context["status_message"]) == ("evaluation", None)
        assert abc.client.patch(ME_URL, {"name": "Sita"}, format="json").status_code in {
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        }

    def test_a_token_from_a_working_consultancy_is_refused_on_a_restricted_one(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz_tokens = xyz.tokens_for(xyz.add_staff(Role.ADMIN))
        abc.consultancy.status = "suspended"
        abc.consultancy.save(update_fields=["status"])
        client = abc.new_client()
        use_access_token(client, xyz_tokens.access)

        assert client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert client.get("/api/students/").status_code == status.HTTP_401_UNAUTHORIZED


class TestSupportAccessIsolation:
    """A Platform Admin's visit to one office never reaches another (issue 27)."""

    def test_a_visit_to_one_office_cant_read_or_change_another(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        theirs = xyz.add_staff(Role.ADMIN, email="admin@xyz.test")
        visit = open_as_platform_admin(platform_site, abc)
        client = xyz.new_client()
        use_access_token(client, visit.access)

        for response in (
            client.get(CONTEXT_URL),
            client.get("/api/students/"),
            client.patch(ME_URL, {"name": "x"}, format="json"),
            client.put("/api/support-access/modules/", {}, format="json"),
            client.post(f"/api/support-access/staff/{theirs.public_id}/reset-code/", format="json"),
            client.post(f"/api/support-access/staff/{theirs.public_id}/sign-out/", format="json"),
            client.post("/api/support-access/export/", format="json"),
        ):
            assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert mail.outbox == []
        renew = xyz.new_client().post(
            "/api/auth/refresh/", {"refresh": visit.refresh}, format="json"
        )
        assert renew.status_code == status.HTTP_401_UNAUTHORIZED

    def test_an_entry_code_for_one_office_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        platform_site.log_in()
        code = start_visit(platform_site.client, abc, "Help")["entry_code"]

        response = xyz.new_client().post(
            "/api/support-access/enter/", {"code": code}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "access" not in response.json()

    def test_another_offices_staff_cant_use_the_platform_only_actions(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        ours = abc.add_staff(Role.ADMIN)
        client = abc.new_client()
        use_access_token(client, xyz.tokens_for(xyz.add_staff(Role.ADMIN)).access)

        for response in (
            client.put("/api/support-access/modules/", {}, format="json"),
            client.post(f"/api/support-access/staff/{ours.public_id}/reset-code/", format="json"),
            client.post(f"/api/support-access/staff/{ours.public_id}/sign-out/", format="json"),
            client.post("/api/support-access/export/", format="json"),
        ):
            assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert mail.outbox == []


class TestCentralLoginIsolation:
    """Issue 44: no office address serves the Central login.

    One office's Staff changes never touch another office's entries.
    """

    def test_the_central_login_is_not_on_any_office_address(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.add_staff(Role.COUNSELLOR, email="sita@example.com")

        response = abc.new_client().post(
            "/api/central-login/", {"email": "sita@example.com"}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert mail.outbox == []

    def test_deactivating_in_one_office_keeps_the_other_offices_entry(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        leaver = abc.add_staff(Role.COUNSELLOR, email="sita@example.com")
        xyz.add_staff(Role.COUNSELLOR, email="sita@example.com")

        response = abc.client.post(f"/api/staff/{leaver.public_id}/deactivate/", format="json")

        assert response.status_code == status.HTTP_200_OK
        activate_public_schema()
        offices = [office.schema_name for office in offices_for("sita@example.com")]
        assert offices == ["xyzoverseas"]

    def test_an_admin_cant_deactivate_another_offices_staff_by_id(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        theirs = xyz.add_staff(Role.COUNSELLOR, email="hari@example.com")

        abc.client.post(f"/api/staff/{theirs.public_id}/deactivate/", format="json")

        activate_public_schema()
        offices = [office.schema_name for office in offices_for("hari@example.com")]
        assert offices == ["xyzoverseas"]


class TestSearchIsolation:
    """Issue 49: the global search finds only this office's Students and classes."""

    URL = "/api/search/"

    def _office(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str, label: str
    ) -> tuple[ConsultancyContext, Staff]:
        """An office with classes on, its Admin in, and a Student "Sita <label>" in a class."""
        office = make_consultancy(name, subdomain)
        switch_modules(office.consultancy, {Module.BATCHES})
        office.log_in_as(Role.ADMIN)
        teacher = office.add_staff(Role.TEACHER)
        today = timezone.localdate()
        with tenant_context(office.consultancy):
            student = Student.objects.create(
                name=f"Sita {label}", phone="9841000001", country="australia", source="walk_in"
            )
            batch = Batch.objects.create(
                name=f"Sita {label} class",
                test_type="ielts",
                teacher=teacher,
                time_slot="7 AM",
                starts_on=today,
                ends_on=today,
            )
            BatchMembership.objects.create(batch=batch, student=student)
        activate_public_schema()
        return office, teacher

    def test_each_office_finds_only_its_own_students_and_classes(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz, _ = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ")

        body = xyz.client.get(self.URL, {"q": "sita"}).json()

        assert [row["name"] for row in body["students"]] == ["Sita XYZ"]
        assert body["student_count"] == 1
        assert [row["name"] for row in body["batches"]] == ["Sita XYZ class"]

    def test_a_teacher_with_the_same_id_finds_only_their_own_office(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz, teacher = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ")
        use_access_token(xyz.client, xyz.tokens_for(teacher).access)

        body = xyz.client.get(self.URL, {"q": "sita"}).json()

        assert [row["name"] for row in body["students"]] == ["Sita XYZ"]
        assert "ABC" not in str(body)

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _ = self._office(make_consultancy, "ABC Education", "abcedu", "ABC")
        xyz, _ = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas", "XYZ")
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        response = intruder.get(self.URL, {"q": "sita"})

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "XYZ" not in response.content.decode()


class TestExportIsolation:
    """Issue 51: an export holds only its own office's rows and is recorded only there."""

    URL = "/api/exports/"

    def _export(self, office: ConsultancyContext, which: str = "students") -> str:
        response = office.client.post(self.URL, {"list": which, "filters": {}}, format="json")
        assert response.status_code == status.HTTP_200_OK, response.content
        content: bytes = response.content
        return content.decode("utf-8")

    @pytest.mark.parametrize("which", ["students", "applications", "visas"])
    def test_each_office_exports_only_its_own_rows(
        self, make_consultancy: ConsultancyFactory, which: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita, application_id = _student_with_coe(abc, "Sita Karki")
        abc.client.post(_visas_url(sita), {"application": application_id}, format="json")
        xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json")

        assert "Sita Karki" not in self._export(xyz, which)
        assert "Sita Karki" in self._export(abc, which)

    def test_export_records_stay_in_their_own_office(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        self._export(abc)
        self._export(abc, "visas")

        assert xyz.client.get(self.URL).json()["count"] == 0
        assert abc.client.get(self.URL).json()["count"] == 2

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)
        xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json")
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        posted = intruder.post(self.URL, {"list": "students", "filters": {}}, format="json")
        listed = intruder.get(self.URL)

        assert posted.status_code == listed.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Ram Thapa" not in posted.content.decode()
        with tenant_context(xyz.consultancy):
            assert not ExportRecord.objects.exists()


class TestOfficeNetworkIsolation:
    """Issue 55: each office's Office Networks and Clock-in switch are its own."""

    URL = "/api/office-networks/"

    def _offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext]:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            switch_modules(office.consultancy, {Module.CLOCK_IN})
            office.log_in_as(Role.ADMIN)
        return abc, xyz

    def test_each_office_sees_only_its_own_networks(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        abc.client.post(self.URL, {"label": "ABC wifi", "network": "110.44.115.82"}, format="json")

        assert xyz.client.get(self.URL).json() == {
            "networks": [],
            "limits": {"max_networks": 20, "ipv4_widest_prefix": 24, "ipv6_widest_prefix": 48},
        }
        assert [n["label"] for n in abc.client.get(self.URL).json()["networks"]] == ["ABC wifi"]

    def test_the_same_network_can_be_in_two_offices_and_only_covers_its_own(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        body = {"label": "Shared building", "network": "110.44.115.0/24"}
        assert abc.client.post(self.URL, body, format="json").status_code == 201
        xyz.client.defaults["HTTP_X_CLIENT_IP"] = "110.44.115.82"

        assert xyz.client.get(f"{self.URL}current/").json()["covered"] is False
        assert xyz.client.post(self.URL, body, format="json").status_code == 201

    def test_an_ipv6_group_saved_in_one_office_never_covers_another_offices_staff(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        abc.client.defaults["HTTP_X_CLIENT_IP"] = "2400:1a00:b030:9::1"
        suggested = abc.client.get(f"{self.URL}current/").json()["suggested_network"]
        abc.client.post(self.URL, {"label": "ABC wifi", "network": suggested}, format="json")
        with tenant_context(xyz.consultancy):
            OfficeNetwork.objects.create(label="XYZ wifi", network="110.44.115.0/24")
        xyz.client.defaults["HTTP_X_CLIENT_IP"] = "2400:1a00:b030:9::2"

        current = xyz.client.get(f"{self.URL}current/").json()
        xyz.log_in_as(Role.COUNSELLOR)
        clocked = xyz.client.post("/api/clock/in/", format="json")

        assert current["covered"] is False
        assert current["suggested_network"] == suggested
        assert clocked.status_code == status.HTTP_403_FORBIDDEN

    def test_another_office_cant_remove_one_by_id(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        added = abc.client.post(
            self.URL, {"label": "ABC wifi", "network": "110.44.115.82"}, format="json"
        ).json()

        response = xyz.client.delete(f"{self.URL}{added['id']}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert len(abc.client.get(self.URL).json()["networks"]) == 1

    def test_clock_in_on_for_one_office_is_off_for_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        switch_modules(abc.consultancy, {Module.CLOCK_IN})
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)

        assert abc.client.get(self.URL).status_code == status.HTTP_200_OK
        assert xyz.client.get(self.URL).status_code == status.HTTP_404_NOT_FOUND

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        posted = intruder.post(
            self.URL, {"label": "Intruder", "network": "27.34.20.9"}, format="json"
        )
        listed = intruder.get(self.URL)
        current = intruder.get(f"{self.URL}current/")

        assert posted.status_code == listed.status_code == current.status_code == 401
        with tenant_context(xyz.consultancy):
            assert not OfficeNetwork.objects.exists()


class TestClockInIsolation:
    """Issue 56: each office's Work Sessions, corrections and Working Day are its own."""

    def _offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext]:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            switch_modules(office.consultancy, {Module.CLOCK_IN})
            with tenant_context(office.consultancy):
                OfficeNetwork.objects.create(label="Wifi", network="110.44.115.0/24")
            office.client.defaults["HTTP_X_CLIENT_IP"] = "110.44.115.82"
        return abc, xyz

    def _forgotten(self, office: ConsultancyContext) -> tuple[Staff, WorkSession]:
        """A Counsellor of `office` who forgot to clock out the day before yesterday."""
        person = office.add_staff(Role.COUNSELLOR, email=f"sita@{office.host}")
        start = timezone.now() - timedelta(days=2)
        if timezone.localdate(start).weekday() == 5:  # Saturday can't be corrected
            start -= timedelta(days=1)
        with tenant_context(office.consultancy):
            session = WorkSession.objects.create(
                staff=person, clocked_in_at=start, not_clocked_out=True
            )
        return person, session

    def test_each_office_sees_only_its_own_people_and_hours(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        abc_person, _ = self._forgotten(abc)
        xyz.log_in_as(Role.ADMIN)

        team = xyz.client.get("/api/clock/team/").json()["rows"]
        now = xyz.client.get("/api/clock/team/now/").json()["people"]
        month = xyz.client.get("/api/clock/month/", {"staff": str(abc_person.public_id)})

        assert [row["staff"]["name"] for row in team] == ["Test Admin"]
        assert team[0]["missing_clock_outs"] == 0
        assert all(person["state"] == "not_in" for person in now)
        assert month.status_code == status.HTTP_404_NOT_FOUND  # ABC's person is unknown in XYZ

    def test_another_office_cant_correct_a_session_or_a_person_by_id(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        abc_person, abc_session = self._forgotten(abc)
        xyz.log_in_as(Role.ADMIN)

        set_out = xyz.client.post(
            f"/api/clock/sessions/{abc_session.public_id}/clock-out/",
            {"clocked_out_at": "17:00", "reason": "Intruder"},
            format="json",
        )
        added = xyz.client.post(
            f"/api/clock/staff/{abc_person.public_id}/missed-time/",
            {"day": "2026-01-05", "from": "09:00", "to": "10:00", "reason": "Intruder"},
            format="json",
        )

        assert set_out.status_code == status.HTTP_404_NOT_FOUND
        assert added.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert WorkSession.objects.get().clocked_out_at is None
            assert not WorkSessionChange.objects.exists()

    def test_clocking_in_one_office_records_nothing_in_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        abc.log_in_as(Role.COUNSELLOR)
        xyz.log_in_as(Role.ADMIN)

        assert abc.client.post("/api/clock/in/", format="json").status_code == 201
        xyz.client.put("/api/clock/working-day/", {"minutes": 420}, format="json")

        assert xyz.client.get("/api/clock/team/now/").json()["clocked_in_count"] == 0
        with tenant_context(xyz.consultancy):
            assert not WorkSession.objects.exists()
        with tenant_context(abc.consultancy):
            assert not WorkingDayChange.objects.exists()
        assert abc.client.get("/api/clock/month/").json()["working_day_minutes"] == 480

    def test_clock_in_on_for_one_office_is_off_for_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        switch_modules(abc.consultancy, {Module.CLOCK_IN})
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)

        assert abc.client.get("/api/clock/").status_code == status.HTTP_200_OK
        assert xyz.client.get("/api/clock/").status_code == status.HTTP_404_NOT_FOUND

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        intruder = api_client(xyz.host, HTTP_X_CLIENT_IP="110.44.115.82")
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        clocked = intruder.post("/api/clock/in/", format="json")
        team = intruder.get("/api/clock/team/")
        working_day = intruder.put("/api/clock/working-day/", {"minutes": 420}, format="json")

        assert clocked.status_code == team.status_code == working_day.status_code == 401
        with tenant_context(xyz.consultancy):
            assert not WorkSession.objects.exists()
            assert not WorkingDayChange.objects.exists()


class TestWeeklyDaysOffIsolation:
    """Weekly days off (asked 2026-11-17): one office's choice never changes another's."""

    def test_one_offices_days_off_leave_another_on_saturday(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            switch_modules(office.consultancy, {Module.CLOCK_IN})
            office.log_in_as(Role.ADMIN)

        abc.client.put("/api/clock/days-off/", {"weekly_off": "both"}, format="json")

        assert abc.client.get("/api/clock/working-day/").json()["weekly_off"] == "both"
        assert xyz.client.get("/api/clock/working-day/").json()["weekly_off"] == "saturday"

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        switch_modules(xyz.consultancy, {Module.CLOCK_IN})
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        response = intruder.put("/api/clock/days-off/", {"weekly_off": "none"}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        xyz.log_in_as(Role.ADMIN)
        assert xyz.client.get("/api/clock/working-day/").json()["weekly_off"] == "saturday"


class TestHolidayIsolation:
    """Holidays (asked 2026-11-16): one office's holidays never reach or change another's."""

    def _offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext]:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            switch_modules(office.consultancy, {Module.CLOCK_IN})
            office.log_in_as(Role.ADMIN)
        return abc, xyz

    def test_one_offices_holidays_are_not_listed_or_counted_in_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        today = timezone.localdate()
        abc.client.post(
            "/api/clock/holidays/",
            {"name": "Dashain", "dates": [today.isoformat()]},
            format="json",
        )

        listed = xyz.client.get("/api/clock/holidays/").json()
        month = xyz.client.get("/api/clock/month/").json()

        assert listed == {"holidays": []}
        assert all(day["holiday"] is None for day in month["days"])
        with tenant_context(xyz.consultancy):
            assert not Holiday.objects.exists()

    def test_another_office_cant_remove_one(self, make_consultancy: ConsultancyFactory) -> None:
        abc, xyz = self._offices(make_consultancy)
        with tenant_context(abc.consultancy):
            dashain = Holiday.objects.create(day=timezone.localdate(), name="Dashain")

        response = xyz.client.delete(f"/api/clock/holidays/{dashain.public_id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert Holiday.objects.filter(pk=dashain.pk).exists()

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        with tenant_context(xyz.consultancy):
            theirs = Holiday.objects.create(day=timezone.localdate(), name="Dashain")

        added = intruder.post(
            "/api/clock/holidays/", {"name": "Intruder", "dates": ["2026-10-21"]}, format="json"
        )
        listed = intruder.get("/api/clock/holidays/")
        removed = intruder.delete(
            "/api/clock/holidays/", {"ids": [str(theirs.public_id)]}, format="json"
        )

        assert added.status_code == listed.status_code == status.HTTP_401_UNAUTHORIZED
        assert removed.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            assert list(Holiday.objects.values_list("name", flat=True)) == ["Dashain"]

    def test_another_office_cant_remove_a_group(self, make_consultancy: ConsultancyFactory) -> None:
        abc, xyz = self._offices(make_consultancy)
        today = timezone.localdate()
        with tenant_context(abc.consultancy):
            dashain = Holiday.objects.create(day=today, name="Dashain")
        with tenant_context(xyz.consultancy):
            own = Holiday.objects.create(day=today, name="Tihar")

        response = xyz.client.delete(
            "/api/clock/holidays/",
            {"ids": [str(own.public_id), str(dashain.public_id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert Holiday.objects.filter(pk=dashain.pk).exists()
        with tenant_context(xyz.consultancy):
            assert Holiday.objects.filter(pk=own.pk).exists()

    def test_a_day_taken_in_one_office_is_free_in_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        today = timezone.localdate()
        with tenant_context(abc.consultancy):
            Holiday.objects.create(day=today, name="Dashain")

        response = xyz.client.post(
            "/api/clock/holidays/", {"name": "Tihar", "dates": [today.isoformat()]}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        with tenant_context(abc.consultancy):
            assert list(Holiday.objects.values_list("name", flat=True)) == ["Dashain"]

    def test_a_counsellor_reads_only_their_own_offices_holidays(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        with tenant_context(abc.consultancy):
            Holiday.objects.create(day=timezone.localdate(), name="ABC only")
        xyz.log_in_as(Role.COUNSELLOR)

        listed = xyz.client.get("/api/clock/holidays/")

        assert listed.status_code == status.HTTP_200_OK
        assert listed.json() == {"holidays": []}


class TestWorkFromHomeIsolation:
    """Issue 87: one office's Work From Home dates never reach or let anyone clock in another."""

    def _offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext]:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            switch_modules(office.consultancy, {Module.CLOCK_IN})
            with tenant_context(office.consultancy):
                OfficeNetwork.objects.create(label="Wifi", network="110.44.115.0/24")
            office.client.defaults["HTTP_X_CLIENT_IP"] = "27.34.20.9"
        return abc, xyz

    def _dates(self, office: ConsultancyContext, state: str) -> tuple[Staff, WorkFromHome]:
        person = office.add_staff(Role.COUNSELLOR, email=f"sita@{office.host}")
        with tenant_context(office.consultancy):
            dates = WorkFromHome.objects.create(
                staff=person,
                starts_on=timezone.localdate(),
                ends_on=None,
                reason="ABC private reason",
                status=state,
                asked_by_staff=person,
                asked_by_name=person.name,
            )
        return person, dates

    def test_another_office_cant_see_answer_end_or_cancel_them(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        abc_person, waiting = self._dates(abc, "waiting")
        xyz.log_in_as(Role.ADMIN)

        seen = xyz.client.get(f"/api/clock/staff/{abc_person.public_id}/work-from-home/")
        answers = [
            xyz.client.post(
                f"/api/clock/work-from-home/{waiting.public_id}/{action}/", format="json"
            )
            for action in ("approve", "decline", "end", "cancel")
        ]
        mine = xyz.client.get("/api/clock/work-from-home/").json()
        waiting_home = xyz.client.get("/api/home/waiting/").json()

        assert seen.status_code == status.HTTP_404_NOT_FOUND
        assert [a.status_code for a in answers] == [404, 404, 404, 404]
        assert "ABC private reason" not in str(mine)
        assert waiting_home["work_from_home_requests"] == {"count": 0, "first_staff_id": None}
        with tenant_context(abc.consultancy):
            assert WorkFromHome.objects.get().status == "waiting"

    def test_dates_in_one_office_never_let_anyone_clock_in_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        self._dates(abc, "approved")
        # The same email and id in XYZ, with no dates there.
        xyz_person = xyz.add_staff(Role.COUNSELLOR, email=f"sita@{xyz.host}")
        use_access_token(xyz.client, xyz.tokens_for(xyz_person).access)

        response = xyz.client.post("/api/clock/in/", format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        with tenant_context(xyz.consultancy):
            assert not WorkSession.objects.exists()

    def test_asking_in_one_office_tells_only_its_own_admins(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        xyz.add_staff(Role.ADMIN)
        abc.add_staff(Role.ADMIN)
        abc.log_in_as(Role.COUNSELLOR)
        today = timezone.localdate().isoformat()

        asked = abc.client.post(
            "/api/clock/work-from-home/",
            {"starts_on": today, "reason": "Plumber"},
            format="json",
        )

        assert asked.status_code == status.HTTP_201_CREATED
        with tenant_context(xyz.consultancy):
            assert not WorkFromHome.objects.exists()
            assert not Notification.objects.exists()
        with tenant_context(abc.consultancy):
            assert Notification.objects.count() == 1

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._offices(make_consultancy)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        listed = intruder.get("/api/clock/work-from-home/")
        set_ = intruder.post(
            "/api/clock/staff/1/work-from-home/",
            {"starts_on": timezone.localdate().isoformat(), "reason": "x"},
            format="json",
        )

        assert listed.status_code == set_.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            assert not WorkFromHome.objects.exists()


class TestTransferRequestIsolation:
    """Issue 57: A's Staff can't ask for, answer, cancel or list B's Transfer Requests."""

    def _office(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str
    ) -> tuple[ConsultancyContext, Staff, Staff, Student, TransferRequest]:
        """An office with two counsellors, a Student of the first, and a waiting request."""
        office = make_consultancy(name, subdomain)
        mine = office.add_staff(Role.COUNSELLOR, email=f"mine@{office.host}")
        asker = office.add_staff(Role.COUNSELLOR, email=f"asker@{office.host}")
        with tenant_context(office.consultancy):
            student = Student.objects.create(
                name=f"{name} Student", phone="9841000001", assigned_counsellor=mine
            )
            waiting = TransferRequest.objects.create(
                student=student, asked_by=asker, reason="Same family."
            )
        return office, mine, asker, student, waiting

    def test_each_office_lists_only_its_own_requests(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        self._office(make_consultancy, "ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)

        page = xyz.client.get("/api/transfer-requests/", {"box": "all"}).json()

        assert page["count"] == 0
        assert xyz.client.get("/api/transfer-requests/count/").json() == {"count": 0}

    def test_another_office_cant_answer_or_cancel_by_id(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        _abc, _mine, _asker, _student, waiting = self._office(
            make_consultancy, "ABC Education", "abcedu"
        )
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.ADMIN)

        for action in ("approve", "decline", "cancel"):
            response = xyz.client.post(
                f"/api/transfer-requests/{waiting.public_id}/{action}/", {}, format="json"
            )
            assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(_abc.consultancy):
            assert TransferRequest.objects.get().status == TransferRequestStatus.WAITING

    def test_another_office_cant_ask_for_a_student_by_id(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, _mine, _asker, student, _waiting = self._office(
            make_consultancy, "ABC Education", "abcedu"
        )
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz.log_in_as(Role.COUNSELLOR)

        response = xyz.client.post(
            f"/api/students/{student.public_id}/transfer-requests/",
            {"reason": "Intruder"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert TransferRequest.objects.count() == 1
        with tenant_context(xyz.consultancy):
            assert not TransferRequest.objects.exists()

    def test_the_same_ids_in_two_offices_stay_apart(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_mine, _a, abc_student, abc_waiting = self._office(
            make_consultancy, "ABC Education", "abcedu"
        )
        xyz, xyz_mine, _x, _xs, xyz_waiting = self._office(
            make_consultancy, "XYZ Overseas", "xyzoverseas"
        )
        assert abc_waiting.pk == xyz_waiting.pk
        use_access_token(xyz.client, xyz.tokens_for(xyz_mine).access)

        response = xyz.client.post(
            f"/api/transfer-requests/{xyz_waiting.public_id}/approve/", {}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["student"]["name"] == "XYZ Overseas Student"
        with tenant_context(abc.consultancy):
            assert TransferRequest.objects.get().status == TransferRequestStatus.WAITING
            assert Student.objects.get(pk=abc_student.pk).assigned_counsellor_id == abc_mine.pk
            assert not Notification.objects.exists()

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_mine, *_ = self._office(make_consultancy, "ABC Education", "abcedu")
        xyz, _m, _a, _s, xyz_waiting = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc_mine).access)

        listed = intruder.get("/api/transfer-requests/")
        approved = intruder.post(
            f"/api/transfer-requests/{xyz_waiting.public_id}/approve/", {}, format="json"
        )

        assert listed.status_code == approved.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(xyz.consultancy):
            assert TransferRequest.objects.get().status == TransferRequestStatus.WAITING


class TestPlatformAccountIsolation:
    """Issue 59: the Platform Admin's Account answers only on the Platform, to Platform Admins.

    No office's Staff (from A or B, on any address) can read or change a Platform Admin, and
    the endpoints don't exist on an office's address at all.
    """

    ME = "/api/platform/me/"
    PASSWORD = "/api/platform/me/password/"  # noqa: S105  # a path, not a secret

    def test_staff_of_either_office_can_never_reach_it(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        ops = platform_site.add_admin()
        abc_admin = abc.add_staff(Role.ADMIN)
        access = abc.tokens_for(abc_admin).access
        clients = [abc.new_client(), xyz.new_client(), platform_site.new_client()]
        change = {"name": "Taken Over", "email": "x@abc.test", "current_password": TEST_PASSWORD}

        for client in clients:
            use_access_token(client, access)
            assert client.get(self.ME).status_code in {401, 403, 404}
            assert client.patch(self.ME, change, format="json").status_code in {401, 403, 404}
            password_change = {"current_password": TEST_PASSWORD, "new_password": "Taken-0ver!"}
            response = client.post(self.PASSWORD, password_change, format="json")
            assert response.status_code in {401, 403, 404}

        activate_public_schema()
        ops.refresh_from_db()
        assert (ops.name, ops.email) == ("Platform Ops", "ops@platform.test")
        assert not ops.check_password("Taken-0ver!")


class TestTwoFactorIsolation:
    """Issue 61: two-step login answers only on the Platform, to Platform Admins.

    No office's Staff (from A or B, on any address) can read or switch a Platform Admin's
    two-step login, and a challenge made on the Platform is never accepted on an office.
    """

    MINE = "/api/platform/me/two-factor/"
    VERIFY = "/api/platform/auth/two-factor/verify/"
    SETUP = "/api/platform/auth/two-factor/setup/"

    def test_staff_of_either_office_can_never_reach_it(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        ops = platform_site.add_admin()
        access = abc.tokens_for(abc.add_staff(Role.ADMIN)).access
        clients = [abc.new_client(), xyz.new_client(), platform_site.new_client()]

        for client in clients:
            use_access_token(client, access)
            assert client.get(self.MINE).status_code in {401, 403, 404}
            response = client.post(self.MINE, {"action": "on"}, format="json")
            assert response.status_code in {401, 403, 404}

        activate_public_schema()
        ops.refresh_from_db()
        assert not ops.two_factor_required

    def test_a_platform_challenge_never_works_on_an_office(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        ops = platform_site.add_admin()
        activate_public_schema()
        PlatformAdmin.objects.filter(pk=ops.pk).update(two_factor_required=True)
        login = platform_site.client.post(
            "/api/platform/auth/login/",
            {"email": ops.email, "password": PLATFORM_TEST_PASSWORD},
            format="json",
        )
        challenge = login.json()["challenge"]

        for path in (self.SETUP, self.VERIFY, "/api/auth/login/"):
            response = abc.client.post(path, {"challenge": challenge}, format="json")
            assert response.status_code in {400, 401, 404}
            assert '"access"' not in response.content.decode()
        # Still usable on the Platform: the office never touched it.
        setup = platform_site.client.post(self.SETUP, {"challenge": challenge}, format="json")
        assert setup.status_code == 200


class TestStaffTwoFactorIsolation:
    """Issue 62: Staff two-step login stays inside its own office.

    A challenge made on A is refused on B, an Admin of A can't switch or reset anyone at B, and
    nobody's two-step login can be read through another office.
    """

    @staticmethod
    def _required(office: ConsultancyContext, staff: Staff) -> bool:
        with tenant_context(office.consultancy):
            return bool(Staff.objects.get(pk=staff.pk).two_factor_required)

    def test_a_challenge_from_a_is_refused_on_b(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")
        xyz.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=sita.pk).update(two_factor_required=True)
        login = abc.client.post(
            "/api/auth/login/", {"email": sita.email, "password": TEST_PASSWORD}, format="json"
        )
        challenge = login.json()["challenge"]

        for path in ("/api/auth/two-factor/setup/", "/api/auth/two-factor/verify/"):
            response = xyz.client.post(
                path, {"challenge": challenge, "code": "123456"}, format="json"
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert '"access"' not in response.content.decode()
        # Still usable on A: B never touched it.
        setup = abc.client.post(
            "/api/auth/two-factor/setup/", {"challenge": challenge}, format="json"
        )
        assert setup.status_code == status.HTTP_200_OK

    def test_an_admin_of_a_cant_switch_or_reset_staff_of_b(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        admin = abc.log_in_as(Role.ADMIN)
        target = xyz.add_staff(Role.COUNSELLOR)
        xyz_with_a_token = xyz.new_client()
        use_access_token(xyz_with_a_token, abc.tokens_for(admin).access)

        switch = xyz_with_a_token.patch(
            f"/api/staff/{target.public_id}/two-factor/", {"required": True}, format="json"
        )
        reset = xyz_with_a_token.post(
            f"/api/staff/{target.public_id}/two-factor/reset/", format="json"
        )
        on_own_host = abc.client.patch(
            f"/api/staff/{target.public_id}/two-factor/", {"required": True}, format="json"
        )

        assert switch.status_code == status.HTTP_401_UNAUTHORIZED
        assert reset.status_code == status.HTTP_401_UNAUTHORIZED
        assert on_own_host.status_code in {
            status.HTTP_200_OK,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        }
        assert self._required(xyz, target) is False

    def test_nobodys_two_step_login_is_readable_through_another_office(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        sita = abc.add_staff(Role.COUNSELLOR)
        access = abc.tokens_for(sita).access
        client = xyz.new_client()
        use_access_token(client, access)

        mine = client.get("/api/me/two-factor/")
        employees = client.get("/api/staff/")

        assert mine.status_code == status.HTTP_401_UNAUTHORIZED
        assert employees.status_code == status.HTTP_401_UNAUTHORIZED


class TestConsultancySetupIsolation:
    """Issue 60: setting up one office never touches another's data.

    Only Platform Admins on the Platform can create offices or start a setup again.
    """

    CREATE = "/api/platform/consultancies/"

    def test_a_new_office_is_built_in_its_own_schema_only(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc_admin = abc.add_staff(Role.ADMIN)
        platform_site.log_in()
        body = {
            "name": "Everest Education",
            "subdomain": "everest",
            "contact_email": "info@everest.test",
        }

        created = platform_site.client.post(self.CREATE, body, format="json").json()
        run_queued_jobs()
        added = platform_site.client.post(
            f"{self.CREATE}{created['id']}/admins/",
            {"name": "Sita Sharma", "email": abc_admin.email},
            format="json",
        )
        assert added.status_code == status.HTTP_201_CREATED

        with tenant_context(abc.consultancy):
            assert Staff.objects.count() == 1
            assert Staff.objects.get().status == "active"
        activate_public_schema()
        assert {office.schema_name for office in offices_for(abc_admin.email)} == {
            "abcedu",
            "everest",
        }
        assert created["host"] != abc.host

    def test_staff_of_either_office_can_never_create_or_start_a_setup_again(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        access = abc.tokens_for(abc.add_staff(Role.ADMIN)).access
        body = {"name": "X", "subdomain": "everest", "contact_email": "info@everest.test"}

        for client in (abc.new_client(), xyz.new_client(), platform_site.new_client()):
            use_access_token(client, access)
            assert client.post(self.CREATE, body, format="json").status_code in {401, 403, 404}
            retry = f"{self.CREATE}{xyz.consultancy.public_id}/retry-setup/"
            assert client.post(retry).status_code in {401, 403, 404}
            add_admin = f"{self.CREATE}{xyz.consultancy.public_id}/admins/"
            admin_body = {"name": "Intruder", "email": "intruder@x.test"}
            assert client.post(add_admin, admin_body, format="json").status_code in {
                401,
                403,
                404,
            }

        activate_public_schema()
        assert not Consultancy.objects.filter(schema_name="everest").exists()


class TestStageChangesIsolation:
    """Issue 82: a Student's Stage changes (for the Activity timeline) stay in their office."""

    def test_another_office_never_sees_them(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()["id"]
        ram = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        assert sita != ram  # Every Student has their own UUID.
        abc.client.post(f"{STUDENTS_URL}{sita}/stage/", {"stage": "counselling"}, format="json")
        abc.client.post(
            f"{STUDENTS_URL}{sita}/stage/",
            {"stage": "lost", "lost_reason": "Only ABC knows this."},
            format="json",
        )

        xyz_body = xyz.client.get(f"{STUDENTS_URL}{ram}/").json()
        abc_body = abc.client.get(f"{STUDENTS_URL}{sita}/").json()

        assert xyz_body["stage_changes"] == []
        assert [c["after"] for c in abc_body["stage_changes"]] == ["counselling", "lost"]

    def test_a_token_from_another_office_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_admin = abc.log_in_as(Role.ADMIN)
        sita = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()["id"]
        abc.client.post(f"{STUDENTS_URL}{sita}/stage/", {"stage": "counselling"}, format="json")
        intruder = xyz.new_client()
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        response = intruder.get(f"{STUDENTS_URL}{sita}/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "stage_changes" not in response.json()


class TestWhoDidEachActionIsolation:
    """Issue 83: who marked a Task done, and the new Activity entries, stay in their office."""

    def test_another_office_never_sees_them(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_admin = abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)
        sita = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()["id"]
        ram = xyz.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()["id"]
        assert sita != ram  # Every Student has their own UUID.
        with tenant_context(abc.consultancy):
            task = Task.objects.create(
                title="Only ABC knows this",
                due_on=timezone.localdate(),
                assignee=abc_admin,
                created_by=abc_admin,
                created_by_name=abc_admin.name,
                student=Student.objects.get(public_id=sita),
            )
        response = abc.client.patch(f"/api/tasks/{task.public_id}/", {"done": True}, format="json")
        assert response.status_code == status.HTTP_200_OK

        xyz_history = xyz.client.get(f"{STUDENTS_URL}{ram}/history/").json()["results"]
        abc_history = abc.client.get(f"{STUDENTS_URL}{sita}/history/").json()["results"]

        assert [e["kind"] for e in xyz_history] == []
        assert [e["after"] for e in abc_history if e["kind"] == "task_done"] == [
            "Only ABC knows this"
        ]
        with tenant_context(xyz.consultancy):
            assert not Task.objects.exclude(done_by_name="").exists()
            assert not HistoryEntry.objects.filter(kind=HistoryKind.TASK_DONE).exists()

    def test_another_office_cannot_mark_its_task_done(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_admin = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            task = Task.objects.create(
                title="Collect bank statement",
                due_on=timezone.localdate(),
                assignee=abc_admin,
                created_by=abc_admin,
                created_by_name=abc_admin.name,
            )
        intruder = xyz.new_client()
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        response = intruder.patch(f"/api/tasks/{task.public_id}/", {"done": True}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(abc.consultancy):
            stored = Task.objects.get(pk=task.pk)
        assert (stored.is_done, stored.done_by_id, stored.done_by_name) == (False, None, "")


class TestStaffProgressIsolation:
    """Issue 84: one office's Staff Progress never counts or lists another office's changes."""

    def test_the_same_id_in_another_office_sees_none_of_it(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_admin = abc.log_in_as(Role.ADMIN)
        xyz_admin = xyz.log_in_as(Role.ADMIN)
        assert abc_admin.pk == xyz_admin.pk  # Each schema numbers its own Staff.
        sita = abc.client.post(STUDENTS_URL, _lead_body("Sita Karki"), format="json").json()["id"]
        today = timezone.localdate().isoformat()
        period = {"from": today, "to": today}

        abc_answer = abc.client.get(f"/api/staff/{abc_admin.public_id}/progress/", period).json()
        xyz_answer = xyz.client.get(f"/api/staff/{xyz_admin.public_id}/progress/", period).json()

        abc_counts = {card["count"]: card["value"] for card in abc_answer["cards"]}
        xyz_counts = {card["count"]: card["value"] for card in xyz_answer["cards"]}
        assert (abc_counts["visitors_registered"], abc_counts["new_leads"]) == (1, 1)
        assert [e["student"]["id"] for d in abc_answer["days"] for e in d["entries"]] == [sita]
        assert set(xyz_counts.values()) == {0}
        assert xyz_answer["days"] == []
        assert "Sita Karki" not in str(xyz_answer)

    def test_a_token_from_another_office_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc_admin = abc.log_in_as(Role.ADMIN)
        intruder = xyz.new_client()
        use_access_token(intruder, abc.tokens_for(abc_admin).access)
        today = timezone.localdate().isoformat()

        response = intruder.get(
            f"/api/staff/{abc_admin.public_id}/progress/", {"from": today, "to": today}
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestInboxIsolation:
    """Issue 86: A's Staff can't read B's conversations or write to B's Staff."""

    def _office(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str
    ) -> tuple[ConsultancyContext, Staff, Staff]:
        """An office where the counsellor wrote to the Admin."""
        office = make_consultancy(name, subdomain)
        admin = office.add_staff(Role.ADMIN, email=f"admin@{office.host}")
        counsellor = office.add_staff(Role.COUNSELLOR, email=f"counsellor@{office.host}")
        with tenant_context(office.consultancy):
            InboxMessage.objects.create(
                sender=counsellor, recipient=admin, text=f"{name} private note"
            )
        return office, admin, counsellor

    def test_the_same_ids_in_another_office_see_none_of_it(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        _abc, abc_admin, abc_counsellor = self._office(make_consultancy, "ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz_admin = xyz.add_staff(Role.ADMIN, email=f"admin@{xyz.host}")
        xyz_counsellor = xyz.add_staff(Role.COUNSELLOR, email=f"counsellor@{xyz.host}")
        assert (xyz_admin.pk, xyz_counsellor.pk) == (abc_admin.pk, abc_counsellor.pk)
        use_access_token(xyz.client, xyz.tokens_for(xyz_admin).access)

        listed = xyz.client.get("/api/inbox/").json()
        checked = xyz.client.get("/api/inbox/check/").json()
        thread = xyz.client.get(f"/api/inbox/{abc_counsellor.public_id}/").json()
        own_number = xyz.client.get(f"/api/inbox/{xyz_counsellor.public_id}/").json()

        assert listed == {"conversations": []}
        assert checked == {"unread": 0, "latest": None}
        assert own_number["messages"] == []
        assert "ABC" not in str(own_number)
        assert thread == {"detail": "Not found."}  # ABC's person is unknown in XYZ

    def test_writing_reaches_only_the_same_office(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_admin, abc_counsellor = self._office(make_consultancy, "ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        xyz_admin = xyz.add_staff(Role.ADMIN, email=f"admin@{xyz.host}")
        use_access_token(xyz.client, xyz.tokens_for(xyz_admin).access)

        response = xyz.client.post(
            f"/api/inbox/{abc_counsellor.public_id}/", {"text": "Hello"}, format="json"
        )

        # The id names nobody in XYZ (only its Admin exists there), so nothing is sent anywhere.
        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert InboxMessage.objects.count() == 1
        with tenant_context(xyz.consultancy):
            assert InboxMessage.objects.count() == 0
        assert abc_admin.pk == xyz_admin.pk

    def test_a_token_from_another_office_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_admin, abc_counsellor = self._office(make_consultancy, "ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        intruder = xyz.new_client()
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        for url in ("/api/inbox/", "/api/inbox/check/", f"/api/inbox/{abc_counsellor.public_id}/"):
            assert intruder.get(url).status_code == status.HTTP_401_UNAUTHORIZED
        response = intruder.post(
            f"/api/inbox/{abc_counsellor.public_id}/", {"text": "Hello"}, format="json"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestPageSwipeIsolation:
    """Swipe between pages (asked 2026-11-16): one office's switch never changes another's."""

    def test_switching_it_on_in_one_office_leaves_another_off(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        abc.log_in_as(Role.ADMIN)
        xyz.log_in_as(Role.ADMIN)

        abc.client.patch("/api/me/", {"page_swipe": True}, format="json")

        assert abc.client.get("/api/context/").json()["page_swipe"] is True
        assert xyz.client.get("/api/context/").json()["page_swipe"] is False

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        response = intruder.patch("/api/me/", {"page_swipe": True}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        xyz.log_in_as(Role.ADMIN)
        assert xyz.client.get("/api/context/").json()["page_swipe"] is False


class TestFeesByTypeIsolation:
    """Ticket 08: Fees by type adds up only this office's money, and its Fee Types only."""

    def _two_offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext, str]:
        """Both offices with an Admin in; ABC has a Fee Type XYZ hasn't, with money on it."""
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            switch_modules(office.consultancy, {Module.ACCOUNTS})
            office.log_in_as(Role.ADMIN)
        abc_only = str(
            abc.client.post("/api/fee-types/", {"name": "ABC only"}, format="json").json()["id"]
        )
        student = abc.client.post(STUDENTS_URL, _lead_body("Ram Thapa"), format="json").json()
        for fee_type in (abc_only, _fee_type_id(abc)):
            created = abc.client.post(
                f"/api/students/{student['id']}/fees/",
                {"fee_type": fee_type, "amount": 40000},
                format="json",
            )
            assert created.status_code == status.HTTP_201_CREATED
        return abc, xyz, abc_only

    def test_each_office_adds_up_only_its_own_money(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz, _ = self._two_offices(make_consultancy)

        mine = abc.client.get("/api/accounts/fees-by-type/").json()
        theirs = xyz.client.get("/api/accounts/fees-by-type/").json()

        assert mine["totals"]["remaining"] == 80000
        assert theirs == {
            "totals": {
                "to_collect": 0,
                "received": 0,
                "remaining": 0,
                "lost_not_counted": 0,
                "owing": 0,
            },
            "fee_types": [],
        }

    def test_another_offices_fee_type_is_not_found(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        _, xyz, abc_only = self._two_offices(make_consultancy)

        response = xyz.client.get(f"/api/accounts/fees-by-type/{abc_only}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_each_offices_own_fee_type_lists_only_its_students(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz, _ = self._two_offices(make_consultancy)
        xyz_url = f"/api/accounts/fees-by-type/{_fee_type_id(xyz)}/"
        abc_url = f"/api/accounts/fees-by-type/{_fee_type_id(abc)}/"

        assert xyz.client.get(xyz_url).json()["results"] == []
        assert abc.client.get(xyz_url).status_code == status.HTTP_404_NOT_FOUND
        assert [row["student"]["name"] for row in abc.client.get(abc_url).json()["results"]] == [
            "Ram Thapa"
        ]

    def test_a_token_from_one_consultancy_is_refused_on_another(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz, _ = self._two_offices(make_consultancy)
        intruder = api_client(xyz.host)
        abc_admin = abc.add_staff(Role.ADMIN, email="second-admin@abc.test")
        use_access_token(intruder, abc.tokens_for(abc_admin).access)

        answers = [
            intruder.get("/api/accounts/fees-by-type/"),
            intruder.get(f"/api/accounts/fees-by-type/{_fee_type_id(xyz)}/"),
        ]

        assert [answer.status_code for answer in answers] == [401, 401]


_COURSE_PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"


def _course_with_file(office: ConsultancyContext, name: str) -> tuple[str, str]:
    """A Course in `office` with one Course File uploaded by its Admin: (course id, file id)."""
    university = office.client.post(
        "/api/catalogue/universities/",
        {"name": f"{name} University", "country": "australia"},
        format="json",
    ).json()
    year = timezone.localdate().year + 1
    course = office.client.post(
        f"/api/catalogue/universities/{university['id']}/courses/",
        {"name": f"{name} course", "level": "master", "intakes": [f"{year}-03"]},
        format="json",
    ).json()
    upload = SimpleUploadedFile("brochure.pdf", _COURSE_PDF, content_type="application/pdf")
    file = office.client.post(
        f"/api/catalogue/courses/{course['id']}/files/",
        {"file": upload, "name": f"{name} brochure"},
        format="multipart",
    )
    assert file.status_code == status.HTTP_201_CREATED
    return str(course["id"]), str(file.json()["id"])


class TestCourseFileIsolation:
    """Ticket 10: Course Files are kept, listed, opened and binned only in their own office."""

    def _two_offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext]:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            office.log_in_as(Role.ADMIN)
        return abc, xyz

    def test_files_are_kept_under_each_offices_own_prefix(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)

        _course_with_file(abc, "ABC")
        _course_with_file(xyz, "XYZ")

        keys = sorted(FakeDocumentStorage.files)
        assert [key.split("/", 2)[:2] for key in keys] == [
            ["abcedu", "course-files"],
            ["xyzoverseas", "course-files"],
        ]

    def test_another_offices_course_file_is_never_reached(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        course_id, file_id = _course_with_file(abc, "ABC")
        with tenant_context(xyz.consultancy):
            assert not Course.objects.filter(public_id=course_id).exists()

        answers = [
            xyz.client.get(f"/api/catalogue/course-files/{file_id}/link/"),
            xyz.client.patch(
                f"/api/catalogue/course-files/{file_id}/", {"name": "Mine"}, format="json"
            ),
            xyz.client.delete(f"/api/catalogue/course-files/{file_id}/"),
            xyz.client.post(
                f"/api/catalogue/courses/{course_id}/files/",
                {
                    "file": SimpleUploadedFile("x.pdf", _COURSE_PDF, "application/pdf"),
                    "name": "Intruder",
                },
                format="multipart",
            ),
        ]

        assert [answer.status_code for answer in answers] == [404] * 4
        mine = abc.client.get(f"/api/catalogue/course-files/{file_id}/link/")
        assert mine.status_code == status.HTTP_200_OK

    def test_the_same_id_changes_only_its_own_office(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _, abc_file = _course_with_file(abc, "ABC")
        _, xyz_file = _course_with_file(xyz, "XYZ")
        assert abc_file != xyz_file  # Each Course File has its own UUID (ADR 0027).

        xyz.client.patch(
            f"/api/catalogue/course-files/{xyz_file}/", {"name": "Renamed"}, format="json"
        )
        xyz.client.delete(f"/api/catalogue/course-files/{xyz_file}/")

        abc_bin = abc.client.get("/api/documents/bin/").json()
        xyz_bin = xyz.client.get("/api/documents/bin/").json()
        assert abc_bin["count"] == 0
        assert [row["course_file"]["name"] for row in xyz_bin["results"]] == ["Renamed"]
        abc_course = abc.client.get("/api/catalogue/universities/").json()["results"][0]
        assert abc_course["courses"][0]["files"][0]["name"] == "ABC brochure"

    def test_another_offices_bin_is_never_reached(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _, file_id = _course_with_file(abc, "ABC")
        abc.client.delete(f"/api/catalogue/course-files/{file_id}/")
        bin_url = f"/api/documents/bin/course-files/{file_id}/"

        answers = [
            xyz.client.get(f"{bin_url}link/"),
            xyz.client.post(f"{bin_url}restore/", {}, format="json"),
            xyz.client.delete(bin_url),
        ]

        assert [answer.status_code for answer in answers] == [404] * 3
        assert abc.client.get("/api/documents/bin/").json()["count"] == 1

    def test_a_token_from_another_office_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        course_id, file_id = _course_with_file(abc, "ABC")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN, "a2@abc.test")).access)

        answers = [
            intruder.get(f"/api/catalogue/course-files/{file_id}/link/"),
            intruder.delete(f"/api/catalogue/course-files/{file_id}/"),
            intruder.post(f"/api/documents/bin/course-files/{file_id}/restore/", {}, format="json"),
            intruder.post(f"/api/catalogue/courses/{course_id}/files/", {}, format="multipart"),
        ]

        assert [answer.status_code for answer in answers] == [401] * 4


def _type_with_example(office: ConsultancyContext, name: str = "Bank Statement") -> tuple[str, str]:
    """A Document Type of `office` with one example uploaded by its Admin: (type id, example id)."""
    types = office.client.get("/api/configuration/document-types/").json()["results"]
    type_id = next(str(t["id"]) for t in types if t["name"] == name)
    upload = SimpleUploadedFile("sample.pdf", _COURSE_PDF, content_type="application/pdf")
    example = office.client.post(
        f"/api/configuration/document-types/{type_id}/examples/",
        {"file": upload},
        format="multipart",
    )
    assert example.status_code == status.HTTP_201_CREATED
    return type_id, str(example.json()["id"])


class TestDocumentExampleIsolation:
    """Ticket 11: Document Examples are kept, opened and binned only in their own office."""

    def _two_offices(
        self, make_consultancy: ConsultancyFactory
    ) -> tuple[ConsultancyContext, ConsultancyContext]:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        for office in (abc, xyz):
            office.log_in_as(Role.ADMIN)
        return abc, xyz

    def test_examples_are_kept_under_each_offices_own_prefix(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)

        _type_with_example(abc)
        _type_with_example(xyz)

        keys = sorted(FakeDocumentStorage.files)
        assert [key.split("/", 2)[:2] for key in keys] == [
            ["abcedu", "document-examples"],
            ["xyzoverseas", "document-examples"],
        ]

    def test_another_offices_example_is_never_reached(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _, example_id = _type_with_example(abc)
        one = f"/api/configuration/document-examples/{example_id}/"

        answers = [
            xyz.client.get(f"/api/documents/examples/{example_id}/link/"),
            xyz.client.delete(one),
            xyz.client.post(
                f"{one}replace/",
                {"file": SimpleUploadedFile("x.pdf", _COURSE_PDF, "application/pdf")},
                format="multipart",
            ),
        ]

        assert [answer.status_code for answer in answers] == [404] * 3
        mine = abc.client.get(f"/api/documents/examples/{example_id}/link/")
        assert mine.status_code == status.HTTP_200_OK

    def test_the_same_ids_change_only_their_own_office(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        abc_type, abc_example = _type_with_example(abc)
        xyz_type, xyz_example = _type_with_example(xyz)
        assert abc_type != xyz_type and abc_example != xyz_example  # their own UUIDs

        xyz.client.put(
            f"/api/configuration/document-types/{xyz_type}/instruction/",
            {"instruction": "XYZ only"},
            format="json",
        )
        xyz.client.delete(f"/api/configuration/document-examples/{xyz_example}/")

        abc_types = abc.client.get("/api/configuration/document-types/").json()["results"]
        [abc_statement] = [t for t in abc_types if t["id"] == abc_type]
        assert abc_statement["instruction"] == ""
        assert [e["id"] for e in abc_statement["examples"]] == [abc_example]
        assert abc.client.get("/api/documents/bin/").json()["count"] == 0
        xyz_bin = xyz.client.get("/api/documents/bin/").json()
        assert [row["kind"] for row in xyz_bin["results"]] == ["document_example"]

    def test_another_offices_bin_is_never_reached(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        _, example_id = _type_with_example(abc)
        abc.client.delete(f"/api/configuration/document-examples/{example_id}/")
        bin_url = f"/api/documents/bin/document-examples/{example_id}/"

        answers = [
            xyz.client.get(f"{bin_url}link/"),
            xyz.client.post(f"{bin_url}restore/", {}, format="json"),
            xyz.client.delete(bin_url),
        ]

        assert [answer.status_code for answer in answers] == [404] * 3
        assert abc.client.get("/api/documents/bin/").json()["count"] == 1

    def test_a_token_from_another_office_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, xyz = self._two_offices(make_consultancy)
        type_id, example_id = _type_with_example(abc)
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN, "a2@abc.test")).access)
        bin_url = f"/api/documents/bin/document-examples/{example_id}/"

        answers = [
            intruder.put(
                f"/api/configuration/document-types/{type_id}/instruction/",
                {"instruction": "x"},
                format="json",
            ),
            intruder.post(
                f"/api/configuration/document-types/{type_id}/examples/", {}, format="multipart"
            ),
            intruder.delete(f"/api/configuration/document-examples/{example_id}/"),
            intruder.get(f"/api/documents/examples/{example_id}/link/"),
            intruder.post(f"{bin_url}restore/", {}, format="json"),
        ]

        assert [answer.status_code for answer in answers] == [401] * 5


_OTHER_PDF = b"%PDF-1.7\n% from the other office\n%%EOF\n"


class TestEmailAttachmentIsolation:
    """Ticket 12: an email carries only this office's files, and only that Student's Documents."""

    def _office(
        self, make_consultancy: ConsultancyFactory, name: str, subdomain: str
    ) -> tuple[ConsultancyContext, Student]:
        """An office (Admin in, Email on, a contact email) with one Student who has an email."""
        office = make_consultancy(name, subdomain)
        switch_modules(office.consultancy, {Module.COMMUNICATION})
        set_office_details(
            office.consultancy, OfficeDetails(contact_email=f"info@{subdomain}.test")
        )
        office.log_in_as(Role.ADMIN)
        with tenant_context(office.consultancy):
            student = Student.objects.create(
                name=f"{name} Student",
                phone="9841000001",
                email=f"student@{subdomain}.test",
                country="australia",
                source="walk_in",
            )
        return office, student

    def _passport_file(self, office: ConsultancyContext, student: Student, content: bytes) -> str:
        """A file of the Student's Passport, with its bytes in storage: its id."""
        with tenant_context(office.consultancy):
            document = Document.objects.create(
                student=student, document_type=DocumentType.objects.get(name="Passport")
            )
            file = DocumentFile.objects.create(
                document=document,
                file_name="passport.pdf",
                content_type="application/pdf",
                size=len(content),
                storage_key=f"{office.consultancy.schema_name}/documents/{student.public_id}/p.pdf",
            )
        FakeDocumentStorage.files[file.storage_key] = StoredFile(content, "application/pdf")
        return str(file.public_id)

    def _send(
        self, office: ConsultancyContext, student_id: object, attachments: list[dict[str, object]]
    ) -> int:
        body = {"subject": "Files", "body": "Dear Student,\n\nHere.", "attachments": attachments}
        response = office.client.post(f"{STUDENTS_URL}{student_id}/messages/", body, format="json")
        return int(response.status_code)

    def test_another_offices_files_are_never_attached(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_student = self._office(make_consultancy, "ABC Education", "abcedu")
        xyz, xyz_student = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas")
        _, file_id = _course_with_file(abc, "Deakin")
        _, example_id = _type_with_example(abc)
        document_file = self._passport_file(abc, abc_student, _COURSE_PDF)

        statuses = [
            self._send(xyz, xyz_student.public_id, [{"kind": "course_file", "id": file_id}]),
            self._send(
                xyz, xyz_student.public_id, [{"kind": "document_example", "id": example_id}]
            ),
            self._send(
                xyz, xyz_student.public_id, [{"kind": "document_file", "id": document_file}]
            ),
        ]

        assert statuses == [400] * 3
        assert mail.outbox == []
        with tenant_context(xyz.consultancy):
            assert not Message.objects.exists()

    def test_the_same_ids_attach_only_this_offices_own_files(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_student = self._office(make_consultancy, "ABC Education", "abcedu")
        xyz, xyz_student = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas")
        abc_file = self._passport_file(abc, abc_student, _COURSE_PDF)
        xyz_file = self._passport_file(xyz, xyz_student, _OTHER_PDF)
        assert abc_file != xyz_file

        theirs = self._send(xyz, xyz_student.public_id, [{"kind": "document_file", "id": abc_file}])
        status_code = self._send(
            xyz, xyz_student.public_id, [{"kind": "document_file", "id": xyz_file}]
        )

        assert theirs == status.HTTP_400_BAD_REQUEST
        assert status_code == status.HTTP_201_CREATED
        [sent] = mail.outbox
        [part] = [p for p in sent.message().walk() if p.get_content_disposition() == "attachment"]
        assert part.get_payload(decode=True) == _OTHER_PDF

    def test_each_office_lists_only_its_own_files(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_student = self._office(make_consultancy, "ABC Education", "abcedu")
        xyz, xyz_student = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas")
        _course_with_file(abc, "Deakin")
        _type_with_example(abc)
        self._passport_file(abc, abc_student, _COURSE_PDF)

        mine = xyz.client.get(f"{STUDENTS_URL}{xyz_student.public_id}/emails/attachable/").json()
        many = xyz.client.get("/api/messages/bulk/attachable/").json()
        theirs = xyz.client.get(f"{STUDENTS_URL}{abc_student.public_id}/emails/attachable/")

        assert (mine["course_files"], mine["document_examples"], mine["documents"]) == ([], [], [])
        assert (many["course_files"], many["document_examples"]) == ([], [])
        assert theirs.status_code == status.HTTP_404_NOT_FOUND

    def test_a_token_from_another_office_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc, abc_student = self._office(make_consultancy, "ABC Education", "abcedu")
        xyz, _ = self._office(make_consultancy, "XYZ Overseas", "xyzoverseas")
        _, file_id = _course_with_file(abc, "Deakin")
        intruder = api_client(xyz.host)
        use_access_token(intruder, abc.tokens_for(abc.add_staff(Role.ADMIN, "a2@abc.test")).access)

        answers = [
            intruder.get(f"{STUDENTS_URL}{abc_student.public_id}/emails/attachable/"),
            intruder.get("/api/messages/bulk/attachable/"),
            intruder.post(
                f"{STUDENTS_URL}{abc_student.public_id}/messages/",
                {
                    "subject": "x",
                    "body": "y",
                    "attachments": [{"kind": "course_file", "id": file_id}],
                },
                format="json",
            ),
        ]

        assert [answer.status_code for answer in answers] == [401] * 3
        assert mail.outbox == []


class TestOfficeRecordIdIsolation:
    """Ticket 18: employees and the office's records are found by UUID only in their own office.

    For every address that names one of them: office A's UUID is not found at B, B's own UUID
    works there, and A's record is unchanged (ADR 0027).
    """

    def test_every_address_finds_only_its_own_offices_records(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        theirs = office_with_every_record(abc)
        ours = office_with_every_record(xyz)  # its Admin is logged in

        for their_url, our_url in zip(
            record_addresses(theirs), record_addresses(ours), strict=True
        ):
            if "?" in their_url:
                continue  # filters: a UUID from elsewhere matches nothing (below)
            assert xyz.client.get(their_url).status_code == status.HTTP_404_NOT_FOUND, their_url
            assert xyz.client.get(our_url).status_code == status.HTTP_200_OK, our_url

    def test_changes_by_uuid_never_reach_another_office(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        theirs = office_with_every_record(abc)
        office_with_every_record(xyz)  # its Admin is logged in
        client = xyz.client

        refused = [
            client.patch(
                f"/api/staff/{theirs.counsellor.public_id}/", {"role": "admin"}, format="json"
            ),
            client.delete(f"/api/clock/holidays/{theirs.holiday.public_id}/"),
            client.delete(f"/api/accounts/expenses/{theirs.expense.public_id}/"),
            client.delete(f"/api/office-networks/{theirs.network.public_id}/"),
            client.patch(f"/api/tasks/{theirs.task.public_id}/", {"done": True}, format="json"),
            client.post(
                f"/api/clock/work-from-home/{theirs.dates.public_id}/approve/", {}, format="json"
            ),
            client.patch(
                f"/api/fee-types/{theirs.fee_type.public_id}/", {"hidden": True}, format="json"
            ),
        ]
        given = client.post(
            "/api/tasks/",
            {"title": "Call", "due_on": "2030-01-01", "assignee": str(theirs.counsellor.public_id)},
            format="json",
        )
        filtered = client.get(f"/api/students/?counsellor={theirs.counsellor.public_id}")

        assert [r.status_code for r in refused] == [status.HTTP_404_NOT_FOUND] * len(refused)
        assert given.status_code == status.HTTP_400_BAD_REQUEST
        assert filtered.json()["count"] == 0
        with tenant_context(abc.consultancy):
            assert Holiday.objects.filter(pk=theirs.holiday.pk).exists()
            assert Expense.objects.filter(pk=theirs.expense.pk).exists()
            assert OfficeNetwork.objects.filter(pk=theirs.network.pk).exists()
            assert Staff.objects.get(pk=theirs.counsellor.pk).role == Role.COUNSELLOR
            assert not Task.objects.get(pk=theirs.task.pk).is_done
            assert FeeType.objects.get(pk=theirs.fee_type.pk).hidden is False


class TestPlatformOnlyOnItsAddress:
    """The Platform's API answers on its own address only (first live fixes, ticket 14)."""

    @pytest.mark.parametrize(
        "path",
        [
            "auth/login/",
            "auth/login/next/",
            "auth/password-reset/",
            "auth/join/",
            "me/",
            "admins/",
            "consultancies/",
            "email/",
            "brand/logos/",
            "product-domain/",
            "support-visits/",
        ],
    )
    @pytest.mark.parametrize("method", ["get", "post"])
    def test_an_offices_address_has_no_platform(
        self, make_consultancy: ConsultancyFactory, method: str, path: str
    ) -> None:
        northwind = make_consultancy("Northwind Overseas", "northwind")

        client = northwind.new_client()

        response = getattr(client, method)(f"/api/platform/{path}", {}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
