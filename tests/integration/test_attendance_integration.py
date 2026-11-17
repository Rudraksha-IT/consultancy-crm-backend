"""Attendance: a class's Teacher (or an Admin) marks each Student for each class day.

Spec 85-86, 86a; issue 24.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from django.db import IntegrityError, transaction
from django_tenants.utils import tenant_context
from rest_framework import status

from batches.models import Attendance, Batch, BatchMembership, Mark
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff, StaffStatus
from students.models import Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_batches_integration import (
    BATCHES_URL,
    ENROLLED_URL,
    NOT_FOR_FRONT_DESK,
    batch_url,
    day,
    make_batch,
)
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

ONLY_TEACHER_OR_ADMIN = {"detail": "Only this class's teacher or an Admin can take attendance."}
NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}
MARKS_MESSAGE = {"marks": ["Mark each Student present, absent, late or leave."]}
STUDENT_NOT_IN_CLASS = {"marks": ["Mark only Students in this class, once each."]}
MARKS = ("present", "absent", "late", "leave")


def attendance_url(batch: Batch | int, on: date | str) -> str:
    """One class day's Attendance."""
    pk = batch if isinstance(batch, int) else batch.pk
    text = on if isinstance(on, str) else on.isoformat()
    return f"/api/batches/{pk}/attendance/{text}/"


def student_classes_url(student: Student | int) -> str:
    """The classes one Student is in, with their Attendance."""
    pk = student if isinstance(student, int) else student.pk
    return f"/api/students/{pk}/batches/"


def outside_dates_message(batch: Batch) -> dict[str, list[str]]:
    """What a day outside the class's dates is refused with."""
    return {
        "day": [f"Choose a day from {batch.starts_on.isoformat()} to {batch.ends_on.isoformat()}."]
    }


def put_marks(
    office: ConsultancyContext, batch: Batch, on: date, marks: object
) -> _MonkeyPatchedResponse:
    """PUT a class day's marks, as given."""
    return office.client.put(attendance_url(batch, on), {"marks": marks}, format="json")


def marks_for(*pairs: tuple[Student, str]) -> list[dict[str, object]]:
    """The body's marks for these Students."""
    return [{"student": student.pk, "mark": mark} for student, mark in pairs]


def stored_marks(office: ConsultancyContext, batch: Batch, on: date) -> dict[str, str]:
    """The stored marks of a class day, by Student name."""
    with tenant_context(office.consultancy):
        rows = Attendance.objects.filter(batch=batch, day=on).select_related("student")
        return {row.student.name: row.mark for row in rows}


def store_marks(
    office: ConsultancyContext, batch: Batch, on: date, *pairs: tuple[Student, str]
) -> None:
    """Store marks directly, as if taken by the class's Teacher."""
    with tenant_context(office.consultancy):
        for student, mark in pairs:
            Attendance.objects.create(
                batch=batch, student=student, day=on, mark=mark, marked_by=batch.teacher
            )


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with test-prep classes switched on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.BATCHES})
    return office


@pytest.fixture
def teacher(abc: ConsultancyContext) -> Staff:
    """The Teacher of the class."""
    return named_staff(abc, "Gita Lama", Role.TEACHER)


@pytest.fixture
def sita(abc: ConsultancyContext) -> Student:
    """A Student."""
    return make_student(abc, name="Sita Gurung")


@pytest.fixture
def hari(abc: ConsultancyContext) -> Student:
    """Another Student."""
    return make_student(abc, name="Hari Thapa", phone="9841000002")


@pytest.fixture
def batch(abc: ConsultancyContext, teacher: Staff, sita: Student, hari: Student) -> Batch:
    """A class running today (from 10 days ago to 30 days ahead) with Sita and Hari in it."""
    return make_batch(abc, teacher, (sita, hari))


