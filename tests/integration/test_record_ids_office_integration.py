"""Employees and the office's other records are known only by UUID (ADR 0027, spec 111-115).

Ticket 18: Staff, classes, the catalogue, documents setup, money, work and configuration. Every
answer names them by UUID; addresses, bodies and filters take only UUIDs; a number, a malformed
id or another office's UUID is refused like an unknown record, never a 500.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.db.models import Model
from django.utils import timezone
from django_tenants.utils import schema_context, tenant_context
from rest_framework import status

from batches.models import Attendance, Batch, BatchMembership, Mark
from catalogue.models import Course, CourseLevel, Intake, University
from clock_in.models import Holiday, WorkFromHome, WorkFromHomeStatus, WorkSession
from commission.models import Commission
from course_files.models import CourseFile
from document_examples.models import DocumentExample
from documents.models import DocumentType
from expenses.models import Expense
from fee_types.models import FeeType
from modules.services import switch_modules
from modules.types import SWITCHABLE_MODULES
from office_networks.models import OfficeNetwork
from staff.models import Role, Staff
from students.models import Stage, Student
from tasks.models import Task
from tests.integration.test_course_files_integration import make_course_file, upload
from tests.integration.test_document_examples_integration import make_example
from tests.integration.test_expenses_integration import make_expense
from tests.integration.test_fees_integration import make_fee
from tests.integration.test_payments_integration import make_payment
from tests.integration.test_stage_and_counsellor_integration import log_in, named_staff
from tests.integration.test_student_profile_integration import make_student
from tests.record_ids import assert_no_record_numbers, is_uuid

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import ConsultancyContext, ConsultancyFactory

TODAY = timezone.localdate()
THIS_MONTH = TODAY.strftime("%Y-%m")


@dataclass(frozen=True)
class Office:
    """One of each record kind ticket 18 switches, in one office."""

    office: ConsultancyContext
    admin: Staff
    counsellor: Staff
    teacher: Staff
    student: Student
    university: University
    course: Course
    document_type: DocumentType
    course_file: CourseFile
    example: DocumentExample
    fee_type: FeeType
    expense: Expense
    commission: Commission
    task: Task
    batch: Batch
    holiday: Holiday
    network: OfficeNetwork
    dates: WorkFromHome
    session: WorkSession


def office_with_every_record(office: ConsultancyContext) -> Office:
    """One of each kind ticket 18 switches, made in `office`, with its Admin logged in."""
    with schema_context("public"):  # an earlier office's request may have left its schema on
        switch_modules(office.consultancy, set(SWITCHABLE_MODULES))
    admin = named_staff(office, "Nabin Adhikari", Role.ADMIN)
    counsellor = named_staff(office, "Sita Karki", Role.COUNSELLOR)
    teacher = named_staff(office, "Ramesh Karki", Role.TEACHER)
    log_in(office, admin)
    student = make_student(office, admin, Stage.COUNSELLING)
    with tenant_context(office.consultancy):
        Student.objects.filter(pk=student.pk).update(assigned_counsellor=counsellor)
        university = University.objects.create(name="Deakin University", country="australia")
        course = Course.objects.create(
            university=university, name="Master of IT", level=CourseLevel.MASTER
        )
        Intake.objects.create(course=course, month="2027-03")
        document_type = DocumentType.objects.get(name="Passport")
        fee_type = FeeType.objects.get(name="Visa fee")
        commission = Commission.objects.create(
            student=student,
            university=university,
            intake="2027-02",
            amount=290000,
            status="waiting_for_enrolment",
            recorded_by=admin,
            recorded_by_name=admin.name,
        )
        task = Task.objects.create(
            title="Collect bank statement",
            due_on=TODAY + timedelta(days=1),
            assignee=counsellor,
            created_by=admin,
            created_by_name=admin.name,
            student=student,
        )
        batch = Batch.objects.create(
            name="PTE Day",
            test_type="pte",
            time_slot="11:00 AM-12:30 PM",
            starts_on=TODAY - timedelta(days=10),
            ends_on=TODAY + timedelta(days=30),
            teacher=teacher,
        )
        BatchMembership.objects.create(batch=batch, student=student)
        Attendance.objects.create(
            batch=batch, student=student, day=TODAY, mark=Mark.PRESENT, marked_by=teacher
        )
        holiday = Holiday.objects.create(day=TODAY + timedelta(days=20), name="Dashain")
        network = OfficeNetwork.objects.create(label="Main office wifi", network="203.0.113.0/24")
        dates = WorkFromHome.objects.create(
            staff=counsellor,
            starts_on=TODAY + timedelta(days=2),
            ends_on=TODAY + timedelta(days=4),
            reason="Looking after my mother",
            status=WorkFromHomeStatus.WAITING,
            asked_by_staff=counsellor,
            asked_by_name=counsellor.name,
        )
        session = WorkSession.objects.create(
            staff=counsellor, clocked_in_at=timezone.now() - timedelta(hours=2)
        )
    course_file = make_course_file(office, course)
    example = make_example(office, document_type)
    expense = make_expense(office, admin, spent_on=TODAY)
    make_fee(office, student, admin)
    make_payment(office, student, admin)
    # Made through the API, so each leaves what it leaves (Notifications, History).
    given = office.client.post(
        "/api/tasks/",
        {
            "title": "Call back",
            "due_on": (TODAY + timedelta(days=1)).isoformat(),
            "assignee": str(counsellor.public_id),
        },
        format="json",
    )
    assert given.status_code == status.HTTP_201_CREATED, given.content
    sent = office.client.post(
        f"/api/inbox/{counsellor.public_id}/", {"text": "Hello"}, format="json"
    )
    assert sent.status_code == status.HTTP_201_CREATED, sent.content
    exported = office.client.post(
        "/api/exports/", {"list": "students", "filters": {}}, format="json"
    )
    assert exported.status_code == status.HTTP_200_OK, exported.content
    return Office(
        office=office,
        admin=admin,
        counsellor=counsellor,
        teacher=teacher,
        student=student,
        university=university,
        course=course,
        document_type=document_type,
        course_file=course_file,
        example=example,
        fee_type=fee_type,
        expense=expense,
        commission=commission,
        task=task,
        batch=batch,
        holiday=holiday,
        network=network,
        dates=dates,
        session=session,
    )


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office every test works in."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def xyz(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """Another office."""
    return make_consultancy("XYZ Overseas", "xyzoverseas")


@pytest.fixture
def full(abc: ConsultancyContext) -> Office:
    """One of each switched kind in office ABC, with its Admin logged in."""
    return office_with_every_record(abc)


# Every address that answers a list or one record, as an Admin sees it.
LISTS = [
    "/api/context/",
    "/api/staff/",
    "/api/staff/counsellors/",
    "/api/catalogue/universities/",
    "/api/catalogue/intakes/",
    "/api/configuration/document-types/",
    "/api/configuration/document-lists/",
    "/api/documents/types/",
    "/api/documents/bin/",
    "/api/fee-types/",
    "/api/accounts/fees-by-type/",
    "/api/accounts/expenses/",
    "/api/accounts/commission/",
    "/api/accounts/commission/received/",
    "/api/tasks/",
    "/api/tasks/assignees/",
    "/api/notifications/",
    "/api/batches/",
    "/api/batches/teachers/",
    "/api/batches/students/",
    "/api/exports/",
    "/api/office-networks/",
    "/api/clock/",
    f"/api/clock/month/?month={THIS_MONTH}",
    f"/api/clock/team/?month={THIS_MONTH}",
    "/api/clock/team/now/",
    "/api/clock/working-day/",
    "/api/clock/holidays/",
    "/api/clock/work-from-home/",
    "/api/transfer-requests/",
    "/api/inbox/",
    "/api/inbox/people/",
    "/api/students/",
    "/api/applications/",
    "/api/applications/filter-choices/",
    "/api/visas/filter-choices/",
    "/api/payments/",
    "/api/home/progress/",
    "/api/home/waiting/",
    f"/api/reports/staff-progress/?from={THIS_MONTH}-01&to={TODAY.isoformat()}",
    f"/api/reports/batches/?from={THIS_MONTH}-01&to={TODAY.isoformat()}",
    "/api/search/?q=PTE",
]


def record_addresses(full: Office) -> list[str]:
    """Every address that answers one of the office's records, by UUID."""
    s, b = full.counsellor.public_id, full.batch.public_id
    return [
        f"/api/staff/{s}/assigned-students/",
        f"/api/staff/{s}/progress/?from={THIS_MONTH}-01&to={TODAY.isoformat()}",
        f"/api/clock/staff/{s}/work-from-home/",
        f"/api/clock/month/?month={THIS_MONTH}&staff={s}",
        f"/api/inbox/{s}/",
        f"/api/catalogue/course-files/{full.course_file.public_id}/link/",
        f"/api/documents/examples/{full.example.public_id}/link/",
        f"/api/catalogue/universities/{full.university.public_id}/",
        f"/api/accounts/fees-by-type/{full.fee_type.public_id}/",
        f"/api/batches/{b}/",
        f"/api/batches/{b}/attendance/{TODAY.isoformat()}/",
        f"/api/students/{full.student.public_id}/",
        f"/api/students/{full.student.public_id}/tasks/",
        f"/api/students/{full.student.public_id}/batches/",
        f"/api/students/{full.student.public_id}/history/",
        f"/api/students/{full.student.public_id}/fees/",
        f"/api/payments/?fee_type={full.fee_type.public_id}",
        f"/api/students/?counsellor={s}",
    ]


