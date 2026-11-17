"""Front Desk adds a Lead; Staff list, search and filter Students (spec 23-31; issue 09)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status
from rest_framework.test import APIClient

from staff.models import Role, Staff, StaffStatus
from students.models import Source, Stage, Student
from students.services import STUDENTS_PER_PAGE
from tests.conftest import (
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    use_access_token,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

STUDENTS_URL = "/api/students/"
NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE students_student; --",
    "}body{background:url(javascript:alert(1))}",
]
WORKS_WITH_STUDENTS = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)


def lead_body(**changes: object) -> dict[str, object]:
    """What the new-visitor form sends."""
    return {
        "name": "Anish Neupane",
        "phone": "9819988776",
        "email": "anish@gmail.test",
        "country": "canada",
        "source": "walk_in",
        "highest_qualification": "bachelor",
        "past_degree": "BBS, Tribhuvan University, 2023",
        "previous_job": "Accountant at a cooperative, 2 years",
        "note": "Wants a business diploma.\nBudget NPR 20 lakh.",
        **changes,
    }


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def add_lead(client: APIClient, **changes: object) -> _MonkeyPatchedResponse:
    """POST a new Lead."""
    return client.post(STUDENTS_URL, lead_body(**changes), format="json")


def make_student(
    office: ConsultancyContext, name: str, counsellor: Staff | None = None, **fields: object
) -> Student:
    """Store a Student directly, for list tests; `fields` override the defaults."""
    details: dict[str, object] = {
        "phone": "9800000000",
        "country": "australia",
        "source": Source.WALK_IN,
        "highest_qualification": "plus_two",
        "stage": Stage.LEAD,
        **fields,
    }
    with tenant_context(office.consultancy):
        return Student.objects.create(name=name, assigned_counsellor=counsellor, **details)


def add_counsellor(office: ConsultancyContext, email: str) -> Staff:
    """An active Counsellor."""
    return office.add_staff(Role.COUNSELLOR, email=email)


def names_in(response: _MonkeyPatchedResponse) -> list[str]:
    """The Students' names on a list page."""
    return [row["name"] for row in response.json()["results"]]


