"""One Student's profile, its edits, the Note and its History (spec 32-37, 45; issue 10)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from catalogue.models import Course, Intake, University
from history.models import HistoryEntry
from staff.models import Role, Staff
from students.models import Source, Stage, Student
from students.profile import change_details
from tests.conftest import (
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    use_access_token,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}
NOT_YOUR_STUDENT = {
    "detail": "Not your Student. Only their counsellor or an Admin can change them."
}
FRONT_DESK_LEADS_ONLY = {"detail": "Front Desk can change only Leads with no counsellor."}
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE students_student; --",
    "}body{background:url(javascript:alert(1))}",
]
WORKS_WITH_STUDENTS = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)


def profile_url(student: Student | int | str) -> str:
    """The Student's profile endpoint."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/"


def note_url(student: Student) -> str:
    """The Student's Note endpoint."""
    return f"{profile_url(student)}note/"


def history_url(student: Student) -> str:
    """The Student's History endpoint."""
    return f"{profile_url(student)}history/"


def records_url(student: Student) -> str:
    """The Student's academic history endpoint."""
    return f"{profile_url(student)}academic-records/"


def scores_url(student: Student) -> str:
    """The Student's test scores endpoint."""
    return f"{profile_url(student)}test-scores/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def make_student(
    office: ConsultancyContext,
    counsellor: Staff | None = None,
    stage: Stage = Stage.LEAD,
    **fields: object,
) -> Student:
    """Store a Student directly; `fields` override the defaults."""
    details: dict[str, object] = {
        "name": "Sita Gurung",
        "phone": "9841000001",
        "country": "australia",
        "source": Source.WALK_IN,
        "highest_qualification": "plus_two",
        "note": "Wants Melbourne only.",
        **fields,
    }
    with tenant_context(office.consultancy):
        return Student.objects.create(assigned_counsellor=counsellor, stage=stage, **details)


def history_rows(office: ConsultancyContext, student: Student) -> list[HistoryEntry]:
    """The Student's stored History, oldest first."""
    with tenant_context(office.consultancy):
        return list(HistoryEntry.objects.filter(student=student).order_by("at", "pk"))


# The preferred Intake is one the Catalogue offers from this month on (issue 92).
PREFERRED_INTAKE = f"{timezone.localdate().year + 1}-02"


def offer_intake_in_catalogue(office: ConsultancyContext, month: str) -> None:
    """Store a Course that runs in `month`, so a Student may prefer it."""
    with tenant_context(office.consultancy):
        university = University.objects.create(name="Deakin University", country="australia")
        course = Course.objects.create(university=university, name="Nursing", level="bachelor")
        Intake.objects.create(course=course, month=month)


def edit(office: ConsultancyContext, student: Student, **body: object) -> _MonkeyPatchedResponse:
    """PATCH the Student's details."""
    return office.client.patch(profile_url(student), body, format="json")


def put_note(office: ConsultancyContext, student: Student, note: object) -> _MonkeyPatchedResponse:
    """Replace the Student's Note."""
    return office.client.put(note_url(student), {"note": note}, format="json")


