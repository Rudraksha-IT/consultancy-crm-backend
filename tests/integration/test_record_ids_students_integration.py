"""Students and their own records are known only by UUID (ADR 0027, spec 111, 115).

Every answer names records by UUID; addresses take only UUIDs; a number, a malformed id, or
another office's or another Student's UUID is refused like an unknown record, never a 500.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from documents.models import DocumentFile
from fee_types.models import FeeType
from modules.services import switch_modules
from modules.types import SWITCHABLE_MODULES
from payments.models import Payment
from staff.models import Role
from students.models import AcademicRecord, Stage, Student, TestScore, WorkExperience
from tests.fakes import FakeDocumentStorage
from tests.integration.test_communication_integration import email, send, set_contact_email
from tests.integration.test_documents_integration import upload
from tests.integration.test_fees_integration import make_fee
from tests.integration.test_payments_integration import make_payment
from tests.integration.test_student_profile_integration import make_student
from tests.integration.test_visa_letters_integration import waiting_visa
from tests.integration.test_visits_integration import log_visit
from tests.record_ids import assert_no_record_numbers, is_uuid

if TYPE_CHECKING:
    from tests.conftest import ConsultancyContext, ConsultancyFactory

STUDENTS_URL = "/api/students/"
ENTRY_MODELS: dict[str, type[AcademicRecord] | type[TestScore] | type[WorkExperience]] = {
    "academic-records": AcademicRecord,
    "test-scores": TestScore,
    "work-experience": WorkExperience,
}
# Each of a Student's own lists, and a body that adds one entry to it.
ENTRY_LISTS = {
    "academic-records": {"level": "plus_two", "institution": "Kathmandu Model College"},
    "test-scores": {"test": "ielts", "overall": "6.5"},
    "work-experience": {"employer": "Himal Bank", "job_title": "Teller", "started_on": "2024-01"},
}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office every test works in."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def xyz(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """Another office."""
    return make_consultancy("XYZ Overseas", "xyzoverseas")


def student_url(student: Student | str) -> str:
    """The Student's address, by its UUID (or any text, to try bad ones)."""
    key = student if isinstance(student, str) else str(student.public_id)
    return f"{STUDENTS_URL}{key}/"


class TestAnswers:
    """The list and the profile name every record by UUID."""

    def test_the_list_names_students_by_uuid(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc, admin, Stage.COUNSELLING)

        answer = abc.client.get(STUDENTS_URL).json()

        assert_no_record_numbers(answer)
        assert answer["results"][0]["id"] == str(student.public_id)

    def test_the_profile_names_every_record_by_uuid(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc, admin, Stage.COUNSELLING)
        for kind, body in ENTRY_LISTS.items():
            added = abc.client.post(f"{student_url(student)}{kind}/", body, format="json")
            assert added.status_code == status.HTTP_201_CREATED, added.json()

        response = abc.client.get(student_url(student))

        assert response.status_code == status.HTTP_200_OK
        answer = response.json()
        assert_no_record_numbers(answer)
        assert answer["id"] == str(student.public_id)

    @pytest.mark.parametrize("kind", ENTRY_LISTS)
    def test_an_added_entry_is_named_by_uuid(self, abc: ConsultancyContext, kind: str) -> None:
        student = make_student(abc, abc.log_in_as(Role.ADMIN), Stage.COUNSELLING)

        answer = abc.client.post(f"{student_url(student)}{kind}/", ENTRY_LISTS[kind], format="json")

        assert_no_record_numbers(answer.json())
        entry_id = answer.json()["id"]
        assert is_uuid(entry_id)
        changed = abc.client.delete(f"{student_url(student)}{kind}/{entry_id}/")
        assert changed.status_code == status.HTTP_204_NO_CONTENT

    def test_the_history_names_records_by_uuid(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, abc.log_in_as(Role.ADMIN), Stage.COUNSELLING)
        abc.client.put(f"{student_url(student)}note/", {"note": "Changed."}, format="json")

        answer = abc.client.get(f"{student_url(student)}history/").json()

        assert_no_record_numbers(answer)


