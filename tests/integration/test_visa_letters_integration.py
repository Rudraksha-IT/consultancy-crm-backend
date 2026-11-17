"""Uploading a visa letter sets Granted or Refused (spec 116, 59; issue 77)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Visa
from applications.statuses import VisaStatus
from documents.files import MAX_DOCUMENT_BYTES
from documents.models import Document, DocumentType
from documents.services import MAX_DOCUMENTS_PER_STUDENT
from history.models import HistoryEntry
from staff.models import Role
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.fakes import FakeDocumentStorage
from tests.integration.test_applications_integration import (
    COUNSELLOR_OR_ADMIN,
    counselled_student,
    stage_entries,
)
from tests.integration.test_documents_integration import files_of
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    NOT_FOR_TEACHERS,
    NOT_YOUR_STUDENT,
    log_in,
    make_student,
    named_staff,
    stored,
)
from tests.integration.test_visas_integration import make_visa, offer_application, visa_url

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

PDF_BYTES = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
CHOOSE_RESULT = {"result": ["Choose Granted or Refused."]}
CHOOSE_FILE = {"file": ["Choose a file to upload."]}
FILE_KIND = {"file": ["Upload a PDF, JPG or PNG file."]}
NOT_STORED = {"detail": "We couldn't store the file. Try again."}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def letter_url(student: Student | int, visa: Visa | int) -> str:
    """Where one Visa's letter is uploaded."""
    return f"{visa_url(student, visa)}letter/"


def a_letter(content: bytes = PDF_BYTES, name: str = "visa-grant.pdf") -> SimpleUploadedFile:
    """A letter as the browser would send it (its stated type is never believed)."""
    return SimpleUploadedFile(name, content, content_type="application/pdf")


def upload(
    office: ConsultancyContext, student: Student | int, visa: Visa | int, **fields: object
) -> _MonkeyPatchedResponse:
    """POST a visa letter as a form: a PDF saying Granted unless `fields` say otherwise."""
    body: dict[str, object] = {"result": "granted", "file": a_letter(), **fields}
    body = {key: value for key, value in body.items() if value is not None}
    return office.client.post(letter_url(student, visa), body, format="multipart")


def waiting_visa(office: ConsultancyContext, student: Student, university: str = "Deakin") -> Visa:
    """A Visa at Applied on the Student's Chosen Offer."""
    return make_visa(office, student, offer_application(office, student, university))


def status_of(office: ConsultancyContext, visa: Visa) -> str:
    """The Visa's Status as stored now."""
    with tenant_context(office.consultancy):
        return Visa.objects.get(pk=visa.pk).status


def documents_of(office: ConsultancyContext, student: Student) -> list[Document]:
    """The Student's stored Documents, oldest first."""
    with tenant_context(office.consultancy):
        return list(
            Document.objects.filter(student=student).select_related("document_type").order_by("pk")
        )


def chosen_of(office: ConsultancyContext, student: Student) -> int | None:
    """The stored Chosen Offer's id."""
    with tenant_context(office.consultancy):
        return Student.objects.get(pk=student.pk).chosen_offer_id


def history_of(office: ConsultancyContext, student: Student) -> list[HistoryEntry]:
    """Everything in the Student's History."""
    with tenant_context(office.consultancy):
        return list(HistoryEntry.objects.filter(student=student))