class TestAnswers:
    """Every answer names Staff and the office's records by UUID only (spec 111)."""

    @pytest.mark.parametrize("url", LISTS)
    def test_each_list(self, full: Office, url: str) -> None:
        response = full.office.client.get(url)

        assert response.status_code == status.HTTP_200_OK, (url, response.content)
        assert_no_record_numbers(response.json())

    def test_each_record(self, full: Office) -> None:
        for url in record_addresses(full):
            response = full.office.client.get(url)

            assert response.status_code == status.HTTP_200_OK, (url, response.content)
            assert_no_record_numbers(response.json())

    def test_the_logged_in_person_is_named_by_uuid(self, full: Office) -> None:
        answer = full.office.client.get("/api/context/").json()

        assert answer["staff"]["id"] == str(full.admin.public_id)

    def test_the_lists_name_the_records_by_their_uuid(self, full: Office) -> None:
        client = full.office.client
        expected = {
            "/api/staff/": full.counsellor,
            "/api/catalogue/universities/": full.university,
            "/api/accounts/expenses/": full.expense,
            "/api/accounts/commission/": full.commission,
            "/api/tasks/?view=given": full.task,
            "/api/batches/": full.batch,
            "/api/office-networks/": full.network,
            "/api/clock/holidays/": full.holiday,
            "/api/fee-types/": full.fee_type,
        }
        for url, record in expected.items():
            public_id = record.serializable_value("public_id")
            assert str(public_id) in client.get(url).content.decode(), url

    def test_a_new_record_is_answered_by_uuid(self, full: Office) -> None:
        added = full.office.client.post(
            "/api/clock/holidays/", {"name": "New Year", "dates": ["2030-01-01"]}, format="json"
        )

        assert added.status_code == status.HTTP_201_CREATED, added.content
        assert_no_record_numbers(added.json())
        assert [is_uuid(holiday["id"]) for holiday in added.json()["holidays"]] == [True]

    def test_a_group_of_holidays_is_never_removed_by_number(self, full: Office) -> None:
        removed = full.office.client.delete(
            "/api/clock/holidays/", {"ids": [full.holiday.pk]}, format="json"
        )

        assert removed.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(full.office.consultancy):
            assert Holiday.objects.filter(pk=full.holiday.pk).exists()


