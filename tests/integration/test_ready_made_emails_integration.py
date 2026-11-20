"""Ready-made emails: the menu, the filled-in draft, and sending what Staff edited (ticket 09).

Spec (2026-11-18) stories 28-46: eight ready-made emails, filled in from the Student's own records
(a Payment, an Offer Letter, a Visa or a class is picked). Emails that need a record are
unavailable without one; Fee reminder needs Accounts and Class details needs Batches. Plain email
and Class details can go to many Students, each copy starting "Dear Student,".
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application, Visa
from applications.statuses import ApplicationStatus, OfferKind, VisaStatus
from batches.models import Batch, BatchMembership
from catalogue.models import Course, CourseLevel, University
from clock_in.models import WeeklyOff
from clock_in.services import set_weekly_off
from documents.models import Document, DocumentType
from documents.verification import Verification
from fee_types.models import FeeType
from fees.models import Fee
from modules.services import switch_modules
from modules.types import Module
from payments.models import Payment
from staff.models import Role, Staff
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs
from tests.integration.test_communication_integration import (
    html_of,
    outbox,
    send,
    set_contact_email,
)
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

BULK_MENU_URL = "/api/messages/bulk/ready-made/"
BULK_DRAFT_URL = "/api/messages/bulk/draft/"
KINDS = [
    "welcome",
    "documents_needed",
    "payment_received",
    "fee_reminder",
    "offer_received",
    "visa_result",
    "class_details",
    "plain",
]
CHOOSE_A_KIND = {"kind": ["Choose one of the ready-made emails."]}
SIGN_OFF = "Kind regards,\nAsha Admin\nABC Education"


def menu_url(student: Student | int) -> str:
    """The ready-made emails for one Student."""
    pk = student if isinstance(student, int) else student.pk
    return f"/api/students/{pk}/emails/ready-made/"


def draft_url(student: Student | int) -> str:
    """One ready-made email's draft for one Student."""
    pk = student if isinstance(student, int) else student.pk
    return f"/api/students/{pk}/emails/draft/"


def menu(office: ConsultancyContext, student: Student) -> dict[str, dict[str, Any]]:
    """The menu as {kind: entry}, checking it answered 200."""
    response = office.client.get(menu_url(student))
    assert response.status_code == status.HTTP_200_OK, response.json()
    return {entry["kind"]: entry for entry in response.json()["kinds"]}


def draft(
    office: ConsultancyContext, student: Student | int, kind: str, record: int | None = None
) -> _MonkeyPatchedResponse:
    """GET a draft of `kind` (about `record`) for one Student."""
    query: dict[str, str | int] = {"kind": kind}
    if record is not None:
        query["record"] = record
    return office.client.get(draft_url(student), query)


def drafted(
    office: ConsultancyContext, student: Student, kind: str, record: int | None = None
) -> dict[str, str]:
    """The draft's subject line and message, checking it answered 200."""
    response = draft(office, student, kind, record)
    assert response.status_code == status.HTTP_200_OK, response.json()
    answer: dict[str, str] = response.json()
    return answer


def bulk_draft(
    office: ConsultancyContext, kind: str, record: int | None = None
) -> _MonkeyPatchedResponse:
    """GET a draft of `kind` for an email to many Students."""
    query: dict[str, str | int] = {"kind": kind}
    if record is not None:
        query["record"] = record
    return office.client.get(BULK_DRAFT_URL, query)


def make_offer(office: ConsultancyContext, student: Student, **fields: str) -> Application:
    """Store an Application: at Offer with a conditional Offer Letter unless `fields` say not.

    `fields`: university, course, kind (an OfferKind) and status (an ApplicationStatus).
    """
    wanted = {
        "university": "Deakin University",
        "course": "Master of IT",
        "kind": OfferKind.CONDITIONAL,
        "status": ApplicationStatus.OFFER,
        **fields,
    }
    with tenant_context(office.consultancy):
        found, _ = University.objects.get_or_create(
            name=wanted["university"], defaults={"country": "australia"}
        )
        made = Course.objects.create(
            university=found, name=wanted["course"], level=CourseLevel.MASTER
        )
        is_offer = wanted["status"] == ApplicationStatus.OFFER
        return Application.objects.create(
            student=student,
            course=made,
            intake="2027-03",
            status=wanted["status"],
            offer_kind=wanted["kind"] if is_offer else "",
        )