class TestUploadingWithTheResult:
    def test_granted_saves_the_letter_and_grants_the_visa(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        student = make_student(abc, me, Stage.VISA)
        visa = waiting_visa(abc, student, "Deakin University")

        response = upload(abc, student, visa, result="granted")

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        [answer] = body["visas"]
        assert answer["status"] == "granted"
        assert body["stage"] == "visa"
        assert body["progress"]["is_done"] is True
        [document] = documents_of(abc, student)
        [file] = files_of(abc, document)
        assert answer["documents"] == [
            {
                "id": document.pk,
                "document_type": {"id": document.document_type_id, "name": "Visa letter"},
                "files": [{"id": file.pk, "file_name": "visa-grant.pdf"}],
                "verification": "pending",
            }
        ]
        assert document.visa_id == visa.pk
        assert document.application_id is None
        assert document.uploaded_by_name == "Bikash Thapa"
        assert fake_document_storage.files[file.storage_key].content == PDF_BYTES
        assert status_of(abc, visa) == VisaStatus.GRANTED

    def test_refused_sends_the_student_back_to_offer(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student, "Deakin University")

        response = upload(abc, student, visa, result="refused")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["visas"][0]["status"] == "refused"
        assert response.json()["stage"] == "offer"
        assert status_of(abc, visa) == VisaStatus.REFUSED
        assert chosen_of(abc, student) is None
        [moved] = stage_entries(abc, student)
        assert (moved.before, moved.after) == ("visa", "offer")
        assert moved.detail == "Automatic: Deakin University visa is Refused."
        [document] = documents_of(abc, student)
        assert document.visa_id == visa.pk

    def test_refused_with_no_offer_left_goes_back_to_counselling(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)
        with tenant_context(abc.consultancy):
            visa.application.status = "rejected"
            visa.application.save(update_fields=["status"])

        response = upload(abc, student, visa, result="refused")

        assert response.json()["stage"] == "counselling"

    def test_a_photo_letter_is_kept_with_its_real_type(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)
        photo = a_letter(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00rest", "scan.html")

        upload(abc, student, visa, file=photo)

        [document] = documents_of(abc, student)
        [file] = files_of(abc, document)
        assert (file.file_name, file.content_type) == ("scan.html.jpg", "image/jpeg")

    def test_the_visa_letter_type_is_made_once_and_reused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        with tenant_context(abc.consultancy):
            DocumentType.objects.create(name="VISA LETTER")
        visa = waiting_visa(abc, student)

        upload(abc, student, visa, result="granted")
        upload(abc, student, visa, result=None)

        with tenant_context(abc.consultancy):
            assert DocumentType.objects.filter(name__iexact="visa letter").count() == 1
        assert [d.document_type.name for d in documents_of(abc, student)] == ["VISA LETTER"] * 2

    def test_the_log_names_ids_never_the_file(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        upload(abc, student, visa, file=a_letter(name="sita-visa.pdf"))

        text = json_logs.text()
        assert f"Visa letter added: visa_id={visa.pk}" in text
        assert "result=granted" in text
        assert "sita" not in text


class TestTogetherOrNotAtAll:
    def test_storage_failing_changes_nothing(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)
        chosen = chosen_of(abc, student)
        fake_document_storage.is_down = True

        response = upload(abc, student, visa, result="refused")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json() == NOT_STORED
        assert status_of(abc, visa) == VisaStatus.APPLIED
        assert stored(abc, student).stage == Stage.VISA
        assert chosen_of(abc, student) == chosen
        assert documents_of(abc, student) == []
        assert history_of(abc, student) == []

    def test_the_documents_limit_changes_nothing(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)
        with tenant_context(abc.consultancy):
            passport = DocumentType.objects.get(name="Passport")
            Document.objects.bulk_create(
                Document(student=student, document_type=passport)
                for _ in range(MAX_DOCUMENTS_PER_STUDENT)
            )

        response = upload(abc, student, visa, result="granted")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": f"A Student can have at most {MAX_DOCUMENTS_PER_STUDENT} documents."
        }
        assert status_of(abc, visa) == VisaStatus.APPLIED

    def test_a_bad_file_leaves_the_status_alone(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        response = upload(abc, student, visa, file=a_letter(b"<html></html>", "grant.pdf"))

        assert response.json() == FILE_KIND
        assert status_of(abc, visa) == VisaStatus.APPLIED


class TestALetterLater:
    @pytest.mark.parametrize("decided", [VisaStatus.GRANTED, VisaStatus.REFUSED])
    def test_a_letter_with_no_result_keeps_the_status(
        self, abc: ConsultancyContext, decided: VisaStatus
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), decided)

        response = upload(abc, student, visa, result=None)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["visas"][0]["status"] == decided.value
        assert status_of(abc, visa) == decided
        [document] = documents_of(abc, student)
        assert document.visa_id == visa.pk
        assert history_of(abc, student) == []

    @pytest.mark.parametrize("decided", [VisaStatus.GRANTED, VisaStatus.REFUSED])
    def test_the_same_result_is_accepted(
        self, abc: ConsultancyContext, decided: VisaStatus
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), decided)

        response = upload(abc, student, visa, result=decided.value)

        assert response.status_code == status.HTTP_201_CREATED
        assert status_of(abc, visa) == decided

    @pytest.mark.parametrize(
        ("decided", "other", "word"),
        [
            (VisaStatus.GRANTED, "refused", "Granted"),
            (VisaStatus.REFUSED, "granted", "Refused"),
        ],
    )
    def test_another_result_is_refused(
        self, abc: ConsultancyContext, decided: VisaStatus, other: str, word: str
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student), decided)

        response = upload(abc, student, visa, result=other)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "result": [f"This visa is already {word}. To change it, set its status by hand."]
        }
        assert status_of(abc, visa) == decided
        assert documents_of(abc, student) == []