def _changing_addresses(full: Office, key: Callable[[Model], object]) -> list[tuple[str, str]]:
    """(method, address) naming each record by `key` (its number, or an unknown UUID)."""
    s = key(full.counsellor)
    student = f"/api/students/{full.student.public_id}/"
    return [
        ("patch", f"/api/staff/{s}/"),
        ("post", f"/api/staff/{s}/deactivate/"),
        ("get", f"/api/staff/{s}/assigned-students/"),
        ("post", f"/api/staff/{s}/invite/"),
        ("post", f"/api/staff/{s}/invite-back/"),
        ("patch", f"/api/staff/{s}/two-factor/"),
        ("post", f"/api/staff/{s}/two-factor/reset/"),
        ("get", f"/api/staff/{s}/progress/?from={THIS_MONTH}-01&to={TODAY.isoformat()}"),
        ("get", f"/api/clock/staff/{s}/work-from-home/"),
        ("post", f"/api/clock/staff/{s}/missed-time/"),
        ("get", f"/api/inbox/{s}/"),
        ("get", f"/api/catalogue/universities/{key(full.university)}/"),
        ("post", f"/api/catalogue/universities/{key(full.university)}/courses/"),
        ("patch", f"/api/catalogue/courses/{key(full.course)}/"),
        ("put", f"/api/catalogue/courses/{key(full.course)}/documents/"),
        ("post", f"/api/catalogue/courses/{key(full.course)}/files/"),
        ("patch", f"/api/catalogue/course-files/{key(full.course_file)}/"),
        ("get", f"/api/catalogue/course-files/{key(full.course_file)}/link/"),
        ("get", f"/api/documents/bin/course-files/{key(full.course_file)}/link/"),
        ("patch", f"/api/configuration/document-types/{key(full.document_type)}/"),
        ("put", f"/api/configuration/document-types/{key(full.document_type)}/instruction/"),
        ("post", f"/api/configuration/document-types/{key(full.document_type)}/examples/"),
        ("delete", f"{student}documents/asked/{key(full.document_type)}/"),
        ("delete", f"/api/configuration/document-examples/{key(full.example)}/"),
        ("get", f"/api/documents/examples/{key(full.example)}/link/"),
        ("get", f"/api/documents/bin/document-examples/{key(full.example)}/link/"),
        ("patch", f"/api/fee-types/{key(full.fee_type)}/"),
        ("get", f"/api/accounts/fees-by-type/{key(full.fee_type)}/"),
        ("put", f"/api/accounts/expenses/{key(full.expense)}/"),
        ("put", f"/api/accounts/commission/{key(full.commission)}/status/"),
        ("patch", f"/api/tasks/{key(full.task)}/"),
        ("get", f"/api/batches/{key(full.batch)}/"),
        ("post", f"/api/batches/{key(full.batch)}/students/"),
        ("get", f"/api/batches/{key(full.batch)}/attendance/{TODAY.isoformat()}/"),
        ("delete", f"/api/office-networks/{key(full.network)}/"),
        ("delete", f"/api/clock/holidays/{key(full.holiday)}/"),
        ("post", f"/api/clock/sessions/{key(full.session)}/clock-out/"),
        ("post", f"/api/clock/work-from-home/{key(full.dates)}/approve/"),
        ("post", f"/api/clock/work-from-home/{key(full.dates)}/cancel/"),
    ]


