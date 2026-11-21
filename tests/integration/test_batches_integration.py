"""Batches (shown as "classes"): create, edit, add and remove Students, Teachers see their own.

Spec 22, 83-84, 86a, 87; issue 23.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from batches.models import Batch, BatchMembership
from batches.serializers import BatchDateField, StudentChoiceField, TeacherField, years_from
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff, StaffStatus
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

BATCHES_URL = "/api/batches/"
TEACHERS_URL = "/api/batches/teachers/"
ENROLLED_URL = "/api/batches/students/"
CHANGING_ROLES = (Role.ADMIN, Role.COUNSELLOR)
SEEING_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.TEACHER)
NOT_FOR_FRONT_DESK = {"detail": "Only Admins, counsellors and teachers can see classes."}
ONLY_ADMIN_OR_COUNSELLOR = {"detail": "Only an Admin or a counsellor can change classes."}
ONLY_ADMIN_OR_COUNSELLOR_SEES = {"detail": "Only counsellors and Admins can see this."}
NAME_MESSAGE = {"name": ["Give the class a name."]}
TEST_TYPE_MESSAGE = {"test_type": ["Choose a test type from the list."]}
TEACHER_MESSAGE = {"teacher": ["Choose a teacher from the list."]}
TIME_SLOT_MESSAGE = {"time_slot": ["Say when the class meets, e.g. 7:00-8:30 AM."]}
STARTS_ON_MESSAGE = {"starts_on": ["Enter a start date within 5 years of today."]}
ENDS_ON_MESSAGE = {"ends_on": ["Enter an end date within 5 years of today."]}
ENDS_BEFORE_STARTS = {"ends_on": ["It can't end before it starts."]}
STUDENT_MESSAGE = {"student": ["Choose a Student from the list."]}
ALREADY_IN = {"student": ["Already in this class."]}
LOST_CANT_JOIN = {"student": ["A Lost Student can't join a class."]}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with test-prep classes switched on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.BATCHES})
    return office


def batch_url(batch: Batch | int) -> str:
    """One Batch."""
    pk = batch if isinstance(batch, int | str) else batch.public_id
    return f"/api/batches/{pk}/"


def members_url(batch: Batch | int) -> str:
    """A Batch's Students."""
    pk = batch if isinstance(batch, int | str) else batch.public_id
    return f"/api/batches/{pk}/students/"


def member_url(batch: Batch | int, student: Student | int | str) -> str:
    """One Student in a Batch."""
    batch_pk = batch if isinstance(batch, int | str) else batch.public_id
    student_pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/batches/{batch_pk}/students/{student_pk}/"


def day(offset: int) -> date:
    """Today in the office's time zone, moved by `offset` days."""
    return timezone.localdate() + timedelta(days=offset)


def good_body(teacher: Staff, /, **changes: object) -> dict[str, object]:
    """A Batch the server accepts; `changes` override it."""
    return {
        "name": "IELTS Morning",
        "test_type": "ielts",
        "teacher": str(teacher.public_id),
        "time_slot": "7:00-8:30 AM",
        "starts_on": day(5).isoformat(),
        "ends_on": day(65).isoformat(),
        **changes,
    }


def create(
    office: ConsultancyContext, teacher: Staff, /, **changes: object
) -> _MonkeyPatchedResponse:
    """POST a new Batch."""
    return office.client.post(BATCHES_URL, good_body(teacher, **changes), format="json")


def make_batch(
    office: ConsultancyContext,
    teacher: Staff,
    students: tuple[Student, ...] = (),
    **fields: object,
) -> Batch:
    """Store a Batch directly, with `students` in it."""
    details: dict[str, object] = {
        "name": "PTE Day",
        "test_type": "pte",
        "time_slot": "11:00 AM-12:30 PM",
        "starts_on": day(-10),
        "ends_on": day(30),
        **fields,
    }
    with tenant_context(office.consultancy):
        batch = Batch.objects.create(teacher=teacher, **details)
        for student in students:
            BatchMembership.objects.create(batch=batch, student=student)
    return batch


def stored_batches(office: ConsultancyContext) -> list[Batch]:
    """Every stored Batch, oldest first."""
    with tenant_context(office.consultancy):
        return list(Batch.objects.order_by("pk"))


def member_names(office: ConsultancyContext, batch: Batch) -> list[str]:
    """Who is stored in the Batch, by name."""
    with tenant_context(office.consultancy):
        return sorted(
            BatchMembership.objects.filter(batch=batch).values_list("student__name", flat=True)
        )


