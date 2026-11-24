"""The Counsellor's and Admin's Homes: what's waiting on me (issues 52, 75; spec 22a1)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application, Visa
from applications.statuses import ApplicationStatus, VisaStatus
from documents.verification import Verification
from staff.models import Role, Staff
from students.models import Stage, Student
from tasks.models import Task
from tests.conftest import ConsultancyContext, ConsultancyFactory, PlatformContext
from tests.integration.test_home_integration import course_in, hand_in
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)
from tests.support_access import open_as_platform_admin
from transfer_requests.models import TransferRequest

if TYPE_CHECKING:
    from pytest_django import DjangoAssertNumQueries

WAITING_URL = "/api/home/waiting/"
PROGRESS_ROLES_ONLY = {"detail": "Only counsellors and Admins can see this."}
# The domain and the Staff member, then the three counts and the appointments: each after
# django-tenants' search_path, however much is waiting.
# Home counts Documents waiting from the Students' checklists, as the Documents page does (list
# tidy-ups 22): building them adds a fixed 8, never one per Student.
MOST_QUERIES = 20
NOTHING_WAITING = {
    "documents_waiting": 0,
    "transfer_requests_waiting": 0,
    "documents_in_bin": 0,
    "work_from_home_requests": {"count": 0, "first_staff_id": None},
    "visa_appointments": {"count": 0, "results": []},
}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def at_kathmandu(day: date, hour: int = 10) -> datetime:
    """A time on a day in the office's time zone."""
    return timezone.make_aware(datetime.combine(day, time(hour)))


def visa_for(
    office: ConsultancyContext,
    student: Student,
    interview_at: datetime | None,
    visa_status: VisaStatus = VisaStatus.APPLIED,
    **dates: datetime,
) -> Visa:
    """A Visa for the Student, with an interview at `interview_at` and any other `dates`."""
    with tenant_context(office.consultancy):
        application = Application.objects.create(
            student=student,
            course=course_in(office, f"University {student.pk}"),
            intake="2027-03",
            status=ApplicationStatus.OFFER,
        )
        return Visa.objects.create(
            student=student,
            application=application,
            status=visa_status,
            interview_at=interview_at,
            **dates,
        )


def ask(office: ConsultancyContext, student: Student, asker: Staff) -> TransferRequest:
    """A waiting Transfer Request from `asker` for the Student."""
    with tenant_context(office.consultancy):
        return TransferRequest.objects.create(student=student, asked_by=asker, reason="Family.")


def today() -> date:
    """Today in the office's time zone."""
    return timezone.localdate()


