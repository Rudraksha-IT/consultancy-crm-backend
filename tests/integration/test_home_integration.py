"""The Homes' Students: progress as arrow steps, and the pipeline (issue 33, spec 22a, 22b)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application, Visa
from applications.statuses import ApplicationStatus, VisaStatus
from catalogue.models import Course, University
from documents.models import Document, DocumentType
from documents.verification import Verification
from staff.models import Role, Staff
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from pytest_django import DjangoAssertNumQueries

PROGRESS_URL = "/api/home/progress/"
PIPELINE_URL = "/api/home/pipeline/"
PROGRESS_ROLES_ONLY = {"detail": "Only counsellors and Admins can see this."}
ADMINS_ONLY = {"detail": "Only an Admin can see this."}
# The domain, the Staff member, then Students, Applications, Visas, Documents, Document Types,
# the Students' countries and Chosen Offers, the Applications' Countries and Courses and the
# Document Requirements: ten, each after django-tenants' search_path, however many Students.
MOST_QUERIES = 20
EVERYONE_NEEDS = 8


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def course_in(office: ConsultancyContext, university: str = "Deakin University") -> Course:
    """A Course at a University in the office's Catalogue."""
    with tenant_context(office.consultancy):
        found = University.objects.create(name=university, country="australia")
        return Course.objects.create(university=found, name="Master of IT", level="master")


def apply(
    office: ConsultancyContext, student: Student, course: Course, at: ApplicationStatus
) -> Application:
    """Store an Application at a Status (the Stage is set by the test)."""
    with tenant_context(office.consultancy):
        return Application.objects.create(
            student=student, course=course, intake="2027-03", status=at
        )


def hand_in(
    office: ConsultancyContext, student: Student, type_name: str, verification: Verification
) -> None:
    """Store one Document of a type, already judged."""
    with tenant_context(office.consultancy):
        Document.objects.create(
            student=student,
            document_type=DocumentType.objects.get(name=type_name),
            verification=verification,
            rejection_reason="Blurry" if verification == Verification.REJECTED else "",
        )


def names(body: dict[str, list[dict[str, dict[str, str]]]]) -> list[str]:
    """The Students' names on the answer, in order."""
    return [row["student"]["name"] for row in body["results"]]


