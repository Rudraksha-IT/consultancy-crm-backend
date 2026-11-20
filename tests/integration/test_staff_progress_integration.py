"""Staff Progress: a Staff member's counts and day-by-day changes for a period (spec 125, 357).

Issue 84. GET /api/staff/<id>/progress/?from=&to= answers the person, their cards for the period
and what they changed, grouped by the office's day, newest first. Admins may see anyone in their
office; everyone else only themselves. Only changes count; a Platform Admin's count for nobody.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application, Visa
from applications.statuses import VisaStatus
from batches.models import Attendance, Batch
from history.models import HistoryEntry, HistoryKind
from modules.services import switch_modules
from modules.types import Module
from payments.models import Payment
from staff.models import Role, Staff, StaffStatus
from students.models import Stage, Student
from tasks.models import Task
from tests.conftest import ConsultancyContext, ConsultancyFactory, PlatformContext
from tests.integration.test_applications_integration import make_application, make_course
from tests.integration.test_batches_integration import make_batch
from tests.integration.test_payments_integration import make_payment
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)
from tests.integration.test_tasks_integration import make_task
from tests.integration.test_visas_integration import make_visa
from tests.support_access import open_as_platform_admin
from visits.models import Visit, VisitKind

KATHMANDU = ZoneInfo("Asia/Kathmandu")
NOVEMBER = {"from": "2026-11-01", "to": "2026-11-30"}
COUNSELLOR_CARDS = [
    "moved_forward",
    "applications_made",
    "visas_applied_for",
    "students_lost",
    "tasks_done",
]
FRONT_DESK_CARDS = ["visitors_registered", "new_leads", "payments_taken"]
TEACHER_CARDS = ["classes_taken", "attendance_marked"]
FIRST = "First visit"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with test-prep classes switched on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.BATCHES})
    return office


def progress_url(staff: Staff | int) -> str:
    """The Staff member's Progress endpoint."""
    staff_id = staff if isinstance(staff, int) else staff.pk
    return f"/api/staff/{staff_id}/progress/"


def progress(
    office: ConsultancyContext, staff: Staff | int, period: dict[str, str] | None = None
) -> dict[str, Any]:
    """GET the Staff member's Progress and expect it."""
    response = office.client.get(progress_url(staff), period or NOVEMBER)
    assert response.status_code == status.HTTP_200_OK, response.json()
    answer: dict[str, Any] = response.json()
    return answer


def cards(answer: dict[str, Any]) -> dict[str, int]:
    """Each card's number."""
    return {card["count"]: card["value"] for card in answer["cards"]}


def card(answer: dict[str, Any], count: str) -> dict[str, Any]:
    """One card in full."""
    found: list[dict[str, Any]] = [c for c in answer["cards"] if c["count"] == count]
    assert len(found) == 1
    return found[0]


