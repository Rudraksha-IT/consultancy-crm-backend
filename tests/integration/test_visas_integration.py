"""Visas and the Stage they move a Student to (spec 58-62; issues 15 and 75)."""

from __future__ import annotations

import importlib
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from django.apps import apps as django_apps
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application, Visa
from applications.services import MAX_VISAS_PER_STUDENT
from applications.statuses import ApplicationStatus, VisaStatus
from history.models import HistoryEntry, HistoryKind
from staff.models import Role
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_applications_integration import (
    COUNSELLOR_OR_ADMIN,
    counselled_student,
    make_application,
    make_course,
    stage_entries,
)
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    NOT_FOR_TEACHERS,
    NOT_YOUR_STUDENT,
    log_in,
    make_student,
    named_staff,
    stored,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

CHOOSE_APPLICATION = {"application": ["Choose an application that has an offer."]}
NOT_CHOSEN = {"application": ["A visa can only be applied for on the chosen offer."]}
VISA_WAITING = {"application": ["This Student already has a visa waiting for an answer."]}
ALREADY_GRANTED = {"application": ["This application already has a granted visa."]}
ALREADY_AT_STATUS = {"status": ["The visa already has this status."]}
TOO_MANY = {"detail": f"A Student can have at most {MAX_VISAS_PER_STUDENT} visas."}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def visas_url(student: Student | int) -> str:
    """Where a Student's Visas are started."""
    pk = student if isinstance(student, int) else student.pk
    return f"/api/students/{pk}/visas/"


def visa_url(student: Student | int, visa: Visa | int) -> str:
    """One of a Student's Visas."""
    pk = student if isinstance(student, int) else student.pk
    visa_pk = visa if isinstance(visa, int) else visa.pk
    return f"/api/students/{pk}/visas/{visa_pk}/"


def offer_application(
    office: ConsultancyContext,
    student: Student,
    university: str = "Deakin University",
    *,
    chosen: bool = True,
) -> Application:
    """An Application of the Student that has an offer; their Chosen Offer unless not `chosen`."""
    application = make_application(
        office, student, make_course(office, university), ApplicationStatus.OFFER
    )
    if chosen:
        with tenant_context(office.consultancy):
            Student.objects.filter(pk=student.pk).update(chosen_offer=application)
    return application


def make_visa(
    office: ConsultancyContext,
    student: Student,
    application: Application,
    visa_status: VisaStatus = VisaStatus.APPLIED,
) -> Visa:
    """Store a Visa directly."""
    with tenant_context(office.consultancy):
        return Visa.objects.create(student=student, application=application, status=visa_status)


def start(
    office: ConsultancyContext, student: Student | int, **body: object
) -> _MonkeyPatchedResponse:
    """POST a new Visa."""
    return office.client.post(visas_url(student), body, format="json")


def change(
    office: ConsultancyContext, student: Student, visa: Visa | int, **body: object
) -> _MonkeyPatchedResponse:
    """PATCH a Visa's Status."""
    return office.client.patch(visa_url(student, visa), body, format="json")


def stored_visas(office: ConsultancyContext, student: Student) -> list[Visa]:
    """The Student's stored Visas, oldest first."""
    with tenant_context(office.consultancy):
        return list(Visa.objects.filter(student=student).order_by("pk"))