class TestProgress:
    def test_a_student_shows_their_stage_progress_next_step_and_documents(
        self, abc: ConsultancyContext
    ) -> None:
        counsellor = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, counsellor, Stage.APPLYING, name="Sita Gurung")
        apply(abc, student, course_in(abc), ApplicationStatus.APPLIED)
        hand_in(abc, student, "Passport", Verification.VERIFIED)
        hand_in(abc, student, "Citizenship", Verification.PENDING)
        hand_in(abc, student, "SEE Marksheet", Verification.REJECTED)
        log_in(abc, counsellor)

        response = abc.client.get(PROGRESS_URL, {"counsellor": "me"})

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "count": 1,
            "results": [
                {
                    "student": {
                        "id": str(student.public_id),
                        "name": "Sita Gurung",
                        "stage": "applying",
                        "country": "australia",
                        "assigned_counsellor": {
                            "id": str(counsellor.public_id),
                            "name": "Bikash Thapa",
                        },
                    },
                    "percent": 45,
                    "next_step": "Waiting for an offer, Deakin University",
                    "is_done": False,
                    "documents": {
                        "done": 2,
                        "total": EVERYONE_NEEDS,
                        "rejected": 1,
                        "waiting": 1,
                        "missing": EVERYONE_NEEDS - 3,
                    },
                }
            ],
        }

    def test_a_visa_in_progress_gives_the_next_step(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc, admin, Stage.VISA)
        application = apply(abc, student, course_in(abc), ApplicationStatus.OFFER)
        with tenant_context(abc.consultancy):
            Visa.objects.create(
                student=student,
                application=application,
                status=VisaStatus.APPLIED,
                medical_at=timezone.now() + timedelta(days=2),
            )

        row = abc.client.get(PROGRESS_URL).json()["results"][0]
        profile = abc.client.get(f"/api/students/{student.public_id}/").json()["progress"]

        assert (row["percent"], row["next_step"], row["is_done"]) == (
            88,
            "Medical coming up",
            False,
        )
        assert profile == {"percent": 88, "next_step": "Medical coming up", "is_done": False}

    def test_a_granted_visa_is_finished_and_left_out_of_home(self, abc: ConsultancyContext) -> None:
        """Finished Students need no work, so Home's progress skips them (list tidy-ups 14)."""
        admin = abc.log_in_as(Role.ADMIN)
        make_student(abc, admin, Stage.OFFER, name="Asha Offer")
        student = make_student(abc, admin, Stage.VISA, name="Binod Granted")
        application = apply(abc, student, course_in(abc), ApplicationStatus.OFFER)
        with tenant_context(abc.consultancy):
            Visa.objects.create(student=student, application=application, status=VisaStatus.GRANTED)

        body = abc.client.get(PROGRESS_URL).json()
        profile = abc.client.get(f"/api/students/{student.public_id}/").json()["progress"]

        assert names(body) == ["Asha Offer"]
        assert body["count"] == 1
        # Their own page still says they are done.
        assert profile == {
            "percent": 100,
            "next_step": "Visa granted. Help with flights and pre-departure.",
            "is_done": True,
        }

    def test_furthest_along_come_first_and_lost_students_are_left_out(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, None, Stage.LEAD, name="Asha Lead")
        make_student(abc, None, Stage.OFFER, name="Binod Offer")
        make_student(abc, None, Stage.COUNSELLING, name="Chandra Signed")
        make_student(abc, None, Stage.COUNSELLING, name="Anil Signed")
        make_student(abc, None, Stage.LOST, name="Dipa Lost", lost_reason="Went elsewhere")

        body = abc.client.get(PROGRESS_URL).json()

        assert body["count"] == 4
        assert names(body) == ["Binod Offer", "Anil Signed", "Chandra Signed", "Asha Lead"]

    def test_a_counsellor_sees_only_their_own_students_with_me(
        self, abc: ConsultancyContext
    ) -> None:
        mine = named_staff(abc, "Bikash Thapa")
        other = named_staff(abc, "Rita Karki")
        make_student(abc, mine, Stage.COUNSELLING, name="Mine")
        make_student(abc, other, Stage.COUNSELLING, name="Theirs")
        make_student(abc, None, Stage.LEAD, name="Nobody's")
        log_in(abc, mine)

        assert names(abc.client.get(PROGRESS_URL, {"counsellor": "me"}).json()) == ["Mine"]
        assert len(abc.client.get(PROGRESS_URL, {"counsellor": "any"}).json()["results"]) == 3

    def test_an_admin_who_counsels_sees_their_own_students_with_me(
        self, abc: ConsultancyContext
    ) -> None:
        admin = named_staff(abc, "Maya Admin", Role.ADMIN)
        make_student(abc, admin, Stage.COUNSELLING, name="Maya's Student")
        make_student(abc, None, Stage.LEAD, name="Someone else")
        log_in(abc, admin)

        assert names(abc.client.get(PROGRESS_URL, {"counsellor": "me"}).json()) == [
            "Maya's Student"
        ]

    def test_the_limit_shortens_the_list_but_not_the_count(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for number in range(8):
            make_student(abc, None, Stage.LEAD, name=f"Student {number}", phone=f"98410000{number}")

        default = abc.client.get(PROGRESS_URL).json()
        two = abc.client.get(PROGRESS_URL, {"limit": 2}).json()

        assert (default["count"], len(default["results"])) == (8, 6)
        assert (two["count"], len(two["results"])) == (8, 2)

    def test_the_list_needs_few_queries_however_many_students(
        self, abc: ConsultancyContext, django_assert_max_num_queries: DjangoAssertNumQueries
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        course = course_in(abc)
        for number in range(12):
            student = make_student(
                abc, admin, Stage.APPLYING, name=f"Student {number}", phone=f"9841000{number:03}"
            )
            apply(abc, student, course, ApplicationStatus.APPLIED)
            hand_in(abc, student, "Passport", Verification.VERIFIED)

        with django_assert_max_num_queries(MOST_QUERIES):
            response = abc.client.get(PROGRESS_URL, {"limit": 50})

        assert len(response.json()["results"]) == 12

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR])
    def test_admins_and_counsellors_may_see_it(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        assert abc.client.get(PROGRESS_URL).status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.TEACHER])
    def test_front_desk_and_teachers_may_not(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = abc.client.get(PROGRESS_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == PROGRESS_ROLES_ONLY

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.client.get(PROGRESS_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_deactivated_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        counsellor = named_staff(abc, "Bikash Thapa")
        log_in(abc, counsellor)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=counsellor.pk).update(status="deactivated")

        assert abc.client.get(PROGRESS_URL).status_code in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        }

    @pytest.mark.parametrize(
        "query",
        [
            {"counsellor": "someone"},
            {"counsellor": "x" * 500},
            {"limit": 0},
            {"limit": 51},
            {"limit": "six"},
            {"page": 2},
            {"stage": "lead"},
            *({"counsellor": text} for text in INJECTION_STRINGS),
        ],
    )
    def test_bad_queries_are_refused(
        self, abc: ConsultancyContext, query: dict[str, str | int]
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(PROGRESS_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "<script>" not in response.content.decode()

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_reading_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(PROGRESS_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_names_typed_with_html_come_back_as_plain_text(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, None, Stage.LEAD, name=INJECTION_STRINGS[0])

        assert names(abc.client.get(PROGRESS_URL).json()) == [INJECTION_STRINGS[0]]


class TestPipeline:
    def test_counts_every_stage_including_lost(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for number, stage in enumerate(
            [Stage.LEAD, Stage.LEAD, Stage.COUNSELLING, Stage.OFFER, Stage.VISA, Stage.LOST]
        ):
            make_student(abc, None, stage, name=f"Student {number}", phone=f"98410000{number}")

        response = abc.client.get(PIPELINE_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "counts": {
                "lead": 2,
                "counselling": 1,
                "applying": 0,
                "offer": 1,
                "visa": 1,
                "lost": 1,
            }
        }

    def test_an_empty_office_counts_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert set(abc.client.get(PIPELINE_URL).json()["counts"].values()) == {0}

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_see_it(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = abc.client.get(PIPELINE_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ADMINS_ONLY

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.client.get(PIPELINE_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_unknown_queries_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(PIPELINE_URL, {"stage": "lead"}).status_code == 400

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_reading_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(PIPELINE_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
