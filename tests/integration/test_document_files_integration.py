"""A Document holds one or more files (spec 117; issue 78).

Uploading several files at once makes one Document; more can be added to it later; each file
opens through its own short-lived link; Verification stays one judgment for the whole Document,
and a new file puts a Verified Document back to Pending.
"""

from __future__ import annotations

from typing import IO, TYPE_CHECKING, Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django_tenants.utils import tenant_context
from rest_framework import status

from documents.exceptions import DocumentStorageError
from documents.files import MAX_DOCUMENT_BYTES, MAX_FILES_AT_ONCE, MAX_UPLOAD_BYTES
from documents.models import Document, DocumentFile
from documents.serializers import DocumentFilesField, NewFilesSerializer
from documents.services import MAX_FILES_PER_DOCUMENT
from documents.verification import Verification
from staff.models import Role
from students.models import Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs
from tests.fakes import FakeDocumentStorage
from tests.integration.test_documents_integration import (
    COUNSELLOR_OR_ADMIN,
    JPEG_BYTES,
    PDF_BYTES,
    PNG_BYTES,
    a_file,
    documents_url,
    files_of,
    make_document,
    own_student,
    stored_documents,
    upload,
)
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    NOT_FOR_TEACHERS,
    NOT_YOUR_STUDENT,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

CHOOSE_FILE = {"file": ["Choose a file to upload."]}
FILE_KIND = {"file": ["Upload a PDF, JPG or PNG file."]}
TOO_MANY_AT_ONCE = {"file": [f"Upload at most {MAX_FILES_AT_ONCE} files at once."]}
TOO_BIG_TOGETHER = {"file": ["The files are too big together. Upload at most 20 MB at once."]}
REJECTED_NEEDS_NEW_COPY = {
    "detail": "This document was rejected. Upload a new copy instead of adding a file."
}
NOT_NEWEST = {"detail": "A newer copy of this document has been uploaded."}
TOO_MANY_FILES = {"detail": f"A document can have at most {MAX_FILES_PER_DOCUMENT} files."}
# Almost the largest one file may be, so two of them are over the limit for one upload.
NEARLY_10_MB = PDF_BYTES + b"0" * (MAX_DOCUMENT_BYTES - len(PDF_BYTES))


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def files_url(student: Student | int | str, document: Document | int | str) -> str:
    """Where files are added to one of a Student's Documents."""
    pk = student if isinstance(student, int | str) else student.public_id
    document_pk = document if isinstance(document, int | str) else document.public_id
    return f"/api/students/{pk}/documents/{document_pk}/files/"


def file_link_url(
    student: Student | int | str, document: Document | int | str, file: DocumentFile | int | str
) -> str:
    """Where a link to one file of a Student's Document is asked for."""
    file_pk = file if isinstance(file, int | str) else file.public_id
    return f"{files_url(student, document)}{file_pk}/link/"


def add_files(
    office: ConsultancyContext,
    student: Student | int | str,
    document: Document | int | str,
    *files: object,
    **fields: object,
) -> _MonkeyPatchedResponse:
    """POST files to a Document as a form (one page of a passport unless `files` say otherwise)."""
    sent = list(files) or [a_file(name="page-2.pdf")]
    body: dict[str, object] = {"file": sent, **fields}
    return office.client.post(files_url(student, document), body, format="multipart")


def reloaded(office: ConsultancyContext, document: Document) -> Document:
    """The Document as stored now."""
    with tenant_context(office.consultancy):
        return Document.objects.get(pk=document.pk)


def shown_document(body: dict[str, Any], type_name: str = "Passport") -> dict[str, Any]:
    """The checklist item's Document, for the type named."""
    items = body["items"]
    assert isinstance(items, list)
    [item] = [one for one in items if one["document_type"]["name"] == type_name]
    document = item["document"]
    assert isinstance(document, dict)
    return document