class TestAddLead:
    @pytest.mark.parametrize("role", WORKS_WITH_STUDENTS)
    def test_every_role_that_works_with_students_can_add_a_lead(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)

        response = add_lead(abc.client)

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["name"] == "Anish Neupane"
        assert body["stage"] == "lead"
        assert body["assigned_counsellor"] is None
        assert body["note"] == "Wants a business diploma.\nBudget NPR 20 lakh."

    def test_a_new_student_starts_as_a_lead_with_every_detail_stored(
        self, abc: ConsultancyContext
    ) -> None:
        desk = abc.log_in_as(Role.FRONT_DESK)

        student_id = add_lead(abc.client).json()["id"]

        with tenant_context(abc.consultancy):
            stored = Student.objects.get(pk=student_id)
        assert stored.stage == Stage.LEAD
        assert stored.phone == "9819988776"
        assert stored.email == "anish@gmail.test"
        assert stored.country == "canada"
        assert stored.source == Source.WALK_IN
        assert stored.highest_qualification == "bachelor"
        assert stored.past_degree == "BBS, Tribhuvan University, 2023"
        assert stored.previous_job == "Accountant at a cooperative, 2 years"
        assert stored.sub_agent_name == ""
        assert stored.created_by_id == desk.pk

    def test_the_answer_has_every_field_the_screen_needs(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        body = add_lead(abc.client).json()

        assert set(body) == {
            "id",
            "name",
            "phone",
            "email",
            "country",
            "highest_qualification",
            "past_degree",
            "previous_job",
            "source",
            "sub_agent_name",
            "note",
            "stage",
            "assigned_counsellor",
            "created_at",
        }

    def test_only_name_phone_country_source_and_qualification_are_needed(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        body = {
            "name": "Nisha Tamang",
            "phone": "9818877665",
            "country": "japan",
            "source": "facebook",
            "highest_qualification": "plus_two",
        }

        response = abc.client.post(STUDENTS_URL, body, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["email"] == ""
        assert response.json()["note"] == ""

    def test_the_phone_is_stored_without_spaces_or_dashes(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, phone="98410-22 33")

        assert response.json()["phone"] == "984102233"

    def test_the_email_is_stored_trimmed_and_lowercase(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, email="Anish@Gmail.TEST")

        assert response.json()["email"] == "anish@gmail.test"

    def test_a_counsellor_can_be_assigned_straight_away(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        counsellor = add_counsellor(abc, "bikash@abc.test")

        response = add_lead(abc.client, assigned_counsellor=counsellor.pk)

        assert response.json()["assigned_counsellor"] == {
            "id": counsellor.pk,
            "name": counsellor.name,
        }

    def test_an_admin_can_be_the_assigned_counsellor(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        admin = abc.add_staff(Role.ADMIN)

        response = add_lead(abc.client, assigned_counsellor=admin.pk)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["assigned_counsellor"]["id"] == admin.pk

    def test_no_counsellor_can_be_sent_as_null(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, assigned_counsellor=None)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["assigned_counsellor"] is None

    def test_adding_is_logged_with_ids_only(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        desk = abc.log_in_as(Role.FRONT_DESK)

        student_id = add_lead(abc.client).json()["id"]

        line = json_logs.only(f"Student added: student_id={student_id}, by_staff_id={desk.pk}")
        assert line["level"] == "INFO"
        assert "Anish" not in json_logs.text()
        assert "9819988776" not in json_logs.text()


class TestSubAgent:
    def test_a_sub_agent_lead_keeps_the_sub_agent_name(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, source="sub_agent", sub_agent_name="Himal Edu Link")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["sub_agent_name"] == "Himal Edu Link"

    @pytest.mark.parametrize("name", [None, "", "   "])
    def test_a_sub_agent_lead_needs_the_sub_agent_name(
        self, abc: ConsultancyContext, name: str | None
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        changes: dict[str, object] = {"source": "sub_agent"}
        if name is not None:
            changes["sub_agent_name"] = name

        response = add_lead(abc.client, **changes)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"sub_agent_name": ["Enter the Sub-agent's name."]}

    def test_a_sub_agent_name_is_refused_for_any_other_source(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, source="referral", sub_agent_name="Himal Edu Link")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "sub_agent_name": ["Only a Student who came through a Sub-agent has one."]
        }

    def test_an_empty_sub_agent_name_is_fine_for_another_source(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, source="referral", sub_agent_name="")

        assert response.status_code == status.HTTP_201_CREATED


class TestRefusedLeads:
    @pytest.mark.parametrize(
        "field", ["name", "phone", "country", "source", "highest_qualification"]
    )
    def test_a_needed_field_cannot_be_missing(self, abc: ConsultancyContext, field: str) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        body = lead_body()
        del body[field]

        response = abc.client.post(STUDENTS_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()

    @pytest.mark.parametrize(
        ("field", "message"),
        [("name", "Enter their full name."), ("phone", "Enter their mobile number.")],
    )
    def test_a_needed_field_cannot_be_blank_and_says_so_plainly(
        self, abc: ConsultancyContext, field: str, message: str
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        blank = add_lead(abc.client, **{field: "  "})
        body = lead_body()
        del body[field]
        missing = abc.client.post(STUDENTS_URL, body, format="json")

        assert blank.status_code == status.HTTP_400_BAD_REQUEST
        assert blank.json() == {field: [message]}
        assert missing.json() == {field: [message]}

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("country", "mars"),
            ("country", "Canada"),
            ("source", "Walk-in"),
            ("source", "tiktok"),
            ("highest_qualification", "phd"),
            ("stage", "visa"),
        ],
    )
    def test_values_outside_the_fixed_lists_are_refused(
        self, abc: ConsultancyContext, field: str, value: str
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, **{field: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # A new visitor has no "stage" field (it is always Lead), so it is refused as unknown.
        assert ("detail" if field == "stage" else field) in response.json()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", 123),
            ("name", ["Anish"]),
            ("phone", 9819988776),
            ("email", True),
            ("note", {"text": "x"}),
            ("country", 1),
            ("source", None),
            ("assigned_counsellor", "5"),
            ("assigned_counsellor", True),
            ("assigned_counsellor", 1.5),
        ],
    )
    def test_wrong_types_are_refused(
        self, abc: ConsultancyContext, field: str, value: object
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, **{field: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()

    @pytest.mark.parametrize(
        ("field", "length"),
        [
            ("name", 201),
            ("email", 255),
            ("past_degree", 201),
            ("previous_job", 201),
            ("note", 5001),
        ],
    )
    def test_over_long_values_are_refused(
        self, abc: ConsultancyContext, field: str, length: int
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        value = "a" * length if field != "email" else "a" * (length - 9) + "@abc.test"

        response = add_lead(abc.client, **{field: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()

    def test_an_over_long_sub_agent_name_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, source="sub_agent", sub_agent_name="a" * 201)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "sub_agent_name" in response.json()

    @pytest.mark.parametrize("phone", ["98-abc", "12345", "+97798123456789012", "9812 34567<"])
    def test_a_bad_phone_is_refused(self, abc: ConsultancyContext, phone: str) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, phone=phone)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "phone" in response.json()

    @pytest.mark.parametrize("email", ["not-an-email", "<script>@x", "a@b@c.test"])
    def test_a_bad_email_is_refused(self, abc: ConsultancyContext, email: str) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, email=email)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.json()

    @pytest.mark.parametrize("field", ["name", "past_degree", "previous_job"])
    def test_one_line_fields_refuse_line_breaks_and_control_characters(
        self, abc: ConsultancyContext, field: str
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, **{field: "Anish\nNeupane\x00"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()

    def test_the_note_keeps_line_breaks_but_refuses_other_control_characters(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, note="First line\x07")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "note" in response.json()

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, stage_reason="x", is_admin=True)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        with tenant_context(abc.consultancy):
            assert not Student.objects.exists()

    def test_the_stage_cannot_be_chosen_when_adding(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, stage="lead")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}

    @pytest.mark.parametrize("body", [[], "text", 5, None])
    def test_a_body_that_is_not_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.post(STUDENTS_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_text_is_stored_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, name=text, note=text, past_degree=text)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["name"] == text
        assert response["Content-Type"] == "application/json"
        with tenant_context(abc.consultancy):
            assert Student.objects.get().note == text

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_choices_are_refused(self, abc: ConsultancyContext, text: str) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, country=text, source=text)

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestAssignedCounsellorChoice:
    def test_an_unknown_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = add_lead(abc.client, assigned_counsellor=99999)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"assigned_counsellor": ["Choose a counsellor from the list."]}

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.TEACHER])
    def test_staff_who_dont_counsel_cannot_be_assigned(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        not_a_counsellor = abc.add_staff(role, email=f"other-{role.value}@abc.test")

        response = add_lead(abc.client, assigned_counsellor=not_a_counsellor.pk)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "assigned_counsellor" in response.json()

    @pytest.mark.parametrize("status_", [StaffStatus.INVITED, StaffStatus.DEACTIVATED])
    def test_a_counsellor_who_is_not_active_cannot_be_assigned(
        self, abc: ConsultancyContext, status_: StaffStatus
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        counsellor = add_counsellor(abc, "gone@abc.test")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=counsellor.pk).update(status=status_)

        response = add_lead(abc.client, assigned_counsellor=counsellor.pk)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "assigned_counsellor" in response.json()


class TestWhoCanUseStudents:
    def test_a_teacher_cannot_list_students(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(STUDENTS_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_a_teacher_cannot_add_a_student(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)

        response = add_lead(abc.client)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS
        with tenant_context(abc.consultancy):
            assert not Student.objects.exists()

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_nobody_logged_in_is_refused(self, abc: ConsultancyContext, method: str) -> None:
        response = getattr(abc.client, method)(STUDENTS_URL, lead_body(), format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_only_get_and_post_are_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(STUDENTS_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_a_deactivated_staff_members_token_stops_working(self, abc: ConsultancyContext) -> None:
        desk = abc.log_in_as(Role.FRONT_DESK)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=desk.pk).update(status=StaffStatus.DEACTIVATED)

        response = abc.client.get(STUDENTS_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_students_are_not_served_on_the_platform_host(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        response = platform_site.client.get(STUDENTS_URL)

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestStudentList:
    @pytest.mark.parametrize("role", WORKS_WITH_STUDENTS)
    def test_every_role_that_works_with_students_sees_every_student(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)
        counsellor = add_counsellor(abc, "bikash@abc.test")
        make_student(abc, "Sita Karki", counsellor=counsellor)
        make_student(abc, "Anish Neupane")

        response = abc.client.get(STUDENTS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert sorted(names_in(response)) == ["Anish Neupane", "Sita Karki"]

    def test_newest_students_come_first(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for name in ["First", "Second", "Third"]:
            make_student(abc, name)

        assert names_in(abc.client.get(STUDENTS_URL)) == ["Third", "Second", "First"]

    def test_a_row_has_what_the_list_shows(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        counsellor = add_counsellor(abc, "bikash@abc.test")
        student = make_student(
            abc, "Sita Karki", counsellor=counsellor, phone="9841234567", email="sita@x.test"
        )

        row = abc.client.get(STUDENTS_URL).json()["results"][0]

        assert row == {
            "id": student.pk,
            "name": "Sita Karki",
            "phone": "9841234567",
            "email": "sita@x.test",
            "country": "australia",
            "source": "walk_in",
            "stage": "lead",
            "assigned_counsellor": {"id": counsellor.pk, "name": counsellor.name},
        }

    def test_the_list_is_paginated(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for number in range(STUDENTS_PER_PAGE + 3):
            make_student(abc, f"Student {number:02d}")

        first = abc.client.get(STUDENTS_URL).json()
        second = abc.client.get(STUDENTS_URL, {"page": 2}).json()

        assert first["count"] == STUDENTS_PER_PAGE + 3
        assert first["page"] == 1
        assert first["page_size"] == STUDENTS_PER_PAGE
        assert len(first["results"]) == STUDENTS_PER_PAGE
        assert len(second["results"]) == 3
        assert second["page"] == 2

    def test_a_page_past_the_end_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, "Only one")

        response = abc.client.get(STUDENTS_URL, {"page": 2})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_empty_list_is_one_empty_page(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        body = abc.client.get(STUDENTS_URL).json()

        assert body["count"] == 0
        assert body["results"] == []


class TestSearch:
    @pytest.fixture
    def students(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        make_student(abc, "Sita Karki", phone="9841234567", email="sita.karki@gmail.test")
        make_student(abc, "Anish Neupane", phone="9819988776", email="anish@yahoo.test")
        make_student(abc, "Manisha Basnet", phone="9841022233")

    @pytest.mark.usefixtures("students")
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("sita", ["Sita Karki"]),
            ("NEUPANE", ["Anish Neupane"]),
            ("  karki  ", ["Sita Karki"]),
            ("98410", ["Manisha Basnet"]),
            ("98410-22", ["Manisha Basnet"]),
            ("9841 234", ["Sita Karki"]),
            ("yahoo", ["Anish Neupane"]),
            ("gmail.test", ["Sita Karki"]),
            ("nobody", []),
            ("", ["Manisha Basnet", "Anish Neupane", "Sita Karki"]),
        ],
    )
    def test_finds_students_by_name_phone_or_email(
        self, abc: ConsultancyContext, query: str, expected: list[str]
    ) -> None:
        response = abc.client.get(STUDENTS_URL, {"q": query})

        assert names_in(response) == expected

    @pytest.mark.usefixtures("students")
    @pytest.mark.parametrize("query", [*INJECTION_STRINGS, "%", "_", "\\"])
    def test_injection_shaped_searches_find_nothing_and_break_nothing(
        self, abc: ConsultancyContext, query: str
    ) -> None:
        response = abc.client.get(STUDENTS_URL, {"q": query})

        assert response.status_code == status.HTTP_200_OK
        assert names_in(response) == []

    def test_an_over_long_search_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.get(STUDENTS_URL, {"q": "a" * 101})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "q" in response.json()


class TestFilters:
    def test_filter_by_stage(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, "Lead one")
        make_student(abc, "Counselled", stage=Stage.COUNSELLING)

        response = abc.client.get(STUDENTS_URL, {"stage": "counselling"})

        assert names_in(response) == ["Counselled"]

    def test_filter_by_source(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, "Walked in")
        make_student(abc, "From Facebook", source=Source.FACEBOOK)

        response = abc.client.get(STUDENTS_URL, {"source": "facebook"})

        assert names_in(response) == ["From Facebook"]

    def test_filter_by_a_counsellor(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        bikash = add_counsellor(abc, "bikash@abc.test")
        anjali = add_counsellor(abc, "anjali@abc.test")
        make_student(abc, "Bikash's", counsellor=bikash)
        make_student(abc, "Anjali's", counsellor=anjali)

        response = abc.client.get(STUDENTS_URL, {"counsellor": str(anjali.pk)})

        assert names_in(response) == ["Anjali's"]

    def test_filter_by_a_counsellor_who_has_left_still_finds_their_students(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        gone = add_counsellor(abc, "gone@abc.test")
        make_student(abc, "Left behind", counsellor=gone)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=gone.pk).update(status=StaffStatus.DEACTIVATED)

        response = abc.client.get(STUDENTS_URL, {"counsellor": str(gone.pk)})

        assert names_in(response) == ["Left behind"]

    def test_filter_assigned_to_me(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        other = add_counsellor(abc, "other@abc.test")
        make_student(abc, "Mine", counsellor=me)
        make_student(abc, "Theirs", counsellor=other)
        make_student(abc, "Nobody's")

        response = abc.client.get(STUDENTS_URL, {"counsellor": "me"})

        assert names_in(response) == ["Mine"]

    def test_filter_no_counsellor_yet(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        counsellor = add_counsellor(abc, "bikash@abc.test")
        make_student(abc, "Assigned", counsellor=counsellor)
        make_student(abc, "Waiting")

        response = abc.client.get(STUDENTS_URL, {"counsellor": "none"})

        assert names_in(response) == ["Waiting"]

    def test_without_a_counsellor_filter_a_counsellor_sees_everyone(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        other = add_counsellor(abc, "other@abc.test")
        make_student(abc, "Mine", counsellor=me)
        make_student(abc, "Theirs", counsellor=other)

        response = abc.client.get(STUDENTS_URL)

        assert sorted(names_in(response)) == ["Mine", "Theirs"]

    def test_filters_and_search_combine(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        make_student(abc, "Sita Mine", counsellor=me, stage=Stage.COUNSELLING)
        make_student(abc, "Sita Lead", counsellor=me)
        make_student(abc, "Ram Mine", counsellor=me, stage=Stage.COUNSELLING)

        response = abc.client.get(
            STUDENTS_URL, {"q": "sita", "stage": "counselling", "counsellor": "me"}
        )

        assert names_in(response) == ["Sita Mine"]

    @pytest.mark.parametrize(
        ("param", "value"),
        [
            ("stage", "Lead"),
            ("stage", "nowhere"),
            ("source", "walk-in"),
            ("counsellor", "someone"),
            ("counsellor", "-1"),
            ("counsellor", "1; DROP TABLE"),
            ("counsellor", "1" * 30),
            ("page", "0"),
            ("page", "two"),
            ("ordering", "name"),
        ],
    )
    def test_bad_filters_are_refused(self, abc: ConsultancyContext, param: str, value: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(STUDENTS_URL, {param: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # There is no "ordering" filter, so it is refused as unknown.
        assert ("detail" if param == "ordering" else param) in response.json()

    def test_a_counsellor_id_nobody_has_finds_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, "Someone")

        response = abc.client.get(STUDENTS_URL, {"counsellor": "99999"})

        assert response.status_code == status.HTTP_200_OK
        assert names_in(response) == []


class TestAssignedToMe:
    def test_means_the_person_whose_token_it_is(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        other = add_counsellor(abc, "other@abc.test")
        make_student(abc, "Mine", counsellor=me)
        make_student(abc, "Theirs", counsellor=other)
        client = abc.new_client()
        use_access_token(client, abc.tokens_for(other).access)

        response = client.get(STUDENTS_URL, {"counsellor": "me"})

        assert names_in(response) == ["Theirs"]
