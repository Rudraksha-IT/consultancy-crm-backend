"""Applications and the Stage they move a Student to (spec 52-57; issues 14 and 74)."""

from __future__ import annotations

import importlib
from itertools import count
from typing import TYPE_CHECKING

import pytest
from django.apps import apps as django_apps
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.exceptions import NotWaitingForOfferError
from applications.models import Application
from applications.services import MAX_APPLICATIONS_PER_STUDENT, record_offer
from applications.statuses import ApplicationStatus, OfferKind
from catalogue.models import Course, CourseLevel, Intake, University
from catalogue.services import change_course
from catalogue.types import CourseChange
from history.models import HistoryEntry, HistoryKind
from staff.models import Role, Staff
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
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

COUNSELLOR_OR_ADMIN = {"detail": "Only the Student's counsellor or an Admin can do this."}
CHOOSE_COURSE = {"course": ["Choose a course from the Catalogue."]}
CHOOSE_INTAKE = {"intake": ["Choose one of this course's intakes."]}
ALREADY_APPLIED = {
    "intake": ["This Student already has an application for this course and intake."]
}
ALREADY_AT_STATUS = {"status": ["The application already has this status."]}
OFFER_NEEDS_LETTER = {"status": ["Offer is set by adding an offer letter."]}
OFFER_STAYS = {"status": ["An application with an offer stays at Offer."]}
TOO_MANY = {"detail": f"A Student can have at most {MAX_APPLICATIONS_PER_STUDENT} applications."}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def applications_url(student: Student | int) -> str:
    """Where a Student's Applications are added."""
    pk = student if isinstance(student, int) else student.pk
    return f"/api/students/{pk}/applications/"


def application_url(student: Student | int, application: Application | int) -> str:
    """One of a Student's Applications."""
    pk = student if isinstance(student, int) else student.pk
    app_pk = application if isinstance(application, int) else application.pk
    return f"/api/students/{pk}/applications/{app_pk}/"


def make_course(
    office: ConsultancyContext,
    university: str = "Deakin University",
    intakes: tuple[str, ...] = ("2027-03", "2027-07"),
    *,
    archived: bool = False,
    university_archived: bool = False,
) -> Course:
    """Store a Course with its University and Intakes."""
    with tenant_context(office.consultancy):
        uni, _ = University.objects.get_or_create(
            name=university, defaults={"country": "australia", "archived": university_archived}
        )
        course = Course.objects.create(
            university=uni, name="Master of IT", level=CourseLevel.MASTER, archived=archived
        )
        Intake.objects.bulk_create(Intake(course=course, month=month) for month in intakes)
        return course


def make_application(
    office: ConsultancyContext,
    student: Student,
    course: Course,
    application_status: ApplicationStatus = ApplicationStatus.APPLIED,
    intake: str = "2027-03",
) -> Application:
    """Store an Application directly."""
    with tenant_context(office.consultancy):
        return Application.objects.create(
            student=student, course=course, intake=intake, status=application_status
        )


def add(
    office: ConsultancyContext, student: Student | int, **body: object
) -> _MonkeyPatchedResponse:
    """POST a new Application."""
    return office.client.post(applications_url(student), body, format="json")


def change(
    office: ConsultancyContext, student: Student, application: Application | int, **body: object
) -> _MonkeyPatchedResponse:
    """PATCH an Application's Status."""
    return office.client.patch(application_url(student, application), body, format="json")


def stored_applications(office: ConsultancyContext, student: Student) -> list[Application]:
    """The Student's stored Applications, oldest first."""
    with tenant_context(office.consultancy):
        return list(Application.objects.filter(student=student).order_by("pk"))


def stage_entries(office: ConsultancyContext, student: Student) -> list[HistoryEntry]:
    """The Student's stored Stage changes, oldest first."""
    with tenant_context(office.consultancy):
        return list(
            HistoryEntry.objects.filter(student=student, kind=HistoryKind.STAGE_CHANGED).order_by(
                "at", "pk"
            )
        )


