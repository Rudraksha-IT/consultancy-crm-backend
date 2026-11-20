"""CSV exports of the Students, Applications and Visas lists, and Export Records (31b, 31c)."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.statuses import ApplicationStatus, VisaStatus
from exports.exceptions import ExportRecordIsAppendOnlyError
from exports.models import ExportRecord
from fee_types.models import FeeType
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff
from students.models import Stage, TargetLevel
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_applications_list_integration import applied, course_at, student_named
from tests.integration.test_consultancy_status_integration import set_status
from tests.integration.test_payments_integration import make_payment
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)
from tests.integration.test_visas_list_integration import in_kathmandu, visa_for
from tests.support_access import open_as_platform_admin

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

URL = "/api/exports/"
BOM = "﻿"
ONLY_ADMINS = {"detail": "Only an Admin can export lists."}
STUDENT_COLUMNS = [
    "Name",
    "Mobile",
    "Email",
    "Stage",
    "Counsellor",
    "Wants to study in",
    "Target level",
    "How they found us",
    "First visit",
]
APPLICATION_COLUMNS = [
    "Student",
    "Email",
    "Mobile",
    "University",
    "Country",
    "Course",
    "Intake",
    "Status",
    "Counsellor",
    "Applied",
    "Added",
]
VISA_COLUMNS = [
    "Student",
    "Email",
    "Mobile",
    "Country",
    "University",
    "Course",
    "Intake",
    "Visa status",
    "Next appointment",
    "Documents",
    "Counsellor",
]
PAYMENT_COLUMNS = [
    "Date",
    "Student",
    "Fee Type",
    "Note",
    "Amount (NPR)",
    "Method",
    "Taken by",
]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def export(
    client: APIClient, list_name: object = "students", filters: object = None
) -> _MonkeyPatchedResponse:
    """POST an export of one list with these filters."""
    body = {"list": list_name, "filters": {} if filters is None else filters}
    return client.post(URL, body, format="json")


def csv_rows(response: _MonkeyPatchedResponse) -> list[list[str]]:
    """The CSV answer's rows, header first; it must start with the BOM."""
    assert response.status_code == status.HTTP_200_OK, response.content
    text = response.content.decode("utf-8")
    assert text.startswith(BOM)
    return list(csv.reader(io.StringIO(text[len(BOM) :])))


def records(office: ConsultancyContext) -> list[ExportRecord]:
    """Every Export Record of the office, newest first."""
    with tenant_context(office.consultancy):
        return list(ExportRecord.objects.order_by("-at", "-pk"))