class TestByHandStillWorks:
    def test_the_status_changes_by_hand_with_no_letter(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        response = abc.client.patch(visa_url(student, visa), {"status": "granted"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert status_of(abc, visa) == VisaStatus.GRANTED
        assert documents_of(abc, student) == []


class TestWhoMayUpload:
    def test_an_admin_can_for_anyones_student(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, abc.add_staff(Role.COUNSELLOR, email="other@abc.test"))
        visa = waiting_visa(abc, student)
        abc.log_in_as(Role.ADMIN)

        assert upload(abc, student, visa).status_code == status.HTTP_201_CREATED

    def test_another_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, abc.add_staff(Role.COUNSELLOR, email="other@abc.test"))
        visa = waiting_visa(abc, student)
        abc.log_in_as(Role.COUNSELLOR)

        response = upload(abc, student, visa)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        assert status_of(abc, visa) == VisaStatus.APPLIED
        assert documents_of(abc, student) == []

    @pytest.mark.parametrize(
        ("role", "refusal"),
        [(Role.FRONT_DESK, COUNSELLOR_OR_ADMIN), (Role.TEACHER, NOT_FOR_TEACHERS)],
    )
    def test_front_desk_and_teachers_are_refused(
        self, abc: ConsultancyContext, role: Role, refusal: dict[str, str]
    ) -> None:
        student = make_student(abc, abc.add_staff(Role.COUNSELLOR, email="other@abc.test"))
        visa = waiting_visa(abc, student)
        abc.log_in_as(role)

        response = upload(abc, student, visa)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == refusal
        assert status_of(abc, visa) == VisaStatus.APPLIED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, abc.add_staff(Role.COUNSELLOR, email="other@abc.test"))
        visa = waiting_visa(abc, student)

        response = abc.new_client().post(
            letter_url(student, visa), {"result": "granted", "file": a_letter()}, format="multipart"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert status_of(abc, visa) == VisaStatus.APPLIED


class TestRefusedInput:
    def test_no_result_on_a_waiting_visa_is_refused(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        response = upload(abc, student, visa, result=None)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_RESULT
        assert status_of(abc, visa) == VisaStatus.APPLIED
        assert documents_of(abc, student) == []
        assert fake_document_storage.files == {}

    @pytest.mark.parametrize(
        "value",
        ["", "applied", "Granted", "GRANTED", "lodged", "x" * 10_000, *INJECTION_STRINGS],
    )
    def test_bad_results_are_refused(self, abc: ConsultancyContext, value: str) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        response = upload(abc, student, visa, result=value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_RESULT
        assert status_of(abc, visa) == VisaStatus.APPLIED

    def test_a_missing_file_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        response = abc.client.post(
            letter_url(student, visa), {"result": "granted"}, format="multipart"
        )

        assert response.json() == CHOOSE_FILE
        assert status_of(abc, visa) == VisaStatus.APPLIED

    @pytest.mark.parametrize(
        "content", [b"<html><script>alert(1)</script></html>", b"GIF89a....", b"PK\x03\x04zip"]
    )
    def test_files_that_are_not_a_pdf_or_photo_are_refused(
        self, abc: ConsultancyContext, content: bytes
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        response = upload(abc, student, visa, file=a_letter(content, "grant.pdf"))

        assert response.json() == FILE_KIND

    def test_a_file_over_the_limit_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        response = upload(abc, student, visa, file=a_letter(PDF_BYTES + b"0" * MAX_DOCUMENT_BYTES))

        assert response.json() == {"file": ["The file is too big. Upload at most 10 MB."]}
        assert status_of(abc, visa) == VisaStatus.APPLIED

    def test_two_files_are_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        response = upload(abc, student, visa, file=[a_letter(), a_letter(name="b.pdf")])

        assert response.json() == {"file": ["Send this only once."]}
        assert status_of(abc, visa) == VisaStatus.APPLIED

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        response = upload(abc, student, visa, document_type="1", verification="verified")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert documents_of(abc, student) == []
        assert status_of(abc, visa) == VisaStatus.APPLIED

    def test_json_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        response = abc.client.post(letter_url(student, visa), {"result": "granted"}, format="json")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_another_students_visa_is_not_found(self, abc: ConsultancyContext) -> None:
        me, student = counselled_student(abc, Stage.VISA)
        someone_else = make_student(abc, me, Stage.VISA, name="Ram Thapa")
        theirs = waiting_visa(abc, someone_else)

        response = upload(abc, student, theirs)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert status_of(abc, theirs) == VisaStatus.APPLIED
        assert documents_of(abc, student) == documents_of(abc, someone_else) == []

    def test_a_visa_that_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)

        assert upload(abc, student, 999_999).status_code == status.HTTP_404_NOT_FOUND

    def test_a_student_who_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert upload(abc, 999_999, 1).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        visa = waiting_visa(abc, student)

        response = getattr(abc.client, method)(letter_url(student, visa))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_the_platform_host_has_no_visa_letter_endpoint(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        student = make_student(abc)
        platform_site.log_in()

        response = platform_site.client.post(letter_url(student, 1), {}, format="multipart")

        assert response.status_code == status.HTTP_404_NOT_FOUND