class TestRefusedAddresses:
    """Only a record's own UUID, in this office, finds it."""

    def test_a_number_finds_nothing(self, full: Office) -> None:
        client = full.office.client
        for method, url in _changing_addresses(full, lambda record: record.pk):
            response = getattr(client, method)(url, {}, format="json")

            assert response.status_code == status.HTTP_404_NOT_FOUND, (method, url)

    @pytest.mark.parametrize("bad", ["not-a-uuid", "16abc", "%27%20OR%201%3D1", "-1"])
    def test_a_malformed_id_finds_nothing(self, full: Office, bad: str) -> None:
        for url in (f"/api/staff/{bad}/progress/", f"/api/batches/{bad}/", f"/api/inbox/{bad}/"):
            response = full.office.client.get(url)

            assert response.status_code == status.HTTP_404_NOT_FOUND, url

    def test_an_unknown_uuid_finds_nothing(self, full: Office) -> None:
        client = full.office.client
        for method, url in _changing_addresses(full, lambda _record: uuid.uuid4()):
            response = getattr(client, method)(url, {}, format="json")

            # Not found, or the empty body refused first (as for any record): never a 500.
            assert response.status_code in {404, 400}, (method, url)

    def test_another_offices_records_are_not_found(
        self, xyz: ConsultancyContext, full: Office
    ) -> None:
        theirs = office_with_every_record(xyz)
        log_in(full.office, full.admin)

        for url in record_addresses(theirs):
            if url.startswith(("/api/payments/?", "/api/students/?", "/api/clock/month/")):
                continue  # filters: refused below
            response = full.office.client.get(url)
            assert response.status_code == status.HTTP_404_NOT_FOUND, url


