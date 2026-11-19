"""A Platform Admin's changes during Support Access are recorded under their own name (issue 27).

Every write service that records who acted works for a Platform Admin inside an office, never
links them as Staff, and names them, e.g. "Platform Ops (Platform Admin)". History names them
"Platform Ops" marked as a Platform Admin (spec 11).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django_tenants.utils import schema_context, tenant_context

from applications.models import Application, Visa
from applications.services import (
    add_application,
    change_application_status,
    choose_offer,
    record_offer,
    start_visa,
)
from applications.statuses import ApplicationStatus, OfferKind, VisaStatus
from applications.types import NewApplication
from batches.attendance import take_attendance
from batches.models import Attendance, Batch, BatchMembership, Mark, TestType
from catalogue.models import Course, CourseLevel, Intake, University
from commission.services import add_commission
from commission.types import NewCommission
from communication.models import Message
from documents.files import PDF
from documents.models import Document, DocumentFile, DocumentType
from documents.services import add_document_type, check_document, upload_document
from documents.types import NewDocument, NewDocumentType, NewFile
from documents.verification import Verification
from documents.visa_letters import NewVisaLetter, add_visa_letter
from emails.types import OfficeDetails
from expenses.models import ExpenseCategory, ExpenseMethod
from expenses.services import add_expense
from expenses.types import NewExpense
from fees.models import Fee
from fees.services import add_fee, change_fee, delete_fee
from fees.types import NewFee
from history.models import HistoryEntry, HistoryKind
from modules.services import switch_modules
from modules.types import SWITCHABLE_MODULES
from notifications.models import Notification
from payments.models import Payment, PaymentMethod
from payments.services import change_payment, delete_payment, record_payment
from payments.types import NewPayment
from platform_admin.models import PlatformAdmin, SupportVisit
from platform_admin.support_access import visitor_for
from staff.employees import change_role, deactivate_employee, invite_employee
from staff.models import PlatformVisitor, PlatformVisitorIsNotStaffError, Role, Staff, StaffStatus
from staff.types import NewStaff, Office
from students.journey import set_stage_by_hand, transfer_student
from students.models import Source, Stage, Student
from tasks.models import Task
from tenancy.services import set_office_details
from tests.conftest import ConsultancyContext, ConsultancyFactory, PlatformContext
from tests.support_access import Visit, open_as_platform_admin
from visits.models import Visit as OfficeVisit
from visits.models import VisitReason
from visits.services import log_follow_up

NAME = "Platform Ops"
MARKED_NAME = "Platform Ops (Platform Admin)"
PDF_BYTES = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office the Platform Admin is inside, with every Module on."""
    office = make_consultancy("ABC Education", "abcedu")
    with schema_context("public"):
        switch_modules(office.consultancy, SWITCHABLE_MODULES)
    return office


@pytest.fixture
def visit(platform_site: PlatformContext, abc: ConsultancyContext) -> Visit:
    """The Platform Admin's open visit to ABC."""
    return open_as_platform_admin(platform_site, abc)


@pytest.fixture
def visitor(visit: Visit) -> PlatformVisitor:
    """The Platform Admin as the office's services see them."""
    with schema_context("public"):
        stored = SupportVisit.objects.select_related("platform_admin").get(pk=visit.visit_id)
        return visitor_for(stored)


@pytest.fixture
def inside(abc: ConsultancyContext) -> Iterator[None]:
    """Run the test body inside ABC's schema."""
    with tenant_context(abc.consultancy):
        yield


def make_student(office: ConsultancyContext, counsellor: Staff | None = None) -> Student:
    """Store a Student directly."""
    with tenant_context(office.consultancy):
        return Student.objects.create(
            name="Sita Gurung",
            phone="9841000001",
            email="sita@example.test",
            country="australia",
            source=Source.WALK_IN,
            highest_qualification="plus_two",
            assigned_counsellor=counsellor,
            stage=Stage.COUNSELLING,
        )