class TestUploadingSeveralFiles:
    def test_several_files_at_once_make_one_document(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        student = own_student(abc)

        response = upload(
            abc, student, file=[a_file(name="front.jpg", content=JPEG_BYTES), a_file(name="back")]
        )

        assert response.status_code == status.HTTP_201_CREATED
        [document] = stored_documents(abc, student)
        front, back = files_of(abc, document)
        assert (front.file_name, front.content_type) == ("front.jpg", "image/jpeg")
        assert (back.file_name, back.content_type) == ("back.pdf", "application/pdf")
        assert fake_document_storage.files[front.storage_key].content == JPEG_BYTES
        assert fake_document_storage.files[back.storage_key].content == PDF_BYTES
        shown = shown_document(response.json())
        assert shown["verification"] == "pending"
        assert [file["file_name"] for file in shown["files"]] == ["front.jpg", "back.pdf"]

    def test_each_file_is_shown_with_its_details(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        student = make_student(abc, me)

        body = upload(abc, student).json()

        [document] = stored_documents(abc, student)
        [file] = files_of(abc, document)
        assert shown_document(body)["files"] == [
            {
                "id": str(file.public_id),
                "file_name": "passport.pdf",
                "content_type": "application/pdf",
                "size": len(PDF_BYTES),
                "uploaded_at": file.uploaded_at.isoformat(),
                "uploaded_by": {"id": me.pk, "name": "Bikash Thapa"},
            }
        ]

    def test_no_storage_key_is_ever_answered(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        response = upload(abc, student, file=[a_file(), a_file()])

        assert "storage_key" not in response.content.decode()
        assert "abcedu/documents" not in response.content.decode()

    def test_each_file_is_kept_under_the_consultancys_own_prefix(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)

        upload(abc, student, file=[a_file(), a_file(PNG_BYTES, "b.png")])

        [document] = stored_documents(abc, student)
        keys = [file.storage_key for file in files_of(abc, document)]
        assert all(key.startswith(f"abcedu/documents/{student.public_id}/") for key in keys)
        assert len(set(keys)) == len(keys)

    def test_the_most_files_at_once_are_accepted(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        files = [a_file(name=f"page-{number}.pdf") for number in range(MAX_FILES_AT_ONCE)]

        assert upload(abc, student, file=files).status_code == status.HTTP_201_CREATED
        [document] = stored_documents(abc, student)
        assert len(files_of(abc, document)) == MAX_FILES_AT_ONCE

    def test_one_more_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        student = own_student(abc)
        files = [a_file() for _ in range(MAX_FILES_AT_ONCE + 1)]

        response = upload(abc, student, file=files)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TOO_MANY_AT_ONCE
        assert stored_documents(abc, student) == []
        assert fake_document_storage.files == {}

    def test_far_too_many_are_refused_safely(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        files = [a_file() for _ in range(MAX_FILES_AT_ONCE * 3)]

        response = upload(abc, student, file=files)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Too much was sent at once. Send less and try again."}
        assert stored_documents(abc, student) == []

    def test_too_big_together_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        files = [a_file(NEARLY_10_MB), a_file(NEARLY_10_MB), a_file()]

        response = upload(abc, student, file=files)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TOO_BIG_TOGETHER
        assert stored_documents(abc, student) == []

    def test_exactly_the_limit_together_is_accepted(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        assert MAX_UPLOAD_BYTES == 2 * MAX_DOCUMENT_BYTES

        response = upload(abc, student, file=[a_file(NEARLY_10_MB), a_file(NEARLY_10_MB)])

        assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize(
        "bad",
        [
            b"<html><script>alert(1)</script></html>",
            b"",
            b"GIF89a\x01\x00",
        ],
    )
    def test_one_bad_file_among_several_refuses_them_all(
        self,
        abc: ConsultancyContext,
        fake_document_storage: type[FakeDocumentStorage],
        bad: bytes,
    ) -> None:
        student = own_student(abc)

        response = upload(abc, student, file=[a_file(), a_file(bad, "page-2.pdf")])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() in (FILE_KIND, CHOOSE_FILE)
        assert stored_documents(abc, student) == []
        assert fake_document_storage.files == {}

    def test_text_among_the_files_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        response = upload(abc, student, file=[a_file(), "%PDF-1.7 not a file"])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_FILE

    def test_storage_failing_half_way_leaves_nothing_behind(
        self,
        abc: ConsultancyContext,
        fake_document_storage: type[FakeDocumentStorage],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        student = own_student(abc)
        save = FakeDocumentStorage.save
        calls = {"count": 0}

        def fail_on_the_second(
            self: FakeDocumentStorage, key: str, content: IO[bytes], content_type: str
        ) -> None:
            calls["count"] += 1
            if calls["count"] == 2:  # the second file
                raise DocumentStorageError
            save(self, key, content, content_type)

        monkeypatch.setattr(FakeDocumentStorage, "save", fail_on_the_second)

        response = upload(abc, student, file=[a_file(), a_file()])

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert stored_documents(abc, student) == []
        assert fake_document_storage.files == {}

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        student = own_student(abc)

        upload(abc, student, file=[a_file(name="Sita front.pdf"), a_file(name="Sita back.pdf")])

        [entry] = json_logs.with_message("Document uploaded")
        assert "files=2" in entry["message"]
        assert "Sita" not in json_logs.text()


class TestAddingFiles:
    def test_the_assigned_counsellor_adds_a_page_to_a_pending_document(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        student = own_student(abc)
        upload(abc, student)
        [document] = stored_documents(abc, student)

        response = add_files(abc, student, document)

        assert response.status_code == status.HTTP_201_CREATED
        first, added = files_of(abc, document)
        assert (first.file_name, added.file_name) == ("passport.pdf", "page-2.pdf")
        assert fake_document_storage.files[added.storage_key].content == PDF_BYTES
        assert reloaded(abc, document).verification == Verification.PENDING
        shown = shown_document(response.json())
        assert [file["file_name"] for file in shown["files"]] == ["passport.pdf", "page-2.pdf"]
        assert stored_documents(abc, student) == [document]

    def test_several_at_once_are_added_in_order(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        add_files(abc, student, document, a_file(name="2.pdf"), a_file(JPEG_BYTES, "3.jpg"))

        assert [file.file_name for file in files_of(abc, document)] == [
            "passport.pdf",
            "2.pdf",
            "3.jpg",
        ]

    def test_a_verified_document_needs_checking_again(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student, verification=Verification.VERIFIED)

        body = add_files(abc, student, document).json()

        assert reloaded(abc, document).verification == Verification.PENDING
        assert shown_document(body)["verification"] == "pending"
        [item] = [one for one in body["items"] if one["document_type"]["name"] == "Passport"]
        assert item["state"] == "waiting"

    def test_a_rejected_document_needs_a_new_copy_instead(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student, verification=Verification.REJECTED)

        response = add_files(abc, student, document)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == REJECTED_NEEDS_NEW_COPY
        assert len(files_of(abc, document)) == 1
        assert fake_document_storage.files == {}

    def test_an_older_copy_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        older = make_document(abc, student)
        make_document(abc, student)

        response = add_files(abc, student, older)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NOT_NEWEST

    def test_there_is_a_limit_per_document(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student, files=MAX_FILES_PER_DOCUMENT - 1)

        assert add_files(abc, student, document).status_code == status.HTTP_201_CREATED
        response = add_files(abc, student, document)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TOO_MANY_FILES
        assert len(files_of(abc, document)) == MAX_FILES_PER_DOCUMENT

    def test_adding_more_than_fit_adds_none(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student, files=MAX_FILES_PER_DOCUMENT - 1)

        response = add_files(abc, student, document, a_file(), a_file())

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert len(files_of(abc, document)) == MAX_FILES_PER_DOCUMENT - 1

    def test_an_admin_adds_to_any_students_document(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"))
        document = make_document(abc, student)
        abc.log_in_as(Role.ADMIN)

        assert add_files(abc, student, document).status_code == status.HTTP_201_CREATED

    def test_who_added_each_file_is_kept(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"))
        document = make_document(abc, student)
        admin = named_staff(abc, "Maya Admin", Role.ADMIN)
        log_in(abc, admin)

        body = add_files(abc, student, document).json()

        assert shown_document(body)["files"][1]["uploaded_by"] == {
            "id": admin.pk,
            "name": "Maya Admin",
        }

    @pytest.mark.parametrize(
        ("role", "refusal"),
        [
            (Role.COUNSELLOR, NOT_YOUR_STUDENT),
            (Role.FRONT_DESK, COUNSELLOR_OR_ADMIN),
            (Role.TEACHER, NOT_FOR_TEACHERS),
        ],
    )
    def test_others_are_refused(
        self, abc: ConsultancyContext, role: Role, refusal: dict[str, str]
    ) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"))
        document = make_document(abc, student, verification=Verification.VERIFIED)
        abc.log_in_as(role)

        response = add_files(abc, student, document)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == refusal
        assert len(files_of(abc, document)) == 1
        assert reloaded(abc, document).verification == Verification.VERIFIED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        document = make_document(abc, student)

        assert add_files(abc, student, document).status_code == status.HTTP_401_UNAUTHORIZED

    def test_another_students_document_is_not_found(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        other = make_student(abc, name="Ram Shrestha")
        document = make_document(abc, other)

        response = add_files(abc, student, document)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert len(files_of(abc, document)) == 1

    def test_an_unknown_document_is_not_found(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        assert add_files(abc, student, 999_999).status_code == status.HTTP_404_NOT_FOUND

    def test_storage_failing_adds_nothing(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student, verification=Verification.VERIFIED)
        fake_document_storage.is_down = True

        response = add_files(abc, student, document)

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json() == {"detail": "We couldn't store the file. Try again."}
        assert len(files_of(abc, document)) == 1
        assert reloaded(abc, document).verification == Verification.VERIFIED

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        add_files(abc, student, document, a_file(name="Sita page 2.pdf"))

        [entry] = json_logs.with_message("Document files added")
        assert f"document_id={document.pk}" in entry["message"]
        assert "files=1" in entry["message"]
        assert "Sita" not in json_logs.text()


class TestRefusedFiles:
    def test_no_file_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = abc.client.post(files_url(student, document), {}, format="multipart")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_FILE

    @pytest.mark.parametrize(
        "content",
        [
            b"<html><script>alert(1)</script></html>",
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            b"MZ\x90\x00 an exe",
        ],
    )
    def test_anything_but_a_pdf_or_photo_is_refused_by_its_bytes(
        self, abc: ConsultancyContext, content: bytes
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = add_files(abc, student, document, a_file(content, "page.pdf"))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == FILE_KIND
        assert len(files_of(abc, document)) == 1

    def test_a_file_over_10_mb_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)
        too_big = NEARLY_10_MB + b"0"

        response = add_files(abc, student, document, a_file(too_big))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"file": ["The file is too big. Upload at most 10 MB."]}

    def test_too_many_at_once_are_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)
        files = [a_file() for _ in range(MAX_FILES_AT_ONCE + 1)]

        response = add_files(abc, student, document, *files)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TOO_MANY_AT_ONCE

    def test_too_big_together_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        files = [a_file(NEARLY_10_MB), a_file(NEARLY_10_MB), a_file()]

        response = add_files(abc, student, document, *files)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TOO_BIG_TOGETHER

    @pytest.mark.parametrize(
        "field", [{"document_type": "1"}, {"verification": "verified"}, {"visa": "1"}]
    )
    def test_an_unknown_field_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, field: dict[str, str]
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = add_files(abc, student, document, **field)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert len(files_of(abc, document)) == 1

    def test_a_json_body_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = abc.client.post(
            files_url(student, document), {"file": "JVBERi0="}, format="json"
        )

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("name", INJECTION_STRINGS)
    def test_a_file_name_is_kept_as_text_without_unsafe_characters(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        add_files(abc, student, document, a_file(PDF_BYTES, f"{name}.pdf"))

        added = files_of(abc, document)[-1]
        assert not set(added.file_name) & set('<>"/\\')
        assert added.file_name.endswith(".pdf")

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_adding_answers_only_post(self, abc: ConsultancyContext, method: str) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = getattr(abc.client, method)(files_url(student, document))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestOpeningEachFile:
    def test_each_file_has_its_own_link(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        upload(abc, student, file=[a_file(name="front.pdf"), a_file(JPEG_BYTES, "back.jpg")])
        [document] = stored_documents(abc, student)
        front, back = files_of(abc, document)

        front_link = abc.client.get(file_link_url(student, document, front)).json()
        back_link = abc.client.get(
            file_link_url(student, document, back), {"action": "download"}
        ).json()

        assert front_link == {
            "url": f"https://files.test/{front.storage_key}?disposition=inline"
            "&name=front.pdf&type=application%2Fpdf&expires=300",
            "expires_in": 300,
        }
        assert back_link["url"] == (
            f"https://files.test/{back.storage_key}?disposition=attachment"
            "&name=back.jpg&type=image%2Fjpeg&expires=300"
        )

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_anyone_who_sees_the_student_opens_each_file(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"))
        document = make_document(abc, student, files=2)
        abc.log_in_as(role)

        for file in files_of(abc, document):
            response = abc.client.get(file_link_url(student, document, file))
            assert response.status_code == status.HTTP_200_OK

    def test_teachers_are_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        document = make_document(abc, student)
        [file] = files_of(abc, document)
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(file_link_url(student, document, file))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_a_file_of_another_document_is_not_found(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        mine = make_document(abc, student)
        other = make_document(abc, student, "Citizenship")
        [others_file] = files_of(abc, other)

        response = abc.client.get(file_link_url(student, mine, others_file))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_file_of_another_students_document_is_not_found(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        other = make_student(abc, name="Ram Shrestha")
        document = make_document(abc, other)
        [file] = files_of(abc, document)

        response = abc.client.get(file_link_url(student, document, file))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_unknown_file_is_not_found(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = abc.client.get(file_link_url(student, document, 999_999))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_old_document_link_is_gone(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = abc.client.get(f"{documents_url(student)}{document.public_id}/link/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("query", [{"action": "delete"}, {"key": "x"}])
    def test_bad_query_parameters_are_refused(
        self, abc: ConsultancyContext, query: dict[str, str]
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)
        [file] = files_of(abc, document)

        response = abc.client.get(file_link_url(student, document, file), query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_a_link_answers_only_get(self, abc: ConsultancyContext, method: str) -> None:
        student = own_student(abc)
        document = make_document(abc, student)
        [file] = files_of(abc, document)

        response = getattr(abc.client, method)(file_link_url(student, document, file))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_issuing_a_link_is_logged_without_the_link(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)
        [file] = files_of(abc, document)

        abc.client.get(file_link_url(student, document, file))

        [entry] = json_logs.with_message("Document link issued")
        assert f"document_id={document.pk}" in entry["message"]
        assert f"file_id={file.pk}" in entry["message"]
        assert "files.test" not in json_logs.text()


class TestOneJudgmentForTheWholeDocument:
    def test_checking_covers_every_file(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        upload(abc, student, file=[a_file(), a_file()])
        [document] = stored_documents(abc, student)

        response = abc.client.post(
            f"{documents_url(student)}{document.public_id}/verification/",
            {"verification": "verified"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        shown = shown_document(response.json())
        assert shown["verification"] == "verified"
        assert len(shown["files"]) == 2

    def test_a_verified_document_back_to_pending_can_be_checked_again(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student, verification=Verification.VERIFIED)
        add_files(abc, student, document)

        response = abc.client.post(
            f"{documents_url(student)}{document.public_id}/verification/",
            {"verification": "rejected", "reason": "Page 2 is blurred."},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert reloaded(abc, document).verification == Verification.REJECTED


class TestExistingDocumentsBecomeOneFile:
    """The migration that moves each Document's file into its own row (issue 78)."""

    def test_each_document_keeps_its_file(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        before = ("documents", "0005_platform_admin_actor")
        after = ("documents", "0006_document_files")
        with tenant_context(abc.consultancy):
            executor = MigrationExecutor(connection)
            executor.migrate([before])
            old_apps = executor.loader.project_state(before).apps
            old_document = old_apps.get_model("documents", "Document")
            passport = old_apps.get_model("documents", "DocumentType").objects.get(name="Passport")
            kept = old_document.objects.create(
                student_id=student.pk,
                document_type=passport,
                file_name="sita-passport.pdf",
                content_type="application/pdf",
                size=1234,
                storage_key="abcedu/documents/1/old.pdf",
                verification="verified",
            )
            with connection.cursor() as cursor:  # check the rows now, so tables can be altered
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            executor = MigrationExecutor(connection)
            executor.migrate([after])
            executor.loader.build_graph()
            executor.migrate(executor.loader.graph.leaf_nodes())

            [file] = DocumentFile.objects.filter(document_id=kept.pk)
            document = Document.objects.get(pk=kept.pk)

        assert (file.file_name, file.content_type, file.size, file.storage_key) == (
            "sita-passport.pdf",
            "application/pdf",
            1234,
            "abcedu/documents/1/old.pdf",
        )
        assert file.uploaded_at == document.uploaded_at
        assert document.verification == Verification.VERIFIED


class TestFilesField:
    def test_an_empty_list_in_a_plain_object_is_refused(self, abc: ConsultancyContext) -> None:
        with tenant_context(abc.consultancy):
            serializer = NewFilesSerializer(data={"file": []})

            assert not serializer.is_valid()
            assert serializer.errors == CHOOSE_FILE

    def test_one_file_in_a_plain_object_is_accepted(self, abc: ConsultancyContext) -> None:
        with tenant_context(abc.consultancy):
            serializer = NewFilesSerializer(data={"file": a_file()})

            assert serializer.is_valid(), serializer.errors
            [file] = serializer.to_new_files()
            assert file.file_name == "passport.pdf"
            DocumentFilesField().to_representation([])  # never shown, returns nothing