def make_visa(office: ConsultancyContext, student: Student, visa_status: str) -> Visa:
    """Store a Visa at `visa_status`, on a new Application at Offer to an Australian University."""
    application = make_offer(office, student, course=f"Course {visa_status}")
    with tenant_context(office.consultancy):
        return Visa.objects.create(student=student, application=application, status=visa_status)


def make_payment(
    office: ConsultancyContext, student: Student, by: Staff, **fields: object
) -> Payment:
    """Store a Payment directly (Visa fee, NPR 25,000, cash, today unless told otherwise)."""
    details: dict[str, object] = {
        "amount": 25000,
        "paid_on": timezone.localdate(),
        "method": "cash",
        "fee_type": "Visa fee",
        **fields,
    }
    with tenant_context(office.consultancy):
        details["fee_type"] = FeeType.objects.get(name=details["fee_type"])
        return Payment.objects.create(
            student=student, recorded_by=by, recorded_by_name=by.name, **details
        )


def make_fee(office: ConsultancyContext, student: Student, by: Staff, **fields: object) -> Fee:
    """Store a Fee directly: `fee_type` is the Fee Type's name (Visa fee, NPR 40,000 if none)."""
    details: dict[str, object] = {"fee_type": "Visa fee", "amount": 40000, **fields}
    with tenant_context(office.consultancy):
        details["fee_type"] = FeeType.objects.get(name=details["fee_type"])
        return Fee.objects.create(student=student, added_by=by, added_by_name=by.name, **details)


def make_batch(
    office: ConsultancyContext,
    teacher: Staff,
    students: tuple[Student, ...] = (),
    **fields: object,
) -> Batch:
    """Store a class starting in two weeks, with `students` in it."""
    details: dict[str, object] = {
        "name": "IELTS Morning",
        "test_type": "ielts",
        "time_slot": "7:00-8:30 AM",
        "starts_on": date(2026, 12, 1),
        "ends_on": date(2027, 2, 1),
        **fields,
    }
    with tenant_context(office.consultancy):
        batch = Batch.objects.create(teacher=teacher, **details)
        for student in students:
            BatchMembership.objects.create(batch=batch, student=student)
    return batch


def hand_in(
    office: ConsultancyContext, student: Student, type_name: str, verification: str
) -> None:
    """Store a Document of this type for the Student (no files needed for the checklist)."""
    with tenant_context(office.consultancy):
        Document.objects.create(
            student=student,
            document_type=DocumentType.objects.get(name=type_name),
            verification=verification,
        )


def needed_by_everyone(office: ConsultancyContext) -> list[str]:
    """The Document Types every Student needs, in checklist order."""
    with tenant_context(office.consultancy):
        return list(
            DocumentType.objects.filter(needed_by_everyone=True)
            .order_by("pk")
            .values_list("name", flat=True)
        )


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office: Email, Accounts and Batches on, with a contact email."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.COMMUNICATION, Module.ACCOUNTS, Module.BATCHES})
    set_contact_email(office)
    return office


@pytest.fixture
def admin(abc: ConsultancyContext) -> Staff:
    """The office's Admin, logged in."""
    staff = named_staff(abc, "Asha Admin", Role.ADMIN)
    log_in(abc, staff)
    return staff


@pytest.fixture
def counsellor(abc: ConsultancyContext) -> Staff:
    """A Counsellor (not logged in)."""
    return named_staff(abc, "Ram Bahadur Karki", Role.COUNSELLOR)


@pytest.fixture
def teacher(abc: ConsultancyContext) -> Staff:
    """A Teacher (not logged in)."""
    return named_staff(abc, "Hari Teacher", Role.TEACHER)