def make_course(office: ConsultancyContext) -> Course:
    """Store a Course with its University and one Intake."""
    with tenant_context(office.consultancy):
        university = University.objects.create(name="Deakin University", country="australia")
        course = Course.objects.create(
            university=university, name="Master of IT", level=CourseLevel.MASTER
        )
        Intake.objects.create(course=course, month="2027-03")
        return course


def history(student: Student) -> list[HistoryEntry]:
    """The Student's History, oldest first (call inside the schema)."""
    return list(HistoryEntry.objects.filter(student=student).order_by("at", "pk"))


def assert_by_platform_admin(entries: list[HistoryEntry]) -> None:
    """Every entry names the Platform Admin, unlinked from Staff."""
    assert entries
    for entry in entries:
        assert entry.by_staff_id is None
        assert entry.by_name == NAME
        assert entry.by_platform_admin is True


class TestPlatformVisitorIsNeverStaff:
    def test_cant_be_saved_or_deleted(self, visitor: PlatformVisitor, inside: None) -> None:
        with pytest.raises(PlatformVisitorIsNotStaffError):
            visitor.save()
        with pytest.raises(PlatformVisitorIsNotStaffError):
            visitor.delete()
        assert Staff.objects.count() == 0

    def test_a_visit_creates_no_staff_row(
        self, abc: ConsultancyContext, visit: Visit, platform_site: PlatformContext
    ) -> None:
        response = visit.client.post(
            "/api/students/",
            {
                "name": "Ram Shrestha",
                "phone": "9841000002",
                "country": "australia",
                "source": "walk_in",
                "highest_qualification": "plus_two",
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        with tenant_context(abc.consultancy):
            assert Staff.objects.count() == 0
        with schema_context("public"):
            assert PlatformAdmin.objects.count() == 1


class TestStudentsAndVisits:
    def test_a_new_visitor_and_their_visit(self, abc: ConsultancyContext, visit: Visit) -> None:
        response = visit.client.post(
            "/api/students/",
            {
                "name": "Ram Shrestha",
                "phone": "9841000002",
                "country": "australia",
                "source": "walk_in",
                "highest_qualification": "plus_two",
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        with tenant_context(abc.consultancy):
            student = Student.objects.get()
            assert student.created_by_id is None
            office_visit = OfficeVisit.objects.get(student=student)
        assert office_visit.recorded_by_id is None
        assert office_visit.recorded_by_name == MARKED_NAME

    def test_a_follow_up_visit(
        self, abc: ConsultancyContext, visitor: PlatformVisitor, inside: None
    ) -> None:
        student = make_student(abc)
        logged = log_follow_up(student, VisitReason.COUNSELLING_SESSION, "", by=visitor)
        assert logged.recorded_by_id is None
        assert logged.recorded_by_name == MARKED_NAME

    def test_stage_by_hand_and_transfer(
        self, abc: ConsultancyContext, visitor: PlatformVisitor
    ) -> None:
        first = abc.add_staff(Role.COUNSELLOR, email="bikash@abc.test")
        second = abc.add_staff(Role.COUNSELLOR, email="mina@abc.test")
        student = make_student(abc, counsellor=first)
        with tenant_context(abc.consultancy):
            set_stage_by_hand(student, Stage.LEAD, by=visitor)
            transfer_student(student, second, by=visitor)
            entries = history(student)
        assert [entry.kind for entry in entries] == [
            HistoryKind.STAGE_CHANGED,
            HistoryKind.TRANSFERRED,
        ]
        assert_by_platform_admin(entries)


class TestMoney:
    def test_a_payment_recorded_changed_and_deleted(
        self, abc: ConsultancyContext, visitor: PlatformVisitor, inside: None
    ) -> None:
        student = make_student(abc)
        details = NewPayment(
            amount=5000,
            paid_on=timezone.localdate(),
            method=PaymentMethod.CASH,
            purpose="PTE class fee",
        )
        payment = record_payment(student, details, by=visitor)
        assert payment.recorded_by_id is None
        assert payment.recorded_by_name == MARKED_NAME
        change_payment(payment.pk, NewPayment(**{**details.__dict__, "amount": 6000}), by=visitor)
        delete_payment(payment.pk, by=visitor)
        entries = history(student)
        assert [entry.kind for entry in entries] == [
            HistoryKind.PAYMENT_RECORDED,
            HistoryKind.PAYMENT_EDITED,
            HistoryKind.PAYMENT_DELETED,
        ]
        assert_by_platform_admin(entries)
        assert not Payment.objects.exists()

    def test_a_fee_added_changed_and_deleted(
        self, abc: ConsultancyContext, visitor: PlatformVisitor, inside: None
    ) -> None:
        student = make_student(abc)
        fee = add_fee(student, NewFee(purpose="Visa processing", amount=40000), by=visitor)
        assert fee.added_by_id is None
        assert fee.added_by_name == MARKED_NAME
        change_fee(fee.pk, NewFee(purpose="Visa processing", amount=45000), by=visitor)
        delete_fee(fee.pk, by=visitor)
        entries = history(student)
        assert [entry.kind for entry in entries] == [
            HistoryKind.FEE_ADDED,
            HistoryKind.FEE_EDITED,
            HistoryKind.FEE_DELETED,
        ]
        assert_by_platform_admin(entries)
        assert not Fee.objects.exists()

    def test_an_expense(self, visitor: PlatformVisitor, inside: None) -> None:
        expense = add_expense(
            NewExpense(
                spent_on=timezone.localdate(),
                category=ExpenseCategory.OFFICE_RENT,
                paid_to="Landlord",
                amount=30000,
                method=ExpenseMethod.CASH,
            ),
            by=visitor,
        )
        assert expense.recorded_by_id is None
        assert expense.recorded_by_name == MARKED_NAME

    def test_commission(
        self, abc: ConsultancyContext, visitor: PlatformVisitor, inside: None
    ) -> None:
        student = make_student(abc)
        university = make_course(abc).university
        commission = add_commission(
            NewCommission(student=student, university=university, intake="2027-03", amount=1),
            by=visitor,
        )
        assert commission.recorded_by_id is None
        assert commission.recorded_by_name == MARKED_NAME

    def test_the_payment_answer_names_the_platform_admin(
        self, abc: ConsultancyContext, visit: Visit
    ) -> None:
        student = make_student(abc)
        response = visit.client.post(
            f"/api/students/{student.pk}/payments/",
            {
                "amount": 5000,
                "paid_on": timezone.localdate().isoformat(),
                "method": "cash",
                "purpose": "PTE class fee",
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        assert response.json()["recorded_by"] == {"name": MARKED_NAME}


class TestTasks:
    def test_a_task_given_by_the_platform_admin(
        self, abc: ConsultancyContext, visit: Visit
    ) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)
        response = visit.client.post(
            "/api/tasks/",
            {
                "title": "Call Sita",
                "due_on": (timezone.localdate() + timedelta(days=1)).isoformat(),
                "assignee": counsellor.pk,
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        assert response.json()["created_by"] == {"id": None, "name": MARKED_NAME}
        with tenant_context(abc.consultancy):
            task = Task.objects.get()
            assert task.created_by_id is None
            assert task.created_by_name == MARKED_NAME
            assert Notification.objects.filter(recipient=counsellor).count() == 1
        abc.client.credentials(HTTP_AUTHORIZATION=f"Bearer {abc.tokens_for(counsellor).access}")
        bell = abc.client.get("/api/notifications/").json()
        assert bell["results"][0]["by"] == {"id": None, "name": MARKED_NAME}
        listed = abc.client.get("/api/tasks/").json()
        assert listed["results"][0]["created_by"] == {"id": None, "name": MARKED_NAME}

    def test_the_platform_admin_can_tick_any_task(
        self, abc: ConsultancyContext, visit: Visit
    ) -> None:
        counsellor = abc.log_in_as(Role.COUNSELLOR)
        created = abc.client.post(
            "/api/tasks/",
            {
                "title": "Own task",
                "due_on": timezone.localdate().isoformat(),
                "assignee": counsellor.pk,
            },
            format="json",
        )
        assert created.status_code == 201, created.content
        task_id = created.json()["id"]
        response = visit.client.patch(f"/api/tasks/{task_id}/", {"done": True}, format="json")
        assert response.status_code == 200, response.content
        assert response.json()["is_done"] is True


class TestApplicationsAndVisas:
    def test_added_and_moved_on_with_history(
        self, abc: ConsultancyContext, visitor: PlatformVisitor
    ) -> None:
        student = make_student(abc)
        course = make_course(abc)
        with tenant_context(abc.consultancy):
            application = add_application(
                student, NewApplication(course=course, intake="2027-03"), by=visitor
            )
            change_application_status(
                student, application.pk, ApplicationStatus.REJECTED, by=visitor
            )
            change_application_status(
                student, application.pk, ApplicationStatus.APPLIED, by=visitor
            )
            record_offer(student, application.pk, OfferKind.CONDITIONAL, by=visitor)
            choose_offer(student, application.pk, by=visitor)
            start_visa(student, application.pk, by=visitor)
            assert Application.objects.get().created_by_id is None
            assert Visa.objects.get().created_by_id is None
            entries = history(student)
        assert {entry.kind for entry in entries} == {
            HistoryKind.STAGE_CHANGED,
            HistoryKind.APPLICATION_STATUS_CHANGED,
            HistoryKind.OFFER_LETTER_ADDED,
            HistoryKind.CHOSEN_OFFER_CHANGED,
        }
        assert_by_platform_admin(entries)

    def test_a_visa_letter_with_its_result(
        self, abc: ConsultancyContext, visitor: PlatformVisitor
    ) -> None:
        student = make_student(abc)
        course = make_course(abc)
        with tenant_context(abc.consultancy):
            application = add_application(
                student, NewApplication(course=course, intake="2027-03"), by=visitor
            )
            record_offer(student, application.pk, OfferKind.CONDITIONAL, by=visitor)
            choose_offer(student, application.pk, by=visitor)
            visa = start_visa(student, application.pk, by=visitor)
            letter = NewVisaLetter(
                result=VisaStatus.GRANTED,
                file=NewFile(io.BytesIO(PDF_BYTES), PDF, "grant.pdf", len(PDF_BYTES)),
            )
            document = add_visa_letter(student, visa.pk, letter, by=visitor)
            assert Visa.objects.get().status == VisaStatus.GRANTED
            assert (document.uploaded_by_id, document.uploaded_by_name) == (None, MARKED_NAME)


class TestDocuments:
    def test_upload_answers_with_the_platform_admins_name(
        self, abc: ConsultancyContext, visit: Visit
    ) -> None:
        student = make_student(abc)
        with tenant_context(abc.consultancy):
            passport = DocumentType.objects.get(name="Passport")
        response = visit.client.post(
            f"/api/students/{student.pk}/documents/",
            {
                "document_type": str(passport.pk),
                "file": SimpleUploadedFile("passport.pdf", PDF_BYTES, "application/pdf"),
            },
            format="multipart",
        )
        assert response.status_code == 201, response.content
        with tenant_context(abc.consultancy):
            document = Document.objects.get()
        assert document.uploaded_by_id is None
        assert document.uploaded_by_name == MARKED_NAME
        checklist = visit.client.get(f"/api/students/{student.pk}/documents/").json()
        held = [item["document"] for item in checklist["items"] if item["document"]]
        assert held[0]["uploaded_by"] == {"id": None, "name": MARKED_NAME}
        assert held[0]["files"][0]["uploaded_by"] == {"id": None, "name": MARKED_NAME}

    def test_a_file_added_to_a_document(self, abc: ConsultancyContext, visit: Visit) -> None:
        student = make_student(abc)
        with tenant_context(abc.consultancy):
            passport = DocumentType.objects.get(name="Passport")
        url = f"/api/students/{student.pk}/documents/"
        visit.client.post(
            url,
            {
                "document_type": str(passport.pk),
                "file": SimpleUploadedFile("front.pdf", PDF_BYTES, "application/pdf"),
            },
            format="multipart",
        )
        with tenant_context(abc.consultancy):
            document = Document.objects.get()

        response = visit.client.post(
            f"{url}{document.pk}/files/",
            {"file": SimpleUploadedFile("back.pdf", PDF_BYTES, "application/pdf")},
            format="multipart",
        )

        assert response.status_code == 201, response.content
        with tenant_context(abc.consultancy):
            added = DocumentFile.objects.order_by("pk").last()
        assert added is not None
        assert (added.uploaded_by_id, added.uploaded_by_name) == (None, MARKED_NAME)

    def test_a_document_type_and_a_verification(
        self, abc: ConsultancyContext, visitor: PlatformVisitor, inside: None
    ) -> None:
        student = make_student(abc)
        police = add_document_type(
            NewDocumentType(name="Police report", needed_by_everyone=True), by=visitor
        )
        document = upload_document(
            student,
            NewDocument(
                document_type=police,
                files=(NewFile(io.BytesIO(PDF_BYTES), PDF, "police.pdf", len(PDF_BYTES)),),
            ),
            by=visitor,
        )
        checked = check_document(student, document.pk, Verification.VERIFIED, by=visitor)
        assert checked.verification == Verification.VERIFIED


class TestCommunication:
    def test_an_email(self, abc: ConsultancyContext, visit: Visit) -> None:
        set_office_details(abc.consultancy, OfficeDetails(contact_email="info@abcedu.test"))
        student = make_student(abc)
        sent = visit.client.post(
            f"/api/students/{student.pk}/messages/",
            {"subject": "Your class", "body": "Your class starts tomorrow."},
            format="json",
        )
        assert sent.status_code == 201, sent.content
        assert sent.json()["sent_by"] == {"id": None, "name": MARKED_NAME}
        with tenant_context(abc.consultancy):
            message = Message.objects.get()
        assert message.sent_by_id is None
        assert message.sent_by_name == MARKED_NAME


class TestAttendance:
    def test_taken_by_the_platform_admin(
        self, abc: ConsultancyContext, visitor: PlatformVisitor
    ) -> None:
        teacher = abc.add_staff(Role.TEACHER)
        student = make_student(abc)
        today = timezone.localdate()
        with tenant_context(abc.consultancy):
            batch = Batch.objects.create(
                name="IELTS Morning",
                test_type=TestType.IELTS,
                teacher=teacher,
                time_slot="07:00-08:00",
                starts_on=today - timedelta(days=7),
                ends_on=today + timedelta(days=7),
            )
            BatchMembership.objects.create(batch=batch, student=student)
            take_attendance(batch, today, {student.pk: Mark.PRESENT}, by=visitor)
            assert Attendance.objects.get().marked_by_id is None


class TestEmployees:
    def test_invite_change_role_and_deactivate(
        self, abc: ConsultancyContext, visitor: PlatformVisitor
    ) -> None:
        admin = abc.add_staff(Role.ADMIN)
        office = Office(name="ABC Education", join_link="https://abcedu.example/join")
        with tenant_context(abc.consultancy):
            invited = invite_employee(
                NewStaff(name="Mina Rai", email="mina@abc.test", role=Role.COUNSELLOR),
                abc.consultancy,
                office,
                by=visitor,
            )
            assert invited.status == StaffStatus.INVITED
            change_role(admin, Role.COUNSELLOR, abc.consultancy, by=visitor)
            deactivate_employee(admin, by=visitor)
            admin.refresh_from_db()
        assert admin.role == Role.COUNSELLOR
        assert admin.status == StaffStatus.DEACTIVATED
