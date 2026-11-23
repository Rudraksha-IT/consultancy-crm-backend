"""Zip files on a Student's Documents (first live fixes, ticket 09; spec stories 72-81).

A zip is kept as it was sent: never unzipped, read inside or previewed. Opening one always
downloads it. Only a Student's Documents take zips; Offer Letters, visa letters, Course Files,
Document Examples and email attachments keep their own kinds.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django_tenants.utils import tenant_context
from rest_framework import status

from documents.files import MAX_DOCUMENT_BYTES
from documents.models import DocumentFile
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory
from tests.fakes import FakeDocumentStorage
from tests.integration.test_applications_integration import make_course
from tests.integration.test_communication_integration import outbox, set_contact_email
from tests.integration.test_course_files_integration import upload as upload_course_file
from tests.integration.test_document_examples_integration import add_example
from tests.integration.test_document_files_integration import add_files, file_link_url
from tests.integration.test_documents_integration import (
    a_file,
    files_of,
    make_document,
    own_student,
    stored_documents,
    type_named,
    upload,
)
from tests.integration.test_email_attachments_integration import (
    GONE,
    attachable_url,
    document_file,
    picked,
    send,
)
from tests.integration.test_offer_letters_integration import a_letter as an_offer_letter
from tests.integration.test_offer_letters_integration import add_letter, applied
from tests.integration.test_stage_and_counsellor_integration import make_student
from tests.integration.test_visa_letters_integration import a_letter as a_visa_letter
from tests.integration.test_visa_letters_integration import upload as upload_visa_letter
from tests.integration.test_visa_letters_integration import waiting_visa

ZIP_TYPE = "application/zip"
DOCUMENT_KINDS = {"file": ["Upload a PDF, JPG, PNG or ZIP file."]}
LETTER_KINDS = {"file": ["Upload a PDF, JPG or PNG file."]}
# A zip with nothing in it is only its end record.
EMPTY_ZIP = b"PK\x05\x06" + b"\x00" * 18


def zip_bytes(**files: bytes) -> bytes:
    """A real zip holding `files`."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in (files or {"transcript.pdf": b"%PDF-1.7 marks"}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def a_zip(content: bytes | None = None, name: str = "papers.zip") -> SimpleUploadedFile:
    """A zip as the browser sends it (its stated type is never believed)."""
    return SimpleUploadedFile(name, zip_bytes() if content is None else content, ZIP_TYPE)


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def only_file(office: ConsultancyContext, student: Student) -> DocumentFile:
    """The one file of the Student's one Document."""
    [document] = stored_documents(office, student)
    [file] = files_of(office, document)
    return file


