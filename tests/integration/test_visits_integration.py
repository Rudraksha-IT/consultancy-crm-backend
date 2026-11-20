"""Visits: the visitor desk and a Student's office visits (issue 34, spec 23, 23a-23c)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application, Visa
from applications.statuses import ApplicationStatus
from catalogue.models import Course, University
from staff.models import Role
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)
from visits.models import Visit, VisitKind

TODAY_URL = "/api/visits/today/"
TEACHERS_REFUSED = {"detail": "Teachers can't see Students."}
NEW_VISITOR = {
    "name": "Prakash Adhikari",
    "phone": "9841234567",
    "country": "canada",
    "source": "walk_in",
    "highest_qualification": "plus_two",
}


def visits_url(student: Student | int | str) -> str:
    """The Student's Visits endpoint."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/visits/"


def progress_url(student: Student | int | str) -> str:
    """The Student's progress endpoint."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/progress/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def stored_visits(office: ConsultancyContext, student: Student) -> list[Visit]:
    """The Student's stored Visits, oldest first."""
    with tenant_context(office.consultancy):
        return list(Visit.objects.filter(student=student).order_by("at", "pk"))


def visit_at(
    office: ConsultancyContext,
    student: Student,
    at: Any,  # noqa: ANN401  # a datetime
    kind: VisitKind = VisitKind.FOLLOW_UP,
    purpose: str = "Counselling session",
) -> Visit:
    """Store a Visit directly at a time."""
    with tenant_context(office.consultancy):
        return Visit.objects.create(
            student=student, at=at, kind=kind, purpose=purpose, recorded_by_name="Ramesh KC"
        )


def log_visit(office: ConsultancyContext, student: Student | int | str, **body: object) -> Any:  # noqa: ANN401
    """POST a follow-up Visit."""
    return office.client.post(visits_url(student), body, format="json")


