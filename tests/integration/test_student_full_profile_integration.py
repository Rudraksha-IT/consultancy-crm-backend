"""A Student's full profile: personal, family, passport, addresses, work and plans (issue 46).

Spec 31, 35, 35a-35f.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from modules.services import switch_modules
from modules.types import Module
from staff.models import Role
from students.models import Stage, Student, WorkExperience
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, use_access_token
from tests.integration.test_student_profile_integration import (
    INJECTION_STRINGS,
    edit,
    make_student,
    profile_url,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

STUDENTS_URL = "/api/students/"
NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}
NOT_YOUR_STUDENT = {
    "detail": "Not your Student. Only their counsellor or an Admin can change them."
}
EMPTY_ADDRESS = {"province": "", "district": "", "municipality": "", "ward": None, "street": ""}
LAKESIDE = {
    "province": "gandaki",
    "district": "Kaski",
    "municipality": "Pokhara Metropolitan City",
    "ward": 6,
    "street": "Lakeside",
}
BALUWATAR = {
    "province": "bagmati",
    "district": "Kathmandu",
    "municipality": "Kathmandu Metropolitan City",
    "ward": 4,
    "street": "Baluwatar, Sunrise Girls Hostel",
}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def today() -> date:
    """Today in the office's time zone."""
    return timezone.localdate()


def years_ago(years: int, days: int = 0) -> date:
    """The day `years` years before today, moved `days` later."""
    now = today()
    return now.replace(year=now.year - years) + timedelta(days=days)


def month(value: date) -> str:
    """A month as the API writes it, "YYYY-MM"."""
    return value.strftime("%Y-%m")


def work_url(student: Student) -> str:
    """The Student's work experience endpoint."""
    return f"{profile_url(student)}work-experience/"


def job_url(student: Student, job_id: int | str | UUID) -> str:
    """One job in the Student's work experience."""
    return f"{work_url(student)}{job_id}/"


def a_job(**changes: object) -> dict[str, object]:
    """A job the server accepts; `changes` override it."""
    return {
        "employer": "Bir Hospital",
        "job_title": "Staff nurse",
        "started_on": "2024-04",
        "ended_on": None,
        **changes,
    }


def add_job(
    office: ConsultancyContext, student: Student, **changes: object
) -> _MonkeyPatchedResponse:
    """POST a job to the Student's work experience."""
    return office.client.post(work_url(student), a_job(**changes), format="json")