class TestRefusedBodies:
    """A record named in a body or filter by number, or from another office, is refused."""

    def test_a_task_for_a_number_is_refused(self, full: Office) -> None:
        response = full.office.client.post(
            "/api/tasks/",
            {"title": "Call", "due_on": "2030-01-01", "assignee": full.counsellor.pk},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize(
        "query",
        ["counsellor=7", "counsellor=not-a-uuid"],
    )
    def test_a_student_filter_by_number_is_refused(self, full: Office, query: str) -> None:
        response = full.office.client.get(f"/api/students/?{query}")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_payments_filter_by_fee_type_number_is_refused(self, full: Office) -> None:
        response = full.office.client.get(f"/api/payments/?fee_type={full.fee_type.pk}")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_batch_teacher_by_number_is_refused(self, full: Office) -> None:
        response = full.office.client.put(
            f"/api/batches/{full.batch.public_id}/",
            {
                "name": "PTE Day",
                "test_type": "pte",
                "time_slot": "11:00 AM-12:30 PM",
                "starts_on": TODAY.isoformat(),
                "ends_on": (TODAY + timedelta(days=30)).isoformat(),
                "teacher": full.teacher.pk,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "teacher" in response.json()

    def test_another_offices_staff_is_refused_as_assignee(
        self, xyz: ConsultancyContext, full: Office
    ) -> None:
        theirs = xyz.add_staff(Role.COUNSELLOR)
        log_in(full.office, full.admin)

        response = full.office.client.post(
            "/api/tasks/",
            {"title": "Call", "due_on": "2030-01-01", "assignee": str(theirs.public_id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestStorage:
    """New Course Files and examples are stored under the UUID; older paths still open."""

    def test_a_new_course_file_is_stored_under_the_courses_uuid(self, full: Office) -> None:
        response = upload(full.office.client, full.course)

        assert response.status_code == status.HTTP_201_CREATED, response.content
        with tenant_context(full.office.consultancy):
            stored = CourseFile.objects.get(public_id=response.json()["id"])
        assert f"/course-files/{full.course.public_id}/" in stored.storage_key

    def test_an_older_course_file_under_the_number_still_opens(self, full: Office) -> None:
        assert f"/course-files/{full.course.pk}/" in full.course_file.storage_key

        response = full.office.client.get(
            f"/api/catalogue/course-files/{full.course_file.public_id}/link/"
        )

        assert response.status_code == status.HTTP_200_OK
