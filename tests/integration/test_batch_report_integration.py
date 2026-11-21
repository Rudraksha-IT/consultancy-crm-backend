"""Reports: Classes, the Students per Batch and their Attendance between two dates (issue 30).

Spec 98, 99. Admin only; shown only while both the Reports and the test-prep Modules are on.
"""

from __future__ import annotations

from datetime import date

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from batches.models import Attendance, Batch, BatchMembership, Mark
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff, StaffStatus
from students.models import Student
from tests.conftest import ConsultancyContext, ConsultancyFactory
from tests.integration.test_batches_integration import day, make_batch
from tests.integration.test_reports_integration import ADMINS_ONLY, date_range
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

BATCHES_REPORT_URL = "/api/reports/batches/"
BOTH_MODULES = {Module.REPORTS, Module.BATCHES}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """An office with Reports and test-prep classes on, and its Admin logged in."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, BOTH_MODULES)
    office.log_in_as(Role.ADMIN)
    return office


@pytest.fixture
def teacher(abc: ConsultancyContext) -> Staff:
    """The Teacher of the classes."""
    return named_staff(abc, "Gita Lama", Role.TEACHER)


def store_marks(
    office: ConsultancyContext, batch: Batch, on: date, *pairs: tuple[Student, Mark]
) -> None:
    """Store marks directly, as if taken by the class's Teacher."""
    with tenant_context(office.consultancy):
        for student, mark in pairs:
            Attendance.objects.create(
                batch=batch, student=student, day=on, mark=mark, marked_by=batch.teacher
            )


def this_week() -> dict[str, str]:
    """The last seven days, today included."""
    return date_range(day(-6), day(0))


def report_rows(office: ConsultancyContext, query: dict[str, str]) -> list[dict[str, object]]:
    """The Classes report's rows for the query."""
    response = office.client.get(BATCHES_REPORT_URL, query)
    assert response.status_code == status.HTTP_200_OK
    rows: list[dict[str, object]] = response.json()["batches"]
    return rows