class TestNewVisitor:
    def test_adding_a_visitor_logs_their_first_visit(self, abc: ConsultancyContext) -> None:
        desk = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        log_in(abc, desk)

        created = abc.client.post("/api/students/", NEW_VISITOR, format="json")
        visits = abc.client.get(visits_url(created.json()["id"])).json()

        assert created.status_code == status.HTTP_201_CREATED
        assert visits["count"] == 1
        visit = visits["results"][0]
        assert visit["kind"] == "new"
        assert visit["purpose"] == "First visit"
        assert visit["recorded_by"] == {"id": desk.pk, "name": "Ramesh KC"}

    def test_a_refused_visitor_logs_no_visit(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.post("/api/students/", {**NEW_VISITOR, "name": ""}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(abc.consultancy):
            assert not Visit.objects.exists()


class TestLogVisit:
    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.COUNSELLOR, Role.ADMIN])
    def test_front_desk_counsellors_and_admins_log_a_follow_up(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        # Not their Student: logging a visit doesn't change the record.
        student = make_student(abc, named_staff(abc, "Bikash Thapa"))
        staff = abc.log_in_as(role)

        response = log_visit(abc, student, reason="bringing_documents", details="Passport copy")

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["kind"] == "follow_up"
        assert body["purpose"] == "Bringing documents: Passport copy"
        assert body["recorded_by"] == {"id": staff.pk, "name": staff.name}
        [visit] = stored_visits(abc, student)
        assert visit.purpose == "Bringing documents: Passport copy"
        assert visit.recorded_by_id == staff.pk

    def test_the_reason_alone_is_the_purpose(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        body = log_visit(abc, student, reason="making_a_payment", details="  ").json()

        assert body["purpose"] == "Making a payment"

    @pytest.mark.parametrize(
        ("reason", "label"),
        [
            ("counselling_session", "Counselling session"),
            ("bringing_documents", "Bringing documents"),
            ("making_a_payment", "Making a payment"),
            ("attending_a_class", "Attending a class"),
            ("asking_about_progress", "Asking about progress"),
            ("other", "Other"),
        ],
    )
    def test_every_reason_has_its_words(
        self, abc: ConsultancyContext, reason: str, label: str
    ) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.ADMIN)

        assert log_visit(abc, student, reason=reason).json()["purpose"] == label

    def test_teachers_cannot_log_a_visit(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = log_visit(abc, student, reason="other")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == TEACHERS_REFUSED
        assert stored_visits(abc, student) == []

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        response = abc.new_client().post(visits_url(student), {"reason": "other"}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        assert log_visit(abc, 999_999, reason="other").status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({}, "reason"),
            ({"reason": "shopping"}, "reason"),
            ({"reason": 1}, "reason"),
            ({"reason": None}, "reason"),
            ({"reason": ["other"]}, "reason"),
            ({"reason": "other", "details": 5}, "details"),
            ({"reason": "other", "details": "x" * 201}, "details"),
            ({"reason": "other", "details": "line\nbreak"}, "details"),
            ({"reason": "other", "details": "bell\x07"}, "details"),
            ({"reason": "other", "kind": "new"}, "detail"),  # unknown fields
            ({"reason": "other", "at": "2020-01-01T00:00:00Z"}, "detail"),
            ({"reason": "other", "recorded_by": 1}, "detail"),
        ],
    )
    def test_refused_input_is_never_stored(
        self, abc: ConsultancyContext, body: dict[str, object], field: str
    ) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        response = log_visit(abc, student, **body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        assert stored_visits(abc, student) == []

    def test_a_body_that_is_not_an_object_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.post(visits_url(student), ["other"], format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_visits(abc, student) == []

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.post(visits_url(student), {"reason": "other"})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_details_are_kept_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        body = log_visit(abc, student, reason="other", details=text).json()

        assert body["purpose"] == f"Other: {text}"
        assert stored_visits(abc, student)[0].purpose == f"Other: {text}"

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_only_get_and_post_are_allowed(self, abc: ConsultancyContext, method: str) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(visits_url(student), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_the_log_names_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        student = make_student(abc, name="Secret Name")
        staff = abc.log_in_as(Role.FRONT_DESK)

        public_id = log_visit(abc, student, reason="other", details="Secret detail").json()["id"]
        with tenant_context(abc.consultancy):
            visit_id = Visit.objects.get(public_id=public_id).pk

        [line] = json_logs.with_message("Visit logged")
        assert line["message"] == (
            f"Visit logged: visit_id={visit_id}, student_id={student.pk}, kind=follow_up, "
            f"by_staff_id={staff.pk}"
        )
        assert "Secret" not in json_logs.text()


class TestStudentVisits:
    def test_newest_first_with_who_recorded_them(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        now = timezone.now()
        older = visit_at(abc, student, now - timedelta(days=3), VisitKind.NEW, "First visit")
        newer = visit_at(abc, student, now - timedelta(hours=1))
        abc.log_in_as(Role.COUNSELLOR)

        body = abc.client.get(visits_url(student)).json()

        assert [row["id"] for row in body["results"]] == [
            str(newer.public_id),
            str(older.public_id),
        ]
        assert body["results"][1] == {
            "id": str(older.public_id),
            "kind": "new",
            "purpose": "First visit",
            "at": older.at.isoformat(),
            "recorded_by": {"id": None, "name": "Ramesh KC"},
        }
        assert (body["count"], body["page"], body["page_size"]) == (2, 1, 50)

    def test_pages_of_fifty(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        now = timezone.now()
        with tenant_context(abc.consultancy):
            Visit.objects.bulk_create(
                Visit(student=student, at=now - timedelta(minutes=n), kind="follow_up")
                for n in range(51)
            )
        abc.log_in_as(Role.ADMIN)

        second = abc.client.get(visits_url(student), {"page": 2}).json()
        past_the_end = abc.client.get(visits_url(student), {"page": 3})

        assert len(second["results"]) == 1
        assert past_the_end.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("query", [{"page": "0"}, {"page": "x"}, {"kind": "new"}])
    def test_a_bad_query_is_refused(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(visits_url(student), query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_teachers_cannot_see_visits(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(visits_url(student))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == TEACHERS_REFUSED

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(visits_url(999_999)).status_code == status.HTTP_404_NOT_FOUND


class TestTodaysVisits:
    def test_todays_visits_newest_first_with_the_notes_first_line(
        self, abc: ConsultancyContext
    ) -> None:
        sita = make_student(abc, name="Sita Gurung", note="Wants Sydney\nBudget 20 lakh")
        hari = make_student(abc, name="Hari Rai", phone="9841000002")
        now = timezone.localtime()
        start = now.replace(hour=0, minute=0, second=1, microsecond=0)
        first = visit_at(abc, sita, start, VisitKind.NEW, "First visit")
        second = visit_at(abc, hari, start + timedelta(seconds=1))
        visit_at(abc, hari, start - timedelta(seconds=2))  # yesterday
        abc.log_in_as(Role.FRONT_DESK)

        body = abc.client.get(TODAY_URL).json()

        assert body["count"] == 2
        assert [row["id"] for row in body["results"]] == [
            str(second.public_id),
            str(first.public_id),
        ]
        assert body["results"][1]["student"] == {
            "id": str(sita.public_id),
            "name": "Sita Gurung",
            "note_first_line": "Wants Sydney",
        }
        assert body["results"][0]["student"]["note_first_line"] == ""
        assert body["results"][1]["kind"] == "new"

    def test_a_long_note_line_is_shortened(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, note="  " + "a" * 300)
        visit_at(abc, student, timezone.now())
        abc.log_in_as(Role.FRONT_DESK)

        line = abc.client.get(TODAY_URL).json()["results"][0]["student"]["note_first_line"]

        assert line == "a" * 159 + "…"

    def test_a_logged_visit_shows_today(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        log_visit(abc, student, reason="other")

        assert abc.client.get(TODAY_URL).json()["count"] == 1

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.COUNSELLOR, Role.ADMIN])
    def test_student_roles_see_today(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        assert abc.client.get(TODAY_URL).status_code == status.HTTP_200_OK

    def test_teachers_cannot_see_today(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(TODAY_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == TEACHERS_REFUSED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.new_client().get(TODAY_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_query_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.get(TODAY_URL, {"date": "2026-01-01"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(TODAY_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestStudentProgress:
    def test_a_lead_is_at_the_first_step(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        body = abc.client.get(progress_url(student)).json()

        assert body == {
            "stage": "lead",
            "percent": 8,
            "next_step": "Have the first counselling session",
            "is_done": False,
        }

    def test_applying_waits_for_an_offer(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"), Stage.APPLYING)
        with tenant_context(abc.consultancy):
            university = University.objects.create(name="Deakin University", country="australia")
            course = Course.objects.create(university=university, name="MIT", level="master")
            Application.objects.create(
                student=student,
                course=course,
                intake="2027-03",
                status=ApplicationStatus.APPLIED,
            )
        abc.log_in_as(Role.FRONT_DESK)

        body = abc.client.get(progress_url(student)).json()

        assert body == {
            "stage": "applying",
            "percent": 45,
            "next_step": "Waiting for an offer, Deakin University",
            "is_done": False,
        }

    def test_a_lost_student_shows_why(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, stage=Stage.LOST, lost_reason="Went elsewhere")
        abc.log_in_as(Role.COUNSELLOR)

        body = abc.client.get(progress_url(student)).json()

        assert body == {
            "stage": "lost",
            "percent": 0,
            "next_step": "Lost: Went elsewhere",
            "is_done": False,
        }

    def test_a_granted_visa_is_the_end_of_the_journey(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"), Stage.VISA)
        with tenant_context(abc.consultancy):
            university = University.objects.create(name="Deakin University", country="australia")
            course = Course.objects.create(university=university, name="MIT", level="master")
            application = Application.objects.create(
                student=student, course=course, intake="2027-03", status=ApplicationStatus.OFFER
            )
            Visa.objects.create(student=student, application=application, status="granted")
        abc.log_in_as(Role.COUNSELLOR)

        body = abc.client.get(progress_url(student)).json()

        assert body == {
            "stage": "visa",
            "percent": 100,
            "next_step": "Visa granted. Help with flights and pre-departure.",
            "is_done": True,
        }

    def test_teachers_cannot_see_progress(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(progress_url(student))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unknown_student_query_and_methods(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(progress_url(999_999)).status_code == status.HTTP_404_NOT_FOUND
        assert (
            abc.client.get(progress_url(student), {"x": "1"}).status_code
            == status.HTTP_400_BAD_REQUEST
        )
        assert (
            abc.client.post(progress_url(student), {}, format="json").status_code
            == status.HTTP_405_METHOD_NOT_ALLOWED
        )