class TestAddingAZip:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR])
    def test_a_zip_is_a_new_document_kept_as_sent(
        self,
        abc: ConsultancyContext,
        fake_document_storage: type[FakeDocumentStorage],
        role: Role,
    ) -> None:
        me = abc.log_in_as(role)
        student = make_student(abc, me if role == Role.COUNSELLOR else None)
        content = zip_bytes(**{"marks.pdf": b"%PDF-1.7 marks", "photo.jpg": b"\xff\xd8\xff"})

        response = upload(abc, student, file=a_zip(content))

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        file = only_file(abc, student)
        assert (file.file_name, file.content_type, file.size) == (
            "papers.zip",
            ZIP_TYPE,
            len(content),
        )
        assert fake_document_storage.files[file.storage_key].content == content

    def test_an_empty_zip_is_accepted(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        response = upload(abc, student, file=a_zip(EMPTY_ZIP))

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert only_file(abc, student).content_type == ZIP_TYPE

    def test_a_zip_with_another_name_keeps_it_and_ends_in_zip(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)

        upload(abc, student, file=a_zip(name="marks.pdf"))

        assert only_file(abc, student).file_name == "marks.pdf.zip"

    def test_a_zip_goes_beside_other_files_in_one_document(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        response = upload(abc, student, file=[a_file(), a_zip()])

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        [document] = stored_documents(abc, student)
        assert [f.content_type for f in files_of(abc, document)] == ["application/pdf", ZIP_TYPE]

    def test_a_zip_is_added_to_a_document(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = add_files(abc, student, document, a_zip())

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert files_of(abc, document)[-1].content_type == ZIP_TYPE

    def test_a_zip_linked_to_an_application(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        application = applied(abc, student, "Deakin")

        response = upload(abc, student, file=a_zip(), application=str(application.public_id))

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        [document] = stored_documents(abc, student)
        assert document.application_id == application.pk


class TestRefusedAsAZip:
    @pytest.mark.parametrize(
        "content",
        [
            b"just text named like a zip",
            b"PK",  # too short to be a zip
            b"PK\x01\x02 only a central record",
            b"\x1f\x8b\x08 a gzip",
            b"Rar!\x1a\x07\x00 a rar",
            b"7z\xbc\xaf\x27\x1c a 7z",
        ],
    )
    def test_a_file_only_named_zip_is_refused_by_its_bytes(
        self, abc: ConsultancyContext, content: bytes
    ) -> None:
        student = own_student(abc)

        response = upload(abc, student, file=a_zip(content))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == DOCUMENT_KINDS
        assert stored_documents(abc, student) == []

    def test_a_zip_over_10_mb_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        too_big = EMPTY_ZIP + b"0" * (MAX_DOCUMENT_BYTES - len(EMPTY_ZIP) + 1)

        response = upload(abc, student, file=a_zip(too_big))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"file": ["The file is too big. Upload at most 10 MB."]}

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, None)
        abc.log_in_as(Role.TEACHER)

        response = upload(abc, student, file=a_zip())

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_another_offices_student_is_not_found(
        self, abc: ConsultancyContext, make_consultancy: ConsultancyFactory
    ) -> None:
        other = make_consultancy("XYZ Education", "xyzedu")
        theirs = make_student(other, None)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            f"/api/students/{theirs.public_id}/documents/",
            {"document_type": str(type_named(abc, "Passport").public_id), "file": a_zip()},
            format="multipart",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        with tenant_context(other.consultancy):
            assert not DocumentFile.objects.exists()


class TestOpeningAZip:
    @pytest.mark.parametrize("query", [{}, {"action": "open"}, {"action": "download"}])
    def test_a_zip_always_downloads(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        student = own_student(abc)
        upload(abc, student, file=a_zip())
        file = only_file(abc, student)

        link = abc.client.get(file_link_url(student, file.document, file), query).json()

        assert link["url"] == (
            f"https://files.test/{file.storage_key}?disposition=attachment"
            "&name=papers.zip&type=application%2Fzip&expires=300"
        )

    def test_a_pdf_still_opens_in_the_browser(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        upload(abc, student)
        file = only_file(abc, student)

        link = abc.client.get(file_link_url(student, file.document, file)).json()

        assert "disposition=inline" in link["url"]

    def test_a_zip_in_the_bin_always_downloads(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        upload(abc, student, file=[a_file(), a_zip()])
        [document] = stored_documents(abc, student)
        zipped = files_of(abc, document)[-1]
        removed = abc.client.post(
            f"/api/students/{student.public_id}/documents/{document.public_id}"
            f"/files/{zipped.public_id}/bin/",
            {"reason": "Sent by mistake"},
            format="json",
        )
        assert removed.status_code == status.HTTP_200_OK, removed.json()
        abc.log_in_as(Role.ADMIN)

        link = abc.client.get(f"/api/documents/bin/files/{zipped.public_id}/link/").json()

        assert "disposition=attachment" in link["url"]


class TestZipsOnlyOnStudentDocuments:
    def test_an_offer_letter_zip_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        application = applied(abc, student, "Deakin")

        response = add_letter(
            abc,
            student,
            application=str(application.public_id),
            file=an_offer_letter(zip_bytes(), "offer.zip"),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == LETTER_KINDS

    def test_a_visa_letter_zip_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        visa = waiting_visa(abc, student)

        response = upload_visa_letter(abc, student, visa, file=a_visa_letter(zip_bytes()))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == LETTER_KINDS

    def test_a_document_example_zip_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add_example(abc.client, type_named(abc, "Passport"), a_zip())

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file" in response.json()

    def test_a_course_file_zip_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc)

        response = upload_course_file(abc.client, course, file=a_zip())

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file" in response.json()


class TestNeverAttachedToAnEmail:
    def test_a_zip_is_not_offered_or_sent(self, abc: ConsultancyContext) -> None:
        switch_modules(abc.consultancy, {Module.COMMUNICATION})
        set_contact_email(abc)
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING, email="sita@example.com")
        passport = document_file(abc, student)
        upload(
            abc, student, file=a_zip(), document_type=str(type_named(abc, "Citizenship").public_id)
        )
        zipped = next(
            f
            for d in stored_documents(abc, student)
            for f in files_of(abc, d)
            if f.content_type == ZIP_TYPE
        )

        offered = abc.client.get(attachable_url(student)).json()["documents"]
        response = send(abc, student, picked(zipped))

        assert [row["id"] for row in offered] == [str(passport.public_id)]
        assert response.json() == GONE
        assert outbox() == []