class TestAccess:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_an_admin_sees_the_report(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = abc.client.get(BATCHES_REPORT_URL, this_week())

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ADMINS_ONLY

    def test_an_admin_sees_the_report(self, abc: ConsultancyContext) -> None:
        response = abc.client.get(BATCHES_REPORT_URL, this_week())

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"batches": []}

    @pytest.mark.parametrize(
        "modules",
        [set(), {Module.REPORTS}, {Module.BATCHES}, {Module.ACCOUNTS, Module.COMMUNICATION}],
    )
    @pytest.mark.parametrize("role", list(Role))
    def test_without_both_modules_every_role_gets_not_found(
        self, abc: ConsultancyContext, modules: set[Module], role: Role
    ) -> None:
        switch_modules(abc.consultancy, modules)
        if role != Role.ADMIN:  # The fixture's Admin is already logged in.
            abc.log_in_as(role)

        response = abc.client.get(BATCHES_REPORT_URL, this_week())

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_switching_the_module_back_on_brings_the_classes_back(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        student = make_student(abc)
        batch = make_batch(abc, teacher, (student,))
        store_marks(abc, batch, day(0), (student, Mark.PRESENT))
        switch_modules(abc.consultancy, {Module.REPORTS})
        hidden = abc.client.get(BATCHES_REPORT_URL, this_week())
        switch_modules(abc.consultancy, BOTH_MODULES)

        rows = report_rows(abc, this_week())

        assert hidden.status_code == status.HTTP_404_NOT_FOUND
        assert [(row["id"], row["attended_percent"]) for row in rows] == [
            (str(batch.public_id), 100)
        ]

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        response = abc.new_client().get(BATCHES_REPORT_URL, this_week())

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        response = getattr(abc.client, method)(BATCHES_REPORT_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_deactivated_admin_is_refused(self, abc: ConsultancyContext) -> None:
        admin = named_staff(abc, "Old Admin", Role.ADMIN)
        log_in(abc, admin)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        response = abc.client.get(BATCHES_REPORT_URL, this_week())

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}


class TestClassesReport:
    def test_each_class_shows_its_students_teacher_and_share_attended(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        sita, ram, hari = (
            make_student(abc, name=name) for name in ("Sita Gurung", "Ram Thapa", "Hari KC")
        )
        ielts = make_batch(abc, teacher, (sita, ram, hari), name="IELTS Morning", test_type="ielts")
        # Sita 2 of 2 (Late counts), Ram 1 of 3 (Leave doesn't), Hari never marked: (100 + 33) / 2.
        store_marks(abc, ielts, day(-3), (ram, Mark.LEAVE))
        store_marks(abc, ielts, day(-2), (sita, Mark.PRESENT), (ram, Mark.ABSENT))
        store_marks(abc, ielts, day(-1), (sita, Mark.LATE), (ram, Mark.PRESENT))

        rows = report_rows(abc, this_week())

        assert rows == [
            {
                "id": str(ielts.public_id),
                "name": "IELTS Morning",
                "test_type": "ielts",
                "teacher": "Gita Lama",
                "students": 3,
                "attended_percent": 67,
            }
        ]

    def test_only_marks_between_the_dates_count(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        sita = make_student(abc)
        batch = make_batch(abc, teacher, (sita,), starts_on=day(-30))
        store_marks(abc, batch, day(-7), (sita, Mark.ABSENT))
        store_marks(abc, batch, day(-6), (sita, Mark.PRESENT))
        store_marks(abc, batch, day(0), (sita, Mark.PRESENT))
        store_marks(abc, batch, day(1), (sita, Mark.ABSENT))

        rows = report_rows(abc, this_week())

        assert rows[0]["attended_percent"] == 100

    def test_only_classes_running_between_the_dates_are_listed_by_name(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        make_batch(abc, teacher, name="Ended", starts_on=day(-40), ends_on=day(-7))
        make_batch(abc, teacher, name="Not started", starts_on=day(1), ends_on=day(30))
        make_batch(abc, teacher, name="PTE Day", starts_on=day(-40), ends_on=day(-6))
        make_batch(abc, teacher, name="IELTS Evening", starts_on=day(0), ends_on=day(30))

        rows = report_rows(abc, this_week())

        assert [(row["name"], row["students"], row["attended_percent"]) for row in rows] == [
            ("IELTS Evening", 0, None),
            ("PTE Day", 0, None),
        ]

    def test_a_student_who_left_still_counts_for_the_days_they_were_marked(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        sita, ram = make_student(abc), make_student(abc, name="Ram Thapa")
        batch = make_batch(abc, teacher, (sita, ram))
        store_marks(abc, batch, day(-1), (sita, Mark.PRESENT), (ram, Mark.ABSENT))
        with tenant_context(abc.consultancy):
            BatchMembership.objects.filter(batch=batch, student=ram).delete()

        rows = report_rows(abc, this_week())

        assert (rows[0]["students"], rows[0]["attended_percent"]) == (1, 50)

    def test_names_are_sent_as_typed(self, abc: ConsultancyContext, teacher: Staff) -> None:
        make_batch(abc, teacher, name=INJECTION_STRINGS[0])

        rows = report_rows(abc, this_week())

        assert rows[0]["name"] == INJECTION_STRINGS[0]

    @pytest.mark.parametrize(
        "query",
        [
            {},
            {"from": "2026-10-01"},
            {"to": "2026-10-01"},
            {"from": "2026-10-02", "to": "2026-10-01"},
            {"from": "2026/10/01", "to": "2026-10-31"},
            {"from": "2026-02-30", "to": "2026-03-01"},
            {"from": "1999-12-31", "to": "2026-03-01"},
            {"from": "2026-01-01", "to": "9999-12-31"},
            {"from": "2026-01-01", "to": "2026-01-31", "batch": "1"},
            *({"from": text, "to": "2026-01-31"} for text in INJECTION_STRINGS),
            *({"from": "2026-01-01", "to": text} for text in INJECTION_STRINGS),
        ],
    )
    def test_a_bad_range_is_refused_without_echoing_it(
        self, abc: ConsultancyContext, query: dict[str, str]
    ) -> None:
        response = abc.client.get(BATCHES_REPORT_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        for text in INJECTION_STRINGS:
            assert text not in response.content.decode()
