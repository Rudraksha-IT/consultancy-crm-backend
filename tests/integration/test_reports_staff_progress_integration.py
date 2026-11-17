"""Reports → Staff Progress: one table per Role, counted like the Progress tab (spec 95, 127).

Issue 85. GET /api/reports/staff-progress/?from=&to= answers the Counsellors table (Students now,
the Counsellor cards and Visas granted), the Front Desk table and, with test-prep classes on, the
Teachers table. The counts come from the same counting as /api/staff/<id>/progress/, so they
always match. Admin only, like every report; the Reports Module off answers 404.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff, StaffStatus
from students.models import Country, Stage
from tests.conftest import ConsultancyContext, ConsultancyFactory
from tests.integration.test_batches_integration import make_batch
from tests.integration.test_reports_integration import visa_for
from tests.integration.test_staff_progress_integration import (
    FIRST,
    NOVEMBER,
    application_by,
    at,
    marks_by,
    payment_by,
    stage_change,
    task_done_by,
    visa_by,
    visit_by,
)
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

URL = "/api/reports/staff-progress/"
COUNSELLOR_COLUMNS = [
    "students",
    "moved_forward",
    "applications_made",
    "visas_applied_for",
    "visas_granted",
    "students_lost",
    "tasks_done",
]
FRONT_DESK_COLUMNS = ["visitors_registered", "new_leads", "payments_taken"]
TEACHER_COLUMNS = ["classes_taken", "attendance_marked"]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """An office with Reports and test-prep classes on, and its Admin ("Test Admin") logged in."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.REPORTS, Module.BATCHES})
    office.log_in_as(Role.ADMIN)
    return office


def report(office: ConsultancyContext, period: dict[str, str] | None = None) -> dict[str, Any]:
    """GET the report and expect it."""
    response = office.client.get(URL, period or NOVEMBER)
    assert response.status_code == status.HTTP_200_OK, response.json()
    answer: dict[str, Any] = response.json()
    return answer


def row_of(answer: dict[str, Any], table: str, staff: Staff) -> dict[str, Any]:
    """One person's row in one table."""
    found: list[dict[str, Any]] = [row for row in answer[table] if row["id"] == staff.pk]
    assert len(found) == 1, answer[table]
    return found[0]


def names(answer: dict[str, Any], table: str) -> list[tuple[str, bool]]:
    """Each row's name and whether they still work here, in order."""
    return [(row["name"], row["is_active"]) for row in answer[table]]


