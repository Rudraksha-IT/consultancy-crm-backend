"""Edit details: every field and the three lists saved together, or not at all (issue 81).

Spec 35f, 122 and Implementation Decisions "Edit details (issue 81)".
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from history.models import HistoryEntry
from staff.models import Role
from students.models import AcademicRecord, Stage, Student, WorkExperience
from students.models import TestScore as StoredScore
from tests.conftest import (
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
)
from tests.integration.test_student_profile_integration import (
    INJECTION_STRINGS,
    make_student,
    profile_url,
)
from tests.support_access import open_as_platform_admin

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}
NOT_YOUR_STUDENT = {
    "detail": "Not your Student. Only their counsellor or an Admin can change them."
}
COUNSELLOR_OR_ADMIN = {"detail": "Only the Student's counsellor or an Admin can do this."}
CHANGED_MEANWHILE = {"detail": "Someone else just changed this Student. Refresh the page."}
UNKNOWN_FIELD = {"detail": ["Unknown field."]}
LAKESIDE = {
    "province": "gandaki",
    "district": "Kaski",
    "municipality": "Pokhara Metropolitan City",
    "ward": 6,
    "street": "Lakeside",
}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def details_url(student: Student | int) -> str:
    """The Edit details endpoint of one Student."""
    return f"{profile_url(student)}details/"


def version_of(office: ConsultancyContext, student: Student) -> int:
    """The Student's details version as the profile shows it."""
    with tenant_context(office.consultancy):
        return Student.objects.get(pk=student.pk).details_version


def body(office: ConsultancyContext, student: Student, **changes: object) -> dict[str, object]:
    """A whole Edit details save with the Student's lists unchanged; `changes` override it."""
    with tenant_context(office.consultancy):
        stored = Student.objects.get(pk=student.pk)
        records = [
            {
                "id": r.pk,
                "level": r.level,
                "institution": r.institution,
                "finished_year": r.finished_year,
                "result": r.result,
            }
            for r in stored.academic_records.all()
        ]
        scores = [
            {
                "id": s.pk,
                "test": s.test,
                "overall": str(s.overall),
                "taken_on": None if s.taken_on is None else s.taken_on.isoformat(),
                "details": s.details,
            }
            for s in stored.test_scores.all()
        ]
        jobs = [
            {
                "id": j.pk,
                "employer": j.employer,
                "job_title": j.job_title,
                "started_on": j.started_on.strftime("%Y-%m"),
                "ended_on": None if j.ended_on is None else j.ended_on.strftime("%Y-%m"),
            }
            for j in stored.work_experience.all()
        ]
        version = stored.details_version
    return {
        "version": version,
        "name": "Sita Gurung",
        "academic_records": records,
        "test_scores": scores,
        "work_experience": jobs,
        **changes,
    }


def save(
    office: ConsultancyContext, student: Student, payload: dict[str, object]
) -> _MonkeyPatchedResponse:
    """PUT a whole Edit details save."""
    return office.client.put(details_url(student), payload, format="json")


def add_rows(office: ConsultancyContext, student: Student) -> tuple[int, int, int]:
    """Give the Student one qualification, one test score and one job; return their ids."""
    with tenant_context(office.consultancy):
        record = AcademicRecord.objects.create(
            student=student, level="plus_two", institution="Trinity College", finished_year=2022
        )
        score = StoredScore.objects.create(student=student, test="ielts", overall=Decimal("6.5"))
        job = WorkExperience.objects.create(
            student=student, employer="Bir Hospital", job_title="Nurse", started_on=date(2023, 1, 1)
        )
    return record.pk, score.pk, job.pk


def a_record(**changes: object) -> dict[str, object]:
    """A new qualification the server accepts."""
    return {
        "level": "bachelor",
        "institution": "Tribhuvan University",
        "finished_year": 2024,
        "result": "GPA 3.4",
        **changes,
    }


def a_score(**changes: object) -> dict[str, object]:
    """A new test score the server accepts."""
    return {"test": "pte", "overall": "65", "taken_on": None, "details": "", **changes}