class TestViewingAProfile:
    @pytest.mark.parametrize("role", WORKS_WITH_STUDENTS)
    def test_every_role_that_works_with_students_can_open_any_student(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.COUNSELLING)
        abc.log_in_as(role)

        response = abc.client.get(profile_url(student))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == "Sita Gurung"
        assert response.json()["note"] == "Wants Melbourne only."

    def test_a_teacher_cannot_open_a_student(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(profile_url(student))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_a_student_who_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(profile_url(999_999)).status_code == status.HTTP_404_NOT_FOUND

    def test_a_non_numeric_id_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get("/api/students/abc/").status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        response = abc.new_client().get(profile_url(student))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_the_answer_has_every_field_the_profile_shows(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.ADMIN)

        body = abc.client.get(profile_url(student)).json()

        assert set(body) == {
            "id",
            "name",
            "phone",
            "email",
            "country",
            "other_countries",
            "date_of_birth",
            "passport_number",
            "gender",
            "marital_status",
            "nationality",
            "father_name",
            "mother_name",
            "guardian_name",
            "guardian_relation",
            "guardian_phone",
            "passport_place_of_issue",
            "passport_issue_date",
            "passport_expiry_date",
            "passport_warning",
            "target_level",
            "permanent_address",
            "mailing_address",
            "mailing_same_as_permanent",
            "work_experience",
            "highest_qualification",
            "past_degree",
            "previous_job",
            "preferred_course",
            "preferred_intake",
            "source",
            "sub_agent_name",
            "note",
            "stage",
            "assigned_counsellor",
            "created_at",
            "academic_records",
            "test_scores",
            "lost_reason",
            "priority",
            "stage_changes",
            "progress",
            "details_version",
            "can_change",
            "can_set_priority",
            "can_edit_details",
            "can_set_stage",
            "can_assign",
            "can_transfer",
            "applications",
            "chosen_offer_id",
            "visas",
            "can_change_applications",
            "documents",
            "can_change_documents",
            "can_record_payment",
            "can_change_fees",
            "open_tasks",
            "can_ask_transfer",
            "transfer_request",
        }
        assert body["other_countries"] == []
        assert body["date_of_birth"] is None
        assert body["preferred_intake"] == ""
        assert body["academic_records"] == []
        assert body["test_scores"] == []
        assert body["applications"] == []
        assert body["visas"] == []

    def test_the_assigned_counsellor_is_told_they_can_change(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)

        assert abc.client.get(profile_url(student)).json()["can_change"] is True

    def test_another_counsellor_is_told_the_student_is_view_only(
        self, abc: ConsultancyContext
    ) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.COUNSELLING)
        abc.log_in_as(Role.COUNSELLOR)

        assert abc.client.get(profile_url(student)).json()["can_change"] is False

    def test_front_desk_can_change_an_unassigned_lead_only(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        lead = make_student(abc)
        assigned = make_student(abc, counsellor, name="Ram Thapa")
        abc.log_in_as(Role.FRONT_DESK)

        assert abc.client.get(profile_url(lead)).json()["can_change"] is True
        assert abc.client.get(profile_url(assigned)).json()["can_change"] is False

    @pytest.mark.parametrize("method", ["post", "put", "delete"])
    def test_only_get_and_patch_are_allowed(self, abc: ConsultancyContext, method: str) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(profile_url(student), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_the_platform_host_has_no_student_profiles(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        student = make_student(abc)
        platform_site.log_in()

        response = platform_site.client.get(profile_url(student))

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestEditingDetails:
    def full_details(self) -> dict[str, object]:
        return {
            "name": "Sita Gurung Thapa",
            "phone": "+977 984-100-0002",
            "email": "Sita@Example.TEST",
            "country": "uk",
            "other_countries": ["australia", "canada"],
            "date_of_birth": "2003-04-12",
            "passport_number": "pa1234567",
            "highest_qualification": "bachelor",
            "past_degree": "BBS, Tribhuvan University, 2024",
            "previous_job": "Bank teller, 1 year",
            "preferred_course": "Master of Business Analytics",
            "preferred_intake": PREFERRED_INTAKE,
            "source": "referral",
        }

    def test_the_assigned_counsellor_can_change_every_detail(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)
        offer_intake_in_catalogue(abc, PREFERRED_INTAKE)

        response = edit(abc, student, **self.full_details())

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["name"] == "Sita Gurung Thapa"
        assert body["phone"] == "+9779841000002"
        assert body["email"] == "sita@example.test"
        assert body["country"] == "uk"
        assert body["other_countries"] == ["australia", "canada"]
        assert body["date_of_birth"] == "2003-04-12"
        assert body["passport_number"] == "PA1234567"
        assert body["preferred_course"] == "Master of Business Analytics"
        assert body["preferred_intake"] == PREFERRED_INTAKE
        assert body["source"] == "referral"
        with tenant_context(abc.consultancy):
            stored = Student.objects.get(pk=student.pk)
        assert stored.passport_number == "PA1234567"
        assert stored.date_of_birth == date(2003, 4, 12)

    def test_only_the_fields_sent_change(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, father_name="Hari")

        body = edit(abc, student, previous_job="Shop assistant").json()

        assert body["previous_job"] == "Shop assistant"
        assert body["father_name"] == "Hari"
        assert body["name"] == "Sita Gurung"

    def test_an_admin_can_change_anyones_student(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, counsellor, Stage.COUNSELLING)
        abc.log_in_as(Role.ADMIN)

        assert edit(abc, student, father_name="Hari").status_code == status.HTTP_200_OK

    def test_front_desk_can_correct_an_unassigned_lead(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        assert edit(abc, student, phone="9841999999").json()["phone"] == "9841999999"

    def test_front_desk_cannot_change_an_assigned_student(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, counsellor)
        abc.log_in_as(Role.FRONT_DESK)

        response = edit(abc, student, father_name="Hari")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == FRONT_DESK_LEADS_ONLY

    def test_front_desk_cannot_change_an_unassigned_student_past_lead(
        self, abc: ConsultancyContext
    ) -> None:
        student = make_student(abc, stage=Stage.COUNSELLING)
        abc.log_in_as(Role.FRONT_DESK)

        assert edit(abc, student, father_name="Hari").status_code == status.HTTP_403_FORBIDDEN

    def test_another_counsellor_is_refused_as_not_their_student(
        self, abc: ConsultancyContext
    ) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.COUNSELLING)
        abc.log_in_as(Role.COUNSELLOR)

        response = edit(abc, student, father_name="Hari")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        with tenant_context(abc.consultancy):
            assert Student.objects.get(pk=student.pk).father_name == ""

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = edit(abc, student, father_name="Hari")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_editing_a_student_who_does_not_exist_is_not_found(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.patch(profile_url(999_999), {"father_name": "x"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_empty_change_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = edit(abc, student)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize(
        "field", ["note", "stage", "assigned_counsellor", "id", "created_at", "is_admin"]
    )
    def test_fields_that_are_not_details_are_refused_and_never_stored(
        self, abc: ConsultancyContext, field: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = edit(abc, student, **{field: "completed"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        with tenant_context(abc.consultancy):
            stored = Student.objects.get(pk=student.pk)
        assert stored.stage == Stage.LEAD
        assert stored.note == "Wants Melbourne only."

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", ""),
            ("name", 7),
            ("name", "x" * 201),
            ("name", "Sita\nGurung"),
            ("phone", "abc"),
            ("phone", 9841000001),
            ("email", "not-an-email"),
            ("country", "mars"),
            ("country", 1),
            ("other_countries", "uk"),
            ("other_countries", ["mars"]),
            ("other_countries", ["uk", "uk"]),
            ("other_countries", ["australia"]),
            ("other_countries", ["uk", "canada", "usa", "new_zealand", "japan", "other", "uk"]),
            ("date_of_birth", "12/04/2003"),
            ("date_of_birth", "1899-12-31"),
            ("date_of_birth", True),
            ("passport_number", "PA 123-4567"),
            ("passport_number", "AB1"),
            ("passport_number", "A" * 21),
            ("highest_qualification", "phd"),
            ("past_degree", "x" * 201),
            ("previous_job", 5),
            ("preferred_course", "x" * 201),
            ("preferred_intake", "Feb 2027"),
            ("preferred_intake", "2027-13"),
            ("preferred_intake", "1999-02"),
            ("source", "tiktok"),
        ],
    )
    def test_bad_values_are_refused(
        self, abc: ConsultancyContext, field: str, value: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = edit(abc, student, **{field: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()

    def test_a_date_of_birth_in_the_future_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()

        response = edit(abc, student, date_of_birth=tomorrow)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "date_of_birth" in response.json()

    def test_the_date_of_birth_and_intake_can_be_cleared(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, date_of_birth=date(2003, 4, 12), preferred_intake="2027-02")

        body = edit(abc, student, date_of_birth=None, preferred_intake="").json()

        assert body["date_of_birth"] is None
        assert body["preferred_intake"] == ""

    def test_a_sub_agent_name_is_needed_when_the_source_becomes_sub_agent(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = edit(abc, student, source="sub_agent")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"sub_agent_name": ["Enter the Sub-agent's name."]}

    def test_the_sub_agent_name_must_go_when_the_source_changes(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, source=Source.SUB_AGENT, sub_agent_name="Everest Links")

        refused = edit(abc, student, source="website")
        allowed = edit(abc, student, source="website", sub_agent_name="")

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert "sub_agent_name" in refused.json()
        assert allowed.status_code == status.HTTP_200_OK
        assert allowed.json()["sub_agent_name"] == ""

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_text_is_stored_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = edit(abc, student, mother_name=text)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["mother_name"] == text
        with tenant_context(abc.consultancy):
            assert Student.objects.count() == 1

    def test_a_body_that_is_not_an_object_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.patch(profile_url(student), ["father_name"], format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_editing_details_writes_no_history(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        edit(abc, student, father_name="Hari")

        assert history_rows(abc, student) == []


class TestTheNote:
    def test_the_assigned_counsellor_can_edit_the_note(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)

        response = put_note(abc, student, "Wants Melbourne or Sydney.\nIELTS 7.0.")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["note"] == "Wants Melbourne or Sydney.\nIELTS 7.0."
        with tenant_context(abc.consultancy):
            assert Student.objects.get(pk=student.pk).note == (
                "Wants Melbourne or Sydney.\nIELTS 7.0."
            )

    def test_editing_the_note_records_who_when_and_the_previous_text(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)

        put_note(abc, student, "Wants Sydney now.")

        [entry] = history_rows(abc, student)
        assert entry.kind == "note_edited"
        assert entry.by_staff_id == me.pk
        assert entry.by_name == me.name
        assert entry.by_platform_admin is False
        assert entry.before == "Wants Melbourne only."
        assert entry.after == "Wants Sydney now."
        assert entry.at is not None

    def test_saving_the_same_note_again_records_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = put_note(abc, student, "Wants Melbourne only.")

        assert response.status_code == status.HTTP_200_OK
        assert history_rows(abc, student) == []

    def test_the_note_can_be_emptied(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        assert put_note(abc, student, "").json()["note"] == ""
        assert history_rows(abc, student)[0].after == ""

    def test_front_desk_can_edit_the_note_of_an_unassigned_lead(
        self, abc: ConsultancyContext
    ) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        assert put_note(abc, student, "Called back.").status_code == status.HTTP_200_OK

    def test_another_counsellor_cannot_edit_the_note(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.COUNSELLING)
        abc.log_in_as(Role.COUNSELLOR)

        response = put_note(abc, student, "Mine now.")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        assert history_rows(abc, student) == []

    def test_front_desk_cannot_edit_an_assigned_students_note(
        self, abc: ConsultancyContext
    ) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, counsellor)
        abc.log_in_as(Role.FRONT_DESK)

        response = put_note(abc, student, "Changed.")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == FRONT_DESK_LEADS_ONLY

    def test_a_teacher_cannot_edit_the_note(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        assert put_note(abc, student, "x").json() == NOT_FOR_TEACHERS

    @pytest.mark.parametrize("note", [None, 5, ["a"], "x" * 5001, "bell\x07", "null\x00byte"])
    def test_bad_notes_are_refused(self, abc: ConsultancyContext, note: object) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = put_note(abc, student, note)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "note" in response.json()
        assert history_rows(abc, student) == []

    def test_the_note_is_needed_and_nothing_else_is_allowed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        missing = abc.client.put(note_url(student), {}, format="json")
        extra = abc.client.put(note_url(student), {"note": "x", "stage": "lost"}, format="json")

        assert missing.status_code == status.HTTP_400_BAD_REQUEST
        assert "note" in missing.json()
        assert extra.status_code == status.HTTP_400_BAD_REQUEST
        assert extra.json() == {"detail": ["Unknown field."]}

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_notes_are_kept_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        assert put_note(abc, student, text).json()["note"] == text

    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_only_put_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = getattr(abc.client, method)(note_url(student), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_the_note_text_is_never_logged(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        put_note(abc, student, "Father's salary NPR 90,000")

        assert "90,000" not in json_logs.text()
        assert json_logs.with_message("Note edited")


class TestHistory:
    def test_staff_who_can_see_the_student_see_its_history_newest_first(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        put_note(abc, student, "First change.")
        put_note(abc, student, "Second change.")
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.get(history_url(student))

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["count"] == 2
        assert body["page"] == 1
        first, second = body["results"]
        assert first["kind"] == "note_edited"
        assert first["before"] == "First change."
        assert first["after"] == "Second change."
        assert first["by"] == {"name": admin.name, "is_platform_admin": False}
        assert set(first) == {"id", "kind", "at", "by", "before", "after", "detail"}
        assert second["before"] == "Wants Melbourne only."

    @pytest.mark.parametrize("role", WORKS_WITH_STUDENTS)
    def test_every_role_but_teacher_can_read_history(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.COUNSELLING)
        abc.log_in_as(role)

        assert abc.client.get(history_url(student)).status_code == status.HTTP_200_OK

    def test_a_teacher_cannot_read_history(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(history_url(student))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_history_of_a_student_who_does_not_exist_is_not_found(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get("/api/students/999999/history/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_history_cannot_be_written_or_removed_through_the_api(
        self, abc: ConsultancyContext, method: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = getattr(abc.client, method)(
            history_url(student), {"kind": "note_edited"}, format="json"
        )

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_history_is_paginated(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        for number in range(52):
            put_note(abc, student, f"Change {number}")

        first = abc.client.get(history_url(student)).json()
        second = abc.client.get(history_url(student), {"page": 2}).json()

        assert first["count"] == 52
        assert first["page_size"] == 50
        assert len(first["results"]) == 50
        assert len(second["results"]) == 2

    @pytest.mark.parametrize("query", [{"page": "0"}, {"page": "x"}, {"sort": "at"}])
    def test_bad_history_queries_are_refused(
        self, abc: ConsultancyContext, query: dict[str, str]
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.get(history_url(student), query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_page_past_the_end_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.get(history_url(student), {"page": 2})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_one_students_history_never_shows_anothers(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = make_student(abc)
        ram = make_student(abc, name="Ram Thapa")
        put_note(abc, sita, "Sita's change.")

        assert abc.client.get(history_url(ram)).json()["count"] == 0


class TestAcademicRecords:
    def record(self, **changes: object) -> dict[str, object]:
        return {
            "level": "bachelor",
            "institution": "Tribhuvan University",
            "finished_year": 2024,
            "result": "GPA 3.2",
            **changes,
        }

    def test_the_counsellor_can_add_change_and_remove_a_record(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)

        added = abc.client.post(records_url(student), self.record(), format="json")
        record_id = added.json()["id"]
        changed = abc.client.patch(
            f"{records_url(student)}{record_id}/", {"result": "GPA 3.4"}, format="json"
        )
        profile = abc.client.get(profile_url(student)).json()
        removed = abc.client.delete(f"{records_url(student)}{record_id}/")

        assert added.status_code == status.HTTP_201_CREATED
        assert added.json() == {"id": record_id, **self.record()}
        assert changed.json()["result"] == "GPA 3.4"
        assert profile["academic_records"] == [{"id": record_id, **self.record(result="GPA 3.4")}]
        assert removed.status_code == status.HTTP_204_NO_CONTENT
        assert abc.client.get(profile_url(student)).json()["academic_records"] == []

    def test_only_level_and_institution_are_needed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.post(
            records_url(student), {"level": "plus_two", "institution": "SOS School"}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["finished_year"] is None
        assert response.json()["result"] == ""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("level", "phd"),
            ("level", None),
            ("institution", ""),
            ("institution", "x" * 201),
            ("institution", "Line\nbreak"),
            ("finished_year", 1949),
            ("finished_year", 2100),
            ("finished_year", "2024"),
            ("finished_year", True),
            ("result", "x" * 51),
            ("result", 3.2),
            ("student", 1),
        ],
    )
    def test_bad_records_are_refused(
        self, abc: ConsultancyContext, field: str, value: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.post(
            records_url(student), self.record(**{field: value}), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # "student" isn't a field here: it comes from the address, so it is refused as unknown.
        assert ("detail" if field == "student" else field) in response.json()

    def test_another_counsellor_cannot_add_change_or_remove_records(
        self, abc: ConsultancyContext
    ) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.COUNSELLING)
        theirs = abc.new_client()
        use_access_token(theirs, abc.tokens_for(other).access)
        record_id = theirs.post(records_url(student), self.record(), format="json").json()["id"]
        abc.log_in_as(Role.COUNSELLOR)

        added = abc.client.post(records_url(student), self.record(), format="json")
        changed = abc.client.patch(
            f"{records_url(student)}{record_id}/", {"result": "x"}, format="json"
        )
        removed = abc.client.delete(f"{records_url(student)}{record_id}/")

        for response in (added, changed, removed):
            assert response.status_code == status.HTTP_403_FORBIDDEN
            assert response.json() == NOT_YOUR_STUDENT

    def test_a_teacher_cannot_add_records(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = abc.client.post(records_url(student), self.record(), format="json")

        assert response.json() == NOT_FOR_TEACHERS

    def test_a_record_of_another_student_is_not_found_under_this_one(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = make_student(abc)
        ram = make_student(abc, name="Ram Thapa")
        record_id = abc.client.post(records_url(sita), self.record(), format="json").json()["id"]

        changed = abc.client.patch(
            f"{records_url(ram)}{record_id}/", {"result": "x"}, format="json"
        )
        removed = abc.client.delete(f"{records_url(ram)}{record_id}/")

        assert changed.status_code == status.HTTP_404_NOT_FOUND
        assert removed.status_code == status.HTTP_404_NOT_FOUND

    def test_a_student_can_have_at_most_twenty_records(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        for _ in range(20):
            abc.client.post(records_url(student), self.record(), format="json")

        response = abc.client.post(records_url(student), self.record(), format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "A Student can have at most 20 of these."}

    @pytest.mark.parametrize("method", ["get", "put"])
    def test_only_the_needed_methods_are_allowed(
        self, abc: ConsultancyContext, method: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        record_id = abc.client.post(records_url(student), self.record(), format="json").json()["id"]

        listing = getattr(abc.client, method)(records_url(student), {}, format="json")
        one = getattr(abc.client, method)(f"{records_url(student)}{record_id}/", {}, format="json")

        assert listing.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert one.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestTestScores:
    def score(self, **changes: object) -> dict[str, object]:
        return {
            "test": "ielts",
            "overall": "6.5",
            "taken_on": "2026-09-14",
            "details": "L 7, R 6.5, W 6, S 6.5",
            **changes,
        }

    def test_the_counsellor_can_add_change_and_remove_a_score(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)

        added = abc.client.post(scores_url(student), self.score(), format="json")
        score_id = added.json()["id"]
        changed = abc.client.patch(
            f"{scores_url(student)}{score_id}/", {"overall": 7}, format="json"
        )
        profile = abc.client.get(profile_url(student)).json()
        removed = abc.client.delete(f"{scores_url(student)}{score_id}/")

        assert added.status_code == status.HTTP_201_CREATED
        assert added.json() == {"id": score_id, **self.score()}
        assert changed.json()["overall"] == "7.0"
        assert profile["test_scores"] == [{"id": score_id, **self.score(overall="7.0")}]
        assert removed.status_code == status.HTTP_204_NO_CONTENT

    @pytest.mark.parametrize(
        ("test", "overall"),
        [("ielts", "9.0"), ("pte", "90.0"), ("toefl", "120.0"), ("duolingo", "160.0")],
    )
    def test_the_top_score_of_each_test_is_allowed(
        self, abc: ConsultancyContext, test: str, overall: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.post(
            scores_url(student), self.score(test=test, overall=overall), format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize(
        ("test", "overall"),
        [("ielts", "9.5"), ("pte", "91"), ("toefl", "121"), ("duolingo", "161")],
    )
    def test_a_score_above_the_tests_top_is_refused(
        self, abc: ConsultancyContext, test: str, overall: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.post(
            scores_url(student), self.score(test=test, overall=overall), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "overall" in response.json()

    def test_changing_only_the_test_checks_the_stored_score_against_it(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        score_id = abc.client.post(
            scores_url(student), self.score(test="pte", overall="65"), format="json"
        ).json()["id"]

        response = abc.client.patch(
            f"{scores_url(student)}{score_id}/", {"test": "ielts"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "overall" in response.json()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("test", "sat"),
            ("overall", "-1"),
            ("overall", "abc"),
            ("overall", "NaN"),
            ("overall", "6.55"),
            ("overall", True),
            ("overall", None),
            ("taken_on", "14/09/2026"),
            ("taken_on", "2999-01-01"),
            ("details", "x" * 201),
            ("details", "a\nb"),
            ("student", 2),
        ],
    )
    def test_bad_scores_are_refused(
        self, abc: ConsultancyContext, field: str, value: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.post(scores_url(student), self.score(**{field: value}), format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # "student" isn't a field here: it comes from the address, so it is refused as unknown.
        assert ("detail" if field == "student" else field) in response.json()

    def test_only_the_test_and_overall_score_are_needed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.post(
            scores_url(student), {"test": "pte", "overall": 58}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {
            "id": response.json()["id"],
            "test": "pte",
            "overall": "58.0",
            "taken_on": None,
            "details": "",
        }

    def test_front_desk_cannot_add_a_score_to_an_assigned_student(
        self, abc: ConsultancyContext
    ) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, counsellor)
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.post(scores_url(student), self.score(), format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == FRONT_DESK_LEADS_ONLY

    def test_a_score_of_another_student_is_not_found_under_this_one(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = make_student(abc)
        ram = make_student(abc, name="Ram Thapa")
        score_id = abc.client.post(scores_url(sita), self.score(), format="json").json()["id"]

        response = abc.client.delete(f"{scores_url(ram)}{score_id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_student_can_have_at_most_twenty_scores(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        for _ in range(20):
            abc.client.post(scores_url(student), self.score(), format="json")

        response = abc.client.post(scores_url(student), self.score(), format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "A Student can have at most 20 of these."}


class TestProfileServices:
    def test_details_outside_the_profile_are_never_changed_by_the_service(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.add_staff(Role.ADMIN)
        student = make_student(abc)

        with tenant_context(abc.consultancy), pytest.raises(ValueError, match="stage"):
            change_details(student, {"stage": Stage.VISA}, by=admin)