class TestStartingAVisa:
    def test_the_assigned_counsellor_adds_one_as_applied(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        application = offer_application(abc, student)

        response = start(abc, student, application=application.pk)

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        [visa] = stored_visas(abc, student)
        assert body["visas"] == [
            {
                "id": visa.pk,
                "application_id": application.pk,
                "university": {"id": application.course.university_id, "name": "Deakin University"},
                "course": {"id": application.course_id, "name": "Master of IT", "level": "master"},
                "status": "applied",
                "started_on": timezone.localdate().isoformat(),
                "biometrics_at": None,
                "medical_at": None,
                "interview_at": None,
                "documents": [],
            }
        ]
        assert body["stage"] == "visa"
        [entry] = stage_entries(abc, student)
        assert (entry.before, entry.after) == ("offer", "visa")
        assert entry.detail == "Automatic: Deakin University visa applied."

    def test_an_admin_can_start_one_for_anyones_student(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.VISA)
        application = offer_application(abc, student)
        abc.log_in_as(Role.ADMIN)

        assert start(abc, student, application=application.pk).status_code == 201

    def test_another_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.VISA)
        application = offer_application(abc, student)
        abc.log_in_as(Role.COUNSELLOR)

        response = start(abc, student, application=application.pk)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        assert stored_visas(abc, student) == []

    @pytest.mark.parametrize(
        ("role", "refusal"),
        [(Role.FRONT_DESK, COUNSELLOR_OR_ADMIN), (Role.TEACHER, NOT_FOR_TEACHERS)],
    )
    def test_front_desk_and_teachers_are_refused(
        self, abc: ConsultancyContext, role: Role, refusal: dict[str, str]
    ) -> None:
        student = make_student(abc)
        application = offer_application(abc, student)
        abc.log_in_as(role)

        response = start(abc, student, application=application.pk)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == refusal
        assert stored_visas(abc, student) == []

    @pytest.mark.parametrize(
        "application_status",
        [s for s in ApplicationStatus if s != ApplicationStatus.OFFER],
    )
    def test_an_application_without_an_offer_is_refused(
        self, abc: ConsultancyContext, application_status: ApplicationStatus
    ) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = make_application(abc, student, make_course(abc), application_status)

        response = start(abc, student, application=application.pk)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NOT_CHOSEN
        assert stored_visas(abc, student) == []

    def test_another_students_application_is_refused(self, abc: ConsultancyContext) -> None:
        me, student = counselled_student(abc, Stage.VISA)
        someone_else = make_student(abc, me, Stage.VISA, name="Ram Thapa", phone="9841000002")
        theirs = offer_application(abc, someone_else)

        response = start(abc, student, application=theirs.pk)

        assert response.json() == NOT_CHOSEN
        assert stored_visas(abc, someone_else) == []

    def test_an_application_that_does_not_exist_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)

        assert start(abc, student, application=99_999).json() == NOT_CHOSEN

    @pytest.mark.parametrize(
        "application", [None, "1", 1.5, True, [], {}, 0, -1, *INJECTION_STRINGS]
    )
    def test_bad_applications_are_refused(
        self, abc: ConsultancyContext, application: object
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        offer_application(abc, student)

        response = start(abc, student, application=application)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_APPLICATION
        assert stored_visas(abc, student) == []

    def test_a_missing_application_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)

        assert start(abc, student).json() == CHOOSE_APPLICATION

    def test_a_second_visa_while_one_is_waiting_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        application = offer_application(abc, student)
        make_visa(abc, student, application, VisaStatus.APPLIED)

        response = start(abc, student, application=application.pk)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == VISA_WAITING
        assert len(stored_visas(abc, student)) == 1

    def test_a_visa_waiting_for_another_application_is_refused_too(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        make_visa(abc, student, offer_application(abc, student), VisaStatus.APPLIED)
        other = offer_application(abc, student, "RMIT University")

        response = start(abc, student, application=other.pk)

        assert response.json() == VISA_WAITING
        assert len(stored_visas(abc, student)) == 1

    def test_a_new_attempt_after_a_refused_one_keeps_both(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        application = offer_application(abc, student)
        first = make_visa(abc, student, application, VisaStatus.REFUSED)

        response = start(abc, student, application=application.pk)

        assert response.status_code == status.HTTP_201_CREATED
        visas = response.json()["visas"]
        assert [v["status"] for v in visas] == ["applied", "refused"]
        assert visas[1]["id"] == first.pk
        assert response.json()["stage"] == "visa"

    def test_a_new_attempt_after_a_granted_one_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        application = offer_application(abc, student)
        make_visa(abc, student, application, VisaStatus.GRANTED)

        response = start(abc, student, application=application.pk)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ALREADY_GRANTED
        assert len(stored_visas(abc, student)) == 1

    def test_a_status_cannot_be_sent_with_a_new_visa(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        application = offer_application(abc, student)

        response = start(abc, student, application=application.pk, status="granted")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_visas(abc, student) == []
        assert stored(abc, student).stage == Stage.VISA

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        application = offer_application(abc, student)

        response = start(
            abc, student, application=application.pk, student_id=999, started_on="2020-01-01"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_visas(abc, student) == []

    @pytest.mark.parametrize("body", [[], "application", 1])
    def test_a_body_that_is_not_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)

        response = abc.client.post(visas_url(student), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_student_has_at_most_the_limit(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.COUNSELLING)
        application = offer_application(abc, student)
        for _ in range(MAX_VISAS_PER_STUDENT):
            make_visa(abc, student, application, VisaStatus.REFUSED)

        response = start(abc, student, application=application.pk)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TOO_MANY

    def test_a_student_who_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert start(abc, 99_999, application=1).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        _, student = counselled_student(abc)

        response = getattr(abc.client, method)(visas_url(student))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        response = abc.new_client().post(visas_url(student), {}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_the_platform_host_has_no_visas_endpoint(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        student = make_student(abc)
        platform_site.log_in()

        response = platform_site.client.post(visas_url(student), {}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestChangingTheStatus:
    def test_the_assigned_counsellor_changes_it(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = change(abc, student, visa, status="granted")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["visas"][0]["status"] == "granted"
        assert response.json()["stage"] == "visa"
        assert stored_visas(abc, student)[0].status == VisaStatus.GRANTED

    def test_granting_records_the_day_it_was_granted(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), VisaStatus.APPLIED)

        assert stored_visas(abc, student)[0].granted_on is None
        response = change(abc, student, visa, status="granted")

        assert response.status_code == status.HTTP_200_OK
        assert stored_visas(abc, student)[0].granted_on == timezone.localdate()

    def test_other_statuses_record_no_granted_day(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        change(abc, student, visa, status="refused")

        assert stored_visas(abc, student)[0].granted_on is None

    @pytest.mark.parametrize(
        ("before", "after"),
        [
            ("applied", "granted"),
            ("applied", "refused"),
            ("granted", "refused"),
            ("granted", "applied"),
            ("refused", "applied"),
            ("refused", "granted"),
        ],
    )
    def test_every_status_can_be_set_by_hand(
        self, abc: ConsultancyContext, before: str, after: str
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), VisaStatus(before))

        assert change(abc, student, visa, status=after).status_code == status.HTTP_200_OK
        assert stored_visas(abc, student)[0].status == after

    def test_back_to_applied_is_refused_while_another_visa_waits(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        refused = make_visa(abc, student, offer_application(abc, student), VisaStatus.REFUSED)
        make_visa(abc, student, offer_application(abc, student, "RMIT"), VisaStatus.APPLIED)

        response = change(abc, student, refused, status="applied")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "status": ["This Student already has a visa waiting for an answer."]
        }
        assert stored_visas(abc, student)[0].status == VisaStatus.REFUSED

    def test_setting_the_status_it_already_has_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = change(abc, student, visa, status="applied")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ALREADY_AT_STATUS

    @pytest.mark.parametrize(
        "value",
        [
            "Granted",
            "GRANTED",
            "preparing",
            "lodged",
            "biometrics_medical",
            "interview",
            "withdrawn",
            "",
            None,
            1,
            True,
            ["granted"],
            "g" * 10_000,
            *INJECTION_STRINGS,
        ],
    )
    def test_bad_statuses_are_refused(self, abc: ConsultancyContext, value: object) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = change(abc, student, visa, status=value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["status"]
        assert stored_visas(abc, student)[0].status == VisaStatus.APPLIED
        assert stored(abc, student).stage == Stage.VISA

    def test_a_missing_status_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        assert change(abc, student, visa).json() == {"status": ["This field is required."]}

    def test_the_application_cannot_be_changed(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        other = offer_application(abc, student, "RMIT University")

        response = change(abc, student, visa, status="refused", application=other.pk)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_visas(abc, student)[0].status == VisaStatus.APPLIED

    def test_another_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        abc.log_in_as(Role.COUNSELLOR)

        response = change(abc, student, visa, status="granted")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        assert stored(abc, student).stage == Stage.VISA
        assert stored_visas(abc, student)[0].status == VisaStatus.APPLIED

    @pytest.mark.parametrize(
        ("role", "refusal"),
        [(Role.FRONT_DESK, COUNSELLOR_OR_ADMIN), (Role.TEACHER, NOT_FOR_TEACHERS)],
    )
    def test_front_desk_and_teachers_are_refused(
        self, abc: ConsultancyContext, role: Role, refusal: dict[str, str]
    ) -> None:
        student = make_student(abc, stage=Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        abc.log_in_as(role)

        response = change(abc, student, visa, status="granted")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == refusal

    def test_an_admin_can_change_anyones(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        abc.log_in_as(Role.ADMIN)

        assert change(abc, student, visa, status="refused").status_code == status.HTTP_200_OK

    def test_a_counsellor_who_transferred_the_student_loses_the_right(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        abc.client.post(
            f"/api/students/{student.pk}/transfer/", {"counsellor": other.pk}, format="json"
        )

        response = change(abc, student, visa, status="refused")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_another_students_visa_is_not_found(self, abc: ConsultancyContext) -> None:
        me, student = counselled_student(abc, Stage.VISA)
        someone_else = make_student(abc, me, Stage.VISA, name="Ram Thapa", phone="9841000002")
        theirs = make_visa(abc, someone_else, offer_application(abc, someone_else))

        response = change(abc, student, theirs, status="granted")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored_visas(abc, someone_else)[0].status == VisaStatus.APPLIED
        assert stored(abc, someone_else).stage == Stage.VISA

    def test_a_visa_that_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)

        assert change(abc, student, 99_999, status="refused").status_code == 404

    @pytest.mark.parametrize("method", ["get", "put", "post", "delete"])
    def test_only_patch_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = getattr(abc.client, method)(visa_url(student, visa))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, stage=Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = abc.new_client().patch(
            visa_url(student, visa), {"status": "granted"}, format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert stored(abc, student).stage == Stage.VISA


class TestTheStageFollows:
    def test_a_visa_applied_for_moves_them_to_visa(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        student = make_student(abc, me, Stage.OFFER)
        application = offer_application(abc, student)

        response = start(abc, student, application=application.pk)

        assert response.json()["stage"] == "visa"
        [entry] = stage_entries(abc, student)
        assert (entry.before, entry.after) == ("offer", "visa")
        assert entry.by_name == "Bikash Thapa"

    def test_a_refused_visa_set_back_to_applied_moves_them_to_visa_again(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        visa = make_visa(abc, student, offer_application(abc, student), VisaStatus.REFUSED)

        response = change(abc, student, visa, status="applied")

        assert response.json()["stage"] == "visa"
        [entry] = stage_entries(abc, student)
        assert entry.detail == "Automatic: Deakin University visa is Applied."

    def test_a_granted_visa_keeps_the_student_at_visa(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), VisaStatus.APPLIED)

        response = change(abc, student, visa, status="granted")

        assert response.json()["stage"] == "visa"
        assert stored(abc, student).stage == Stage.VISA
        assert stage_entries(abc, student) == []

    def test_a_refused_visa_moves_them_back_to_offer(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), VisaStatus.APPLIED)

        response = change(abc, student, visa, status="refused")

        assert response.json()["stage"] == "offer"
        [entry] = stage_entries(abc, student)
        assert (entry.before, entry.after) == ("visa", "offer")
        assert entry.detail == "Automatic: Deakin University visa is Refused."

    def test_a_refused_visa_with_no_offer_left_moves_them_back_to_counselling(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        application = offer_application(abc, student)
        visa = make_visa(abc, student, application, VisaStatus.APPLIED)
        with tenant_context(abc.consultancy):
            Application.objects.filter(pk=application.pk).update(status=ApplicationStatus.REJECTED)

        response = change(abc, student, visa, status="refused")

        assert response.json()["stage"] == "counselling"
        [entry] = stage_entries(abc, student)
        assert (entry.before, entry.after) == ("visa", "counselling")

    def test_a_refused_visa_with_another_waiting_keeps_them_at_visa(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), VisaStatus.APPLIED)
        make_visa(
            abc, student, offer_application(abc, student, "RMIT University"), VisaStatus.APPLIED
        )

        response = change(abc, student, visa, status="refused")

        assert response.json()["stage"] == "visa"
        assert stage_entries(abc, student) == []

    def test_refused_then_granted_keeps_both_attempts(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        application = offer_application(abc, student)
        first = make_visa(abc, student, application, VisaStatus.APPLIED)
        change(abc, student, first, status="refused")
        abc.client.put(
            f"/api/students/{student.pk}/chosen-offer/",
            {"application": application.pk},
            format="json",
        )
        second_id = start(abc, student, application=application.pk).json()["visas"][0]["id"]

        response = change(abc, student, second_id, status="granted")

        body = response.json()
        assert body["stage"] == "visa"
        assert [v["status"] for v in body["visas"]] == ["granted", "refused"]
        assert [(e.before, e.after) for e in stage_entries(abc, student)] == [
            ("visa", "offer"),
            ("offer", "visa"),
        ]

    def test_granted_set_back_to_applied_moves_a_student_at_visa_nowhere(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), VisaStatus.GRANTED)

        response = change(abc, student, visa, status="applied")

        assert response.json()["stage"] == "visa"
        assert stage_entries(abc, student) == []

    @pytest.mark.parametrize("value", ["granted", "refused"])
    def test_a_lost_student_is_never_moved(self, abc: ConsultancyContext, value: str) -> None:
        _, student = counselled_student(abc, Stage.LOST)
        visa = make_visa(abc, student, offer_application(abc, student), VisaStatus.APPLIED)

        response = change(abc, student, visa, status=value)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["stage"] == "lost"
        assert response.json()["visas"][0]["status"] == value
        assert stage_entries(abc, student) == []

    def test_the_history_endpoint_shows_the_automatic_move(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), VisaStatus.APPLIED)
        change(abc, student, visa, status="refused")

        page = abc.client.get(f"/api/students/{student.pk}/history/").json()

        assert page["results"][0]["kind"] == "stage_changed"
        assert page["results"][0]["after"] == "offer"
        assert page["results"][0]["detail"] == "Automatic: Deakin University visa is Refused."

    def test_changes_are_logged_without_names(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        application = offer_application(abc, student)
        visa_id = start(abc, student, application=application.pk).json()["visas"][0]["id"]

        change(abc, student, visa_id, status="refused")

        assert json_logs.with_message("Visa applied")
        assert json_logs.with_message("Visa status changed")
        assert json_logs.with_message("Stage changed automatically")
        assert "Deakin" not in json_logs.text()
        assert "Sita" not in json_logs.text()


class TestTheProfile:
    def test_it_lists_every_attempt_newest_first(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        application = offer_application(abc, student)
        make_visa(abc, student, application, VisaStatus.REFUSED)
        make_visa(abc, student, application, VisaStatus.GRANTED)

        body = abc.client.get(f"/api/students/{student.pk}/").json()

        assert [v["status"] for v in body["visas"]] == ["granted", "refused"]
        assert body["can_change_applications"] is True

    def test_a_student_with_no_visa_has_an_empty_list(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)

        assert abc.client.get(f"/api/students/{student.pk}/").json()["visas"] == []

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.COUNSELLOR])
    def test_others_see_them_but_may_not_change_them(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        owner = abc.add_staff(Role.COUNSELLOR, email="owner@abc.test")
        student = make_student(abc, owner, Stage.VISA)
        make_visa(abc, student, offer_application(abc, student))
        abc.log_in_as(role)

        body = abc.client.get(f"/api/students/{student.pk}/").json()

        assert len(body["visas"]) == 1
        assert body["can_change_applications"] is False

    def test_an_application_moved_off_offer_keeps_its_visas(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        application = offer_application(abc, student)
        make_visa(abc, student, application)
        with tenant_context(abc.consultancy):
            Application.objects.filter(pk=application.pk).update(status=ApplicationStatus.REJECTED)

        body = abc.client.get(f"/api/students/{student.pk}/").json()

        assert body["visas"][0]["university"]["name"] == "Deakin University"


class TestGrantedDayBackfill:
    """Visas granted before the day was recorded get one from History, else their start day."""

    def _fill(self, office: ConsultancyContext) -> None:
        migration = importlib.import_module("applications.migrations.0005_visa_granted_on")
        with tenant_context(office.consultancy):
            migration.fill_granted_on(django_apps, None)

    def test_the_day_the_student_moved_to_completed_is_used(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        granted = make_visa(abc, student, offer_application(abc, student), VisaStatus.GRANTED)
        refused = make_visa(
            abc, student, offer_application(abc, student, "RMIT University"), VisaStatus.REFUSED
        )
        moved_at = timezone.make_aware(datetime(2026, 3, 9, 23, 30))
        with tenant_context(abc.consultancy):
            HistoryEntry.objects.create(
                student=student,
                kind=HistoryKind.STAGE_CHANGED,
                at=moved_at,
                by_name="Someone",
                before="visa",
                after="completed",
            )

        self._fill(abc)

        days = {visa.pk: visa.granted_on for visa in stored_visas(abc, student)}
        assert days == {granted.pk: date(2026, 3, 9), refused.pk: None}

    def test_without_history_the_start_day_is_used(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), VisaStatus.GRANTED)
        with tenant_context(abc.consultancy):
            Visa.objects.filter(pk=visa.pk).update(started_on=date(2025, 12, 1))

        self._fill(abc)

        assert stored_visas(abc, student)[0].granted_on == date(2025, 12, 1)


KATHMANDU = ZoneInfo("Asia/Kathmandu")
DATE_FIELDS = ("biometrics_at", "medical_at", "interview_at")
NOT_WAITING = {"detail": "Only a visa waiting for an answer has dates to change."}
NO_DATE_SENT = {"detail": ["Change at least one date."]}


def in_kathmandu(days: int, hour: int = 10, minute: int = 0) -> datetime:
    """`hour:minute` in Kathmandu, `days` from today."""
    day = timezone.localdate() + timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KATHMANDU)


def local_text(at: datetime) -> str:
    """How the API writes a moment: Kathmandu's "YYYY-MM-DDTHH:MM"."""
    return at.astimezone(KATHMANDU).strftime("%Y-%m-%dT%H:%M")


def dates_url(student: Student | int, visa: Visa | int) -> str:
    """Where one Visa's appointment dates are changed."""
    return f"{visa_url(student, visa)}dates/"


def set_dates(
    office: ConsultancyContext, student: Student, visa: Visa | int, body: object
) -> _MonkeyPatchedResponse:
    """PATCH a Visa's appointment dates."""
    return office.client.patch(dates_url(student, visa), body, format="json")


def stored_dates(office: ConsultancyContext, visa: Visa) -> tuple[datetime | None, ...]:
    """The Visa's Biometrics, Medical and Interview as stored."""
    with tenant_context(office.consultancy):
        stored_visa = Visa.objects.get(pk=visa.pk)
    return (stored_visa.biometrics_at, stored_visa.medical_at, stored_visa.interview_at)


class TestAppointmentDates:
    def test_the_assigned_counsellor_books_all_three_in_kathmandu_time(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        when = [in_kathmandu(3, 14, 30), in_kathmandu(5, 9), in_kathmandu(20, 11, 15)]

        response = set_dates(
            abc, student, visa, dict(zip(DATE_FIELDS, map(local_text, when), strict=True))
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        shown = response.json()["visas"][0]
        assert [shown[field] for field in DATE_FIELDS] == [local_text(at) for at in when]
        assert stored_dates(abc, visa) == tuple(when)
        stored_biometrics = stored_dates(abc, visa)[0]
        assert stored_biometrics is not None
        assert (
            stored_biometrics.astimezone(UTC).hour
            == (when[0] - timedelta(hours=5, minutes=45)).hour
        )

    def test_only_the_dates_sent_change(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        set_dates(abc, student, visa, {"biometrics_at": local_text(in_kathmandu(2))})

        response = set_dates(abc, student, visa, {"interview_at": local_text(in_kathmandu(9))})

        assert response.status_code == status.HTTP_200_OK
        assert stored_dates(abc, visa) == (in_kathmandu(2), None, in_kathmandu(9))

    def test_a_date_can_be_moved_and_cleared(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        set_dates(abc, student, visa, {"medical_at": local_text(in_kathmandu(2))})

        moved = set_dates(abc, student, visa, {"medical_at": local_text(in_kathmandu(4, 8))})
        assert stored_dates(abc, visa)[1] == in_kathmandu(4, 8)
        cleared = set_dates(abc, student, visa, {"medical_at": None})

        assert (moved.status_code, cleared.status_code) == (status.HTTP_200_OK,) * 2
        assert stored_dates(abc, visa) == (None, None, None)
        assert cleared.json()["visas"][0]["medical_at"] is None

    def test_a_past_date_already_stored_can_still_be_cleared(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        with tenant_context(abc.consultancy):
            Visa.objects.filter(pk=visa.pk).update(biometrics_at=in_kathmandu(-4))

        response = set_dates(abc, student, visa, {"biometrics_at": None})

        assert response.status_code == status.HTTP_200_OK
        assert stored_dates(abc, visa)[0] is None

    def test_today_is_allowed(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = set_dates(
            abc, student, visa, {"interview_at": local_text(in_kathmandu(0, 0, 5))}
        )

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("field", DATE_FIELDS)
    def test_a_day_before_today_is_refused(self, abc: ConsultancyContext, field: str) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = set_dates(abc, student, visa, {field: local_text(in_kathmandu(-1, 23, 59))})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {field: ["Pick today or a later day."]}
        assert stored_dates(abc, visa) == (None, None, None)

    def test_more_than_two_years_ahead_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        far = set_dates(abc, student, visa, {"medical_at": local_text(in_kathmandu(731))})
        edge = set_dates(abc, student, visa, {"medical_at": local_text(in_kathmandu(730))})

        assert far.json() == {"medical_at": ["Pick a day within the next two years."]}
        assert edge.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "2027-02-30T10:00",
            "2027-13-01T10:00",
            "2027-02-01T24:00",
            "2027-02-01T10:60",
            "2027-02-01",
            "10:00",
            "2027-02-01 10:00",
            "2027-02-01T10:00:00",
            "2027-02-01T10:00Z",
            "2027-02-01T10:00+05:45",
            "1 Feb 2027 10am",
            " 2027-02-01T10:00",
            "\uff12\uff10\uff12\uff17-02-01T10:00",
            "2" * 10_000,
            20270201,
            True,
            ["2027-02-01T10:00"],
            {"date": "2027-02-01"},
            *INJECTION_STRINGS,
        ],
    )
    @pytest.mark.parametrize("field", DATE_FIELDS)
    def test_bad_values_are_refused(
        self, abc: ConsultancyContext, field: str, value: object
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = set_dates(abc, student, visa, {field: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {field: ["Enter the date and time."]}
        assert stored_dates(abc, visa) == (None, None, None)

    def test_one_bad_value_stores_none_of_them(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = set_dates(
            abc,
            student,
            visa,
            {"biometrics_at": local_text(in_kathmandu(1)), "interview_at": "soon"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_dates(abc, visa) == (None, None, None)

    def test_an_empty_change_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = set_dates(abc, student, visa, {})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NO_DATE_SENT

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = set_dates(
            abc, student, visa, {"interview_at": local_text(in_kathmandu(1)), "status": "granted"}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_visas(abc, student)[0].status == VisaStatus.APPLIED
        assert stored_dates(abc, visa) == (None, None, None)

    @pytest.mark.parametrize("body", [[], "2027-02-01T10:00", None, 1])
    def test_a_body_that_is_not_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        assert set_dates(abc, student, visa, body).status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("closed", [VisaStatus.GRANTED, VisaStatus.REFUSED])
    def test_a_decided_visa_is_refused(self, abc: ConsultancyContext, closed: VisaStatus) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), closed)

        set_response = set_dates(abc, student, visa, {"medical_at": local_text(in_kathmandu(1))})
        clear_response = set_dates(abc, student, visa, {"medical_at": None})

        assert set_response.status_code == status.HTTP_400_BAD_REQUEST
        assert set_response.json() == clear_response.json() == NOT_WAITING
        assert stored_dates(abc, visa) == (None, None, None)

    def test_an_admin_sets_them_for_anyones_student(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        abc.log_in_as(Role.ADMIN)

        response = set_dates(abc, student, visa, {"biometrics_at": local_text(in_kathmandu(1))})

        assert response.status_code == status.HTTP_200_OK

    def test_another_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        abc.log_in_as(Role.COUNSELLOR)

        response = set_dates(abc, student, visa, {"biometrics_at": local_text(in_kathmandu(1))})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        assert stored_dates(abc, visa) == (None, None, None)

    @pytest.mark.parametrize(
        ("role", "refusal"),
        [(Role.FRONT_DESK, COUNSELLOR_OR_ADMIN), (Role.TEACHER, NOT_FOR_TEACHERS)],
    )
    def test_front_desk_and_teachers_are_refused(
        self, abc: ConsultancyContext, role: Role, refusal: dict[str, str]
    ) -> None:
        student = make_student(abc, stage=Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        abc.log_in_as(role)

        response = set_dates(abc, student, visa, {"biometrics_at": local_text(in_kathmandu(1))})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == refusal
        assert stored_dates(abc, visa) == (None, None, None)

    def test_another_students_visa_is_not_found(self, abc: ConsultancyContext) -> None:
        me, student = counselled_student(abc, Stage.VISA)
        someone_else = make_student(abc, me, Stage.VISA, name="Ram Thapa", phone="9841000002")
        theirs = make_visa(abc, someone_else, offer_application(abc, someone_else))

        response = set_dates(abc, student, theirs, {"interview_at": local_text(in_kathmandu(1))})

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored_dates(abc, theirs) == (None, None, None)

    def test_a_visa_that_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)

        response = set_dates(abc, student, 999_999, {"interview_at": None})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "put", "post", "delete"])
    def test_only_patch_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = getattr(abc.client, method)(dates_url(student, visa), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_the_old_interview_address_is_gone(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = abc.client.put(
            f"{visa_url(student, visa)}interview/", {"interview_at": None}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, stage=Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = abc.new_client().patch(
            dates_url(student, visa), {"interview_at": None}, format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_it_is_logged_without_names_or_the_times(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        when = in_kathmandu(4, 11, 15)

        set_dates(abc, student, visa, {"medical_at": local_text(when)})

        text = json_logs.text()
        assert f"visa_id={visa.pk}" in text
        assert "changed=medical" in text
        assert "Sita" not in text
        assert local_text(when) not in text


class TestConversion:
    """Migration 0007 moves the old Visa Statuses onto Applied, Granted and Refused."""

    def _convert(self, office: ConsultancyContext) -> None:
        migration = importlib.import_module("applications.migrations.0007_visa_three_statuses")
        with tenant_context(office.consultancy):
            migration.convert(django_apps, None)

    def _old_visa(
        self,
        office: ConsultancyContext,
        student: Student,
        application: Application,
        old_status: str,
        **fields: object,
    ) -> Visa:
        visa = make_visa(office, student, application)
        with tenant_context(office.consultancy):
            Visa.objects.filter(pk=visa.pk).update(status=old_status, **fields)
        return visa

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ("lodged", "applied"),
            ("biometrics_medical", "applied"),
            ("interview", "applied"),
            ("withdrawn", "refused"),
            ("granted", "granted"),
            ("refused", "refused"),
        ],
    )
    def test_each_old_status_moves_to_its_new_one(
        self, abc: ConsultancyContext, old: str, new: str
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        self._old_visa(abc, student, offer_application(abc, student), old)

        self._convert(abc)

        assert [visa.status for visa in stored_visas(abc, student)] == [new]

    def test_the_interview_date_stays_as_the_interview_date(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        when = in_kathmandu(3)
        visa = self._old_visa(
            abc, student, offer_application(abc, student), "interview", interview_at=when
        )

        self._convert(abc)

        assert stored_dates(abc, visa) == (None, None, when)

    def test_a_preparing_visa_is_removed_and_its_documents_stay_unlinked(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        visa = self._old_visa(abc, student, offer_application(abc, student), "preparing")
        with tenant_context(abc.consultancy):
            document_model = django_apps.get_model("documents", "Document")
            document_type = django_apps.get_model("documents", "DocumentType").objects.all()[0]
            document = document_model.objects.create(
                student=student,
                document_type=document_type,
                visa=visa,
            )

        self._convert(abc)

        assert stored_visas(abc, student) == []
        with tenant_context(abc.consultancy):
            kept = document_model.objects.get(pk=document.pk)
        assert (kept.student_id, kept.visa_id) == (student.pk, None)
        assert stored(abc, student).stage == Stage.OFFER

    def test_a_student_at_visa_with_only_a_preparing_visa_is_at_offer(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        self._old_visa(abc, student, offer_application(abc, student), "preparing")

        self._convert(abc)

        assert stored(abc, student).stage == Stage.OFFER

    def test_a_student_with_another_visa_stays_at_visa(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        self._old_visa(abc, student, offer_application(abc, student), "preparing")
        self._old_visa(abc, student, offer_application(abc, student, "RMIT"), "lodged")

        self._convert(abc)

        assert stored(abc, student).stage == Stage.VISA
        assert [visa.status for visa in stored_visas(abc, student)] == ["applied"]

    def test_history_is_never_rewritten(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        self._old_visa(abc, student, offer_application(abc, student), "preparing")

        self._convert(abc)

        assert stage_entries(abc, student) == []