def a_job(**changes: object) -> dict[str, object]:
    """A new job the server accepts."""
    return {
        "employer": "Himalayan Bank",
        "job_title": "Teller",
        "started_on": "2024-02",
        "ended_on": None,
        **changes,
    }


class TestSavingEverythingTogether:
    def test_the_profile_carries_the_version_and_who_may_edit_details(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)

        answer = abc.client.get(profile_url(student)).json()

        assert answer["details_version"] == 1
        assert answer["can_edit_details"] is True

    def test_one_save_changes_fields_and_adds_changes_and_removes_list_rows(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)
        record_id, score_id, job_id = add_rows(abc, student)
        payload = body(
            abc,
            student,
            name="Sita Karki",
            father_name="Hari Karki",
            permanent_address=LAKESIDE,
            mailing_same_as_permanent=True,
            academic_records=[
                a_record(id=record_id, institution="Trinity International College"),
                a_record(),
            ],
            test_scores=[],
            work_experience=[a_job(id=job_id, job_title="Staff nurse"), a_job()],
        )

        response = save(abc, student, payload)

        assert response.status_code == status.HTTP_200_OK, response.content
        answer = response.json()
        assert answer["name"] == "Sita Karki"
        assert answer["father_name"] == "Hari Karki"
        assert answer["permanent_address"] == LAKESIDE
        assert [r["institution"] for r in answer["academic_records"]] == [
            "Trinity International College",
            "Tribhuvan University",
        ]
        assert answer["academic_records"][0]["id"] == record_id
        assert answer["test_scores"] == []
        assert sorted(j["job_title"] for j in answer["work_experience"]) == [
            "Staff nurse",
            "Teller",
        ]
        assert job_id in [j["id"] for j in answer["work_experience"]]
        assert answer["details_version"] == 2
        with tenant_context(abc.consultancy):
            assert not StoredScore.objects.filter(pk=score_id).exists()

    def test_a_save_with_nothing_changed_is_fine(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        add_rows(abc, student)

        response = save(abc, student, body(abc, student))

        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()["academic_records"]) == 1

    def test_a_refused_field_saves_nothing_at_all(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)
        record_id, _, _ = add_rows(abc, student)
        payload = body(
            abc,
            student,
            name="Sita Karki",
            passport_number="no spaces allowed",
            academic_records=[],
            work_experience=[a_job()],
        )

        response = save(abc, student, payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "passport_number": ["Enter 5 to 20 letters and numbers, without spaces."]
        }
        with tenant_context(abc.consultancy):
            stored = Student.objects.get(pk=student.pk)
            assert stored.name == "Sita Gurung"
            assert stored.details_version == 1
            assert AcademicRecord.objects.filter(pk=record_id).exists()
            assert stored.work_experience.count() == 1

    def test_a_refused_row_is_reported_beside_that_row_and_saves_nothing(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        payload = body(
            abc,
            student,
            name="Sita Karki",
            academic_records=[a_record(), a_record(institution="")],
            test_scores=[a_score(test="ielts", overall="9.5")],
            work_experience=[a_job(started_on="2024-05", ended_on="2024-01")],
        )

        response = save(abc, student, payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "academic_records": {"1": {"institution": ["This field may not be blank."]}},
            "test_scores": {"0": {"overall": ["IELTS scores go up to 9."]}},
            "work_experience": {"0": {"ended_on": ["The end can't be before the start."]}},
        }
        with tenant_context(abc.consultancy):
            assert Student.objects.get(pk=student.pk).name == "Sita Gurung"
            assert not AcademicRecord.objects.filter(student=student).exists()

    def test_the_joined_rules_are_checked_against_the_student_as_they_will_be(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = save(abc, student, body(abc, student, source="sub_agent"))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"sub_agent_name": ["Enter the Sub-agent's name."]}

    def test_a_failure_while_saving_rows_rolls_back_the_fields(
        self, abc: ConsultancyContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        payload = body(abc, student, name="Sita Karki", work_experience=[a_job()])

        def broken(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError

        monkeypatch.setattr("students.edit_details._sync_jobs", broken)

        response = save(abc, student, payload)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        with tenant_context(abc.consultancy):
            stored = Student.objects.get(pk=student.pk)
            assert stored.name == "Sita Gurung"
            assert stored.details_version == 1

    def test_writes_history_exactly_as_the_section_edits_did_none(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        payload = body(abc, student, name="Sita Karki", academic_records=[a_record()])

        assert save(abc, student, payload).status_code == status.HTTP_200_OK

        with tenant_context(abc.consultancy):
            assert not HistoryEntry.objects.filter(student=student).exists()

    def test_logs_only_ids_and_counts(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        payload = body(
            abc,
            student,
            passport_number="PA1234567",
            father_name="Hari Karki",
            test_scores=[a_score()],
        )

        assert save(abc, student, payload).status_code == status.HTTP_200_OK

        line = json_logs.only(
            f"Student details saved: student_id={student.pk}, by_staff_id={admin.pk}, "
            "academic_records=0, test_scores=1, work_experience=0"
        )
        assert line["level"] == "INFO"
        assert "PA1234567" not in json_logs.text()
        assert "Hari Karki" not in json_logs.text()


class TestSomeoneElsesChange:
    def test_an_old_version_is_refused_and_nothing_is_overwritten(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        mine = body(abc, student, name="Sita Karki")
        theirs = body(abc, student, name="Sita Rai")
        assert save(abc, student, theirs).status_code == status.HTTP_200_OK

        response = save(abc, student, mine)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == CHANGED_MEANWHILE
        with tenant_context(abc.consultancy):
            assert Student.objects.get(pk=student.pk).name == "Sita Rai"

    def test_a_change_from_another_way_in_also_counts(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        mine = body(abc, student, name="Sita Karki")
        abc.client.post(
            f"{profile_url(student)}academic-records/",
            {"level": "see", "institution": "Budhanilkantha School"},
            format="json",
        )

        response = save(abc, student, mine)

        assert response.status_code == status.HTTP_409_CONFLICT

    @pytest.mark.parametrize(
        "path",
        ["", "academic-records/", "test-scores/", "work-experience/"],
    )
    def test_every_older_change_moves_the_version_on(
        self, abc: ConsultancyContext, path: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        url = f"{profile_url(student)}{path}"
        sent = {
            "": {"father_name": "Hari"},
            "academic-records/": {"level": "see", "institution": "X School"},
            "test-scores/": {"test": "pte", "overall": "60"},
            "work-experience/": {
                "employer": "Bank",
                "job_title": "Teller",
                "started_on": "2024-01",
            },
        }[path]
        response = (
            abc.client.patch(url, sent, format="json")
            if path == ""
            else abc.client.post(url, sent, format="json")
        )
        assert response.status_code in {status.HTTP_200_OK, status.HTTP_201_CREATED}

        assert version_of(abc, student) == 2

    def test_changing_and_removing_a_row_the_older_way_moves_the_version_on(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        record_id, score_id, job_id = add_rows(abc, student)
        base = profile_url(student)

        abc.client.patch(f"{base}academic-records/{record_id}/", {"result": "A"}, format="json")
        abc.client.patch(f"{base}test-scores/{score_id}/", {"details": "L 7"}, format="json")
        abc.client.delete(f"{base}work-experience/{job_id}/")

        assert version_of(abc, student) == 4

    def test_a_note_or_stage_change_does_not_block_editing_details(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)
        mine = body(abc, student, name="Sita Karki")
        abc.client.put(f"{profile_url(student)}note/", {"note": "Called today."}, format="json")

        assert save(abc, student, mine).status_code == status.HTTP_200_OK


class TestWhoMayEditDetails:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR])
    def test_the_assigned_counsellor_and_admins_may(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        me = abc.log_in_as(role)
        counsellor = me if role == Role.COUNSELLOR else abc.add_staff(Role.COUNSELLOR)
        student = make_student(abc, counsellor, Stage.COUNSELLING)

        assert save(abc, student, body(abc, student)).status_code == status.HTTP_200_OK

    def test_an_admin_may_edit_a_student_with_no_counsellor(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        assert save(abc, student, body(abc, student)).status_code == status.HTTP_200_OK

    def test_another_counsellor_may_not(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        other = abc.add_staff(Role.COUNSELLOR, "other@abcedu.test")
        student = make_student(abc, other, Stage.COUNSELLING)

        response = save(abc, student, body(abc, student, name="Stolen"))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        assert abc.client.get(profile_url(student)).json()["can_edit_details"] is False

    def test_front_desk_may_not_even_on_an_open_lead(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        student = make_student(abc)

        response = save(abc, student, body(abc, student, name="Stolen"))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == COUNSELLOR_OR_ADMIN
        assert abc.client.get(profile_url(student)).json()["can_edit_details"] is False

    def test_a_teacher_may_not(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)
        student = make_student(abc)

        response = save(abc, student, body(abc, student, name="Stolen"))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_someone_logged_out_may_not(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        response = save(abc, student, body(abc, student, name="Stolen"))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        with tenant_context(abc.consultancy):
            assert Student.objects.get(pk=student.pk).name == "Sita Gurung"

    def test_a_platform_admin_in_support_access_may(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        student = make_student(abc)
        visitor = open_as_platform_admin(platform_site, abc).client
        payload = body(abc, student, name="Sita Karki")

        response = visitor.put(details_url(student), payload, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["can_edit_details"] is True

    def test_a_missing_student_is_404(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(details_url(999_999), {"version": 1}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_only_put_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = getattr(abc.client, method)(details_url(student))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestRefusedInput:
    @pytest.fixture
    def student(self, abc: ConsultancyContext) -> Student:
        abc.log_in_as(Role.ADMIN)
        return make_student(abc)

    def _refused(
        self, abc: ConsultancyContext, student: Student, payload: object
    ) -> dict[str, object]:
        response = abc.client.put(details_url(student), payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        with tenant_context(abc.consultancy):
            stored = Student.objects.get(pk=student.pk)
            assert stored.name == "Sita Gurung"
            assert stored.details_version == 1
        answer: dict[str, object] = response.json()
        return answer

    @pytest.mark.parametrize(
        "missing", ["version", "academic_records", "test_scores", "work_experience"]
    )
    def test_version_and_every_list_are_needed(
        self, abc: ConsultancyContext, student: Student, missing: str
    ) -> None:
        payload = body(abc, student)
        del payload[missing]

        assert self._refused(abc, student, payload) == {missing: ["This field is required."]}

    @pytest.mark.parametrize("version", ["1", 1.0, True, None, -1, 0])
    def test_the_version_is_a_whole_number(
        self, abc: ConsultancyContext, student: Student, version: object
    ) -> None:
        answer = self._refused(abc, student, body(abc, student, version=version))

        assert set(answer) == {"version"}

    def test_an_unknown_field_is_refused_and_never_stored(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        assert self._refused(abc, student, body(abc, student, is_admin=True)) == UNKNOWN_FIELD

    @pytest.mark.parametrize("field", ["note", "stage", "assigned_counsellor", "lost_reason"])
    def test_what_has_its_own_endpoint_is_refused_here(
        self, abc: ConsultancyContext, student: Student, field: str
    ) -> None:
        assert self._refused(abc, student, body(abc, student, **{field: "lost"})) == UNKNOWN_FIELD

    def test_an_unknown_field_on_a_row_is_refused(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        payload = body(abc, student, academic_records=[a_record(student=7)])

        assert self._refused(abc, student, payload) == {"academic_records": {"0": UNKNOWN_FIELD}}

    @pytest.mark.parametrize("value", ["rows", {"level": "see"}, 7, None])
    def test_a_list_must_be_a_list(
        self, abc: ConsultancyContext, student: Student, value: object
    ) -> None:
        answer = self._refused(abc, student, body(abc, student, test_scores=value))

        assert set(answer) == {"test_scores"}

    @pytest.mark.parametrize("row", ["a row", 7, None, ["see"]])
    def test_a_row_must_be_an_object(
        self, abc: ConsultancyContext, student: Student, row: object
    ) -> None:
        answer = self._refused(abc, student, body(abc, student, work_experience=[row]))

        assert set(answer) == {"work_experience"}

    def test_a_list_has_at_most_twenty_rows(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        payload = body(abc, student, academic_records=[a_record() for _ in range(21)])

        assert self._refused(abc, student, payload) == {
            "academic_records": {
                "non_field_errors": ["Ensure this field has no more than 20 elements."]
            }
        }

    @pytest.mark.parametrize("row_id", ["1", 1.5, True, -1, 999_999])
    def test_a_row_id_must_be_one_of_this_students_rows(
        self, abc: ConsultancyContext, student: Student, row_id: object
    ) -> None:
        answer = self._refused(abc, student, body(abc, student, test_scores=[a_score(id=row_id)]))

        assert set(answer) == {"test_scores"}

    def test_another_students_row_cannot_be_changed(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        gita = make_student(abc, name="Gita Rai")
        record_id, _, _ = add_rows(abc, gita)
        payload = body(
            abc, student, academic_records=[a_record(id=record_id, institution="Stolen")]
        )

        assert self._refused(abc, student, payload) == {
            "academic_records": ["A row here isn't one of this Student's. Refresh the page."]
        }
        with tenant_context(abc.consultancy):
            assert AcademicRecord.objects.get(pk=record_id).institution == "Trinity College"

    def test_the_same_row_cannot_be_sent_twice(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        record_id, _, _ = add_rows(abc, student)
        payload = body(
            abc, student, academic_records=[a_record(id=record_id), a_record(id=record_id)]
        )

        assert self._refused(abc, student, payload) == {
            "academic_records": ["A row here isn't one of this Student's. Refresh the page."]
        }

    def test_an_over_long_value_is_refused(self, abc: ConsultancyContext, student: Student) -> None:
        payload = body(
            abc,
            student,
            name="x" * 201,
            work_experience=[a_job(employer="x" * 121)],
        )

        answer = self._refused(abc, student, payload)

        assert set(answer) == {"name", "work_experience"}

    @pytest.mark.parametrize("value", [7, True, ["Sita"], {"first": "Sita"}])
    def test_text_must_be_text(
        self, abc: ConsultancyContext, student: Student, value: object
    ) -> None:
        answer = self._refused(
            abc,
            student,
            body(abc, student, father_name=value, academic_records=[a_record(result=value)]),
        )

        assert set(answer) == {"father_name", "academic_records"}

    def test_a_body_that_is_not_an_object_is_refused(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        response = abc.client.put(details_url(student), ["version", 1], format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_form_body_is_refused(self, abc: ConsultancyContext, student: Student) -> None:
        response = abc.client.put(details_url(student), "version=1", content_type="text/plain")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_text_is_stored_as_plain_text_or_refused(
        self, abc: ConsultancyContext, student: Student, text: str
    ) -> None:
        payload = body(
            abc,
            student,
            father_name=text,
            academic_records=[a_record(institution=text)],
            work_experience=[a_job(employer=text)],
        )

        response = save(abc, student, payload)

        if response.status_code == status.HTTP_200_OK:
            answer = response.json()
            assert answer["father_name"] == text
            assert answer["academic_records"][0]["institution"] == text
        else:
            assert response.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(abc.consultancy):
            assert Student.objects.filter(pk=student.pk).exists()

    @pytest.mark.parametrize("field", ["gender", "province"])
    def test_a_choice_outside_the_list_is_refused(
        self, abc: ConsultancyContext, student: Student, field: str
    ) -> None:
        payload = (
            body(abc, student, gender="<b>x</b>")
            if field == "gender"
            else body(abc, student, permanent_address={**LAKESIDE, "province": "texas"})
        )

        answer = self._refused(abc, student, payload)

        assert set(answer) == {"gender" if field == "gender" else "permanent_address"}

    def test_a_future_test_date_is_refused(self, abc: ConsultancyContext, student: Student) -> None:
        tomorrow = timezone.localdate().toordinal() + 1
        payload = body(
            abc, student, test_scores=[a_score(taken_on=date.fromordinal(tomorrow).isoformat())]
        )

        assert self._refused(abc, student, payload) == {
            "test_scores": {"0": {"taken_on": ["This date can't be in the future."]}}
        }


class TestTheNewStudentForm:
    def test_adding_a_student_keeps_the_short_form(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.post(
            "/api/students/",
            {
                "name": "Ram Thapa",
                "phone": "9841000002",
                "country": "uk",
                "source": "walk_in",
                "highest_qualification": "plus_two",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