def listed_names(office: ConsultancyContext) -> list[str]:
    """The Batch names on the list, in order."""
    return [batch["name"] for batch in office.client.get(BATCHES_URL).json()["results"]]


@pytest.fixture
def teacher(abc: ConsultancyContext) -> Staff:
    """A Teacher of the office."""
    return named_staff(abc, "Gita Lama", Role.TEACHER)


class TestCreating:
    @pytest.mark.parametrize("role", CHANGING_ROLES)
    def test_an_admin_or_counsellor_creates_a_class(
        self, abc: ConsultancyContext, teacher: Staff, role: Role
    ) -> None:
        abc.log_in_as(role)

        response = create(abc, teacher)

        assert response.status_code == status.HTTP_201_CREATED
        batch = stored_batches(abc)[0]
        assert response.json() == {
            "id": str(batch.public_id),
            "name": "IELTS Morning",
            "test_type": "ielts",
            "teacher": {"id": str(teacher.public_id), "name": "Gita Lama"},
            "time_slot": "7:00-8:30 AM",
            "starts_on": day(5).isoformat(),
            "ends_on": day(65).isoformat(),
            "timing": "upcoming",
            "student_count": 0,
            "attendance_taken_today": False,
            "can_change": True,
            "students": [],
        }

    @pytest.mark.parametrize("test_type", ["ielts", "pte", "toefl", "duolingo", "japanese", "sat"])
    def test_every_test_type_is_accepted(
        self, abc: ConsultancyContext, teacher: Staff, test_type: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        assert create(abc, teacher, test_type=test_type).json()["test_type"] == test_type

    def test_one_day_classes_and_past_starts_are_accepted(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = create(abc, teacher, starts_on=day(-3).isoformat(), ends_on=day(-3).isoformat())

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["timing"] == "finished"

    def test_name_and_time_slot_are_trimmed(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.ADMIN)

        body = create(abc, teacher, name="  IELTS Morning ", time_slot=" 7 AM ").json()

        assert (body["name"], body["time_slot"]) == ("IELTS Morning", "7 AM")

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.TEACHER])
    def test_others_cant_create_a_class(
        self, abc: ConsultancyContext, teacher: Staff, role: Role
    ) -> None:
        abc.log_in_as(role)

        response = create(abc, teacher)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == (
            NOT_FOR_FRONT_DESK if role == Role.FRONT_DESK else ONLY_ADMIN_OR_COUNSELLOR
        )
        assert stored_batches(abc) == []

    def test_logs_carry_ids_only(
        self, abc: ConsultancyContext, teacher: Staff, json_logs: JsonLogs
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        batch_id = create(abc, teacher).json()["id"]
        with tenant_context(abc.consultancy):  # logs keep the number
            batch_id = Batch.objects.get(public_id=batch_id).pk

        json_logs.only(
            f"Batch created: batch_id={batch_id}, teacher_id={teacher.pk}, by_staff_id={me.pk}"
        )
        assert "IELTS Morning" not in json_logs.text()


class TestRefusedInput:
    @pytest.fixture(autouse=True)
    def admin(self, abc: ConsultancyContext) -> Staff:
        return abc.log_in_as(Role.ADMIN)

    @pytest.mark.parametrize("name", ["", "   ", None, 7, True, ["IELTS"], {"x": 1}])
    def test_a_missing_or_wrong_name_is_refused(
        self, abc: ConsultancyContext, teacher: Staff, name: object
    ) -> None:
        response = create(abc, teacher, name=name)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NAME_MESSAGE
        assert stored_batches(abc) == []

    def test_an_over_long_name_is_refused(self, abc: ConsultancyContext, teacher: Staff) -> None:
        assert create(abc, teacher, name="x" * 80).status_code == status.HTTP_201_CREATED

        response = create(abc, teacher, name="x" * 81)

        assert response.json() == {"name": ["Use at most 80 characters."]}
        assert len(stored_batches(abc)) == 1

    @pytest.mark.parametrize("field", ["name", "time_slot"])
    def test_control_characters_are_refused(
        self, abc: ConsultancyContext, teacher: Staff, field: str
    ) -> None:
        response = create(abc, teacher, **{field: "IELTS\nMorning"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == [field]

    @pytest.mark.parametrize("test_type", ["", "IELTS", "gre", None, 1, ["ielts"]])
    def test_a_bad_test_type_is_refused(
        self, abc: ConsultancyContext, teacher: Staff, test_type: object
    ) -> None:
        response = create(abc, teacher, test_type=test_type)

        assert response.json() == TEST_TYPE_MESSAGE
        assert stored_batches(abc) == []

    @pytest.mark.parametrize("value", ["", "7", None, 7.0, True, 2**63, -1, 999_999])
    def test_a_bad_teacher_is_refused(
        self, abc: ConsultancyContext, teacher: Staff, value: object
    ) -> None:
        response = create(abc, teacher, teacher=value)

        assert response.json() == TEACHER_MESSAGE

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_only_a_teacher_can_teach(
        self, abc: ConsultancyContext, teacher: Staff, role: Role
    ) -> None:
        other = abc.add_staff(role, email=f"not-teacher-{role.value}@abc.test")

        response = create(abc, teacher, teacher=str(other.public_id))

        assert response.json() == TEACHER_MESSAGE

    @pytest.mark.parametrize("staff_status", [StaffStatus.INVITED, StaffStatus.DEACTIVATED])
    def test_a_teacher_who_isnt_active_cant_teach(
        self, abc: ConsultancyContext, teacher: Staff, staff_status: StaffStatus
    ) -> None:
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=teacher.pk).update(status=staff_status)

        assert create(abc, teacher).json() == TEACHER_MESSAGE

    @pytest.mark.parametrize("time_slot", ["", "  ", None, 7, ["7 AM"]])
    def test_a_missing_or_wrong_time_slot_is_refused(
        self, abc: ConsultancyContext, teacher: Staff, time_slot: object
    ) -> None:
        assert create(abc, teacher, time_slot=time_slot).json() == TIME_SLOT_MESSAGE

    def test_an_over_long_time_slot_is_refused(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        assert create(abc, teacher, time_slot="x" * 40).status_code == status.HTTP_201_CREATED

        response = create(abc, teacher, time_slot="x" * 41)

        assert response.json() == {"time_slot": ["Use at most 40 characters."]}

    @pytest.mark.parametrize(
        "value", ["", "tomorrow", "2026-13-01", "2026-02-30", "2026/11/02", "02-11-2026", None, 1]
    )
    @pytest.mark.parametrize("field", ["starts_on", "ends_on"])
    def test_a_bad_date_is_refused(
        self, abc: ConsultancyContext, teacher: Staff, field: str, value: object
    ) -> None:
        response = create(abc, teacher, **{field: value})

        assert response.json() == (STARTS_ON_MESSAGE if field == "starts_on" else ENDS_ON_MESSAGE)

    @pytest.mark.parametrize("offset", [-5 * 366 - 1, 5 * 366 + 1])
    def test_a_date_more_than_five_years_away_is_refused(
        self, abc: ConsultancyContext, teacher: Staff, offset: int
    ) -> None:
        far = day(offset).isoformat()

        response = create(abc, teacher, starts_on=far, ends_on=far)

        assert response.json() == {**STARTS_ON_MESSAGE, **ENDS_ON_MESSAGE}

    def test_it_cant_end_before_it_starts(self, abc: ConsultancyContext, teacher: Staff) -> None:
        response = create(abc, teacher, starts_on=day(10).isoformat(), ends_on=day(9).isoformat())

        assert response.json() == ENDS_BEFORE_STARTS
        assert stored_batches(abc) == []

    def test_every_field_is_required(self, abc: ConsultancyContext) -> None:
        response = abc.client.post(BATCHES_URL, {}, format="json")

        assert response.json() == {
            **NAME_MESSAGE,
            **TEST_TYPE_MESSAGE,
            **TEACHER_MESSAGE,
            **TIME_SLOT_MESSAGE,
            **STARTS_ON_MESSAGE,
            **ENDS_ON_MESSAGE,
        }

    @pytest.mark.parametrize("field", ["students", "id", "created_at", "is_admin"])
    def test_an_unknown_field_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, teacher: Staff, field: str
    ) -> None:
        response = create(abc, teacher, **{field: 1})

        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_batches(abc) == []

    @pytest.mark.parametrize("body", [["IELTS"], "IELTS", 1])
    def test_a_body_that_isnt_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        response = abc.client.post(BATCHES_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_batches(abc) == []

    def test_a_form_body_is_refused(self, abc: ConsultancyContext, teacher: Staff) -> None:
        response = abc.client.post(BATCHES_URL, good_body(teacher))

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        assert stored_batches(abc) == []

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_text_is_kept_as_plain_text(
        self, abc: ConsultancyContext, teacher: Staff, text: str
    ) -> None:
        response = create(abc, teacher, name=text[:80], time_slot=text[:40])

        assert response.status_code == status.HTTP_201_CREATED
        assert (response.json()["name"], response.json()["time_slot"]) == (text[:80], text[:40])
        assert stored_batches(abc)[0].name == text[:80]


class TestEditing:
    @pytest.mark.parametrize("role", CHANGING_ROLES)
    def test_an_admin_or_counsellor_edits_any_class(
        self, abc: ConsultancyContext, teacher: Staff, role: Role
    ) -> None:
        abc.log_in_as(role)
        batch = make_batch(abc, teacher)
        other_teacher = named_staff(abc, "Hari Rai", Role.TEACHER)

        response = abc.client.put(
            batch_url(batch),
            good_body(other_teacher, name="PTE Evening", test_type="pte"),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["teacher"] == {
            "id": str(other_teacher.public_id),
            "name": "Hari Rai",
        }
        stored = stored_batches(abc)[0]
        assert (stored.name, stored.test_type, stored.teacher_id) == (
            "PTE Evening",
            "pte",
            other_teacher.pk,
        )

    def test_editing_keeps_the_students(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.ADMIN)
        batch = make_batch(abc, teacher, (make_student(abc),))

        body = abc.client.put(batch_url(batch), good_body(teacher), format="json").json()

        assert body["student_count"] == 1
        assert member_names(abc, batch) == ["Sita Gurung"]

    def test_bad_input_changes_nothing(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.ADMIN)
        batch = make_batch(abc, teacher)

        response = abc.client.put(
            batch_url(batch), good_body(teacher, name="", students=[1]), format="json"
        )

        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_batches(abc)[0].name == "PTE Day"

    def test_every_field_is_needed_to_edit(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.ADMIN)
        batch = make_batch(abc, teacher)

        response = abc.client.put(batch_url(batch), {"name": "New name"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_batches(abc)[0].name == "PTE Day"

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.TEACHER])
    def test_others_cant_edit(self, abc: ConsultancyContext, teacher: Staff, role: Role) -> None:
        batch = make_batch(abc, teacher)
        me = teacher if role == Role.TEACHER else abc.add_staff(role)
        log_in(abc, me)

        response = abc.client.put(batch_url(batch), good_body(teacher, name="Mine"), format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert stored_batches(abc)[0].name == "PTE Day"

    def test_an_unknown_class_is_not_found(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(batch_url(999_999), good_body(teacher), format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logs_carry_ids_only(
        self, abc: ConsultancyContext, teacher: Staff, json_logs: JsonLogs
    ) -> None:
        me = abc.log_in_as(Role.ADMIN)
        batch = make_batch(abc, teacher)

        abc.client.put(batch_url(batch), good_body(teacher, name="Secret name"), format="json")

        json_logs.only(f"Batch changed: batch_id={batch.pk}, by_staff_id={me.pk}")
        assert "Secret name" not in json_logs.text()


class TestListing:
    def test_running_and_upcoming_come_first_then_finished_latest_first(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        make_batch(abc, teacher, name="Old", starts_on=day(-90), ends_on=day(-60))
        make_batch(abc, teacher, name="Later", starts_on=day(20), ends_on=day(80))
        make_batch(abc, teacher, name="Recent", starts_on=day(-50), ends_on=day(-1))
        make_batch(abc, teacher, name="Now", starts_on=day(-10), ends_on=day(10))
        make_batch(abc, teacher, name="Ends today", starts_on=day(-5), ends_on=day(0))

        body = abc.client.get(BATCHES_URL).json()

        assert [b["name"] for b in body["results"]] == [
            "Now",
            "Ends today",
            "Later",
            "Recent",
            "Old",
        ]
        assert [b["timing"] for b in body["results"]] == [
            "running",
            "running",
            "upcoming",
            "finished",
            "finished",
        ]
        assert (body["count"], body["page"], body["page_size"]) == (5, 1, 25)

    def test_each_class_counts_its_students(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = make_student(abc)
        ram = make_student(abc, name="Ram Shrestha", phone="9841000002")
        make_batch(abc, teacher, (sita, ram))

        [listed] = abc.client.get(BATCHES_URL).json()["results"]

        assert listed["student_count"] == 2
        assert "students" not in listed

    def test_the_list_is_paged(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.ADMIN)
        for number in range(26):
            make_batch(abc, teacher, name=f"Class {number:02}")

        second = abc.client.get(BATCHES_URL, {"page": 2}).json()

        assert (second["count"], len(second["results"])) == (26, 1)
        assert abc.client.get(BATCHES_URL, {"page": 3}).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("query", [{"page": "0"}, {"page": "x"}, {"teacher": "1"}])
    def test_bad_queries_are_refused(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(BATCHES_URL, query).status_code == status.HTTP_400_BAD_REQUEST

    def test_a_teacher_sees_only_the_classes_they_teach(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        other = named_staff(abc, "Hari Rai", Role.TEACHER)
        make_batch(abc, teacher, name="Gita's class")
        make_batch(abc, other, name="Hari's class")
        log_in(abc, teacher)

        body = abc.client.get(BATCHES_URL).json()

        assert [b["name"] for b in body["results"]] == ["Gita's class"]
        assert body["results"][0]["can_change"] is False

    def test_front_desk_cant_see_classes(self, abc: ConsultancyContext, teacher: Staff) -> None:
        make_batch(abc, teacher)
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.get(BATCHES_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_FRONT_DESK

    def test_a_deactivated_teacher_keeps_their_name_on_the_class(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        make_batch(abc, teacher)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=teacher.pk).update(status=StaffStatus.DEACTIVATED)
        abc.log_in_as(Role.ADMIN)

        [listed] = abc.client.get(BATCHES_URL).json()["results"]

        assert listed["teacher"] == {"id": str(teacher.public_id), "name": "Gita Lama"}


class TestOneClass:
    def test_it_lists_its_students_by_name(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        sita = make_student(abc)
        anil = make_student(abc, name="Anil Rai", phone="9841000003")
        batch = make_batch(abc, teacher, (sita, anil))

        body = abc.client.get(batch_url(batch)).json()

        assert body["students"] == [
            {"id": str(anil.public_id), "name": "Anil Rai", "attended_percent": None},
            {"id": str(sita.public_id), "name": "Sita Gurung", "attended_percent": None},
        ]
        assert body["student_count"] == 2
        assert body["can_change"] is True

    def test_its_teacher_sees_its_students(self, abc: ConsultancyContext, teacher: Staff) -> None:
        batch = make_batch(abc, teacher, (make_student(abc),))
        log_in(abc, teacher)

        body = abc.client.get(batch_url(batch)).json()

        assert [s["name"] for s in body["students"]] == ["Sita Gurung"]
        assert body["can_change"] is False

    def test_another_teacher_finds_nothing(self, abc: ConsultancyContext, teacher: Staff) -> None:
        batch = make_batch(abc, teacher, (make_student(abc),))
        log_in(abc, named_staff(abc, "Hari Rai", Role.TEACHER))

        response = abc.client.get(batch_url(batch))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Sita" not in response.content.decode()

    def test_a_teacher_never_sees_finances_or_applications(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        batch = make_batch(abc, teacher, (make_student(abc),))
        log_in(abc, teacher)

        [student] = abc.client.get(batch_url(batch)).json()["students"]

        assert set(student) == {"id", "name", "attended_percent"}

    def test_front_desk_is_refused(self, abc: ConsultancyContext, teacher: Staff) -> None:
        batch = make_batch(abc, teacher)
        abc.log_in_as(Role.FRONT_DESK)

        assert abc.client.get(batch_url(batch)).json() == NOT_FOR_FRONT_DESK

    def test_an_unknown_class_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(batch_url(999_999)).status_code == status.HTTP_404_NOT_FOUND


class TestAddingStudents:
    @pytest.mark.parametrize("role", CHANGING_ROLES)
    def test_an_admin_or_counsellor_adds_any_student(
        self, abc: ConsultancyContext, teacher: Staff, role: Role
    ) -> None:
        abc.log_in_as(role)
        batch = make_batch(abc, teacher)
        # Not their own Student: any counsellor fills any class (spec 84).
        student = make_student(abc, named_staff(abc, "Anjali Gurung"), stage=Stage.COUNSELLING)

        response = abc.client.post(
            members_url(batch), {"student": str(student.public_id)}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["students"] == [
            {"id": str(student.public_id), "name": "Sita Gurung", "attended_percent": None}
        ]
        assert member_names(abc, batch) == ["Sita Gurung"]

    def test_a_student_can_be_in_several_classes(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        first = make_batch(abc, teacher, (student,))
        second = make_batch(abc, teacher, name="IELTS Evening")

        abc.client.post(members_url(second), {"student": str(student.public_id)}, format="json")

        assert member_names(abc, first) == member_names(abc, second) == ["Sita Gurung"]

    def test_adding_twice_is_refused(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        batch = make_batch(abc, teacher, (student,))

        response = abc.client.post(
            members_url(batch), {"student": str(student.public_id)}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ALREADY_IN
        assert member_names(abc, batch) == ["Sita Gurung"]

    def test_a_lost_student_cant_join(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.LOST, lost_reason="Moved away.")
        batch = make_batch(abc, teacher)

        response = abc.client.post(
            members_url(batch), {"student": str(student.public_id)}, format="json"
        )

        assert response.json() == LOST_CANT_JOIN
        assert member_names(abc, batch) == []

    @pytest.mark.parametrize("value", ["", "1", None, 1.0, True, -1, 2**63, 999_999, [1]])
    def test_a_bad_student_is_refused(
        self, abc: ConsultancyContext, teacher: Staff, value: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        batch = make_batch(abc, teacher)

        response = abc.client.post(members_url(batch), {"student": value}, format="json")

        assert response.json() == STUDENT_MESSAGE

    def test_unknown_fields_are_refused(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        batch = make_batch(abc, teacher)

        response = abc.client.post(
            members_url(batch), {"student": str(student.public_id), "batch": 9}, format="json"
        )

        assert response.json() == {"detail": ["Unknown field."]}
        assert member_names(abc, batch) == []

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.TEACHER])
    def test_others_cant_add(self, abc: ConsultancyContext, teacher: Staff, role: Role) -> None:
        batch = make_batch(abc, teacher)
        student = make_student(abc)
        log_in(abc, teacher if role == Role.TEACHER else abc.add_staff(role))

        response = abc.client.post(
            members_url(batch), {"student": str(student.public_id)}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert member_names(abc, batch) == []

    def test_an_unknown_class_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.post(
            members_url(999_999), {"student": str(student.public_id)}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logs_carry_ids_only(
        self, abc: ConsultancyContext, teacher: Staff, json_logs: JsonLogs
    ) -> None:
        me = abc.log_in_as(Role.ADMIN)
        batch = make_batch(abc, teacher)
        student = make_student(abc)

        abc.client.post(members_url(batch), {"student": str(student.public_id)}, format="json")

        json_logs.only(
            f"Student added to Batch: batch_id={batch.pk}, student_id={student.pk}, "
            f"by_staff_id={me.pk}"
        )
        assert "Sita" not in json_logs.text()


class TestRemovingStudents:
    @pytest.mark.parametrize("role", CHANGING_ROLES)
    def test_an_admin_or_counsellor_removes_a_student(
        self, abc: ConsultancyContext, teacher: Staff, role: Role
    ) -> None:
        abc.log_in_as(role)
        sita = make_student(abc)
        ram = make_student(abc, name="Ram Shrestha", phone="9841000002")
        batch = make_batch(abc, teacher, (sita, ram))

        response = abc.client.delete(member_url(batch, sita))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert member_names(abc, batch) == ["Ram Shrestha"]
        with tenant_context(abc.consultancy):
            assert Student.objects.filter(pk=sita.pk).exists()

    def test_a_student_not_in_the_class_is_not_found(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        make_batch(abc, teacher, (student,))
        other = make_batch(abc, teacher, name="Other")

        assert abc.client.delete(member_url(other, student)).status_code == 404
        assert abc.client.delete(member_url(999_999, student)).status_code == 404

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.TEACHER])
    def test_others_cant_remove(self, abc: ConsultancyContext, teacher: Staff, role: Role) -> None:
        student = make_student(abc)
        batch = make_batch(abc, teacher, (student,))
        log_in(abc, teacher if role == Role.TEACHER else abc.add_staff(role))

        response = abc.client.delete(member_url(batch, student))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert member_names(abc, batch) == ["Sita Gurung"]

    def test_logs_carry_ids_only(
        self, abc: ConsultancyContext, teacher: Staff, json_logs: JsonLogs
    ) -> None:
        me = abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        batch = make_batch(abc, teacher, (student,))

        abc.client.delete(member_url(batch, student))

        json_logs.only(
            f"Student removed from Batch: batch_id={batch.pk}, student_id={student.pk}, "
            f"by_staff_id={me.pk}"
        )


class TestStudentsAndTheirClasses:
    def test_each_enrolled_student_with_their_classes(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        sita = make_student(abc)
        anil = make_student(abc, name="Anil Rai", phone="9841000003")
        make_student(abc, name="Not enrolled", phone="9841000004")
        morning = make_batch(abc, teacher, (sita, anil), name="IELTS Morning")
        pte = make_batch(abc, teacher, (sita,), name="PTE Day")

        body = abc.client.get(ENROLLED_URL).json()

        assert body == {
            "count": 2,
            "page": 1,
            "page_size": 25,
            "results": [
                {
                    "id": str(anil.public_id),
                    "name": "Anil Rai",
                    "batches": [{"id": str(morning.public_id), "name": "IELTS Morning"}],
                    "attended_percent": None,
                },
                {
                    "id": str(sita.public_id),
                    "name": "Sita Gurung",
                    "batches": [
                        {"id": str(morning.public_id), "name": "IELTS Morning"},
                        {"id": str(pte.public_id), "name": "PTE Day"},
                    ],
                    "attended_percent": None,
                },
            ],
        }

    def test_it_is_paged(self, abc: ConsultancyContext, teacher: Staff) -> None:
        abc.log_in_as(Role.ADMIN)
        students = tuple(
            make_student(abc, name=f"Student {n:02}", phone=f"98410001{n:02}") for n in range(26)
        )
        make_batch(abc, teacher, students)

        second = abc.client.get(ENROLLED_URL, {"page": 2}).json()

        assert (second["count"], [s["name"] for s in second["results"]]) == (26, ["Student 25"])
        assert abc.client.get(ENROLLED_URL, {"page": 3}).status_code == 404
        assert abc.client.get(ENROLLED_URL, {"page": "x"}).status_code == 400

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.TEACHER])
    def test_others_are_refused(self, abc: ConsultancyContext, teacher: Staff, role: Role) -> None:
        make_batch(abc, teacher, (make_student(abc),))
        log_in(abc, teacher if role == Role.TEACHER else abc.add_staff(role))

        response = abc.client.get(ENROLLED_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == (
            NOT_FOR_FRONT_DESK if role == Role.FRONT_DESK else ONLY_ADMIN_OR_COUNSELLOR_SEES
        )


class TestTeachers:
    @pytest.mark.parametrize("role", CHANGING_ROLES)
    def test_lists_active_teachers_by_name(
        self, abc: ConsultancyContext, teacher: Staff, role: Role
    ) -> None:
        abc.log_in_as(role)
        hari = named_staff(abc, "Hari Rai", Role.TEACHER)
        gone = named_staff(abc, "Anita Gone", Role.TEACHER)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=gone.pk).update(status=StaffStatus.DEACTIVATED)

        assert abc.client.get(TEACHERS_URL).json() == [
            {"id": str(teacher.public_id), "name": "Gita Lama"},
            {"id": str(hari.public_id), "name": "Hari Rai"},
        ]

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.TEACHER])
    def test_others_are_refused(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        assert abc.client.get(TEACHERS_URL).status_code == status.HTTP_403_FORBIDDEN


class TestModuleSwitch:
    def test_every_endpoint_is_not_found_while_classes_are_off(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        student = make_student(abc)
        batch = make_batch(abc, teacher, (student,))
        abc.log_in_as(Role.ADMIN)
        switch_modules(abc.consultancy, set())

        responses = [
            abc.client.get(BATCHES_URL),
            create(abc, teacher),
            abc.client.get(batch_url(batch)),
            abc.client.put(batch_url(batch), good_body(teacher), format="json"),
            abc.client.post(members_url(batch), {"student": str(student.public_id)}, format="json"),
            abc.client.delete(member_url(batch, student)),
            abc.client.get(ENROLLED_URL),
            abc.client.get(TEACHERS_URL),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 8
        assert len(stored_batches(abc)) == 1
        assert member_names(abc, batch) == ["Sita Gurung"]

    @pytest.mark.parametrize("role", list(Role))
    def test_no_role_reaches_classes_while_off(
        self, abc: ConsultancyContext, teacher: Staff, role: Role
    ) -> None:
        make_batch(abc, teacher)
        log_in(abc, teacher if role == Role.TEACHER else abc.add_staff(role))
        switch_modules(abc.consultancy, set())

        assert abc.client.get(BATCHES_URL).status_code == status.HTTP_404_NOT_FOUND

    def test_the_classes_come_back_when_switched_on_again(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        make_batch(abc, teacher, (make_student(abc),), name="Kept class")
        abc.log_in_as(Role.ADMIN)
        switch_modules(abc.consultancy, set())
        switch_modules(abc.consultancy, {Module.BATCHES})

        [listed] = abc.client.get(BATCHES_URL).json()["results"]

        assert (listed["name"], listed["student_count"]) == ("Kept class", 1)


class TestAccess:
    def test_logged_out_is_refused_everywhere_whether_on_or_off(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        student = make_student(abc)
        batch = make_batch(abc, teacher, (student,))
        client = abc.new_client()

        def calls() -> list[int]:
            return [
                client.get(BATCHES_URL).status_code,
                client.post(BATCHES_URL, good_body(teacher), format="json").status_code,
                client.get(batch_url(batch)).status_code,
                client.put(batch_url(batch), good_body(teacher), format="json").status_code,
                client.post(members_url(batch), {"student": 1}, format="json").status_code,
                client.delete(member_url(batch, student)).status_code,
                client.get(ENROLLED_URL).status_code,
                client.get(TEACHERS_URL).status_code,
            ]

        on = calls()
        switch_modules(abc.consultancy, set())

        assert on == calls() == [status.HTTP_401_UNAUTHORIZED] * 8
        assert member_names(abc, batch) == ["Sita Gurung"]

    def test_a_deactivated_staff_member_is_refused(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        me = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=me.pk).update(status=StaffStatus.DEACTIVATED)

        response = create(abc, teacher)

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
        assert stored_batches(abc) == []

    @pytest.mark.parametrize(
        ("url", "methods"),
        [
            (BATCHES_URL, ["put", "patch", "delete"]),
            (TEACHERS_URL, ["post", "put", "patch", "delete"]),
            (ENROLLED_URL, ["post", "put", "patch", "delete"]),
        ],
    )
    def test_lists_allow_only_their_methods(
        self, abc: ConsultancyContext, url: str, methods: list[str]
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        codes = [getattr(abc.client, m)(url, {}, format="json").status_code for m in methods]

        assert codes == [status.HTTP_405_METHOD_NOT_ALLOWED] * len(methods)

    def test_one_class_and_its_students_allow_only_their_methods(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        batch = make_batch(abc, teacher, (student,))

        codes = [
            abc.client.post(batch_url(batch), {}, format="json").status_code,
            abc.client.patch(batch_url(batch), {}, format="json").status_code,
            abc.client.delete(batch_url(batch)).status_code,
            abc.client.get(members_url(batch)).status_code,
            abc.client.put(members_url(batch), {}, format="json").status_code,
            abc.client.get(member_url(batch, student)).status_code,
            abc.client.post(member_url(batch, student), {}, format="json").status_code,
        ]

        assert codes == [status.HTTP_405_METHOD_NOT_ALLOWED] * 7
        assert len(stored_batches(abc)) == 1

    def test_not_on_the_platform_host(
        self, abc: ConsultancyContext, teacher: Staff, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        listed = platform_site.client.get(BATCHES_URL)
        created = platform_site.client.post(BATCHES_URL, good_body(teacher), format="json")

        assert (listed.status_code, created.status_code) == (404, 404)
        assert stored_batches(abc) == []


class TestModel:
    def test_a_batch_reads_as_its_name(self, abc: ConsultancyContext, teacher: Staff) -> None:
        assert str(make_batch(abc, teacher)) == "PTE Day"

    def test_a_membership_reads_as_student_in_batch(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        batch = make_batch(abc, teacher, (make_student(abc),))
        with tenant_context(abc.consultancy):
            membership = BatchMembership.objects.select_related("batch", "student").get()

            assert str(membership) == "Sita Gurung in PTE Day"
            assert batch.pk == membership.batch_id

    def test_five_years_after_a_leap_day_is_the_28th(self) -> None:
        assert years_from(date(2028, 2, 29), 5) == date(2033, 2, 28)
        assert years_from(date(2028, 2, 29), -5) == date(2023, 2, 28)

    def test_the_fields_show_their_values(self, abc: ConsultancyContext, teacher: Staff) -> None:
        student = make_student(abc)

        assert BatchDateField("x").to_representation(date(2026, 11, 2)) == "2026-11-02"
        assert TeacherField().to_representation(teacher) == str(teacher.public_id)
        assert StudentChoiceField().to_representation(student) == str(student.public_id)