class TestStudentsExport:
    def test_the_rows_are_the_filtered_list_with_fixed_columns(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        hari = named_staff(abc, "Hari Thapa")
        student_named(
            abc,
            "Sita Karki",
            hari,
            email="sita@example.com",
            target_level=TargetLevel.MASTER,
            note="Secret note",
            passport_number="PA1234567",
        )
        make_student(abc, None, Stage.LEAD, name="Ram Shah", phone="9841999999")
        log_in(abc, admin)

        rows = csv_rows(export(abc.client, "students", {"stage": "applying"}))

        assert rows[0] == STUDENT_COLUMNS
        assert rows[1:] == [
            [
                "Sita Karki",
                rows[1][1],
                "sita@example.com",
                "Applying",
                "Hari Thapa",
                "Australia",
                "Master",
                "Walk-in",
                timezone.localdate().isoformat(),
            ]
        ]
        assert "Secret note" not in str(rows)
        assert "PA1234567" not in str(rows)

    def test_the_same_rows_as_the_list_shows(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for name in ("Sita Karki", "Sita Rai", "Ram Shah"):
            student_named(abc, name)
        filters = {"q": "sita"}

        listed = [
            row["name"] for row in abc.client.get("/api/students/", filters).json()["results"]
        ]
        exported = [row[0] for row in csv_rows(export(abc.client, "students", filters))[1:]]

        assert exported == listed
        assert len(exported) == 2

    def test_my_students_means_the_admin_who_exports(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student_named(abc, "Mine", admin)
        student_named(abc, "Not mine", named_staff(abc, "Hari Thapa"))

        rows = csv_rows(export(abc.client, "students", {"counsellor": "me"}))

        assert [row[0] for row in rows[1:]] == ["Mine"]

    def test_a_file_name_with_the_list_and_today(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = export(abc.client, "students")

        assert response["Content-Type"] == "text/csv; charset=utf-8"
        today = timezone.localdate().isoformat()
        assert response["Content-Disposition"] == f'attachment; filename="students-{today}.csv"'
        assert response["Cache-Control"] == "no-store"

    def test_nepali_names_come_out_as_written(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student_named(abc, "सीता कार्की")

        assert csv_rows(export(abc.client))[1][0] == "सीता कार्की"


class TestFormulaCells:
    @pytest.mark.parametrize(
        "name",
        ['=HYPERLINK("x")', "+SUM(A1)", "-2+3", "@cmd", "\tTabbed", "\rReturned"],
    )
    def test_a_cell_that_starts_like_a_formula_is_prefixed(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student_named(abc, name)

        assert csv_rows(export(abc.client))[1][0] == f"'{name}"

    def test_other_cells_are_left_alone(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student_named(abc, 'Sita "Sunny", Karki')

        assert csv_rows(export(abc.client))[1][0] == 'Sita "Sunny", Karki'


class TestApplicationsExport:
    def test_the_rows_are_the_filtered_list(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        hari = named_staff(abc, "Hari Thapa")
        sita = student_named(abc, "Sita Karki", hari, email="sita@example.com")
        ram = student_named(abc, "Ram Shah", hari)
        applied(
            abc,
            sita,
            course_at(abc, "Deakin University", "australia"),
            "2027-02",
            status=ApplicationStatus.OFFER,
            applied_on=timezone.localdate(),
        )
        applied(abc, ram, course_at(abc, "Toronto College", "canada"), "2027-07")
        filters = {"status": ["offer"]}

        listed = abc.client.get("/api/applications/", filters).json()["results"]
        rows = csv_rows(export(abc.client, "applications", filters))

        assert rows[0] == APPLICATION_COLUMNS
        assert len(rows) - 1 == len(listed) == 1
        today = timezone.localdate().isoformat()
        assert rows[1] == [
            "Sita Karki",
            "sita@example.com",
            sita.phone,
            "Deakin University",
            "Australia",
            listed[0]["course"]["name"],
            "Feb 2027",
            "Offer",
            "Hari Thapa",
            today,
            today,
        ]

    def test_bad_filters_are_refused_as_the_list_refuses_them(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = export(abc.client, "applications", {"status": ["nope"]})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert records(abc) == []


class TestVisasExport:
    def test_the_rows_with_the_next_appointment_and_documents(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = student_named(abc, "Sita Karki", None, email="sita@example.com")
        at = in_kathmandu(3, 14, 30)
        visa_for(
            abc,
            sita,
            "Deakin University",
            "australia",
            "2027-02",
            biometrics_at=in_kathmandu(-1),
            interview_at=at,
        )
        ram = student_named(abc, "Ram Shah")
        visa_for(abc, ram, "Toronto College", "canada", status=VisaStatus.GRANTED)

        listed = abc.client.get("/api/visas/", {"country": "australia"}).json()["results"]
        rows = csv_rows(export(abc.client, "visas", {"country": "australia"}))

        assert rows[0] == VISA_COLUMNS
        documents = listed[0]["documents"]
        assert rows[1:] == [
            [
                "Sita Karki",
                "sita@example.com",
                sita.phone,
                "Australia",
                "Deakin University",
                listed[0]["course"]["name"],
                "Feb 2027",
                "Applied",
                f"Interview {at:%Y-%m-%d %H:%M}",
                f"{documents['done']}/{documents['total']}",
                "",
            ]
        ]

    def test_the_appointment_range_is_checked_like_the_list(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = export(
            abc.client, "visas", {"appointment_from": "2030-02-02", "appointment_to": "2030-02-01"}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestPaymentsExport:
    """The Admin Payments list as a CSV file, with each Payment's Fee Type (spec 78)."""

    def test_the_rows_are_the_filtered_list_with_the_fee_type(
        self, abc: ConsultancyContext
    ) -> None:
        switch_modules(abc.consultancy, {Module.ACCOUNTS})
        abc.log_in_as(Role.ADMIN)
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        sita = make_student(abc, name="Sita Karki")
        visa = make_payment(abc, sita, desk, amount=40000, fee_type="Visa fee", note="first half")
        make_payment(abc, sita, desk, amount=15000, fee_type="PTE class")
        with tenant_context(abc.consultancy):
            visa_id = FeeType.objects.get(name="Visa fee").public_id
        filters = {"fee_type": str(visa_id)}

        listed = abc.client.get("/api/payments/", filters).json()["results"]
        rows = csv_rows(export(abc.client, "payments", filters))

        assert rows[0] == PAYMENT_COLUMNS
        assert len(rows) - 1 == len(listed) == 1
        assert rows[1] == [
            visa.paid_on.isoformat(),
            "Sita Karki",
            "Visa fee",
            "first half",
            "40000",
            "Cash",
            "Sunita Shrestha",
        ]
        [record] = records(abc)
        assert record.filters_text == "Fee Type: Visa fee"

    def test_the_filters_in_words(self, abc: ConsultancyContext) -> None:
        switch_modules(abc.consultancy, {Module.ACCOUNTS})
        abc.log_in_as(Role.ADMIN)
        sita = make_student(abc, name="Sita Karki")
        filters = {
            "from": "2026-10-01",
            "to": "2026-10-31",
            "student": str(sita.public_id),
            "fee_type": "00000000-0000-4000-8000-000000000001",
            "q": "000",
        }

        export(abc.client, "payments", filters)

        [record] = records(abc)
        assert record.filters_text == (
            "From: 2026-10-01, To: 2026-10-31, Student: Sita Karki, "
            "Fee Type: A Fee Type since deleted, Search: 000"
        )

    def test_not_while_accounts_is_off(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = export(abc.client, "payments")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert records(abc) == []

    def test_bad_filters_are_refused_as_the_list_refuses_them(
        self, abc: ConsultancyContext
    ) -> None:
        switch_modules(abc.consultancy, {Module.ACCOUNTS})
        abc.log_in_as(Role.ADMIN)

        response = export(abc.client, "payments", {"fee_type": "visa"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert records(abc) == []


class TestRowLimit:
    def test_more_than_the_limit_is_refused_and_nothing_recorded(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student_named(abc, "Sita Karki")
        student_named(abc, "Ram Shah")

        with mock.patch("exports.services.MAX_ROWS", 1):
            response = export(abc.client)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": "That's more than 10,000 rows. Narrow the filters first."
        }
        assert records(abc) == []

    def test_exactly_the_limit_is_fine(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student_named(abc, "Sita Karki")

        with mock.patch("exports.services.MAX_ROWS", 1):
            assert len(csv_rows(export(abc.client))) == 2


class TestExportRecords:
    def test_each_export_is_recorded(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        hari = named_staff(abc, "Hari Thapa")
        student_named(abc, "Sita Karki", hari)

        export(abc.client, "students", {"stage": "applying", "counsellor": str(hari.public_id)})

        [record] = records(abc)
        assert record.list == "students"
        assert record.row_count == 1
        assert record.filters == {"stage": "applying", "counsellor": str(hari.public_id)}
        assert record.filters_text == "Stage: Applying, Counsellor: Hari Thapa"
        assert record.by_staff_id == admin.pk
        assert record.by_name == admin.name
        assert record.by_platform_admin is False

    def test_no_filters_is_described_as_everyone(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)

        export(abc.client, "visas")

        [record] = records(abc)
        assert record.filters_text == "No filters, everyone"
        assert record.row_count == 0
        assert record.by_name == admin.name

    def test_the_descriptions_of_each_list(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)

        export(abc.client, "applications", {"q": "deakin", "status": ["offer", "applied"]})
        export(abc.client, "students", {"counsellor": "me", "target_level": "phd"})
        export(
            abc.client,
            "visas",
            {"counsellor": "none", "appointment_from": "2030-02-01", "has_appointment": "true"},
        )

        assert [r.filters_text for r in records(abc)] == [
            "Counsellor: No counsellor yet, Appointments from: 2030-02-01, Appointment: Has a date",
            f"Counsellor: {admin.name}'s Students, Target level: PhD",
            "Search: deakin, Status: Offer or Applied",
        ]

    def test_a_platform_admin_is_named_as_such(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visitor = open_as_platform_admin(platform_site, abc).client

        rows = csv_rows(export(visitor, "students"))

        assert rows[0] == STUDENT_COLUMNS
        [record] = records(abc)
        assert record.by_staff_id is None
        assert record.by_name == "Platform Ops (Platform Admin)"
        assert record.by_platform_admin is True

    def test_records_are_append_only(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        export(abc.client)

        with tenant_context(abc.consultancy):
            record = ExportRecord.objects.get()
            with pytest.raises(ExportRecordIsAppendOnlyError):
                record.save()
            with pytest.raises(ExportRecordIsAppendOnlyError):
                record.delete()
            with pytest.raises(ExportRecordIsAppendOnlyError):
                ExportRecord.objects.update(row_count=5)
            with pytest.raises(ExportRecordIsAppendOnlyError):
                ExportRecord.objects.all().delete()

    def test_a_failure_while_writing_records_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student_named(abc, "Sita Karki")

        with mock.patch("exports.services._write_rows", side_effect=RuntimeError("boom")):
            response = export(abc.client)

        # Answered as any unexpected error (issue 58): a short message and a reference to quote.
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert set(response.json()) == {"detail", "reference"}
        assert "boom" not in response.content.decode()
        assert records(abc) == []


class TestExportRecordList:
    def test_admins_see_them_newest_first(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student_named(abc, "Sita Karki")
        export(abc.client, "students", {"stage": "applying"})
        export(abc.client, "visas")

        body = abc.client.get(URL).json()

        assert body["count"] == 2
        assert body["page"] == 1
        assert body["page_size"] == 25
        first, second = body["results"]
        assert first["list"] == "visas"
        assert second == {
            "id": second["id"],
            "list": "students",
            "filters": "Stage: Applying",
            "row_count": 1,
            "by": admin.name,
            "at": second["at"],
        }

    def test_pages(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(URL, {"page": "2"}).status_code == status.HTTP_404_NOT_FOUND
        assert abc.client.get(URL, {"page": "0"}).status_code == status.HTTP_400_BAD_REQUEST
        assert abc.client.get(URL, {"x": "1"}).status_code == status.HTTP_400_BAD_REQUEST

    def test_a_platform_admin_sees_them_too(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visitor = open_as_platform_admin(platform_site, abc).client
        export(visitor)

        body = visitor.get(URL).json()

        assert body["results"][0]["by"] == "Platform Ops (Platform Admin)"


class TestWhoMayExport:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_other_roles_are_refused_and_nothing_recorded(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)
        student_named(abc, "Sita Karki")

        response = export(abc.client)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMINS
        assert abc.client.get(URL).status_code == status.HTTP_403_FORBIDDEN
        assert records(abc) == []

    def test_a_deactivated_admins_token_is_refused(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status="deactivated")

        assert export(abc.client).status_code in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        }
        assert records(abc) == []

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert export(abc.new_client()).status_code == status.HTTP_401_UNAUTHORIZED
        assert abc.new_client().get(URL).status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_only_get_and_post(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestRefusedInput:
    @pytest.fixture(autouse=True)
    def admin(self, abc: ConsultancyContext) -> Staff:
        return abc.log_in_as(Role.ADMIN)

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"filters": {}},
            {"list": "students"},
            {"list": "clock", "filters": {}},
            {"list": "fees", "filters": {}},
            {"list": 1, "filters": {}},
            {"list": ["students"], "filters": {}},
            {"list": "students", "filters": []},
            {"list": "students", "filters": "stage=lead"},
            {"list": "students", "filters": None},
            {"list": "students", "filters": {}, "extra": "x"},
        ],
    )
    def test_a_bad_body_is_refused(self, abc: ConsultancyContext, body: object) -> None:
        response = abc.client.post(URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert records(abc) == []

    @pytest.mark.parametrize(
        ("list_name", "filters"),
        [
            ("students", {"unknown": "x"}),
            ("students", {"page": "2"}),
            ("students", {"stage": "nope"}),
            ("students", {"stage": 1}),
            ("students", {"counsellor": 5}),
            ("students", {"q": ["sita"]}),
            ("students", {"q": True}),
            ("students", {"q": "x" * 101}),
            ("students", {"q": {"a": "b"}}),
            ("applications", {"status": "submitted"}),
            ("applications", {"status": "draft"}),
            ("applications", {"status": [1]}),
            ("applications", {"intake": "Feb 2027"}),
            ("visas", {"appointment_from": "tomorrow"}),
            ("visas", {"has_appointment": True}),
            ("visas", {"interview_from": "2030-02-01"}),
            ("visas", {"q": "sita"}),
        ],
    )
    def test_bad_filters_are_refused_without_repeating_them(
        self, abc: ConsultancyContext, list_name: str, filters: dict[str, object]
    ) -> None:
        response = export(abc.client, list_name, filters)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "unknown" not in str(response.json())
        assert "nope" not in str(response.json())
        assert records(abc) == []

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_search_is_only_text(self, abc: ConsultancyContext, text: str) -> None:
        student_named(abc, "Sita Karki")

        rows = csv_rows(export(abc.client, "students", {"q": text}))

        assert rows == [STUDENT_COLUMNS]
        [record] = records(abc)
        assert record.filters_text == f"Search: {text}"

    def test_the_answer_to_a_bad_json_body_is_short(self, abc: ConsultancyContext) -> None:
        response = abc.client.post(URL, "{not json", content_type="application/json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestLogging:
    def test_ids_and_counts_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        abc.log_in_as(Role.ADMIN)
        student_named(abc, "Sita Karki", None, email="sita@example.com")
        json_logs.clear()

        export(abc.client, "students", {"q": "sita@example.com"})

        [entry] = json_logs.with_message("List exported")
        assert "list=students" in entry["message"]
        assert "rows=1" in entry["message"]
        assert "sita" not in json_logs.text()


class TestRestrictedConsultancy:
    @pytest.mark.parametrize("value", ["past_due", "suspended"])
    def test_an_export_is_a_change_and_is_refused(
        self, abc: ConsultancyContext, value: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        set_status(abc, value)

        response = export(abc.client)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert abc.client.get(URL).status_code == status.HTTP_200_OK
        assert records(abc) == []