def counselled_student(
    office: ConsultancyContext, stage: Stage = Stage.COUNSELLING
) -> tuple[Staff, Student]:
    """Log in as a Counsellor and give them a Student at `stage`."""
    me = office.log_in_as(Role.COUNSELLOR)
    return me, make_student(office, me, stage)


class TestAddingAnApplication:
    def test_the_assigned_counsellor_adds_one_as_applied(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        course = make_course(abc)

        response = add(abc, student, course=course.pk, intake="2027-07")

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["id"] == student.pk
        assert body["stage"] == "applying"
        assert body["applications"] == [
            {
                "id": stored_applications(abc, student)[0].pk,
                "university": {"id": course.university_id, "name": "Deakin University"},
                "course": {"id": course.pk, "name": "Master of IT", "level": "master"},
                "intake": "2027-07",
                "status": "applied",
                "offer_kind": "",
                "offered_on": None,
                "offer_letter": None,
                "documents": [],
                "course_files": [],
            }
        ]
        assert body["chosen_offer_id"] is None
        assert stored_applications(abc, student)[0].applied_on is not None
        assert stage_entries(abc, student) == []

    def test_a_student_can_have_several(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)
        first = make_course(abc)
        second = make_course(abc, "RMIT University")

        add(abc, student, course=first.pk, intake="2027-03")
        body = add(abc, student, course=second.pk, intake="2027-03").json()

        names = [a["university"]["name"] for a in body["applications"]]
        assert names == ["Deakin University", "RMIT University"]

    def test_an_admin_can_add_to_anyones_student(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.COUNSELLING)
        abc.log_in_as(Role.ADMIN)

        response = add(abc, student, course=make_course(abc).pk, intake="2027-03")

        assert response.status_code == status.HTTP_201_CREATED

    def test_an_admin_can_add_to_a_student_with_no_counsellor(
        self, abc: ConsultancyContext
    ) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.ADMIN)

        response = add(abc, student, course=make_course(abc).pk, intake="2027-03")

        assert response.status_code == status.HTTP_201_CREATED

    def test_another_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other)
        abc.log_in_as(Role.COUNSELLOR)

        response = add(abc, student, course=make_course(abc).pk, intake="2027-03")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        assert stored_applications(abc, student) == []

    def test_front_desk_is_refused_even_for_an_unassigned_lead(
        self, abc: ConsultancyContext
    ) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        response = add(abc, student, course=make_course(abc).pk, intake="2027-03")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == COUNSELLOR_OR_ADMIN

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = add(abc, student, course=make_course(abc).pk, intake="2027-03")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_a_counsellor_who_transferred_the_student_loses_the_right(
        self, abc: ConsultancyContext
    ) -> None:
        me, student = counselled_student(abc)
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        abc.client.post(
            f"/api/students/{student.pk}/transfer/", {"counsellor": other.pk}, format="json"
        )

        response = add(abc, student, course=make_course(abc).pk, intake="2027-03")

        assert me.pk != other.pk
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize(
        "course", [None, "1", 1.5, True, [], {}, 0, -1, 99_999, *INJECTION_STRINGS]
    )
    def test_a_course_not_in_the_catalogue_is_refused(
        self, abc: ConsultancyContext, course: object
    ) -> None:
        _, student = counselled_student(abc)
        make_course(abc)

        response = add(abc, student, course=course, intake="2027-03")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_COURSE
        assert stored_applications(abc, student) == []

    def test_a_missing_course_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)

        response = add(abc, student, intake="2027-03")

        assert response.json() == CHOOSE_COURSE

    def test_an_archived_course_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)
        course = make_course(abc, archived=True)

        response = add(abc, student, course=course.pk, intake="2027-03")

        assert response.json() == CHOOSE_COURSE

    def test_a_course_of_an_archived_university_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)
        course = make_course(abc, university_archived=True)

        response = add(abc, student, course=course.pk, intake="2027-03")

        assert response.json() == CHOOSE_COURSE

    @pytest.mark.parametrize(
        "intake",
        [
            "2027-04",
            "2027-3",
            "Mar 2027",
            " 2027-03",
            "",
            None,
            202703,
            ["2027-03"],
            *INJECTION_STRINGS,
        ],
    )
    def test_an_intake_the_course_does_not_run_is_refused(
        self, abc: ConsultancyContext, intake: object
    ) -> None:
        _, student = counselled_student(abc)
        course = make_course(abc)

        response = add(abc, student, course=course.pk, intake=intake)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_INTAKE
        assert stored_applications(abc, student) == []

    def test_a_missing_intake_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)

        response = add(abc, student, course=make_course(abc).pk)

        assert response.json() == CHOOSE_INTAKE

    def test_the_same_course_and_intake_twice_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)
        course = make_course(abc)
        add(abc, student, course=course.pk, intake="2027-03")

        again = add(abc, student, course=course.pk, intake="2027-03")
        other_intake = add(abc, student, course=course.pk, intake="2027-07")

        assert again.status_code == status.HTTP_400_BAD_REQUEST
        assert again.json() == ALREADY_APPLIED
        assert other_intake.status_code == status.HTTP_201_CREATED
        assert len(stored_applications(abc, student)) == 2

    def test_a_status_cannot_be_sent_with_a_new_application(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)

        response = add(abc, student, course=make_course(abc).pk, intake="2027-03", status="offer")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_applications(abc, student) == []

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)

        response = add(
            abc, student, course=make_course(abc).pk, intake="2027-03", student_id=999, note="x"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_applications(abc, student) == []

    @pytest.mark.parametrize("body", [[], "course", 1])
    def test_a_body_that_is_not_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        _, student = counselled_student(abc)

        response = abc.client.post(applications_url(student), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_student_has_at_most_the_limit(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)
        months = tuple(f"20{year}-{month:02d}" for year in (27, 28, 29) for month in range(1, 13))
        course = make_course(abc, intakes=months[:24])
        other = make_course(abc, "RMIT University", intakes=months[24:])
        for month in months[:MAX_APPLICATIONS_PER_STUDENT]:
            make_application(abc, student, course if month in months[:24] else other, intake=month)

        response = add(abc, student, course=other.pk, intake=months[-1])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TOO_MANY

    def test_a_student_who_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add(abc, 99_999, course=make_course(abc).pk, intake="2027-03")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        _, student = counselled_student(abc)

        response = getattr(abc.client, method)(applications_url(student))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        response = abc.new_client().post(applications_url(student), {}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_the_platform_host_has_no_applications_endpoint(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        student = make_student(abc)
        platform_site.log_in()

        response = platform_site.client.post(applications_url(student), {}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestChangingTheStatus:
    def test_the_assigned_counsellor_rejects_it(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = make_application(abc, student, make_course(abc))

        response = change(abc, student, application, status="rejected")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["applications"][0]["status"] == "rejected"
        assert stored_applications(abc, student)[0].status == ApplicationStatus.REJECTED

    def test_a_rejected_one_can_be_set_back_to_applied(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = make_application(abc, student, make_course(abc), ApplicationStatus.REJECTED)

        response = change(abc, student, application, status="applied")

        assert response.status_code == status.HTTP_200_OK
        [stored_one] = stored_applications(abc, student)
        assert stored_one.status == ApplicationStatus.APPLIED
        assert stored_one.applied_on is not None

    def test_offer_cannot_be_set_by_hand(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = make_application(abc, student, make_course(abc))

        response = change(abc, student, application, status="offer")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == OFFER_NEEDS_LETTER
        assert stored_applications(abc, student)[0].status == ApplicationStatus.APPLIED
        assert stored(abc, student).stage == Stage.APPLYING

    @pytest.mark.parametrize("value", ["applied", "rejected"])
    def test_an_application_with_an_offer_stays_offer(
        self, abc: ConsultancyContext, value: str
    ) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        application = make_application(abc, student, make_course(abc), ApplicationStatus.OFFER)

        response = change(abc, student, application, status=value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == OFFER_STAYS
        assert stored_applications(abc, student)[0].status == ApplicationStatus.OFFER

    def test_setting_the_status_it_already_has_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)
        application = make_application(abc, student, make_course(abc))

        response = change(abc, student, application, status="applied")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ALREADY_AT_STATUS

    @pytest.mark.parametrize(
        "value",
        [
            "Rejected",
            "REJECTED",
            "draft",
            "submitted",
            "withdrawn",
            "coe_received",
            "",
            None,
            1,
            True,
            ["rejected"],
            "r" * 10_000,
            *INJECTION_STRINGS,
        ],
    )
    def test_bad_statuses_are_refused(self, abc: ConsultancyContext, value: object) -> None:
        _, student = counselled_student(abc)
        application = make_application(abc, student, make_course(abc))

        response = change(abc, student, application, status=value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["status"]
        assert stored_applications(abc, student)[0].status == ApplicationStatus.APPLIED

    def test_a_missing_status_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)
        application = make_application(abc, student, make_course(abc))

        response = change(abc, student, application)

        assert response.json() == {"status": ["This field is required."]}

    @pytest.mark.parametrize(
        "extra",
        [{"intake": "2027-07"}, {"offer_kind": "conditional"}, {"applied_on": "2020-01-01"}],
    )
    def test_nothing_else_can_be_changed(
        self, abc: ConsultancyContext, extra: dict[str, str]
    ) -> None:
        _, student = counselled_student(abc)
        course = make_course(abc)
        application = make_application(abc, student, course)

        response = change(abc, student, application, status="rejected", **extra)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_applications(abc, student)[0].status == ApplicationStatus.APPLIED

    def test_another_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.APPLYING)
        application = make_application(abc, student, make_course(abc))
        abc.log_in_as(Role.COUNSELLOR)

        response = change(abc, student, application, status="rejected")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        assert stored_applications(abc, student)[0].status == ApplicationStatus.APPLIED

    @pytest.mark.parametrize(
        ("role", "refusal"),
        [(Role.FRONT_DESK, COUNSELLOR_OR_ADMIN), (Role.TEACHER, NOT_FOR_TEACHERS)],
    )
    def test_front_desk_and_teachers_are_refused(
        self, abc: ConsultancyContext, role: Role, refusal: dict[str, str]
    ) -> None:
        student = make_student(abc)
        application = make_application(abc, student, make_course(abc))
        abc.log_in_as(role)

        response = change(abc, student, application, status="rejected")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == refusal

    def test_an_admin_can_change_anyones(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.APPLYING)
        application = make_application(abc, student, make_course(abc))
        abc.log_in_as(Role.ADMIN)

        response = change(abc, student, application, status="rejected")

        assert response.status_code == status.HTTP_200_OK

    def test_another_students_application_is_not_found(self, abc: ConsultancyContext) -> None:
        me, student = counselled_student(abc)
        someone_else = make_student(abc, me, name="Ram Thapa", phone="9841000002")
        theirs = make_application(abc, someone_else, make_course(abc))

        response = change(abc, student, theirs, status="rejected")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored_applications(abc, someone_else)[0].status == ApplicationStatus.APPLIED

    def test_an_application_that_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)

        assert change(abc, student, 99_999, status="rejected").status_code == 404

    @pytest.mark.parametrize("method", ["get", "put", "post", "delete"])
    def test_only_patch_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        _, student = counselled_student(abc)
        application = make_application(abc, student, make_course(abc))

        response = getattr(abc.client, method)(application_url(student, application))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        application = make_application(abc, student, make_course(abc))

        response = abc.new_client().patch(
            application_url(student, application), {"status": "rejected"}, format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


def offer(
    office: ConsultancyContext,
    student: Student,
    application: Application,
    by: Staff,
    kind: OfferKind = OfferKind.CONDITIONAL,
) -> None:
    """Record an offer on the Application, as adding its Offer Letter does (ticket 76)."""
    with tenant_context(office.consultancy):
        record_offer(student, application.pk, kind, by)


class TestTheStageFollows:
    @pytest.mark.parametrize("stage", [Stage.LEAD, Stage.COUNSELLING])
    def test_the_first_application_moves_them_to_applying(
        self, abc: ConsultancyContext, stage: Stage
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        student = make_student(abc, me, stage)

        response = add(abc, student, course=make_course(abc).pk, intake="2027-03")

        assert response.json()["stage"] == "applying"
        assert stored(abc, student).stage == Stage.APPLYING
        [entry] = stage_entries(abc, student)
        assert (entry.before, entry.after) == (stage.value, "applying")
        assert entry.detail == "Automatic: Deakin University application added."
        assert entry.by_name == "Bikash Thapa"

    def test_the_history_endpoint_shows_the_automatic_move(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)
        add(abc, student, course=make_course(abc).pk, intake="2027-03")

        page = abc.client.get(f"/api/students/{student.pk}/history/").json()

        assert page["results"][0]["kind"] == "stage_changed"
        assert page["results"][0]["after"] == "applying"
        assert page["results"][0]["detail"].startswith("Automatic:")

    def test_an_offer_letter_makes_it_offer_and_moves_them_to_offer(
        self, abc: ConsultancyContext
    ) -> None:
        me, student = counselled_student(abc, Stage.APPLYING)
        application = make_application(abc, student, make_course(abc))

        offer(abc, student, application, me, OfferKind.UNCONDITIONAL)

        [stored_one] = stored_applications(abc, student)
        assert (stored_one.status, stored_one.offer_kind) == ("offer", "unconditional")
        assert stored(abc, student).stage == Stage.OFFER
        [entry] = stage_entries(abc, student)
        assert (entry.before, entry.after) == ("applying", "offer")
        assert entry.detail == "Automatic: offer letter from Deakin University added."
        body = abc.client.get(f"/api/students/{student.pk}/").json()
        assert body["applications"][0]["offer_kind"] == "unconditional"

    def test_a_second_offer_letter_on_the_same_application_is_refused(
        self, abc: ConsultancyContext
    ) -> None:
        me, student = counselled_student(abc, Stage.APPLYING)
        application = make_application(abc, student, make_course(abc))
        offer(abc, student, application, me)

        with pytest.raises(NotWaitingForOfferError):
            offer(abc, student, application, me)

    def test_a_second_offer_keeps_them_at_offer(self, abc: ConsultancyContext) -> None:
        me, student = counselled_student(abc, Stage.OFFER)
        make_application(abc, student, make_course(abc), ApplicationStatus.OFFER)
        other = make_application(abc, student, make_course(abc, "RMIT University"))

        offer(abc, student, other, me)

        assert stored(abc, student).stage == Stage.OFFER
        assert stage_entries(abc, student) == []

    def test_every_application_rejected_leaves_them_applying(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        make_application(abc, student, make_course(abc), ApplicationStatus.REJECTED)
        last = make_application(abc, student, make_course(abc, "RMIT University"))

        response = change(abc, student, last, status="rejected")

        assert response.json()["stage"] == "applying"
        assert stage_entries(abc, student) == []

    @pytest.mark.parametrize("stage", [Stage.OFFER, Stage.VISA])
    def test_a_student_already_further_on_stays(
        self, abc: ConsultancyContext, stage: Stage
    ) -> None:
        _, student = counselled_student(abc, stage)

        response = add(abc, student, course=make_course(abc).pk, intake="2027-03")

        assert response.json()["stage"] == stage.value
        assert stage_entries(abc, student) == []

    def test_a_lost_student_is_never_moved(self, abc: ConsultancyContext) -> None:
        me, student = counselled_student(abc, Stage.LOST)

        response = add(abc, student, course=make_course(abc).pk, intake="2027-03")
        [application] = stored_applications(abc, student)
        offer(abc, student, application, me)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["stage"] == "lost"
        assert stored(abc, student).stage == Stage.LOST
        assert stage_entries(abc, student) == []

    def test_the_move_is_logged_without_names(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        _, student = counselled_student(abc)

        add(abc, student, course=make_course(abc).pk, intake="2027-03")
        change(abc, student, stored_applications(abc, student)[0], status="rejected")

        assert json_logs.with_message("Application status changed")
        assert json_logs.with_message("Stage changed automatically")
        assert "Deakin" not in json_logs.text()
        assert "Sita" not in json_logs.text()


class TestTheProfile:
    @pytest.mark.parametrize(
        ("stage", "statuses", "expected"),
        [
            (Stage.LEAD, (), (8, "Have the first counselling session")),
            (Stage.COUNSELLING, (), (20, "Choose a course and apply")),
            (
                Stage.APPLYING,
                (ApplicationStatus.APPLIED,),
                (45, "Waiting for an offer, Deakin University"),
            ),
            (Stage.APPLYING, (ApplicationStatus.REJECTED,), (35, "Find new options")),
            (Stage.OFFER, (ApplicationStatus.OFFER,), (70, "Choose an offer (1 received)")),
        ],
    )
    def test_it_shows_the_same_next_step_as_home_for_every_stage(
        self,
        abc: ConsultancyContext,
        stage: Stage,
        statuses: tuple[ApplicationStatus, ...],
        expected: tuple[int, str],
    ) -> None:
        me, student = counselled_student(abc, stage)
        for application_status in statuses:
            make_application(abc, student, make_course(abc), application_status)

        body = abc.client.get(f"/api/students/{student.pk}/").json()
        [home_row] = abc.client.get("/api/home/progress/", {"counsellor": "me"}).json()["results"]

        percent, next_step = expected
        assert body["progress"] == {"percent": percent, "next_step": next_step, "is_done": False}
        assert (home_row["percent"], home_row["next_step"]) == (percent, next_step)
        assert me.pk == body["assigned_counsellor"]["id"]

    def test_it_lists_every_application_with_its_status(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc)
        make_application(abc, student, make_course(abc), ApplicationStatus.REJECTED)
        make_application(abc, student, make_course(abc, "RMIT University"))

        body = abc.client.get(f"/api/students/{student.pk}/").json()

        assert [a["status"] for a in body["applications"]] == ["rejected", "applied"]
        assert body["can_change_applications"] is True

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.COUNSELLOR])
    def test_others_see_them_but_may_not_change_them(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        owner = abc.add_staff(Role.COUNSELLOR, email="owner@abc.test")
        student = make_student(abc, owner)
        make_application(abc, student, make_course(abc))
        abc.log_in_as(role)

        body = abc.client.get(f"/api/students/{student.pk}/").json()

        assert len(body["applications"]) == 1
        assert body["can_change_applications"] is False

    def test_an_archived_course_still_shows_on_old_applications(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc)
        course = make_course(abc)
        make_application(abc, student, course)
        with tenant_context(abc.consultancy):
            Course.objects.filter(pk=course.pk).update(archived=True)
            University.objects.filter(pk=course.university_id).update(archived=True)

        body = abc.client.get(f"/api/students/{student.pk}/").json()

        assert body["applications"][0]["course"]["name"] == "Master of IT"
        assert body["applications"][0]["university"]["name"] == "Deakin University"

    def test_removing_an_intake_from_the_course_keeps_old_applications(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc, admin)
        course = make_course(abc)
        make_application(abc, student, course, intake="2027-03")
        with tenant_context(abc.consultancy):
            change_course(course, CourseChange(intakes=("2027-07",)), by=admin)

        body = abc.client.get(f"/api/students/{student.pk}/").json()

        assert body["applications"][0]["intake"] == "2027-03"


_PHONES = count(1)


class TestConvertingEarlierData:
    """The migration moves stored Stages and Statuses onto the new ones (issue 74)."""

    def _convert(self, office: ConsultancyContext) -> None:
        migration = importlib.import_module("applications.migrations.0006_new_stages")
        with tenant_context(office.consultancy):
            migration.convert(django_apps, None)

    def _student_at(self, office: ConsultancyContext, stage: str, name: str) -> Student:
        student = make_student(office, name=name, phone=f"98410{next(_PHONES):05d}")
        with tenant_context(office.consultancy):
            Student.objects.filter(pk=student.pk).update(stage=stage)
        return student

    def _application(
        self, office: ConsultancyContext, student: Student, old_status: str, university: str
    ) -> Application:
        application = make_application(office, student, make_course(office, university))
        with tenant_context(office.consultancy):
            Application.objects.filter(pk=application.pk).update(status=old_status)
        return application

    @pytest.mark.parametrize(
        ("old", "new", "kind"),
        [
            ("draft", "applied", ""),
            ("submitted", "applied", ""),
            ("conditional_offer", "offer", "conditional"),
            ("unconditional_offer", "offer", "unconditional"),
            ("deposit_paid", "offer", ""),
            ("coe_received", "offer", ""),
            ("withdrawn", "rejected", ""),
            ("rejected", "rejected", ""),
        ],
    )
    def test_application_statuses(
        self, abc: ConsultancyContext, old: str, new: str, kind: str
    ) -> None:
        student = self._student_at(abc, "applying", "Sita Karki")
        self._application(abc, student, old, "Deakin University")

        self._convert(abc)

        [stored_one] = stored_applications(abc, student)
        assert (stored_one.status, stored_one.offer_kind) == (new, kind)

    def test_stages(self, abc: ConsultancyContext) -> None:
        onboarded_alone = self._student_at(abc, "onboarded", "Asha Rai")
        onboarded_applied = self._student_at(abc, "onboarded", "Binod Rai")
        self._application(abc, onboarded_applied, "draft", "Deakin University")
        applying_with_offer = self._student_at(abc, "applying", "Chandra Rai")
        self._application(abc, applying_with_offer, "deposit_paid", "RMIT University")
        applying = self._student_at(abc, "applying", "Dipa Rai")
        self._application(abc, applying, "submitted", "Monash University")
        completed = self._student_at(abc, "completed", "Esha Rai")
        lost = self._student_at(abc, "lost", "Gita Rai")
        self._application(abc, lost, "conditional_offer", "Torrens University")

        self._convert(abc)

        assert [stored(abc, s).stage for s in (onboarded_alone, onboarded_applied)] == [
            Stage.COUNSELLING,
            Stage.APPLYING,
        ]
        assert stored(abc, applying_with_offer).stage == Stage.OFFER
        assert stored(abc, applying).stage == Stage.APPLYING
        assert stored(abc, completed).stage == Stage.VISA
        assert stored(abc, lost).stage == Stage.LOST

    def test_history_is_never_rewritten(self, abc: ConsultancyContext) -> None:
        student = self._student_at(abc, "onboarded", "Asha Rai")
        with tenant_context(abc.consultancy):
            HistoryEntry.objects.create(
                student=student,
                kind=HistoryKind.STAGE_CHANGED,
                by_name="Someone",
                before="counselling",
                after="onboarded",
            )

        self._convert(abc)

        [entry] = stage_entries(abc, student)
        assert (entry.before, entry.after) == ("counselling", "onboarded")