def entries(answer: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every listed change as (day, kind, after), newest first."""
    return [(d["day"], e["kind"], e["after"]) for d in answer["days"] for e in d["entries"]]


def at(day: str, hour: int = 10, minute: int = 0) -> datetime:
    """`hour:minute` on `day` in the office's time zone."""
    return datetime.combine(date.fromisoformat(day), time(hour, minute), tzinfo=KATHMANDU)


def stage_change(
    office: ConsultancyContext,
    student: Student,
    by: Staff | None,
    move: tuple[str, str],
    when: datetime,
) -> None:
    """Store a Stage change in History, made by `by` (None: a Platform Admin) at `when`."""
    with tenant_context(office.consultancy):
        HistoryEntry.objects.create(
            student=student,
            kind=HistoryKind.STAGE_CHANGED,
            at=when,
            by_staff=by,
            by_name=by.name if by else "Ops (Platform Admin)",
            by_platform_admin=by is None,
            before=move[0],
            after=move[1],
        )


def application_by(
    office: ConsultancyContext,
    student: Student,
    by: Staff | None,
    when: datetime,
    university: str = "Deakin University",
) -> Application:
    """An Application added by `by` at `when`, to a new Course at `university`."""
    application = make_application(office, student, make_course(office, university))
    with tenant_context(office.consultancy):
        Application.objects.filter(pk=application.pk).update(created_by=by, created_at=when)
    return application


def visa_by(office: ConsultancyContext, student: Student, by: Staff | None, when: datetime) -> Visa:
    """A Visa applied for by `by` at `when`."""
    application = make_application(office, student, make_course(office, "Monash University"))
    visa = make_visa(office, student, application, VisaStatus.APPLIED)
    with tenant_context(office.consultancy):
        Visa.objects.filter(pk=visa.pk).update(created_by=by, created_at=when)
    return visa


def task_done_by(
    office: ConsultancyContext,
    by: Staff | None,
    when: datetime,
    student: Student | None = None,
    title: str = "Collect bank statement",
) -> Task:
    """A Task marked done by `by` (None: a Platform Admin) at `when`."""
    assignee = by or named_staff(office, "Someone Else")
    task = make_task(office, assignee, student=student, title=title)
    with tenant_context(office.consultancy):
        Task.objects.filter(pk=task.pk).update(
            is_done=True,
            done_at=when,
            done_by=by,
            done_by_name=by.name if by else "Ops (Platform Admin)",
        )
    return task


def visit_by(
    office: ConsultancyContext,
    student: Student,
    by: Staff | None,
    when: datetime,
    *,
    purpose: str = "Bringing documents",
) -> None:
    """A follow-up Visit logged by `by` at `when`; a new visitor's if `purpose` is FIRST."""
    kind = VisitKind.NEW if purpose == FIRST else VisitKind.FOLLOW_UP
    with tenant_context(office.consultancy):
        Visit.objects.create(
            student=student,
            at=when,
            kind=kind,
            purpose=purpose,
            recorded_by=by,
            recorded_by_name=by.name if by else "Ops (Platform Admin)",
        )


def payment_by(
    office: ConsultancyContext, student: Student, by: Staff, when: datetime, amount: int
) -> None:
    """A Payment recorded by `by` at `when`."""
    payment = make_payment(office, student, by, amount=amount)
    with tenant_context(office.consultancy):
        Payment.objects.filter(pk=payment.pk).update(recorded_at=when)


def marks_by(
    office: ConsultancyContext, batch: Batch, by: Staff | None, day: str, *students: Student
) -> None:
    """Attendance for `day` of `batch`, marked by `by`."""
    with tenant_context(office.consultancy):
        for student in students:
            Attendance.objects.create(
                batch=batch,
                student=student,
                day=date.fromisoformat(day),
                mark="present",
                marked_by=by,
                marked_at=at(day),
            )


class TestCounsellorCards:
    def test_counts_only_their_own_changes_inside_the_period(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        other = named_staff(abc, "Rina Rai")
        log_in(abc, me)
        sita = make_student(abc, me, Stage.APPLYING)
        stage_change(abc, sita, me, ("lead", "counselling"), at("2026-11-02"))
        stage_change(abc, sita, me, ("counselling", "applying"), at("2026-11-30", 23, 50))
        stage_change(abc, sita, me, ("applying", "counselling"), at("2026-11-05"))  # back
        stage_change(abc, sita, me, ("applying", "lost"), at("2026-11-06"))
        stage_change(abc, sita, me, ("lead", "counselling"), at("2026-10-31", 23, 59))  # before
        stage_change(abc, sita, me, ("lead", "counselling"), at("2026-12-01", 0, 1))  # after
        stage_change(abc, sita, other, ("lead", "counselling"), at("2026-11-03"))
        application_by(abc, sita, me, at("2026-11-04"))
        application_by(abc, sita, other, at("2026-11-04"), "RMIT University")
        visa_by(abc, sita, me, at("2026-11-20"))
        task_done_by(abc, me, at("2026-11-21"))
        task_done_by(abc, me, at("2026-11-22"), student=sita)

        answer = progress(abc, me)

        assert [c["count"] for c in answer["cards"]] == COUNSELLOR_CARDS
        assert cards(answer) == {
            "moved_forward": 2,
            "applications_made": 1,
            "visas_applied_for": 1,
            "students_lost": 1,
            "tasks_done": 2,
        }

    def test_tasks_done_say_counted_from_when_the_period_starts_before_recording(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)

        early = progress(abc, me, NOVEMBER)
        later = progress(abc, me, {"from": "2026-11-15", "to": "2026-11-30"})

        assert card(early, "tasks_done")["counted_from"] == "2026-11-15"
        assert card(later, "tasks_done")["counted_from"] is None
        assert card(early, "moved_forward")["counted_from"] is None

    def test_a_platform_admins_actions_count_for_nobody(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        sita = make_student(abc, me, Stage.APPLYING)
        stage_change(abc, sita, None, ("lead", "counselling"), at("2026-11-02"))
        application_by(abc, sita, None, at("2026-11-04"))
        visa_by(abc, sita, None, at("2026-11-04"))
        task_done_by(abc, None, at("2026-11-21"))
        visit_by(abc, sita, None, at("2026-11-05"))

        answer = progress(abc, me)

        assert set(cards(answer).values()) == {0}
        assert answer["days"] == []


class TestFrontDeskCards:
    def test_visitors_new_leads_and_payments_with_their_amount(
        self, abc: ConsultancyContext
    ) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        log_in(abc, desk)
        sita = make_student(abc)
        visit_by(abc, sita, desk, at("2026-11-02"), purpose=FIRST)
        visit_by(abc, sita, desk, at("2026-11-09"))
        visit_by(abc, sita, desk, at("2026-10-09"))  # before the period
        payment_by(abc, sita, desk, at("2026-11-03"), 18000)
        payment_by(abc, sita, desk, at("2026-11-04"), 2500)
        payment_by(abc, sita, desk, at("2026-12-04"), 9999)  # after the period

        answer = progress(abc, desk)

        assert [c["count"] for c in answer["cards"]] == FRONT_DESK_CARDS
        assert cards(answer) == {"visitors_registered": 2, "new_leads": 1, "payments_taken": 2}
        assert card(answer, "payments_taken")["amount"] == 20500
        assert card(answer, "new_leads")["amount"] is None


class TestTeacherCards:
    def test_class_days_and_marks_they_took(self, abc: ConsultancyContext) -> None:
        teacher = named_staff(abc, "Gita Lama", Role.TEACHER)
        admin = named_staff(abc, "Asha Admin", Role.ADMIN)
        log_in(abc, teacher)
        sita, hari = make_student(abc), make_student(abc, name="Hari Thapa", phone="9841000002")
        batch = make_batch(abc, teacher, (sita, hari), starts_on=date(2026, 10, 1))
        marks_by(abc, batch, teacher, "2026-11-02", sita, hari)
        marks_by(abc, batch, teacher, "2026-11-03", sita)
        marks_by(abc, batch, admin, "2026-11-04", sita)  # someone else marked that day
        marks_by(abc, batch, teacher, "2026-10-30", sita)  # before

        answer = progress(abc, teacher)

        assert [c["count"] for c in answer["cards"]] == TEACHER_CARDS
        assert cards(answer) == {"classes_taken": 2, "attendance_marked": 3}
        assert entries(answer) == [
            ("2026-11-03", "attendance_marked", "PTE Day"),
            ("2026-11-02", "attendance_marked", "PTE Day"),
        ]
        assert answer["days"][1]["entries"][0]["detail"] == "2"

    def test_a_teacher_has_no_cards_while_classes_are_off(self, abc: ConsultancyContext) -> None:
        teacher = named_staff(abc, "Gita Lama", Role.TEACHER)
        log_in(abc, teacher)
        switch_modules(abc.consultancy, set())

        answer = progress(abc, teacher)

        assert answer["cards"] == []
        assert answer["days"] == []


class TestAdminCards:
    def test_an_admin_gets_every_card_that_applies(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)

        assert [c["count"] for c in progress(abc, admin)["cards"]] == [
            *COUNSELLOR_CARDS,
            *FRONT_DESK_CARDS,
        ]

    def test_an_admin_who_teaches_a_class_gets_the_teacher_cards_too(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        make_batch(abc, admin)

        assert [c["count"] for c in progress(abc, admin)["cards"]] == [
            *COUNSELLOR_CARDS,
            *FRONT_DESK_CARDS,
            *TEACHER_CARDS,
        ]


class TestDayByDayList:
    def test_changes_are_grouped_by_the_offices_day_newest_first(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        other = named_staff(abc, "Rina Rai")
        log_in(abc, me)
        sita = make_student(abc, me, Stage.APPLYING, name="Sita Gurung")
        stage_change(abc, sita, me, ("lead", "counselling"), at("2026-11-02", 9))
        visit_by(abc, sita, me, at("2026-11-02", 0, 30), purpose="Counselling session")
        application_by(abc, sita, me, at("2026-11-04", 15))
        visa_by(abc, sita, me, at("2026-11-04", 16))
        stage_change(abc, sita, other, ("counselling", "applying"), at("2026-11-04"))

        answer = progress(abc, me)

        assert [d["day"] for d in answer["days"]] == ["2026-11-04", "2026-11-02"]
        assert entries(answer) == [
            ("2026-11-04", "visa_applied_for", "Monash University"),
            ("2026-11-04", "application_made", "Deakin University"),
            ("2026-11-02", "stage_changed", "counselling"),
            ("2026-11-02", "visit_logged", "Counselling session"),
        ]
        first = answer["days"][0]["entries"][0]
        assert first["student"] == {"id": str(sita.public_id), "name": "Sita Gurung"}
        assert set(first) == {"kind", "at", "before", "after", "detail", "student"}
        assert answer["more"] is False

    def test_a_task_done_about_a_student_is_listed_but_an_only_me_task_is_not(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        sita = make_student(abc, me)
        task_done_by(abc, me, at("2026-11-21"), title="Private reminder")
        with tenant_context(abc.consultancy):
            HistoryEntry.objects.create(
                student=sita,
                kind=HistoryKind.TASK_DONE,
                at=at("2026-11-22"),
                by_staff=me,
                by_name=me.name,
                after="Call Sita about IELTS",
            )

        answer = progress(abc, me)

        assert entries(answer) == [("2026-11-22", "task_done", "Call Sita about IELTS")]
        assert "Private reminder" not in str(answer)

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_text_comes_back_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        sita = make_student(abc, me, name=text)
        visit_by(abc, sita, me, at("2026-11-02"), purpose=text)

        [entry] = progress(abc, me)["days"][0]["entries"]

        assert entry["after"] == text
        assert entry["student"]["name"] == text

    def test_a_long_period_lists_only_the_newest_changes_and_says_there_are_more(
        self, abc: ConsultancyContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("staff_progress.services.MAX_ENTRIES", 3)
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        sita = make_student(abc, me)
        for day in ("2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"):
            visit_by(abc, sita, me, at(day), purpose=day)

        answer = progress(abc, me)

        assert [after for _, _, after in entries(answer)] == [
            "2026-11-05",
            "2026-11-04",
            "2026-11-03",
        ]
        assert answer["more"] is True
        assert cards(answer)["moved_forward"] == 0

    def test_the_answer_names_the_person_and_the_period(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)

        answer = progress(abc, me)

        assert answer["staff"] == {
            "id": me.pk,
            "name": "Bikash Thapa",
            "job_title": me.job_title,
            "mobile": me.mobile,
            "role": "counsellor",
            "status": "active",
        }
        assert (answer["from"], answer["to"]) == ("2026-11-01", "2026-11-30")


class TestWhoMaySee:
    def test_an_admin_sees_anyone_in_their_office(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for role in (Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER, Role.ADMIN):
            person = named_staff(abc, f"Person{role.value.replace('_', '')} Lama", role)
            assert progress(abc, person)["staff"]["id"] == person.pk

    def test_an_admin_sees_someone_who_has_left(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        gone = named_staff(abc, "Gone Person")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=gone.pk).update(status=StaffStatus.DEACTIVATED)

        assert progress(abc, gone)["staff"]["status"] == "deactivated"

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_everyone_else_sees_only_themselves(self, abc: ConsultancyContext, role: Role) -> None:
        other = named_staff(abc, "Rina Rai")
        me = abc.log_in_as(role)

        assert progress(abc, me)["staff"]["id"] == me.pk
        response = abc.client.get(progress_url(other), NOVEMBER)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == {"detail": "You can only see your own progress."}

    def test_someone_who_is_not_an_admin_learns_nothing_about_unknown_ids(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.get(progress_url(99999), NOVEMBER)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_unknown_person_is_not_found_for_an_admin(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(progress_url(99999), NOVEMBER)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")

        response = abc.client.get(progress_url(me), NOVEMBER)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_platform_admin_in_support_access_sees_anyone(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        visit = open_as_platform_admin(platform_site, abc)

        response = visit.client.get(progress_url(me), NOVEMBER)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["staff"]["id"] == me.pk

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_reading_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        me = abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(progress_url(me), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestRefusedInput:
    @pytest.mark.parametrize(
        ("query", "field"),
        [
            ({"to": "2026-11-30"}, "from"),
            ({"from": "2026-11-01"}, "to"),
            ({"from": "2026-13-01", "to": "2026-11-30"}, "from"),
            ({"from": "yesterday", "to": "2026-11-30"}, "from"),
            ({"from": "2026-11-01", "to": "<script>alert(1)</script>"}, "to"),
            ({"from": "2026-11-01'; DROP TABLE staff_staff; --", "to": "2026-11-30"}, "from"),
            ({"from": "2026-11-30", "to": "2026-11-01"}, "to"),
            ({"from": "1999-12-31", "to": "2000-01-01"}, "from"),
            ({"from": "2025-11-01", "to": "2026-11-02"}, "to"),
            ({"from": "2026-11-01" * 50, "to": "2026-11-30"}, "from"),
        ],
    )
    def test_bad_periods_are_refused(
        self, abc: ConsultancyContext, query: dict[str, str], field: str
    ) -> None:
        me = abc.log_in_as(Role.ADMIN)

        response = abc.client.get(progress_url(me), query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()

    def test_a_period_over_one_year_says_so(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)

        response = abc.client.get(progress_url(me), {"from": "2025-11-01", "to": "2026-11-02"})

        assert response.json() == {"to": ["Choose a period of one year or less."]}

    def test_a_whole_year_is_fine(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)

        response = abc.client.get(progress_url(me), {"from": "2025-11-15", "to": "2026-11-15"})

        assert response.status_code == status.HTTP_200_OK

    def test_unknown_query_fields_are_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)

        response = abc.client.get(progress_url(me), {**NOVEMBER, "staff": "2"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_non_number_id_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get("/api/staff/abc/progress/", NOVEMBER)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_today_is_a_fine_period(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)
        today = timezone.localdate().isoformat()

        assert progress(abc, me, {"from": today, "to": today})["from"] == today
