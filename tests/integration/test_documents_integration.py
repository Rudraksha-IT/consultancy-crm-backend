"""A Student's Documents: upload, checklist and private links (spec 64-65, 67, 70; issue 16)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django_tenants.utils import tenant_context
from rest_framework import status

from documents.files import MAX_DOCUMENT_BYTES
from documents.models import Document, DocumentFile, DocumentType
from documents.serializers import DocumentTypeField, DocumentUploadSerializer
from documents.services import MAX_DOCUMENTS_PER_STUDENT
from documents.verification import Verification
from staff.models import Role
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.fakes import FakeDocumentStorage
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

PDF_BYTES = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00rest-of-photo"
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRrest"
DEFAULT_TYPES = [
    "Passport",
    "Citizenship",
    "SEE Marksheet",
    "+2 Transcript",
    "IELTS/PTE Score Card",
    "Bank Statement",
    "Sponsor Letter",
    "Statement of Purpose",
]
CHOOSE_TYPE = {"document_type": ["Choose a document type from the list."]}
CHOOSE_FILE = {"file": ["Choose a file to upload."]}
FILE_KIND = {"file": ["Upload a PDF, JPG or PNG file."]}
COUNSELLOR_OR_ADMIN = {"detail": "Only the Student's counsellor or an Admin can do this."}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def documents_url(student: Student | int) -> str:
    """A Student's checklist, where Documents are uploaded too."""
    pk = student if isinstance(student, int) else student.pk
    return f"/api/students/{pk}/documents/"


def link_url(office: ConsultancyContext, student: Student | int, document: Document | int) -> str:
    """Where a link to the first file of one of a Student's Documents is asked for."""
    pk = student if isinstance(student, int) else student.pk
    document_pk = document if isinstance(document, int) else document.pk
    first = files_of(office, document)[0].pk if isinstance(document, Document) else 1
    return f"/api/students/{pk}/documents/{document_pk}/files/{first}/link/"


def type_named(office: ConsultancyContext, name: str) -> DocumentType:
    """One of the office's Document Types."""
    with tenant_context(office.consultancy):
        return DocumentType.objects.get(name=name)


def a_file(content: bytes = PDF_BYTES, name: str = "passport.pdf") -> SimpleUploadedFile:
    """A file as the browser would send it (its stated type is never believed)."""
    return SimpleUploadedFile(name, content, content_type="application/pdf")


def upload(
    office: ConsultancyContext, student: Student | int, **fields: object
) -> _MonkeyPatchedResponse:
    """POST a Document as a form, with a Passport PDF unless `fields` say otherwise."""
    body: dict[str, object] = {
        "document_type": str(type_named(office, "Passport").pk),
        "file": a_file(),
        **fields,
    }
    return office.client.post(documents_url(student), body, format="multipart")


def checklist(office: ConsultancyContext, student: Student | int) -> _MonkeyPatchedResponse:
    """GET a Student's checklist."""
    return office.client.get(documents_url(student))


def stored_documents(office: ConsultancyContext, student: Student) -> list[Document]:
    """The Student's stored Documents, oldest first."""
    with tenant_context(office.consultancy):
        return list(Document.objects.filter(student=student).order_by("pk"))


def make_document(
    office: ConsultancyContext,
    student: Student,
    type_name: str = "Passport",
    verification: Verification = Verification.PENDING,
    files: int = 1,
) -> Document:
    """Store a Document directly with `files` files, the first named after its type.

    The files aren't in storage.
    """
    with tenant_context(office.consultancy):
        document = Document.objects.create(
            student=student,
            document_type=DocumentType.objects.get(name=type_name),
            verification=verification,
        )
        add_stored_files(office, document, files, first_name=f"{type_name.lower()}.pdf")
        return document


def add_stored_files(
    office: ConsultancyContext, document: Document, count: int = 1, first_name: str = "page.pdf"
) -> list[DocumentFile]:
    """Store `count` PDF files of a Document directly (not in storage); call in its schema."""
    prefix = f"{office.consultancy.schema_name}/documents/{document.student_id}/"
    start = DocumentFile.objects.count()
    return [
        DocumentFile.objects.create(
            document=document,
            file_name=first_name if number == 0 else f"page-{number + 1}.pdf",
            content_type="application/pdf",
            size=len(PDF_BYTES),
            storage_key=f"{prefix}f{start + number}.pdf",
        )
        for number in range(count)
    ]


def files_of(office: ConsultancyContext, document: Document) -> list[DocumentFile]:
    """The Document's files, oldest first."""
    with tenant_context(office.consultancy):
        return list(DocumentFile.objects.filter(document_id=document.pk).order_by("pk"))


def own_student(office: ConsultancyContext, stage: Stage = Stage.COUNSELLING) -> Student:
    """Log in as a Counsellor and give them a Student."""
    me = office.log_in_as(Role.COUNSELLOR)
    return make_student(office, me, stage)