class TestTheNewDetails:
    def test_a_new_student_starts_with_empty_details_and_nepali_nationality(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        body = abc.client.get(profile_url(student)).json()

        assert body["gender"] == ""
        assert body["marital_status"] == ""
        assert body["nationality"] == "Nepali"
        assert body["father_name"] == ""
        assert body["mother_name"] == ""
        assert body["guardian_name"] == ""
        assert body["guardian_relation"] == ""
        assert body["guardian_phone"] == ""
        assert body["passport_place_of_issue"] == ""
        assert body["passport_issue_date"] is None
        assert body["passport_expiry_date"] is None
        assert body["passport_warning"] is None
        assert body["target_level"] == ""
        assert body["permanent_address"] == EMPTY_ADDRESS
        assert body["mailing_address"] == EMPTY_ADDRESS
        assert body["mailing_same_as_permanent"] is True
        assert body["work_experience"] == []
        assert "address" not in body

    def test_the_assigned_counsellor_can_fill_every_new_detail(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)
        details = {
            "gender": "female",
            "marital_status": "single",
            "nationality": "Nepali",
            "father_name": "Hari Prasad Karki",
            "mother_name": "Gita Karki",
            "guardian_name": "Hari Prasad Karki",
            "guardian_relation": "Father",
            "guardian_phone": "984-601-2345",
            "passport_place_of_issue": "Kaski",
            "passport_issue_date": "2017-03-11",
            "passport_expiry_date": "2037-03-10",
            "target_level": "master",
            "permanent_address": LAKESIDE,
            "mailing_address": BALUWATAR,
            "mailing_same_as_permanent": False,
        }

        response = edit(abc, student, **details)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert {key: body[key] for key in details} == {**details, "guardian_phone": "9846012345"}
        with tenant_context(abc.consultancy):
            stored = Student.objects.get(pk=student.pk)
        assert stored.passport_expiry_date == date(2037, 3, 10)
        assert stored.permanent_ward == 6
        assert stored.mailing_street == "Baluwatar, Sunrise Girls Hostel"

    def test_same_as_permanent_is_a_flag_and_the_mailing_address_is_not_copied(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        body = edit(abc, student, permanent_address=LAKESIDE, mailing_same_as_permanent=True).json()

        assert body["mailing_same_as_permanent"] is True
        assert body["mailing_address"] == EMPTY_ADDRESS
        with tenant_context(abc.consultancy):
            assert Student.objects.get(pk=student.pk).mailing_district == ""

    def test_an_address_is_replaced_whole_and_parts_can_be_left_empty(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        edit(abc, student, permanent_address=LAKESIDE)

        body = edit(abc, student, permanent_address={**EMPTY_ADDRESS, "district": "Kaski"}).json()

        assert body["permanent_address"] == {**EMPTY_ADDRESS, "district": "Kaski"}

    def test_every_new_detail_can_be_cleared(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(
            abc,
            gender="male",
            marital_status="married",
            target_level="phd",
            passport_issue_date=date(2020, 1, 1),
            passport_expiry_date=date(2030, 1, 1),
            guardian_phone="9846012345",
        )

        body = edit(
            abc,
            student,
            gender="",
            marital_status="",
            nationality="",
            target_level="",
            passport_issue_date=None,
            passport_expiry_date=None,
            guardian_phone="",
        ).json()

        assert body["gender"] == ""
        assert body["marital_status"] == ""
        assert body["nationality"] == ""
        assert body["target_level"] == ""
        assert body["passport_issue_date"] is None
        assert body["passport_expiry_date"] is None
        assert body["guardian_phone"] == ""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("gender", "woman"),
            ("gender", 1),
            ("marital_status", "engaged"),
            ("nationality", "x" * 51),
            ("nationality", 5),
            ("nationality", "Nepali\nIndian"),
            ("father_name", "x" * 201),
            ("mother_name", ["Gita"]),
            ("guardian_name", "x" * 201),
            ("guardian_relation", "x" * 51),
            ("guardian_phone", "abc"),
            ("guardian_phone", 9846012345),
            ("passport_place_of_issue", "x" * 101),
            ("passport_issue_date", "11/03/2017"),
            ("passport_issue_date", True),
            ("passport_expiry_date", "2037-02-30"),
            ("target_level", "see"),
            ("target_level", None),
            ("permanent_address", "Lakeside, Pokhara"),
            ("permanent_address", {**LAKESIDE, "province": "himalaya"}),
            ("permanent_address", {**LAKESIDE, "ward": 0}),
            ("permanent_address", {**LAKESIDE, "ward": 36}),
            ("permanent_address", {**LAKESIDE, "ward": "6"}),
            ("permanent_address", {**LAKESIDE, "ward": True}),
            ("permanent_address", {**LAKESIDE, "district": "x" * 61}),
            ("permanent_address", {**LAKESIDE, "municipality": "x" * 81}),
            ("permanent_address", {**LAKESIDE, "street": "x" * 201}),
            ("permanent_address", {**LAKESIDE, "street": "Lakeside\nRoad"}),
            ("permanent_address", {**LAKESIDE, "country": "Nepal"}),
            ("permanent_address", {"district": "Kaski"}),
            ("mailing_address", {**BALUWATAR, "ward": -1}),
            ("mailing_same_as_permanent", "yes"),
            ("mailing_same_as_permanent", None),
            ("address", "Lakeside, Pokhara"),
        ],
    )
    def test_bad_values_are_refused(
        self, abc: ConsultancyContext, field: str, value: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = edit(abc, student, **{field: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json() or response.json() == {"detail": ["Unknown field."]}
        with tenant_context(abc.consultancy):
            stored = Student.objects.get(pk=student.pk)
        assert stored.gender == ""
        assert stored.permanent_street == ""

    @pytest.mark.parametrize("value", ["woman", "<script>alert(1)</script>"])
    def test_a_refused_choice_never_repeats_what_was_sent(
        self, abc: ConsultancyContext, value: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = edit(
            abc, student, gender=value, permanent_address={**LAKESIDE, "province": value}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert value not in response.content.decode()

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_text_is_stored_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = edit(
            abc, student, father_name=text, permanent_address={**EMPTY_ADDRESS, "street": text}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["father_name"] == text
        assert response.json()["permanent_address"]["street"] == text


class TestDateOfBirth:
    @pytest.mark.parametrize("age", [10, 50, 100])
    def test_an_age_from_10_to_100_is_allowed(self, abc: ConsultancyContext, age: int) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        born = years_ago(age) if age < 100 else years_ago(101, days=1)

        response = edit(abc, student, date_of_birth=born.isoformat())

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("born", [years_ago(10, days=1), years_ago(101), date(2999, 1, 1)])
    def test_younger_than_10_older_than_100_or_unborn_is_refused(
        self, abc: ConsultancyContext, born: date
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = edit(abc, student, date_of_birth=born.isoformat())

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "date_of_birth": ["Enter a date of birth for someone aged 10 to 100."]
        }


class TestPassport:
    def test_the_expiry_must_be_after_the_issue_date(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        same_day = edit(
            abc, student, passport_issue_date="2020-01-01", passport_expiry_date="2020-01-01"
        )

        assert same_day.status_code == status.HTTP_400_BAD_REQUEST
        assert same_day.json() == {
            "passport_expiry_date": ["The expiry date must be after the issue date."]
        }

    def test_the_rule_checks_against_the_stored_date(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, passport_issue_date=date(2020, 1, 1))

        response = edit(abc, student, passport_expiry_date="2019-12-31")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "passport_expiry_date" in response.json()

    def test_the_issue_date_cannot_be_in_the_future(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = edit(abc, student, passport_issue_date=(today() + timedelta(1)).isoformat())

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"passport_issue_date": ["This date can't be in the future."]}

    @pytest.mark.parametrize(
        ("expires", "warning"),
        [
            (None, None),
            (timedelta(days=-1), "expired"),
            (timedelta(days=0), "expires_soon"),
            (timedelta(days=150), "expires_soon"),
            (timedelta(days=200), None),
        ],
    )
    def test_the_warning_is_computed_from_the_expiry_date(
        self, abc: ConsultancyContext, expires: timedelta | None, warning: str | None
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        expiry = None if expires is None else today() + expires
        student = make_student(abc, passport_expiry_date=expiry)

        body = abc.client.get(profile_url(student)).json()

        assert body["passport_warning"] == warning

    def test_six_months_ahead_to_the_day_still_warns(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        now = today()
        six_months = now.replace(
            year=now.year + (now.month + 5) // 12, month=(now.month + 5) % 12 + 1, day=1
        )
        student = make_student(abc, passport_expiry_date=six_months)

        assert abc.client.get(profile_url(student)).json()["passport_warning"] == "expires_soon"


class TestWhoCanChangeTheNewDetails:
    def test_another_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.COUNSELLING)
        abc.log_in_as(Role.COUNSELLOR)

        response = edit(abc, student, father_name="Someone")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT

    def test_front_desk_can_fill_an_unassigned_lead(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        response = edit(abc, student, permanent_address=LAKESIDE)

        assert response.status_code == status.HTTP_200_OK

    def test_a_teacher_never_gets_the_details(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, father_name="Hari Prasad Karki")
        abc.log_in_as(Role.TEACHER)

        responses = [
            abc.client.get(profile_url(student)),
            abc.client.get(STUDENTS_URL),
            edit(abc, student, father_name="X"),
            abc.client.post(work_url(student), a_job(), format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 4
        assert all("Hari" not in r.content.decode() for r in responses)

    def test_a_teacher_sees_only_names_of_the_students_in_their_class(
        self, abc: ConsultancyContext
    ) -> None:
        switch_modules(abc.consultancy, {Module.BATCHES})
        abc.log_in_as(Role.ADMIN)
        student = make_student(
            abc,
            father_name="Hari Prasad Karki",
            permanent_street="Lakeside",
            stage=Stage.COUNSELLING,
        )
        teacher = abc.add_staff(Role.TEACHER, email="teacher@abc.test")
        batch = abc.client.post(
            "/api/batches/",
            {
                "name": "IELTS Morning",
                "test_type": "ielts",
                "teacher": str(teacher.public_id),
                "time_slot": "7:00-8:30 AM",
                "starts_on": today().isoformat(),
                "ends_on": (today() + timedelta(days=60)).isoformat(),
            },
            format="json",
        ).json()
        abc.client.post(
            f"/api/batches/{batch['id']}/students/",
            {"student": str(student.public_id)},
            format="json",
        )
        use_access_token(abc.client, abc.tokens_for(teacher).access)

        text = abc.client.get(f"/api/batches/{batch['id']}/").content.decode()

        assert "Sita Gurung" in text
        assert "Hari" not in text
        assert "Lakeside" not in text


class TestPersonalDataIsNeverLogged:
    def test_passport_family_and_address_values_stay_out_of_logs_and_errors(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        edit(
            abc,
            student,
            passport_number="PA7654321",
            passport_place_of_issue="Dolpa",
            father_name="Krishnabahadur",
            guardian_phone="9851023456",
            permanent_address={**LAKESIDE, "street": "Jhamsikhel"},
        )
        refused = edit(
            abc,
            student,
            passport_number="PA 7654321",
            father_name="Krishnabahadur\n",
            guardian_phone="98510-abc",
            permanent_address={**LAKESIDE, "street": "Jhamsikhel\n", "ward": 99},
        )

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        text = json_logs.text() + refused.content.decode()
        for value in ("PA7654321", "Dolpa", "Krishnabahadur", "9851023456", "Jhamsikhel"):
            assert value not in text
        assert json_logs.with_message("Student details changed")


class TestWorkExperience:
    def test_the_counsellor_can_add_change_and_remove_a_job(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)

        added = add_job(abc, student)
        job_id = added.json()["id"]
        changed = abc.client.patch(job_url(student, job_id), {"ended_on": "2025-09"}, format="json")
        profile = abc.client.get(profile_url(student)).json()
        removed = abc.client.delete(job_url(student, job_id))

        assert added.status_code == status.HTTP_201_CREATED
        assert added.json() == {
            "id": job_id,
            "employer": "Bir Hospital",
            "job_title": "Staff nurse",
            "started_on": "2024-04",
            "ended_on": None,
        }
        assert changed.status_code == status.HTTP_200_OK
        assert changed.json()["ended_on"] == "2025-09"
        assert profile["work_experience"] == [changed.json()]
        assert removed.status_code == status.HTTP_204_NO_CONTENT
        with tenant_context(abc.consultancy):
            assert not WorkExperience.objects.exists()

    def test_jobs_are_listed_newest_first(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        add_job(abc, student, employer="Old", started_on="2019-01", ended_on="2020-01")
        add_job(abc, student, employer="New", started_on="2023-06")
        add_job(abc, student, employer="Middle", started_on="2021-02", ended_on="2023-05")

        jobs = abc.client.get(profile_url(student)).json()["work_experience"]

        assert [job["employer"] for job in jobs] == ["New", "Middle", "Old"]

    def test_only_the_end_month_is_optional(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        missing = abc.client.post(work_url(student), {}, format="json")
        no_end = abc.client.post(
            work_url(student),
            {"employer": "Bir Hospital", "job_title": "Nurse", "started_on": "2024-04"},
            format="json",
        )

        assert missing.status_code == status.HTTP_400_BAD_REQUEST
        assert set(missing.json()) == {"employer", "job_title", "started_on"}
        assert no_end.status_code == status.HTTP_201_CREATED
        assert no_end.json()["ended_on"] is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("employer", ""),
            ("employer", "x" * 121),
            ("employer", 7),
            ("job_title", ""),
            ("job_title", "x" * 121),
            ("job_title", "Nurse\nDoctor"),
            ("started_on", "2024-04-01"),
            ("started_on", "April 2024"),
            ("started_on", "2024-13"),
            ("started_on", "1949-12"),
            ("started_on", 202404),
            ("ended_on", "2024-4"),
            ("ended_on", "2024-03"),
            ("ended_on", True),
        ],
    )
    def test_bad_jobs_are_refused(self, abc: ConsultancyContext, field: str, value: object) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = add_job(abc, student, **{field: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        with tenant_context(abc.consultancy):
            assert not WorkExperience.objects.exists()

    def test_a_month_in_the_future_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        next_month = month(today().replace(day=1) + timedelta(days=32))

        starts = add_job(abc, student, started_on=next_month)
        ends = add_job(abc, student, ended_on=next_month)

        assert starts.json() == {"started_on": ["This month can't be in the future."]}
        assert ends.json() == {"ended_on": ["This month can't be in the future."]}

    def test_the_end_cannot_be_before_the_start(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        job_id = add_job(abc, student, started_on="2024-04").json()["id"]

        added = add_job(abc, student, started_on="2024-04", ended_on="2024-03")
        changed = abc.client.patch(job_url(student, job_id), {"ended_on": "2023-12"}, format="json")

        assert added.json() == {"ended_on": ["The end can't be before the start."]}
        assert changed.json() == {"ended_on": ["The end can't be before the start."]}

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = add_job(abc, student, salary="NPR 50,000")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_text_is_stored_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = add_job(abc, student, employer=text[:120])

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["employer"] == text[:120]

    def test_a_student_can_have_at_most_twenty_jobs(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        with tenant_context(abc.consultancy):
            WorkExperience.objects.bulk_create(
                WorkExperience(
                    student=student, employer=f"E{n}", job_title="T", started_on=date(2020, 1, 1)
                )
                for n in range(20)
            )

        response = add_job(abc, student)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "A Student can have at most 20 of these."}

    def test_another_counsellor_cannot_add_change_or_remove_jobs(
        self, abc: ConsultancyContext
    ) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.COUNSELLING)
        with tenant_context(abc.consultancy):
            job = WorkExperience.objects.create(
                student=student, employer="Bir", job_title="Nurse", started_on=date(2024, 4, 1)
            )
        abc.log_in_as(Role.COUNSELLOR)

        responses = [
            add_job(abc, student),
            abc.client.patch(job_url(student, job.public_id), {"employer": "X"}, format="json"),
            abc.client.delete(job_url(student, job.public_id)),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 3
        assert all(r.json() == NOT_YOUR_STUDENT for r in responses)
        with tenant_context(abc.consultancy):
            assert WorkExperience.objects.get(pk=job.pk).employer == "Bir"

    def test_a_job_of_another_student_is_not_found_under_this_one(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        sita = make_student(abc)
        ram = make_student(abc, name="Ram Thapa")
        with tenant_context(abc.consultancy):
            job = WorkExperience.objects.create(
                student=ram, employer="Bir", job_title="Nurse", started_on=date(2024, 4, 1)
            )

        responses = [
            abc.client.patch(job_url(sita, job.pk), {"employer": "X"}, format="json"),
            abc.client.delete(job_url(sita, job.pk)),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2

    @pytest.mark.parametrize(
        ("url_kind", "method"),
        [("list", "get"), ("list", "put"), ("one", "get"), ("one", "post"), ("one", "put")],
    )
    def test_only_the_needed_methods_are_allowed(
        self, abc: ConsultancyContext, url_kind: str, method: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        job_id = add_job(abc, student).json()["id"]
        url = work_url(student) if url_kind == "list" else job_url(student, job_id)

        response = getattr(abc.client, method)(url, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestStudentListFilters:
    def test_students_can_be_filtered_by_target_level(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, name="Sita Master", target_level="master")
        make_student(abc, name="Ram Bachelor", target_level="bachelor")
        make_student(abc, name="Gita Undecided")

        body = abc.client.get(STUDENTS_URL, {"target_level": "master"}).json()

        assert [row["name"] for row in body["results"]] == ["Sita Master"]

    def test_students_can_be_filtered_by_preferred_country(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, name="Sita Australia", country="australia")
        make_student(abc, name="Ram UK", country="uk", other_countries=["australia"])

        body = abc.client.get(STUDENTS_URL, {"country": "uk"}).json()

        assert [row["name"] for row in body["results"]] == ["Ram UK"]

    def test_the_filters_combine(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, name="Sita", country="uk", target_level="master")
        make_student(abc, name="Ram", country="uk", target_level="bachelor")
        make_student(abc, name="Gita", country="canada", target_level="master")

        body = abc.client.get(STUDENTS_URL, {"country": "uk", "target_level": "master"}).json()

        assert [row["name"] for row in body["results"]] == ["Sita"]

    @pytest.mark.parametrize(
        ("param", "value"),
        [("target_level", "see"), ("country", "mars"), ("country", "UK")],
    )
    def test_bad_filters_are_refused(self, abc: ConsultancyContext, param: str, value: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(STUDENTS_URL, {param: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert param in response.json()