class TestRefusedAddresses:
    """Only the Student's own UUID, in this office, finds them."""

    @pytest.mark.parametrize("suffix", ["", "note/", "history/", "details/", "academic-records/"])
    def test_a_number_finds_nobody(self, abc: ConsultancyContext, suffix: str) -> None:
        student = make_student(abc, abc.log_in_as(Role.ADMIN), Stage.COUNSELLING)

        response = abc.client.get(f"{STUDENTS_URL}{student.pk}/{suffix}")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        "bad",
        ["not-a-uuid", "16abc", "3f8a2c1e9b7d4c2a8e1f0a1b2c3d4e5f", "%27%20OR%201%3D1", "-1"],
    )
    def test_a_malformed_id_finds_nobody(self, abc: ConsultancyContext, bad: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(student_url(bad))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_unknown_uuid_finds_nobody(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(student_url(str(uuid.uuid4())))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_another_offices_student_is_not_found(
        self, abc: ConsultancyContext, xyz: ConsultancyContext
    ) -> None:
        theirs = make_student(xyz, xyz.add_staff(Role.ADMIN), Stage.COUNSELLING)
        abc.log_in_as(Role.ADMIN)

        for suffix in ("", "history/"):
            response = abc.client.get(f"{student_url(theirs)}{suffix}")
            assert response.status_code == status.HTTP_404_NOT_FOUND
        note = abc.client.put(f"{student_url(theirs)}note/", {"note": "x"}, format="json")
        assert note.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("kind", ENTRY_LISTS)
    def test_another_students_entry_is_not_found(self, abc: ConsultancyContext, kind: str) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        mine = make_student(abc, admin, Stage.COUNSELLING)
        other = make_student(abc, admin, Stage.COUNSELLING, phone="9841000002")
        added = abc.client.post(f"{student_url(other)}{kind}/", ENTRY_LISTS[kind], format="json")
        their_entry = added.json()["id"]

        response = abc.client.delete(f"{student_url(mine)}{kind}/{their_entry}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(abc.consultancy):
            assert ENTRY_MODELS[kind].objects.filter(public_id=their_entry).exists()

    @pytest.mark.parametrize("kind", ENTRY_LISTS)
    def test_an_entry_by_number_is_not_found(self, abc: ConsultancyContext, kind: str) -> None:
        student = make_student(abc, abc.log_in_as(Role.ADMIN), Stage.COUNSELLING)
        abc.client.post(f"{student_url(student)}{kind}/", ENTRY_LISTS[kind], format="json")
        with tenant_context(abc.consultancy):
            number = ENTRY_MODELS[kind].objects.get().pk

        response = abc.client.delete(f"{student_url(student)}{kind}/{number}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestEveryAnswerAboutAStudent:
    """Each answer that shows a Student or their records names them by UUID only (spec 111)."""

    @pytest.fixture
    def full(self, abc: ConsultancyContext) -> Student:
        """A Student with a Document, Fee, Payment, Visit, email, Application and Visa."""
        switch_modules(abc.consultancy, set(SWITCHABLE_MODULES))
        set_contact_email(abc)
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc, admin, Stage.COUNSELLING, email="sita@example.com")
        assert upload(abc, student).status_code == status.HTTP_201_CREATED
        make_fee(abc, student, admin)
        make_payment(abc, student, admin)
        assert (
            log_visit(abc, student, reason="other", details="Asked about fees").status_code
            == status.HTTP_201_CREATED
        )
        assert send(abc, student, email()).status_code == status.HTTP_201_CREATED
        waiting_visa(abc, student)
        return student

    @pytest.mark.parametrize(
        "suffix",
        [
            "",
            "history/",
            "documents/",
            "fees/",
            "visits/",
            "messages/",
            "emails/ready-made/",
            "emails/attachable/",
            "batches/",
            "tasks/",
            "progress/",
        ],
    )
    def test_the_student_page(self, abc: ConsultancyContext, full: Student, suffix: str) -> None:
        response = abc.client.get(f"{student_url(full)}{suffix}")

        assert response.status_code == status.HTTP_200_OK, (suffix, response.content)
        assert_no_record_numbers(response.json())

    @pytest.mark.parametrize(
        "url",
        [
            STUDENTS_URL,
            "/api/payments/",
            "/api/accounts/student-fees/",
            "/api/applications/",
            "/api/visas/",
            "/api/messages/",
            "/api/visits/today/",
            "/api/documents/students/",
            "/api/home/progress/",
            "/api/search/?q=Sita",
        ],
    )
    def test_the_office_lists(self, abc: ConsultancyContext, full: Student, url: str) -> None:
        response = abc.client.get(url)

        assert response.status_code == status.HTTP_200_OK, (url, response.content)
        assert_no_record_numbers(response.json())
        assert str(full.public_id) in response.content.decode()

    def test_a_receipt_and_fees_by_type(self, abc: ConsultancyContext, full: Student) -> None:
        with tenant_context(abc.consultancy):
            payment = Payment.objects.get().public_id
            fee_type = FeeType.objects.get(name="Visa fee").pk

        answers = [
            abc.client.get(f"/api/payments/{payment}/"),
            abc.client.get(f"/api/accounts/fees-by-type/{fee_type}/"),
        ]

        for answer in answers:
            assert answer.status_code == status.HTTP_200_OK, answer.content
            assert_no_record_numbers(answer.json())

    def test_a_file_saved_under_the_students_number_still_opens(
        self, abc: ConsultancyContext, full: Student
    ) -> None:
        """Files stored before the switch keep their path; nothing moves (spec 116)."""
        with tenant_context(abc.consultancy):
            file = DocumentFile.objects.select_related("document").get()
            old_key = f"{abc.consultancy.schema_name}/documents/{full.pk}/old.pdf"
            FakeDocumentStorage.files[old_key] = FakeDocumentStorage.files.pop(file.storage_key)
            DocumentFile.objects.filter(pk=file.pk).update(storage_key=old_key)
        link = (
            f"{student_url(full)}documents/{file.document.public_id}/files/{file.public_id}/link/"
        )

        response = abc.client.get(link)

        assert response.status_code == status.HTTP_200_OK
        assert old_key in response.json()["url"]