class TestCounsellorsTable:
    def test_counts_the_changes_each_counsellor_made_in_the_period(
        self, abc: ConsultancyContext
    ) -> None:
        bikash = named_staff(abc, "Bikash Thapa")
        rina = named_staff(abc, "Rina Rai")
        sita = make_student(abc, bikash, Stage.APPLYING)
        hari = make_student(abc, bikash, Stage.VISA, name="Hari KC", phone="9841000002")
        make_student(abc, rina, Stage.LEAD, name="Gita KC", phone="9841000003")
        stage_change(abc, sita, bikash, ("lead", "counselling"), at("2026-11-02"))
        stage_change(abc, sita, bikash, ("counselling", "applying"), at("2026-11-03"))
        stage_change(abc, hari, bikash, ("applying", "lost"), at("2026-11-04"))
        stage_change(abc, sita, bikash, ("lead", "counselling"), at("2026-10-31", 23, 59))
        # Rina moved Bikash's Student: it counts for Rina, who made the change.
        stage_change(abc, sita, rina, ("applying", "offer"), at("2026-11-05"))
        application_by(abc, sita, bikash, at("2026-11-04"))
        visa_by(abc, hari, bikash, at("2026-11-20"))
        task_done_by(abc, bikash, at("2026-11-21"))
        visa_for(abc, hari, Country.AUSTRALIA, date(2026, 11, 22))
        visa_for(abc, sita, Country.AUSTRALIA, date(2026, 10, 22))  # before the period

        answer = report(abc)

        bikash_row = row_of(answer, "counsellors", bikash)
        assert list(bikash_row) == ["id", "name", "is_active", *COUNSELLOR_COLUMNS]
        assert bikash_row == {
            "id": bikash.pk,
            "name": "Bikash Thapa",
            "is_active": True,
            "students": 2,
            "moved_forward": 2,
            "applications_made": 1,
            "visas_applied_for": 1,
            "visas_granted": 1,
            "students_lost": 1,
            "tasks_done": 1,
        }
        rina_row = row_of(answer, "counsellors", rina)
        assert (rina_row["students"], rina_row["moved_forward"], rina_row["visas_granted"]) == (
            1,
            1,
            0,
        )

    def test_admins_and_counsellors_are_listed_and_people_who_left_only_with_something_to_show(
        self, abc: ConsultancyContext
    ) -> None:
        kept = named_staff(abc, "Old Counsellor")
        busy = named_staff(abc, "Busy Leaver")
        named_staff(abc, "Quiet Leaver")
        named_staff(abc, "Desk Person", Role.FRONT_DESK)
        named_staff(abc, "Class Teacher", Role.TEACHER)
        make_student(abc, kept)
        other = make_student(abc, name="Hari KC", phone="9841000002")
        stage_change(abc, other, busy, ("lead", "counselling"), at("2026-11-02"))
        invited = named_staff(abc, "Not Joined")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(name__in=["Old Counsellor", "Busy Leaver", "Quiet Leaver"]).update(
                status=StaffStatus.DEACTIVATED
            )
            Staff.objects.filter(pk=invited.pk).update(status=StaffStatus.INVITED)

        answer = report(abc)

        assert names(answer, "counsellors") == [
            ("Busy Leaver", False),
            ("Old Counsellor", False),
            ("Test Admin", True),
        ]

    def test_names_are_sent_as_typed(self, abc: ConsultancyContext) -> None:
        typed = abc.add_staff(Role.COUNSELLOR, email="typed@abc.test")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=typed.pk).update(name=INJECTION_STRINGS[0])

        answer = report(abc)

        assert INJECTION_STRINGS[0] in [row["name"] for row in answer["counsellors"]]


class TestFrontDeskTable:
    def test_visitors_new_leads_and_payments_they_took(self, abc: ConsultancyContext) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        other = named_staff(abc, "Maya Gurung", Role.FRONT_DESK)
        sita = make_student(abc)
        visit_by(abc, sita, desk, at("2026-11-02"), purpose=FIRST)
        visit_by(abc, sita, desk, at("2026-11-09"))
        visit_by(abc, sita, desk, at("2026-10-09"))  # before the period
        payment_by(abc, sita, desk, at("2026-11-03"), 18000)

        answer = report(abc)

        assert names(answer, "front_desk") == [("Maya Gurung", True), ("Sunita Shrestha", True)]
        assert row_of(answer, "front_desk", desk) == {
            "id": desk.pk,
            "name": "Sunita Shrestha",
            "is_active": True,
            "visitors_registered": 2,
            "new_leads": 1,
            "payments_taken": 1,
        }
        assert row_of(answer, "front_desk", other)["visitors_registered"] == 0


class TestTeachersTable:
    def test_classes_taken_and_attendance_marked(self, abc: ConsultancyContext) -> None:
        teacher = named_staff(abc, "Gita Lama", Role.TEACHER)
        sita = make_student(abc)
        hari = make_student(abc, name="Hari Thapa", phone="9841000002")
        batch = make_batch(abc, teacher, (sita, hari), starts_on=date(2026, 10, 1))
        marks_by(abc, batch, teacher, "2026-11-02", sita, hari)
        marks_by(abc, batch, teacher, "2026-11-03", sita)

        answer = report(abc)

        assert row_of(answer, "teachers", teacher) == {
            "id": teacher.pk,
            "name": "Gita Lama",
            "is_active": True,
            "classes_taken": 2,
            "attendance_marked": 3,
        }

    def test_no_teachers_table_while_classes_are_off(self, abc: ConsultancyContext) -> None:
        named_staff(abc, "Gita Lama", Role.TEACHER)
        switch_modules(abc.consultancy, {Module.REPORTS})

        answer = report(abc)

        assert answer["teachers"] == []
        assert answer["counsellors"] != []