@pytest.fixture
def sita(abc: ConsultancyContext, counsellor: Staff) -> Student:
    """Ram's Student, at Counselling, with an email address."""
    return make_student(
        abc, counsellor, stage=Stage.COUNSELLING, name="Sita Gurung", email="sita@example.com"
    )


class TestTheMenu:
    def test_it_lists_the_eight_ready_made_emails_in_order(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        response = abc.client.get(menu_url(sita))

        assert response.status_code == status.HTTP_200_OK
        assert [entry["kind"] for entry in response.json()["kinds"]] == KINDS

    def test_emails_needing_a_record_are_unavailable_without_one(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        kinds = menu(abc, sita)

        assert kinds["payment_received"] == {
            "kind": "payment_received",
            "record": "payment",
            "records": [],
            "unavailable": "No payments yet",
        }
        assert kinds["offer_received"]["unavailable"] == "No offer letters yet"
        assert kinds["visa_result"]["unavailable"] == "No visa result yet"
        assert kinds["class_details"]["unavailable"] == "Not in a class"
        assert kinds["fee_reminder"]["unavailable"] == "Nothing left to pay"
        assert kinds["welcome"] == {
            "kind": "welcome",
            "record": None,
            "records": [],
            "unavailable": None,
        }
        assert kinds["plain"]["unavailable"] is None
        assert kinds["documents_needed"]["unavailable"] is None

    def test_it_lists_the_records_to_pick_newest_first(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, teacher: Staff
    ) -> None:
        older = make_payment(abc, sita, admin, paid_on=date(2026, 10, 1), amount=10000)
        newer = make_payment(
            abc, sita, admin, paid_on=date(2026, 11, 2), fee_type="Other", note="Courier"
        )
        offer = make_offer(abc, sita, kind=OfferKind.UNCONDITIONAL)
        make_offer(abc, sita, course="Applied only", status=ApplicationStatus.APPLIED)
        granted = make_visa(abc, sita, VisaStatus.GRANTED)
        waiting = make_visa(abc, sita, VisaStatus.APPLIED)
        batch = make_batch(abc, teacher, (sita,))

        kinds = menu(abc, sita)

        assert kinds["payment_received"]["records"] == [
            {
                "id": newer.pk,
                "label": "Other (Courier), NPR 25,000, 2 Nov 2026",
            },
            {
                "id": older.pk,
                "label": "Visa fee, NPR 10,000, 1 Oct 2026",
            },
        ]
        assert kinds["offer_received"]["records"] == [
            {
                "id": waiting.application_id,
                "label": "Deakin University, Course applied, Mar 2027 (Conditional)",
            },
            {
                "id": granted.application_id,
                "label": "Deakin University, Course granted, Mar 2027 (Conditional)",
            },
            {"id": offer.pk, "label": "Deakin University, Master of IT, Mar 2027 (Unconditional)"},
        ]
        assert kinds["visa_result"]["records"] == [
            {"id": granted.pk, "label": "Australia visa: Granted"}
        ]
        assert kinds["class_details"]["records"] == [
            {"id": batch.pk, "label": "IELTS Morning, 7:00-8:30 AM"}
        ]
        for kind in ("payment_received", "offer_received", "visa_result", "class_details"):
            assert kinds[kind]["unavailable"] is None

    def test_every_document_in_makes_documents_needed_unavailable(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        for name in needed_by_everyone(abc):
            hand_in(abc, sita, name, Verification.PENDING)

        assert menu(abc, sita)["documents_needed"]["unavailable"] == "Every document is in"

    def test_a_rejected_document_is_still_needed(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        names = needed_by_everyone(abc)
        for name in names:
            hand_in(abc, sita, name, Verification.VERIFIED)
        hand_in(abc, sita, names[0], Verification.REJECTED)

        assert menu(abc, sita)["documents_needed"]["unavailable"] is None

    def test_accounts_off_leaves_out_the_fee_reminder(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        switch_modules(abc.consultancy, {Module.COMMUNICATION, Module.BATCHES})

        kinds = menu(abc, sita)

        assert "fee_reminder" not in kinds
        assert "payment_received" in kinds

    def test_batches_off_leaves_out_class_details(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        switch_modules(abc.consultancy, {Module.COMMUNICATION, Module.ACCOUNTS})

        assert "class_details" not in menu(abc, sita)

    def test_only_an_admin_can_send_a_fee_reminder(
        self, abc: ConsultancyContext, sita: Student, counsellor: Staff
    ) -> None:
        make_fee(abc, sita, counsellor)
        log_in(abc, counsellor)

        assert menu(abc, sita)["fee_reminder"]["unavailable"] == "Only an Admin can send it"

    def test_others_pick_only_a_payment_they_just_recorded(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        desk = named_staff(abc, "Sunita Desk", Role.FRONT_DESK)
        make_payment(abc, sita, admin)
        mine = make_payment(abc, sita, desk)
        log_in(abc, desk)

        kinds = menu(abc, sita)

        assert [record["id"] for record in kinds["payment_received"]["records"]] == [mine.pk]

    def test_with_no_payment_of_their_own_others_are_told_so(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        make_payment(abc, sita, admin)
        abc.log_in_as(Role.FRONT_DESK)

        entry = menu(abc, sita)["payment_received"]

        assert entry["records"] == []
        assert entry["unavailable"] == "No payment you just recorded"

    def test_another_students_records_are_never_listed(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, teacher: Staff
    ) -> None:
        ram = make_student(abc, name="Ram Thapa", email="ram@example.com")
        make_payment(abc, ram, admin)
        make_offer(abc, ram)
        make_visa(abc, ram, VisaStatus.GRANTED)
        make_batch(abc, teacher, (ram,))

        kinds = menu(abc, sita)

        for kind in ("payment_received", "offer_received", "visa_result", "class_details"):
            assert kinds[kind]["records"] == []


class TestDrafts:
    def test_welcome_greets_them_and_names_their_counsellor(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        answer = drafted(abc, sita, "welcome")

        assert answer["subject"] == "Welcome to ABC Education"
        assert answer["body"].startswith("Dear Sita,\n\nThank you for choosing ABC Education")
        assert (
            "Your counsellor is Ram Bahadur Karki. Ram will guide you through choosing a course"
            in answer["body"]
        )
        assert answer["body"].endswith(SIGN_OFF)

    def test_welcome_without_a_counsellor_promises_one(
        self, abc: ConsultancyContext, admin: Staff
    ) -> None:
        student = make_student(abc, name="Gita Rai", email="gita@example.com")

        assert "We'll give you a counsellor soon" in drafted(abc, student, "welcome")["body"]

    def test_documents_needed_lists_the_missing_and_rejected_documents(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        names = needed_by_everyone(abc)
        hand_in(abc, sita, names[0], Verification.VERIFIED)
        hand_in(abc, sita, names[1], Verification.REJECTED)
        hand_in(abc, sita, names[2], Verification.PENDING)

        answer = drafted(abc, sita, "documents_needed")

        assert answer["subject"] == "Documents we still need from you"
        listed = [line[2:] for line in answer["body"].splitlines() if line.startswith("- ")]
        assert listed == [names[1], *names[3:]]
        assert "attached examples" not in answer["body"]

    def test_payment_received_states_the_payment(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        payment = make_payment(
            abc, sita, admin, note="first half", paid_on=date(2026, 11, 2), method="esewa"
        )

        answer = drafted(abc, sita, "payment_received", payment.pk)

        assert answer["subject"] == "Payment received"
        assert (
            "Amount: NPR 25,000\nFor: Visa fee (first half)\nDate: 2 Nov 2026\nPaid by: eSewa\n\n"
        ) in answer["body"]
        assert "eceipt" not in answer["body"]

    def test_fee_reminder_gives_the_fees_what_was_paid_and_the_one_balance(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        make_fee(abc, sita, admin)
        make_fee(abc, sita, admin, fee_type="Application fee", amount=10000, note="Deakin")
        make_payment(abc, sita, admin, amount=45000, fee_type="Visa fee")

        answer = drafted(abc, sita, "fee_reminder")

        assert answer["subject"] == "A reminder about your fees"
        assert "- Visa fee: NPR 40,000\n- Application fee (Deakin): NPR 10,000" in answer["body"]
        assert (
            "Total fees: NPR 50,000\nPaid so far: NPR 45,000\nStill to pay: NPR 5,000"
            in answer["body"]
        )

    def test_offer_received_names_the_university_course_and_intake(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        offer = make_offer(abc, sita)

        answer = drafted(abc, sita, "offer_received", offer.pk)

        assert answer["subject"] == "Good news: an offer from Deakin University"
        assert "has made you a conditional offer" in answer["body"]
        assert (
            "University: Deakin University\nCourse: Master of IT\nIntake: Mar 2027"
            in answer["body"]
        )

    def test_visa_result_is_kind_for_granted_and_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        granted = make_visa(abc, sita, VisaStatus.GRANTED)
        refused = make_visa(abc, sita, VisaStatus.REFUSED)

        good = drafted(abc, sita, "visa_result", granted.pk)
        bad = drafted(abc, sita, "visa_result", refused.pk)

        assert good["subject"] == "Your visa has been granted"
        assert "Your visa for Australia has been granted." in good["body"]
        assert bad["subject"] == "About your visa application"
        assert "your visa application for Australia was refused" in bad["body"]
        assert "A refusal is not the end of your plans." in bad["body"]

    @pytest.mark.parametrize(
        ("weekly_off", "days"),
        [
            (WeeklyOff.SATURDAY, "Sunday to Friday"),
            (WeeklyOff.SUNDAY, "Monday to Saturday"),
            (WeeklyOff.BOTH, "Monday to Friday"),
            (WeeklyOff.NONE, "Every day"),
        ],
    )
    def test_class_details_give_the_class(
        self,
        abc: ConsultancyContext,
        admin: Staff,
        sita: Student,
        weekly_off: WeeklyOff,
        days: str,
    ) -> None:
        teacher = named_staff(abc, "Hari Teacher", Role.TEACHER)
        with tenant_context(abc.consultancy):
            set_weekly_off(weekly_off, by=admin)
        batch = make_batch(abc, teacher, (sita,))

        answer = drafted(abc, sita, "class_details", batch.pk)

        assert answer["subject"] == "Your class: IELTS Morning"
        assert (
            f"Class: IELTS Morning\nDays: {days}\nTime: 7:00-8:30 AM\nStarts: 1 Dec 2026\n"
            "Teacher: Hari Teacher"
        ) in answer["body"]

    def test_plain_email_is_only_the_greeting_and_sign_off(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        assert drafted(abc, sita, "plain") == {
            "subject": "",
            "body": f"Dear Sita,\n\n\n\n{SIGN_OFF}",
            "attachments": [],
        }

    def test_it_is_signed_by_whoever_asks(self, abc: ConsultancyContext, sita: Student) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        log_in(abc, desk)

        body = drafted(abc, sita, "plain")["body"]

        assert body.endswith("Kind regards,\nSunita Shrestha\nABC Education")

    def test_a_long_name_never_makes_the_subject_line_too_long_to_send(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        abc.consultancy.name = "A" * 200
        abc.consultancy.save(update_fields=["name"])

        subject = drafted(abc, sita, "welcome")["subject"]

        assert subject == f"Welcome to {'A' * 139}"
        assert len(subject) == 150

    def test_names_and_notes_come_back_as_they_were_typed(
        self, abc: ConsultancyContext, admin: Staff
    ) -> None:
        name = INJECTION_STRINGS[0]
        student = make_student(abc, name=name, email="x@example.com")
        payment = make_payment(abc, student, admin, fee_type="Other", note=INJECTION_STRINGS[1])

        answer = drafted(abc, student, "payment_received", payment.pk)

        assert answer["body"].startswith(f"Dear {name.split(' ')[0]},")
        assert f"For: Other ({INJECTION_STRINGS[1]})" in answer["body"]


class TestRefusedDrafts:
    @pytest.mark.parametrize(
        "kind",
        ["", "unknown", "Welcome", "x" * 1000, *INJECTION_STRINGS],
    )
    def test_an_unknown_kind_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, kind: str
    ) -> None:
        response = draft(abc, sita, kind)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_A_KIND

    def test_no_kind_is_refused(self, abc: ConsultancyContext, admin: Staff, sita: Student) -> None:
        response = abc.client.get(draft_url(sita))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_A_KIND

    def test_an_unknown_field_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        response = abc.client.get(draft_url(sita), {"kind": "plain", "student": "2"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "student" not in str(response.json())

    @pytest.mark.parametrize(
        ("kind", "message"),
        [
            ("payment_received", "Choose a Payment."),
            ("offer_received", "Choose an Offer Letter."),
            ("visa_result", "Choose a Visa."),
            ("class_details", "Choose a class."),
        ],
    )
    def test_a_missing_record_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, kind: str, message: str
    ) -> None:
        response = draft(abc, sita, kind)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"record": [message]}

    @pytest.mark.parametrize("record", ["abc", "1.5", "-1", "0", str(2**63), "<b>1</b>"])
    def test_a_record_that_isnt_an_id_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, record: str
    ) -> None:
        response = abc.client.get(draft_url(sita), {"kind": "payment_received", "record": record})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"record": ["Choose a Payment."]}

    def test_a_record_for_an_email_that_needs_none_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        response = draft(abc, sita, "welcome", 1)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"record": ["This email isn't about a record."]}

    def test_another_students_records_are_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, teacher: Staff
    ) -> None:
        ram = make_student(abc, name="Ram Thapa", email="ram@example.com")
        theirs = {
            "payment_received": make_payment(abc, ram, admin).pk,
            "offer_received": make_offer(abc, ram).pk,
            "visa_result": make_visa(abc, ram, VisaStatus.GRANTED).pk,
            "class_details": make_batch(abc, teacher, (ram,)).pk,
        }

        for kind, record in theirs.items():
            response = draft(abc, sita, kind, record)
            assert response.status_code == status.HTTP_400_BAD_REQUEST, kind
            assert list(response.json()) == ["record"]

    def test_an_application_without_an_offer_and_a_visa_still_applied_are_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        applied = make_offer(abc, sita, status=ApplicationStatus.APPLIED)
        waiting = make_visa(abc, sita, VisaStatus.APPLIED)

        assert draft(abc, sita, "offer_received", applied.pk).json() == {
            "record": ["Choose an Offer Letter."]
        }
        assert draft(abc, sita, "visa_result", waiting.pk).json() == {"record": ["Choose a Visa."]}

    def test_someone_elses_old_payment_is_refused_to_front_desk(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        payment = make_payment(abc, sita, admin)
        abc.log_in_as(Role.FRONT_DESK)

        response = draft(abc, sita, "payment_received", payment.pk)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"record": ["Choose a Payment."]}

    def test_documents_needed_with_everything_in_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        for name in needed_by_everyone(abc):
            hand_in(abc, sita, name, Verification.VERIFIED)

        response = draft(abc, sita, "documents_needed")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"kind": ["Every document is in"]}

    def test_a_fee_reminder_with_nothing_to_pay_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        response = draft(abc, sita, "fee_reminder")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"kind": ["Nothing left to pay"]}

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_a_fee_reminder_is_refused_to_all_but_admins(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, role: Role
    ) -> None:
        make_fee(abc, sita, admin)
        abc.log_in_as(role)

        response = draft(abc, sita, "fee_reminder")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == {"detail": "Only an Admin can send it."}

    def test_a_fee_reminder_with_accounts_off_is_not_found(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        make_fee(abc, sita, admin)
        switch_modules(abc.consultancy, {Module.COMMUNICATION})

        assert draft(abc, sita, "fee_reminder").status_code == status.HTTP_404_NOT_FOUND

    def test_class_details_with_batches_off_are_not_found(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, teacher: Staff
    ) -> None:
        batch = make_batch(abc, teacher, (sita,))
        switch_modules(abc.consultancy, {Module.COMMUNICATION})

        response = draft(abc, sita, "class_details", batch.pk)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext, admin: Staff) -> None:
        assert draft(abc, 999_999, "plain").status_code == status.HTTP_404_NOT_FOUND
        assert abc.client.get(menu_url(999_999)).status_code == status.HTTP_404_NOT_FOUND

    def test_logs_ids_and_the_kind_only(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, json_logs: JsonLogs
    ) -> None:
        drafted(abc, sita, "welcome")

        [line] = json_logs.with_message("Email draft made")
        assert line["message"] == (
            f"Email draft made: kind=welcome, student_id={sita.pk}, attachments=0"
        )
        for private in ("Sita", "sita@example.com", "Ram Bahadur"):
            assert private not in json_logs.text()


class TestWhoMayDraft:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_admins_counsellors_and_front_desk_may(
        self, abc: ConsultancyContext, sita: Student, role: Role
    ) -> None:
        abc.log_in_as(role)

        assert abc.client.get(menu_url(sita)).status_code == status.HTTP_200_OK
        assert draft(abc, sita, "welcome").status_code == status.HTTP_200_OK
        assert abc.client.get(BULK_MENU_URL).status_code == status.HTTP_200_OK
        assert bulk_draft(abc, "plain").status_code == status.HTTP_200_OK

    def test_teachers_may_not(self, abc: ConsultancyContext, sita: Student) -> None:
        abc.log_in_as(Role.TEACHER)

        for response in (
            abc.client.get(menu_url(sita)),
            draft(abc, sita, "welcome"),
            abc.client.get(BULK_MENU_URL),
            bulk_draft(abc, "plain"),
        ):
            assert response.status_code == status.HTTP_403_FORBIDDEN
            assert response.json() == {"detail": "Teachers can't see Students."}

    def test_logged_out_is_refused(self, abc: ConsultancyContext, sita: Student) -> None:
        for response in (
            abc.client.get(menu_url(sita)),
            draft(abc, sita, "welcome"),
            abc.client.get(BULK_MENU_URL),
            bulk_draft(abc, "plain"),
        ):
            assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_email_switched_off_is_not_found(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        switch_modules(abc.consultancy, {Module.ACCOUNTS, Module.BATCHES})

        for response in (
            abc.client.get(menu_url(sita)),
            draft(abc, sita, "welcome"),
            abc.client.get(BULK_MENU_URL),
            bulk_draft(abc, "plain"),
        ):
            assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, method: str
    ) -> None:
        for url in (menu_url(sita), draft_url(sita), BULK_MENU_URL, BULK_DRAFT_URL):
            response = getattr(abc.client, method)(url, {}, format="json")
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, url


class TestEmailToMany:
    def test_only_plain_email_and_class_details_are_offered(
        self, abc: ConsultancyContext, admin: Staff, teacher: Staff
    ) -> None:
        later = make_batch(abc, teacher, name="PTE Evening", starts_on=date(2027, 1, 5))
        first = make_batch(abc, teacher)
        make_batch(
            abc,
            teacher,
            name="Ended",
            starts_on=timezone.localdate() - timedelta(days=60),
            ends_on=timezone.localdate() - timedelta(days=1),
        )

        response = abc.client.get(BULK_MENU_URL)

        assert response.json() == {
            "kinds": [
                {
                    "kind": "class_details",
                    "record": "batch",
                    "records": [
                        {"id": first.pk, "label": "IELTS Morning, 7:00-8:30 AM"},
                        {"id": later.pk, "label": "PTE Evening, 7:00-8:30 AM"},
                    ],
                    "unavailable": None,
                },
                {"kind": "plain", "record": None, "records": [], "unavailable": None},
            ]
        }

    def test_with_no_class_running_class_details_are_unavailable(
        self, abc: ConsultancyContext, admin: Staff
    ) -> None:
        [entry, _] = abc.client.get(BULK_MENU_URL).json()["kinds"]

        assert entry["unavailable"] == "No classes yet"

    def test_batches_off_offers_only_plain_email(
        self, abc: ConsultancyContext, admin: Staff
    ) -> None:
        switch_modules(abc.consultancy, {Module.COMMUNICATION})

        assert [k["kind"] for k in abc.client.get(BULK_MENU_URL).json()["kinds"]] == ["plain"]
        assert bulk_draft(abc, "class_details", 1).status_code == status.HTTP_404_NOT_FOUND

    def test_each_copy_starts_dear_student(
        self, abc: ConsultancyContext, admin: Staff, teacher: Staff
    ) -> None:
        batch = make_batch(abc, teacher)

        plain = bulk_draft(abc, "plain").json()
        details = bulk_draft(abc, "class_details", batch.pk).json()

        assert plain == {
            "subject": "",
            "body": f"Dear Student,\n\n\n\n{SIGN_OFF}",
            "attachments": [],
        }
        assert details["subject"] == "Your class: IELTS Morning"
        assert details["body"].startswith("Dear Student,\n\nHere are the details of your class")

    def test_front_desk_may_send_class_details_to_many(
        self, abc: ConsultancyContext, teacher: Staff
    ) -> None:
        batch = make_batch(abc, teacher)
        abc.log_in_as(Role.FRONT_DESK)

        assert bulk_draft(abc, "class_details", batch.pk).status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        "kind",
        ["welcome", "documents_needed", "payment_received", "fee_reminder", "offer_received"],
    )
    def test_emails_about_one_students_records_go_to_one_at_a_time(
        self, abc: ConsultancyContext, admin: Staff, kind: str
    ) -> None:
        response = bulk_draft(abc, kind)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"kind": ["This email goes to one Student at a time."]}

    def test_an_unknown_class_is_refused(self, abc: ConsultancyContext, admin: Staff) -> None:
        response = bulk_draft(abc, "class_details", 999_999)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"record": ["Choose a class."]}

    @pytest.mark.parametrize("kind", ["", "nope", *INJECTION_STRINGS])
    def test_an_unknown_kind_is_refused(
        self, abc: ConsultancyContext, admin: Staff, kind: str
    ) -> None:
        response = bulk_draft(abc, kind)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_A_KIND

    def test_the_menu_takes_no_query(self, abc: ConsultancyContext, admin: Staff) -> None:
        response = abc.client.get(BULK_MENU_URL, {"batch": 1})

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestEditingAndSending:
    def test_the_edited_draft_is_what_the_student_gets(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        offer = make_offer(abc, sita)
        answer = drafted(abc, sita, "offer_received", offer.pk)
        edited = answer["body"].replace("Please get in touch", "Come on Sunday at 11")

        response = send(abc, sita, {"subject": answer["subject"] + "!", "body": edited})

        assert response.status_code == status.HTTP_201_CREATED
        [sent] = outbox()
        assert sent.subject == "Good news: an offer from Deakin University!"
        assert "Come on Sunday at 11" in sent.body
        assert "Please get in touch" not in sent.body
        assert "University: Deakin University<br>Course: Master of IT" in html_of(sent)

    def test_a_typed_record_name_is_only_words_in_the_email(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        offer = make_offer(abc, sita, university=INJECTION_STRINGS[0])
        answer = drafted(abc, sita, "offer_received", offer.pk)

        send(abc, sita, {"subject": "Offer", "body": answer["body"]})

        [sent] = outbox()
        assert "<script>" not in html_of(sent)
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_of(sent)
        assert INJECTION_STRINGS[0] in sent.body

    def test_class_details_go_to_many_each_starting_dear_student(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, teacher: Staff
    ) -> None:
        ram = make_student(abc, name="Ram Thapa", email="ram@example.com")
        batch = make_batch(abc, teacher, (sita, ram))
        answer = bulk_draft(abc, "class_details", batch.pk).json()

        response = abc.client.post(
            "/api/messages/bulk/",
            {"subject": answer["subject"], "body": answer["body"], "students": [sita.pk, ram.pk]},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert sorted(sent.to[0] for sent in outbox()) == ["ram@example.com", "sita@example.com"]
        for sent in outbox():
            assert "ABC Education\n\nDear Student,\n\nHere are the details" in sent.body
            assert "Sita" not in sent.body
        assert len(mail.outbox) == 2