def item_states(body: dict[str, object]) -> list[tuple[str, str]]:
    """Each checklist item's type name and state."""
    items = body["items"]
    assert isinstance(items, list)
    return [(item["document_type"]["name"], item["state"]) for item in items]


class TestChecklist:
    def test_a_new_student_needs_every_default_type(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        response = checklist(abc, student)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert item_states(body) == [(name, "not_received") for name in DEFAULT_TYPES]
        assert (body["done"], body["total"], body["can_change"]) == (0, 8, True)
        assert body["items"][0] == {
            "document_type": {"id": type_named(abc, "Passport").pk, "name": "Passport"},
            "reason": "",
            "is_required": True,
            "added_for_student": False,
            "group": "applying",
            "state": "not_received",
            "document": None,
            "in_bin": [],
            "instruction": "",
            "examples": [],
        }
        assert body["asked"] == []

    def test_each_received_document_shows_its_state(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        make_document(abc, student, "Passport", Verification.VERIFIED)
        make_document(abc, student, "Citizenship", Verification.PENDING)
        make_document(abc, student, "SEE Marksheet", Verification.REJECTED)

        body = checklist(abc, student).json()

        assert item_states(body)[:4] == [
            ("Passport", "verified"),
            ("Citizenship", "waiting"),
            ("SEE Marksheet", "rejected"),
            ("+2 Transcript", "not_received"),
        ]
        assert (body["done"], body["total"]) == (2, 8)

    def test_an_item_shows_its_newest_document(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        make_document(abc, student, "Passport", Verification.REJECTED)
        newest = make_document(abc, student, "Passport")

        [passport] = checklist(abc, student).json()["items"][:1]

        assert passport["state"] == "waiting"
        [file] = files_of(abc, newest)
        assert passport["document"] == {
            "id": newest.pk,
            "files": [
                {
                    "id": file.pk,
                    "file_name": "passport.pdf",
                    "content_type": "application/pdf",
                    "size": len(PDF_BYTES),
                    "uploaded_at": file.uploaded_at.isoformat(),
                    "uploaded_by": None,
                }
            ],
            "binned_files": [],
            "verification": "pending",
            "rejection_reason": "",
            "link": None,
            "uploaded_at": newest.uploaded_at.isoformat(),
            "uploaded_by": None,
        }

    def test_a_type_no_longer_needed_by_everyone_shows_only_once_received(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        with tenant_context(abc.consultancy):
            DocumentType.objects.filter(name__in=["Sponsor Letter", "Bank Statement"]).update(
                needed_by_everyone=False
            )
        make_document(abc, student, "Sponsor Letter")

        body = checklist(abc, student).json()

        assert item_states(body)[-1] == ("Sponsor Letter", "waiting")
        assert body["items"][-1]["is_required"] is False
        assert "Bank Statement" not in [name for name, _ in item_states(body)]
        assert (body["done"], body["total"]) == (0, 6)

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_every_student_role_sees_any_checklist(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"))
        abc.log_in_as(role)

        response = checklist(abc, student)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["can_change"] is (role == Role.ADMIN)

    def test_teachers_are_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = checklist(abc, student)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert checklist(abc, 999_999).status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        assert checklist(abc, student).status_code == status.HTTP_401_UNAUTHORIZED

    def test_the_profile_shows_the_progress(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        make_document(abc, student, "Passport")

        body = abc.client.get(f"/api/students/{student.pk}/").json()

        assert body["documents"] == {"done": 1, "total": 8}
        assert body["can_change_documents"] is True


class TestUploading:
    def test_the_assigned_counsellor_uploads_one_as_pending(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        student = own_student(abc)

        response = upload(abc, student)

        assert response.status_code == status.HTTP_201_CREATED
        [document] = stored_documents(abc, student)
        assert document.verification == Verification.PENDING
        [file] = files_of(abc, document)
        assert (file.file_name, file.content_type, file.size) == (
            "passport.pdf",
            "application/pdf",
            len(PDF_BYTES),
        )
        stored = fake_document_storage.files[file.storage_key]
        assert (stored.content, stored.content_type) == (PDF_BYTES, "application/pdf")
        body = response.json()
        assert item_states(body)[0] == ("Passport", "waiting")
        assert body["items"][0]["document"]["id"] == document.pk
        assert body["done"] == 1

    def test_the_file_is_kept_under_the_consultancys_own_prefix(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)

        upload(abc, student)

        [document] = stored_documents(abc, student)
        [file] = files_of(abc, document)
        assert file.storage_key.startswith(f"abcedu/documents/{student.pk}/")
        assert file.storage_key.endswith(".pdf")
        assert "passport" not in file.storage_key

    def test_who_uploaded_it_is_kept(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        student = make_student(abc, me)

        body = upload(abc, student).json()

        assert body["items"][0]["document"]["uploaded_by"] == {"id": me.pk, "name": "Bikash Thapa"}

    @pytest.mark.parametrize(
        ("content", "content_type"), [(JPEG_BYTES, "image/jpeg"), (PNG_BYTES, "image/png")]
    )
    def test_photos_are_accepted(
        self, abc: ConsultancyContext, content: bytes, content_type: str
    ) -> None:
        student = own_student(abc)

        response = upload(abc, student, file=a_file(content, "scan"))

        assert response.status_code == status.HTTP_201_CREATED
        [document] = stored_documents(abc, student)
        assert files_of(abc, document)[0].content_type == content_type

    def test_a_new_copy_replaces_the_old_one_on_the_checklist(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        make_document(abc, student, "Passport", Verification.REJECTED)

        body = upload(abc, student).json()

        assert item_states(body)[0] == ("Passport", "waiting")
        assert len(stored_documents(abc, student)) == 2

    def test_an_admin_uploads_to_any_student(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"))
        abc.log_in_as(Role.ADMIN)

        assert upload(abc, student).status_code == status.HTTP_201_CREATED

    def test_a_type_not_needed_by_everyone_can_be_uploaded(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        with tenant_context(abc.consultancy):
            police = DocumentType.objects.create(name="Police report")

        body = upload(abc, student, document_type=str(police.pk)).json()

        assert item_states(body)[-1] == ("Police report", "waiting")

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
        abc.log_in_as(role)

        response = upload(abc, student)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == refusal
        assert stored_documents(abc, student) == []

    def test_a_counsellor_loses_the_right_after_a_transfer(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, named_staff(abc, "Gita Rai"))
        log_in(abc, me)

        assert upload(abc, student).status_code == status.HTTP_403_FORBIDDEN

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert upload(abc, 999_999).status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        assert upload(abc, student).status_code == status.HTTP_401_UNAUTHORIZED

    def test_storage_failing_adds_nothing(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        student = own_student(abc)
        fake_document_storage.is_down = True

        response = upload(abc, student)

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json() == {"detail": "We couldn't store the file. Try again."}
        assert stored_documents(abc, student) == []

    def test_there_is_a_limit_per_student(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        with tenant_context(abc.consultancy):
            passport = DocumentType.objects.get(name="Passport")
            Document.objects.bulk_create(
                Document(student=student, document_type=passport)
                for _ in range(MAX_DOCUMENTS_PER_STUDENT)
            )

        response = upload(abc, student)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": f"A Student can have at most {MAX_DOCUMENTS_PER_STUDENT} documents."
        }

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        student = own_student(abc)

        upload(abc, student, file=a_file(name="Sita Gurung passport.pdf"))

        [entry] = json_logs.with_message("Document uploaded")
        [document] = stored_documents(abc, student)
        assert f"document_id={document.pk}" in entry["message"]
        assert "Sita" not in json_logs.text()
        assert "passport.pdf" not in json_logs.text()


class TestRefusedUploads:
    def test_no_file_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        body = {"document_type": str(type_named(abc, "Passport").pk)}

        response = abc.client.post(documents_url(student), body, format="multipart")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_FILE

    def test_text_sent_in_place_of_a_file_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        response = upload(abc, student, file="%PDF-1.7 pretending to be a file")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_FILE

    def test_an_empty_file_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        response = upload(abc, student, file=a_file(b""))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_FILE

    def test_a_file_over_10_mb_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        too_big = PDF_BYTES + b"0" * (MAX_DOCUMENT_BYTES - len(PDF_BYTES) + 1)

        response = upload(abc, student, file=a_file(too_big))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"file": ["The file is too big. Upload at most 10 MB."]}

    def test_exactly_10_mb_is_accepted(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        largest = PDF_BYTES + b"0" * (MAX_DOCUMENT_BYTES - len(PDF_BYTES))

        assert upload(abc, student, file=a_file(largest)).status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize(
        ("content", "name"),
        [
            (b"<html><script>alert(1)</script></html>", "passport.pdf"),
            (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "a.svg"),
            (b"GIF89a\x01\x00", "photo.png"),
            (b"MZ\x90\x00 an exe", "passport.pdf"),
            (b"plain text", "passport.txt"),
        ],
    )
    def test_anything_but_a_pdf_or_photo_is_refused_by_its_bytes(
        self, abc: ConsultancyContext, content: bytes, name: str
    ) -> None:
        student = own_student(abc)

        response = upload(abc, student, file=a_file(content, name))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == FILE_KIND
        assert stored_documents(abc, student) == []

    def test_a_misleading_name_gets_the_real_extension(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        upload(abc, student, file=a_file(PDF_BYTES, "../../etc/pass<word>.html"))

        [document] = stored_documents(abc, student)
        [file] = files_of(abc, document)
        assert (file.file_name, file.content_type) == ("password.html.pdf", "application/pdf")

    @pytest.mark.parametrize("value", ["", "abc", "1.5", "-1", "\u0661", "99999999", "1 OR 1=1"])
    def test_a_bad_document_type_is_refused(self, abc: ConsultancyContext, value: str) -> None:
        student = own_student(abc)

        response = upload(abc, student, document_type=value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_TYPE

    def test_a_missing_document_type_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        response = abc.client.post(documents_url(student), {"file": a_file()}, format="multipart")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_TYPE

    def test_an_unknown_field_is_refused_and_nothing_stored(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        response = upload(abc, student, verification="verified")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_documents(abc, student) == []

    def test_a_field_sent_twice_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        passport = str(type_named(abc, "Passport").pk)
        citizenship = str(type_named(abc, "Citizenship").pk)

        response = upload(abc, student, document_type=[passport, citizenship])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"document_type": ["Send this only once."]}

    def test_a_json_body_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        body = {"document_type": type_named(abc, "Passport").pk, "file": "JVBERi0="}

        response = abc.client.post(documents_url(student), body, format="json")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


class TestLinks:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_anyone_who_sees_the_student_gets_a_short_link(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"))
        document = make_document(abc, student)
        abc.log_in_as(role)

        response = abc.client.get(link_url(abc, student, document))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "url": f"https://files.test/{files_of(abc, document)[0].storage_key}?disposition=inline"
            "&name=passport.pdf&type=application%2Fpdf&expires=300",
            "expires_in": 300,
        }

    def test_a_download_link_saves_the_file(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        body = abc.client.get(link_url(abc, student, document), {"action": "download"}).json()

        assert "disposition=attachment" in body["url"]

    def test_an_open_link_can_be_asked_for_by_name(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        body = abc.client.get(link_url(abc, student, document), {"action": "open"}).json()

        assert "disposition=inline" in body["url"]

    def test_teachers_are_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        document = make_document(abc, student)
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(link_url(abc, student, document))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_another_students_document_is_not_found(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        other = make_student(abc, name="Ram Shrestha")
        document = make_document(abc, other)

        assert abc.client.get(link_url(abc, student, document)).status_code == 404

    def test_an_unknown_document_is_not_found(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        assert abc.client.get(link_url(abc, student, 999_999)).status_code == 404

    @pytest.mark.parametrize(
        "query", [{"action": "delete"}, {"action": "OPEN"}, {"expires": "99999"}, {"key": "x"}]
    )
    def test_bad_query_parameters_are_refused(
        self, abc: ConsultancyContext, query: dict[str, str]
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = abc.client.get(link_url(abc, student, document), query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        document = make_document(abc, student)

        assert abc.client.get(link_url(abc, student, document)).status_code == 401

    def test_issuing_a_link_is_logged_without_the_link(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        abc.client.get(link_url(abc, student, document))

        [entry] = json_logs.with_message("Document link issued")
        assert f"document_id={document.pk}" in entry["message"]
        assert "files.test" not in json_logs.text()


class TestMethods:
    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_the_checklist_answers_only_get_and_post(
        self, abc: ConsultancyContext, method: str
    ) -> None:
        student = own_student(abc)

        response = getattr(abc.client, method)(documents_url(student))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_a_link_answers_only_get(self, abc: ConsultancyContext, method: str) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = getattr(abc.client, method)(link_url(abc, student, document))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_not_on_the_platform_host(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        student = make_student(abc)
        platform_site.log_in()

        response = platform_site.client.get(documents_url(student))

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestInjectionShapedNames:
    @pytest.mark.parametrize("name", INJECTION_STRINGS)
    def test_a_file_name_is_kept_as_text_without_unsafe_characters(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        student = own_student(abc)

        response = upload(abc, student, file=a_file(PDF_BYTES, f"{name}.pdf"))

        assert response.status_code == status.HTTP_201_CREATED
        [document] = stored_documents(abc, student)
        [file] = files_of(abc, document)
        assert not set(file.file_name) & set('<>"/\\')
        assert file.file_name.endswith(".pdf")


class TestUploadSerializer:
    def test_a_plain_object_body_is_checked_like_a_form(self, abc: ConsultancyContext) -> None:
        with tenant_context(abc.consultancy):
            passport = DocumentType.objects.get(name="Passport")
            serializer = DocumentUploadSerializer(
                data={"document_type": str(passport.pk), "file": a_file()}
            )

            assert serializer.is_valid(), serializer.errors
            assert DocumentTypeField().to_representation(passport) == passport.pk

    def test_models_name_themselves(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        document = make_document(abc, student)

        [file] = files_of(abc, document)

        assert (str(document.document_type), str(document), str(file)) == (
            "Passport",
            str(document.pk),
            "passport.pdf",
        )