class TestSameAsTheProgressTab:
    def test_every_row_matches_that_persons_progress_cards(self, abc: ConsultancyContext) -> None:
        admin = named_staff(abc, "Asha Admin", Role.ADMIN)
        bikash = named_staff(abc, "Bikash Thapa")
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        teacher = named_staff(abc, "Gita Lama", Role.TEACHER)
        log_in(abc, admin)
        sita = make_student(abc, bikash, Stage.APPLYING)
        stage_change(abc, sita, bikash, ("counselling", "applying"), at("2026-11-03"))
        stage_change(abc, sita, admin, ("applying", "lost"), at("2026-11-04"))
        application_by(abc, sita, admin, at("2026-11-04"))
        visa_by(abc, sita, bikash, at("2026-11-05"))
        task_done_by(abc, bikash, at("2026-11-21"))
        visit_by(abc, sita, desk, at("2026-11-02"), purpose=FIRST)
        visit_by(abc, sita, admin, at("2026-11-06"))
        payment_by(abc, sita, admin, at("2026-11-07"), 500)
        batch = make_batch(abc, teacher, (sita,), starts_on=date(2026, 10, 1))
        marks_by(abc, batch, teacher, "2026-11-02", sita)
        period = {"from": "2026-11-03", "to": "2026-11-21"}

        answer = report(abc, period)

        compared: set[tuple[str, int]] = set()
        for table in ("counsellors", "front_desk", "teachers"):
            for row in answer[table]:
                progress = abc.client.get(f"/api/staff/{row['id']}/progress/", period).json()
                for card in progress["cards"]:
                    if card["count"] in row:
                        assert row[card["count"]] == card["value"], (table, row, card)
                        compared.add((table, row["id"]))
        assert {("counsellors", admin.pk), ("counsellors", bikash.pk)} <= compared
        assert {("front_desk", desk.pk), ("teachers", teacher.pk)} <= compared
        assert row_of(answer, "counsellors", admin)["applications_made"] == 1
        assert row_of(answer, "front_desk", desk)["new_leads"] == 0  # before the period


class TestCountedFrom:
    def test_tasks_done_say_when_counting_began_if_the_period_starts_earlier(
        self, abc: ConsultancyContext
    ) -> None:
        early = report(abc)
        later = report(abc, {"from": "2026-11-15", "to": "2026-11-30"})

        assert early["counted_from"] == {"tasks_done": "2026-11-15"}
        assert later["counted_from"] == {}


class TestPlatformAdminActions:
    def test_count_for_nobody(self, abc: ConsultancyContext) -> None:
        bikash = named_staff(abc, "Bikash Thapa")
        sita = make_student(abc, bikash, Stage.APPLYING)
        stage_change(abc, sita, None, ("lead", "counselling"), at("2026-11-02"))
        application_by(abc, sita, None, at("2026-11-04"))
        visa_by(abc, sita, None, at("2026-11-04"))
        task_done_by(abc, None, at("2026-11-21"))
        visit_by(abc, sita, None, at("2026-11-05"))

        answer = report(abc)

        for table in ("counsellors", "front_desk", "teachers"):
            for row in answer[table]:
                counts = {k: v for k, v in row.items() if k not in {"id", "name", "is_active"}}
                if row["id"] == bikash.pk:
                    counts.pop("students")
                assert set(counts.values()) <= {0}, row
        assert row_of(answer, "counsellors", bikash)["students"] == 1


class TestRefusedInput:
    def test_a_period_over_one_year_says_so(self, abc: ConsultancyContext) -> None:
        response = abc.client.get(URL, {"from": "2025-01-01", "to": "2026-01-02"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"to": ["Choose a period of one year or less."]}

    def test_a_whole_year_is_fine(self, abc: ConsultancyContext) -> None:
        response = abc.client.get(URL, {"from": "2025-01-01", "to": "2026-01-01"})

        assert response.status_code == status.HTTP_200_OK

    def test_unknown_query_fields_are_refused(self, abc: ConsultancyContext) -> None:
        response = abc.client.get(URL, {**NOVEMBER, "staff": "1"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