class TestDocumentsWaiting:
    def test_a_counsellor_counts_documents_waiting_on_their_own_students(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        other = named_staff(abc, "Anita Rai")
        mine = make_student(abc, me, Stage.COUNSELLING, phone="9841000001")
        theirs = make_student(abc, other, Stage.COUNSELLING, name="Hari", phone="9841000002")
        hand_in(abc, mine, "Passport", Verification.PENDING)
        hand_in(abc, mine, "Citizenship", Verification.PENDING)
        hand_in(abc, mine, "SEE Marksheet", Verification.VERIFIED)
        hand_in(abc, mine, "Bank Statement", Verification.REJECTED)
        hand_in(abc, theirs, "Passport", Verification.PENDING)
        log_in(abc, me)

        response = abc.client.get(WAITING_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["documents_waiting"] == 2

    def test_an_admin_counts_everyones(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        counsellor = named_staff(abc, "Bikash Thapa")
        for number, owner in enumerate((admin, counsellor, None), start=1):
            student = make_student(
                abc, owner, Stage.COUNSELLING, name=f"S {number}", phone=f"984100000{number}"
            )
            hand_in(abc, student, "Passport", Verification.PENDING)

        assert abc.client.get(WAITING_URL).json()["documents_waiting"] == 3

    def test_leads_and_lost_students_are_left_out_like_the_documents_page(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        for number, stage in enumerate((Stage.LEAD, Stage.LOST), start=1):
            student = make_student(abc, None, stage, name=f"S {number}", phone=f"984100000{number}")
            hand_in(abc, student, "Passport", Verification.PENDING)

        assert abc.client.get(WAITING_URL).json() == NOTHING_WAITING


class TestTransferRequestsWaiting:
    def test_a_counsellor_counts_requests_for_their_students_only(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        asker = named_staff(abc, "Anita Rai")
        mine = make_student(abc, me, Stage.COUNSELLING, phone="9841000001")
        nobodys = make_student(abc, None, Stage.LEAD, name="Hari", phone="9841000002")
        ask(abc, mine, asker)
        ask(abc, nobodys, asker)
        log_in(abc, me)

        assert abc.client.get(WAITING_URL).json()["transfer_requests_waiting"] == 1

    def test_an_admin_counts_every_waiting_request_but_their_own(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        counsellor = named_staff(abc, "Bikash Thapa")
        first = make_student(abc, counsellor, Stage.COUNSELLING, phone="9841000001")
        second = make_student(abc, None, Stage.LEAD, name="Hari", phone="9841000002")
        third = make_student(abc, counsellor, Stage.COUNSELLING, name="Gita", phone="9841000003")
        ask(abc, first, named_staff(abc, "Anita Rai"))
        ask(abc, second, counsellor)
        ask(abc, third, admin)
        with tenant_context(abc.consultancy):
            TransferRequest.objects.filter(student=second).update(status="declined")

        assert abc.client.get(WAITING_URL).json()["transfer_requests_waiting"] == 1


class TestVisaAppointments:
    def test_a_counsellor_sees_their_students_interviews_in_the_next_7_days(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        other = named_staff(abc, "Anita Rai")
        soon = make_student(abc, me, Stage.VISA, name="Sita Gurung", phone="9841000001")
        later = make_student(abc, me, Stage.VISA, name="Ram Karki", phone="9841000002")
        theirs = make_student(abc, other, Stage.VISA, name="Hari", phone="9841000003")
        no_date = make_student(abc, me, Stage.VISA, name="Gita", phone="9841000004")
        last_day = make_student(abc, me, Stage.VISA, name="Maya Tamang", phone="9841000005")
        visa = visa_for(abc, soon, at_kathmandu(today() + timedelta(days=2), 14))
        edge = visa_for(abc, last_day, at_kathmandu(today() + timedelta(days=6), 9))
        visa_for(abc, later, at_kathmandu(today() + timedelta(days=7)))
        visa_for(abc, theirs, at_kathmandu(today() + timedelta(days=1)))
        visa_for(abc, no_date, None)
        log_in(abc, me)

        interviews = abc.client.get(WAITING_URL).json()["visa_appointments"]

        day = today() + timedelta(days=2)
        assert interviews == {
            "count": 2,
            "results": [
                {
                    "visa_id": str(visa.public_id),
                    "student": {"id": str(soon.public_id), "name": "Sita Gurung"},
                    "kind": "interview",
                    "at": f"{day.isoformat()}T14:00",
                },
                {
                    "visa_id": str(edge.public_id),
                    "student": {"id": str(last_day.public_id), "name": "Maya Tamang"},
                    "kind": "interview",
                    "at": f"{(today() + timedelta(days=6)).isoformat()}T09:00",
                },
            ],
        }

    def test_biometrics_and_medical_are_items_too_soonest_first(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, me, Stage.VISA, name="Sita Gurung", phone="9841000001")
        visa = visa_for(
            abc,
            student,
            at_kathmandu(today() + timedelta(days=20)),
            biometrics_at=at_kathmandu(today() + timedelta(days=3), 11),
            medical_at=at_kathmandu(today() + timedelta(days=1), 9),
        )
        log_in(abc, me)

        appointments = abc.client.get(WAITING_URL).json()["visa_appointments"]

        assert appointments["count"] == 2
        assert [(row["visa_id"], row["kind"], row["at"]) for row in appointments["results"]] == [
            (str(visa.public_id), "medical", f"{(today() + timedelta(days=1)).isoformat()}T09:00"),
            (
                str(visa.public_id),
                "biometrics",
                f"{(today() + timedelta(days=3)).isoformat()}T11:00",
            ),
        ]

    @pytest.mark.parametrize("decided", [VisaStatus.GRANTED, VisaStatus.REFUSED])
    def test_a_decided_visa_has_no_appointments_waiting(
        self, abc: ConsultancyContext, decided: VisaStatus
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, me, Stage.VISA, phone="9841000001")
        visa_for(abc, student, at_kathmandu(today() + timedelta(days=1)), decided)
        log_in(abc, me)

        assert abc.client.get(WAITING_URL).json()["visa_appointments"]["count"] == 0

    def test_an_interview_earlier_today_still_counts_but_yesterday_does_not(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        first = make_student(abc, me, Stage.VISA, phone="9841000001")
        second = make_student(abc, me, Stage.VISA, name="Ram", phone="9841000002")
        visa_for(abc, first, at_kathmandu(today(), 0))
        visa_for(abc, second, at_kathmandu(today() - timedelta(days=1), 23))
        log_in(abc, me)

        assert abc.client.get(WAITING_URL).json()["visa_appointments"]["count"] == 1

    def test_only_the_first_five_are_listed_but_all_are_counted(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        for number in range(7):
            student = make_student(
                abc, me, Stage.VISA, name=f"S {number}", phone=f"984100000{number}"
            )
            visa_for(abc, student, at_kathmandu(today() + timedelta(days=1), 8 + number))
        log_in(abc, me)

        interviews = abc.client.get(WAITING_URL).json()["visa_appointments"]

        assert interviews["count"] == 7
        assert [row["student"]["name"] for row in interviews["results"]] == [
            f"S {number}" for number in range(5)
        ]

    def test_an_admin_gets_no_appointments(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc, admin, Stage.VISA, phone="9841000001")
        visa_for(abc, student, at_kathmandu(today() + timedelta(days=1)))

        assert abc.client.get(WAITING_URL).json()["visa_appointments"] == {
            "count": 0,
            "results": [],
        }

    def test_names_typed_with_html_come_back_as_plain_text(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, me, Stage.VISA, name=INJECTION_STRINGS[0])
        visa_for(abc, student, at_kathmandu(today() + timedelta(days=1)))
        log_in(abc, me)

        results = abc.client.get(WAITING_URL).json()["visa_appointments"]["results"]

        assert results[0]["student"]["name"] == INJECTION_STRINGS[0]


class TestWaitingAccess:
    def test_nothing_waiting_answers_zeros(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        assert abc.client.get(WAITING_URL).json() == NOTHING_WAITING

    def test_only_me_tasks_and_other_records_never_count(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        with tenant_context(abc.consultancy):
            Task.objects.create(
                title="Private reminder",
                due_on=today(),
                assignee=me,
                created_by=me,
                only_me=True,
            )
        log_in(abc, me)

        assert abc.client.get(WAITING_URL).json() == NOTHING_WAITING

    def test_counts_take_a_fixed_number_of_queries(
        self, abc: ConsultancyContext, django_assert_max_num_queries: DjangoAssertNumQueries
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        asker = named_staff(abc, "Anita Rai")
        for number in range(8):
            student = make_student(
                abc, me, Stage.VISA, name=f"S {number}", phone=f"984100000{number}"
            )
            hand_in(abc, student, "Passport", Verification.PENDING)
            visa_for(abc, student, at_kathmandu(today() + timedelta(days=1)))
            ask(abc, student, asker)
        log_in(abc, me)

        with django_assert_max_num_queries(MOST_QUERIES):
            response = abc.client.get(WAITING_URL)

        assert response.json()["documents_waiting"] == 8

    def test_a_platform_admin_inside_sees_it_as_an_admin(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        counsellor = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, counsellor, Stage.COUNSELLING, phone="9841000001")
        hand_in(abc, student, "Passport", Verification.PENDING)
        ask(abc, student, named_staff(abc, "Anita Rai"))
        visit = open_as_platform_admin(platform_site, abc)

        response = visit.client.get(WAITING_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["documents_waiting"] == 1
        assert response.json()["transfer_requests_waiting"] == 1

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.TEACHER])
    def test_front_desk_and_teachers_may_not(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = abc.client.get(WAITING_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == PROGRESS_ROLES_ONLY

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.client.get(WAITING_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_deactivated_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        counsellor = named_staff(abc, "Bikash Thapa")
        log_in(abc, counsellor)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=counsellor.pk).update(status="deactivated")

        assert abc.client.get(WAITING_URL).status_code in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        }

    @pytest.mark.parametrize(
        "query",
        [
            {"counsellor": "me"},
            {"days": 30},
            {"scope": "everyone"},
            *({"q": text} for text in INJECTION_STRINGS),
        ],
    )
    def test_unknown_queries_are_refused(
        self, abc: ConsultancyContext, query: dict[str, str | int]
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(WAITING_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "<script>" not in response.content.decode()

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_reading_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(WAITING_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