class TestTakingAttendance:
    def test_the_teacher_marks_their_class_for_today(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student, hari: Student
    ) -> None:
        log_in(abc, teacher)

        response = put_marks(abc, batch, day(0), marks_for((sita, "present"), (hari, "late")))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "day": day(0).isoformat(),
            "can_mark": True,
            # By name, as the class lists them.
            "marks": [
                {"student": hari.pk, "mark": "late"},
                {"student": sita.pk, "mark": "present"},
            ],
        }
        assert stored_marks(abc, batch, day(0)) == {"Sita Gurung": "present", "Hari Thapa": "late"}

    def test_any_admin_marks_any_class(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = put_marks(abc, batch, day(-1), marks_for((sita, "absent")))

        assert response.status_code == status.HTTP_200_OK
        assert stored_marks(abc, batch, day(-1)) == {"Sita Gurung": "absent"}

    @pytest.mark.parametrize("mark", MARKS)
    def test_every_mark_is_accepted(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student, mark: str
    ) -> None:
        log_in(abc, teacher)

        assert put_marks(abc, batch, day(0), marks_for((sita, mark))).status_code == 200
        assert stored_marks(abc, batch, day(0)) == {"Sita Gurung": mark}

    def test_who_took_it_is_recorded(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)

        put_marks(abc, batch, day(0), marks_for((sita, "present")))

        with tenant_context(abc.consultancy):
            assert Attendance.objects.get().marked_by_id == admin.pk

    def test_saving_a_day_again_replaces_it(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student, hari: Student
    ) -> None:
        log_in(abc, teacher)
        put_marks(abc, batch, day(0), marks_for((sita, "present"), (hari, "absent")))

        answer = put_marks(abc, batch, day(0), marks_for((sita, "late"))).json()

        assert stored_marks(abc, batch, day(0)) == {"Sita Gurung": "late"}
        assert answer["marks"] == [
            {"student": hari.pk, "mark": None},
            {"student": sita.pk, "mark": "late"},
        ]

    def test_other_days_are_left_alone(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student
    ) -> None:
        store_marks(abc, batch, day(-1), (sita, "absent"))
        log_in(abc, teacher)

        put_marks(abc, batch, day(0), [])

        assert stored_marks(abc, batch, day(-1)) == {"Sita Gurung": "absent"}

    def test_the_first_and_last_days_and_a_day_ahead_can_be_marked(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student
    ) -> None:
        # A Student can tell the Teacher ahead that they'll be on Leave.
        log_in(abc, teacher)

        codes = [
            put_marks(abc, batch, on, marks_for((sita, "leave"))).status_code
            for on in (batch.starts_on, batch.ends_on, day(3))
        ]

        assert codes == [status.HTTP_200_OK] * 3

    @pytest.mark.parametrize("offset", [-11, 31])
    def test_a_day_outside_the_class_dates_is_refused(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student, offset: int
    ) -> None:
        log_in(abc, teacher)

        response = put_marks(abc, batch, day(offset), marks_for((sita, "present")))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == outside_dates_message(batch)
        with tenant_context(abc.consultancy):
            assert not Attendance.objects.exists()

    def test_a_counsellor_cant_take_attendance(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = put_marks(abc, batch, day(0), marks_for((sita, "present")))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_TEACHER_OR_ADMIN
        assert stored_marks(abc, batch, day(0)) == {}

    def test_front_desk_cant(self, abc: ConsultancyContext, batch: Batch, sita: Student) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = put_marks(abc, batch, day(0), marks_for((sita, "present")))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_FRONT_DESK

    def test_another_teacher_finds_no_such_class(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        log_in(abc, named_staff(abc, "Ram Karki", Role.TEACHER))

        response = put_marks(abc, batch, day(0), marks_for((sita, "present")))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored_marks(abc, batch, day(0)) == {}

    def test_a_deactivated_teacher_cant(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student
    ) -> None:
        log_in(abc, teacher)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=teacher.pk).update(status=StaffStatus.DEACTIVATED)

        response = put_marks(abc, batch, day(0), marks_for((sita, "present")))

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
        assert stored_marks(abc, batch, day(0)) == {}

    def test_an_unknown_class_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(attendance_url(99999, day(0)), {"marks": []}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logs_carry_ids_only(
        self,
        abc: ConsultancyContext,
        teacher: Staff,
        batch: Batch,
        sita: Student,
        json_logs: JsonLogs,
    ) -> None:
        log_in(abc, teacher)

        put_marks(abc, batch, day(0), marks_for((sita, "present")))

        json_logs.only(
            f"Attendance taken: batch_id={batch.pk}, day={day(0).isoformat()}, marked=1, "
            f"by_staff_id={teacher.pk}"
        )
        assert "Sita" not in json_logs.text()


class TestRefusedInput:
    @pytest.fixture(autouse=True)
    def admin(self, abc: ConsultancyContext) -> Staff:
        """Everything here is sent by an Admin."""
        return abc.log_in_as(Role.ADMIN)

    def nothing_stored(self, office: ConsultancyContext) -> bool:
        """No Attendance was stored."""
        with tenant_context(office.consultancy):
            return not Attendance.objects.exists()

    @pytest.mark.parametrize(
        "mark", ["", "Present", "P", "here", None, 1, True, ["present"], *INJECTION_STRINGS]
    )
    def test_a_bad_mark_is_refused(
        self, abc: ConsultancyContext, batch: Batch, sita: Student, mark: object
    ) -> None:
        response = put_marks(abc, batch, day(0), [{"student": sita.pk, "mark": mark}])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == MARKS_MESSAGE
        assert self.nothing_stored(abc)

    @pytest.mark.parametrize("student", ["1", 1.5, True, None, 0, -1, 2**63, [1], {"id": 1}])
    def test_a_bad_student_is_refused(
        self, abc: ConsultancyContext, batch: Batch, student: object
    ) -> None:
        response = put_marks(abc, batch, day(0), [{"student": student, "mark": "present"}])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == MARKS_MESSAGE
        assert self.nothing_stored(abc)

    def test_a_student_not_in_the_class_is_refused(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        outsider = make_student(abc, name="Outside Student", phone="9841000009")

        response = put_marks(abc, batch, day(0), marks_for((sita, "present"), (outsider, "late")))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == STUDENT_NOT_IN_CLASS
        assert self.nothing_stored(abc)

    def test_a_student_marked_twice_is_refused(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        response = put_marks(abc, batch, day(0), marks_for((sita, "present"), (sita, "absent")))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == STUDENT_NOT_IN_CLASS
        assert self.nothing_stored(abc)

    def test_a_removed_student_cant_be_marked(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        with tenant_context(abc.consultancy):
            BatchMembership.objects.filter(student=sita).delete()

        response = put_marks(abc, batch, day(0), marks_for((sita, "present")))

        assert response.json() == STUDENT_NOT_IN_CLASS

    @pytest.mark.parametrize("marks", [None, "present", 1, {"student": 1}, [1], ["present"]])
    def test_marks_that_arent_a_list_of_objects_are_refused(
        self, abc: ConsultancyContext, batch: Batch, marks: object
    ) -> None:
        response = put_marks(abc, batch, day(0), marks)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == MARKS_MESSAGE

    def test_too_many_marks_are_refused(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        marks = [{"student": sita.pk, "mark": "present"}] * 501

        response = put_marks(abc, batch, day(0), marks)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"marks": ["Send at most 500 marks at once."]}

    def test_marks_are_required(self, abc: ConsultancyContext, batch: Batch) -> None:
        response = abc.client.put(attendance_url(batch, day(0)), {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == MARKS_MESSAGE

    def test_an_unknown_field_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        response = abc.client.put(
            attendance_url(batch, day(0)),
            {"marks": marks_for((sita, "present")), "day": "2020-01-01"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert self.nothing_stored(abc)

    def test_an_unknown_field_in_a_mark_is_refused(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        mark = {"student": sita.pk, "mark": "present", "marked_by": 1}

        response = put_marks(abc, batch, day(0), [mark])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == MARKS_MESSAGE
        assert self.nothing_stored(abc)

    def test_a_body_that_isnt_an_object_is_refused(
        self, abc: ConsultancyContext, batch: Batch
    ) -> None:
        response = abc.client.put(attendance_url(batch, day(0)), [], format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_form_body_is_refused(self, abc: ConsultancyContext, batch: Batch) -> None:
        response = abc.client.put(attendance_url(batch, day(0)), {"marks": "x"})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("text", ["2026-02-30", "2026-13-01", "0000-01-01"])
    def test_a_day_that_isnt_a_date_is_refused(
        self, abc: ConsultancyContext, batch: Batch, text: str
    ) -> None:
        response = abc.client.put(attendance_url(batch, text), {"marks": []}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == outside_dates_message(batch)

    @pytest.mark.parametrize("text", ["today", "2026-1-1", "20261101", "<script>"])
    def test_a_day_not_shaped_like_a_date_is_not_found(
        self, abc: ConsultancyContext, batch: Batch, text: str
    ) -> None:
        response = abc.client.put(attendance_url(batch, text), {"marks": []}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestReadingADay:
    def test_each_student_with_their_mark_or_none(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student, hari: Student
    ) -> None:
        store_marks(abc, batch, day(0), (sita, "absent"))
        log_in(abc, teacher)

        response = abc.client.get(attendance_url(batch, day(0)))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "day": day(0).isoformat(),
            "can_mark": True,
            "marks": [
                {"student": hari.pk, "mark": None},
                {"student": sita.pk, "mark": "absent"},
            ],
        }

    @pytest.mark.parametrize(("role", "can_mark"), [(Role.ADMIN, True), (Role.COUNSELLOR, False)])
    def test_admins_and_counsellors_read_any_class(
        self, abc: ConsultancyContext, batch: Batch, role: Role, can_mark: bool
    ) -> None:
        abc.log_in_as(role)

        response = abc.client.get(attendance_url(batch, day(0)))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["can_mark"] is can_mark

    def test_another_teacher_finds_nothing(self, abc: ConsultancyContext, batch: Batch) -> None:
        log_in(abc, named_staff(abc, "Ram Karki", Role.TEACHER))

        assert abc.client.get(attendance_url(batch, day(0))).status_code == 404

    def test_front_desk_is_refused(self, abc: ConsultancyContext, batch: Batch) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.get(attendance_url(batch, day(0)))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_FRONT_DESK

    def test_a_day_outside_the_class_dates_is_refused(
        self, abc: ConsultancyContext, batch: Batch
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(attendance_url(batch, day(31)))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == outside_dates_message(batch)

    def test_a_removed_student_is_no_longer_listed(
        self, abc: ConsultancyContext, batch: Batch, sita: Student, hari: Student
    ) -> None:
        store_marks(abc, batch, day(0), (sita, "present"))
        with tenant_context(abc.consultancy):
            BatchMembership.objects.filter(student=sita).delete()
        abc.log_in_as(Role.ADMIN)

        marks = abc.client.get(attendance_url(batch, day(0))).json()["marks"]

        assert marks == [{"student": hari.pk, "mark": None}]
        # Their mark is kept, should they come back.
        assert stored_marks(abc, batch, day(0)) == {"Sita Gurung": "present"}


class TestAttendedShare:
    def test_each_student_in_a_class_shows_the_share_attended(
        self, abc: ConsultancyContext, batch: Batch, sita: Student, hari: Student
    ) -> None:
        # Present and Late count as attended (the prototype's rule); Absent and Leave don't.
        store_marks(abc, batch, day(-3), (sita, "present"), (hari, "absent"))
        store_marks(abc, batch, day(-2), (sita, "late"), (hari, "leave"))
        store_marks(abc, batch, day(-1), (sita, "absent"))
        abc.log_in_as(Role.ADMIN)

        students = abc.client.get(batch_url(batch)).json()["students"]

        assert [(s["name"], s["attended_percent"]) for s in students] == [
            ("Hari Thapa", 0),
            ("Sita Gurung", 67),
        ]

    def test_a_student_never_marked_has_no_share(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        store_marks(abc, batch, day(-1), (sita, "present"))
        abc.log_in_as(Role.ADMIN)

        students = abc.client.get(batch_url(batch)).json()["students"]

        assert [s["attended_percent"] for s in students] == [None, 100]

    def test_marks_in_another_class_dont_count(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student
    ) -> None:
        other = make_batch(abc, teacher, (sita,), name="Other class")
        store_marks(abc, other, day(-1), (sita, "absent"))
        abc.log_in_as(Role.ADMIN)

        students = abc.client.get(batch_url(batch)).json()["students"]

        assert [s["attended_percent"] for s in students] == [None, None]

    def test_students_and_their_classes_average_their_classes(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student, hari: Student
    ) -> None:
        other = make_batch(abc, teacher, (sita,), name="Other class")
        store_marks(abc, batch, day(-1), (sita, "present"))
        store_marks(abc, other, day(-1), (sita, "absent"))
        store_marks(abc, other, day(-2), (sita, "present"))
        abc.log_in_as(Role.COUNSELLOR)

        results = abc.client.get(ENROLLED_URL).json()["results"]

        # Sita: 100% and 50% average to 75%; Hari was never marked.
        assert [(s["name"], s["attended_percent"]) for s in results] == [
            ("Hari Thapa", None),
            ("Sita Gurung", 75),
        ]

    def test_the_list_says_whether_todays_attendance_is_taken(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student
    ) -> None:
        make_batch(abc, teacher, (sita,), name="Untaken class")
        store_marks(abc, batch, day(0), (sita, "present"))
        log_in(abc, teacher)

        listed = abc.client.get(BATCHES_URL).json()["results"]

        assert {b["name"]: b["attendance_taken_today"] for b in listed} == {
            "PTE Day": True,
            "Untaken class": False,
        }

    def test_yesterdays_attendance_isnt_todays(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        store_marks(abc, batch, day(-1), (sita, "present"))
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(batch_url(batch)).json()["attendance_taken_today"] is False


class TestStudentClasses:
    def test_a_students_classes_with_their_attendance(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student, hari: Student
    ) -> None:
        other = make_batch(abc, teacher, (sita,), name="IELTS Evening", time_slot="5 PM")
        store_marks(abc, batch, day(-3), (sita, "present"))
        # A class day Sita wasn't marked on still shows, as not marked.
        store_marks(abc, batch, day(-2), (hari, "present"))
        store_marks(abc, batch, day(-1), (sita, "absent"))
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.get(student_classes_url(sita))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == [
            {
                "id": other.pk,
                "name": "IELTS Evening",
                "time_slot": "5 PM",
                "attended_percent": None,
                "recent_days": [],
            },
            {
                "id": batch.pk,
                "name": "PTE Day",
                "time_slot": "11:00 AM-12:30 PM",
                "attended_percent": 50,
                "recent_days": [
                    {"day": day(-3).isoformat(), "mark": "present"},
                    {"day": day(-2).isoformat(), "mark": None},
                    {"day": day(-1).isoformat(), "mark": "absent"},
                ],
            },
        ]

    def test_only_the_last_twelve_class_days_are_shown(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        for offset in range(-10, 4):
            store_marks(abc, batch, day(offset), (sita, "present"))
        abc.log_in_as(Role.ADMIN)

        [listed] = abc.client.get(student_classes_url(sita)).json()

        assert [d["day"] for d in listed["recent_days"]] == [
            day(offset).isoformat() for offset in range(-8, 4)
        ]
        assert listed["attended_percent"] == 100

    def test_a_student_in_no_class_has_none(self, abc: ConsultancyContext) -> None:
        loner = make_student(abc, name="No Class", phone="9841000003")
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(student_classes_url(loner)).json() == []

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_everyone_who_sees_students_sees_them(
        self, abc: ConsultancyContext, batch: Batch, sita: Student, role: Role
    ) -> None:
        abc.log_in_as(role)

        response = abc.client.get(student_classes_url(sita))

        assert response.status_code == status.HTTP_200_OK
        assert [c["name"] for c in response.json()] == ["PTE Day"]

    def test_a_teacher_is_refused(
        self, abc: ConsultancyContext, teacher: Staff, batch: Batch, sita: Student
    ) -> None:
        log_in(abc, teacher)

        response = abc.client.get(student_classes_url(sita))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(student_classes_url(99999)).status_code == 404


class TestModuleSwitch:
    def test_every_endpoint_is_not_found_while_classes_are_off(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        store_marks(abc, batch, day(0), (sita, "present"))
        abc.log_in_as(Role.ADMIN)
        switch_modules(abc.consultancy, set())

        responses = [
            abc.client.get(attendance_url(batch, day(0))),
            put_marks(abc, batch, day(0), marks_for((sita, "absent"))),
            abc.client.get(student_classes_url(sita)),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 3
        assert stored_marks(abc, batch, day(0)) == {"Sita Gurung": "present"}

    def test_attendance_comes_back_when_switched_on_again(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        store_marks(abc, batch, day(0), (sita, "present"))
        abc.log_in_as(Role.ADMIN)
        switch_modules(abc.consultancy, set())
        switch_modules(abc.consultancy, {Module.BATCHES})

        [listed] = abc.client.get(student_classes_url(sita)).json()

        assert listed["attended_percent"] == 100


class TestAccess:
    def test_logged_out_is_refused_whether_on_or_off(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        client = abc.new_client()

        def calls() -> list[int]:
            return [
                client.get(attendance_url(batch, day(0))).status_code,
                client.put(attendance_url(batch, day(0)), {"marks": []}, format="json").status_code,
                client.get(student_classes_url(sita)).status_code,
            ]

        on = calls()
        switch_modules(abc.consultancy, set())

        assert on == calls() == [status.HTTP_401_UNAUTHORIZED] * 3

    def test_only_their_methods_are_allowed(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        one_day = attendance_url(batch, day(0))
        classes = student_classes_url(sita)

        codes = [
            abc.client.post(one_day, {}, format="json").status_code,
            abc.client.patch(one_day, {}, format="json").status_code,
            abc.client.delete(one_day).status_code,
            abc.client.post(classes, {}, format="json").status_code,
            abc.client.put(classes, {}, format="json").status_code,
            abc.client.delete(classes).status_code,
        ]

        assert codes == [status.HTTP_405_METHOD_NOT_ALLOWED] * 6

    def test_not_on_the_platform_host(
        self, batch: Batch, sita: Student, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        responses = [
            platform_site.client.get(attendance_url(batch, day(0))),
            platform_site.client.put(attendance_url(batch, day(0)), {"marks": []}, format="json"),
            platform_site.client.get(student_classes_url(sita)),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 3


class TestModel:
    def test_an_attendance_reads_as_student_day_and_mark(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        store_marks(abc, batch, day(0), (sita, Mark.LATE))

        with tenant_context(abc.consultancy):
            text = str(Attendance.objects.get())

        assert text == f"Sita Gurung on {day(0).isoformat()}: Late"

    def test_one_mark_per_student_per_class_day(
        self, abc: ConsultancyContext, batch: Batch, sita: Student
    ) -> None:
        store_marks(abc, batch, day(0), (sita, Mark.PRESENT))

        with pytest.raises(IntegrityError), transaction.atomic():
            store_marks(abc, batch, day(0), (sita, Mark.ABSENT))
