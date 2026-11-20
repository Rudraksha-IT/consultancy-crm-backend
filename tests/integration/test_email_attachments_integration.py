"""Attachments: Course Files, Document Examples and the Student's own Documents (ticket 12).

Spec (2026-11-18) stories 34, 47-52, 58, 65. Files come only from the app, named by kind and id;
a Student's Document file only if it is theirs and neither it nor its Document is in the Bin. At
most 10 MB in all; an email to many Students takes only Course Files and Document Examples. Sent
email is read from Django's test mailbox; files from the in-memory storage.
"""

from __future__ import annotations

import uuid
from email.message import Message as Message_
from typing import TYPE_CHECKING

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from communication.models import Message
from course_files.models import CourseFile
from document_examples.models import DocumentExample
from documents.models import Document, DocumentFile, DocumentType
from documents.verification import Verification
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff
from students.models import Student
from tenancy.logo import PNG, CheckedLogo
from tenancy.services import set_logo
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs
from tests.fakes import FakeDocumentStorage, StoredFile
from tests.integration.test_applications_integration import make_course
from tests.integration.test_communication_integration import (
    CONTACT_EMAIL,
    NO_CONTACT_EMAIL,
    outbox,
    set_contact_email,
)
from tests.integration.test_course_files_integration import make_course_file
from tests.integration.test_document_examples_integration import make_example
from tests.integration.test_documents_integration import PDF_BYTES, PNG_BYTES, add_stored_files
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from email.message import EmailMessage

    from django.core.mail import EmailMultiAlternatives
    from rest_framework.response import _MonkeyPatchedResponse

MB = 1024 * 1024
BULK_URL = "/api/messages/bulk/"
BULK_ATTACHABLE_URL = "/api/messages/bulk/attachable/"
CHOOSE_FILES = {"attachments": ["Choose files from the list."]}
GONE = {"attachments": ["One of these files can't be attached any more. Choose the files again."]}
TOO_MANY_FILES = {"attachments": ["Attach at most 20 files."]}
NOT_TO_MANY = {"attachments": ["Only Course Files and Document Examples can go to many Students."]}
NOT_FETCHED = {"detail": "We couldn't get the attached files. Try again."}
NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}
PNG_LOGO = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office: Email on, with a contact email."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.COMMUNICATION})
    set_contact_email(office)
    return office


@pytest.fixture
def admin(abc: ConsultancyContext) -> Staff:
    """The office's Admin, logged in."""
    staff = named_staff(abc, "Asha Admin", Role.ADMIN)
    log_in(abc, staff)
    return staff


@pytest.fixture
def sita(abc: ConsultancyContext) -> Student:
    """A Student with an email address."""
    return make_student(abc, name="Sita Gurung", email="sita@example.com")


def messages_url(student: Student | int | str) -> str:
    """One Student's emails."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/messages/"


def attachable_url(student: Student | int | str) -> str:
    """The files that can be attached to an email to one Student."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/emails/attachable/"


def draft_url(student: Student) -> str:
    """One ready-made email's draft for one Student."""
    return f"/api/students/{student.public_id}/emails/draft/"


def send(
    office: ConsultancyContext, student: Student | int | str, attachments: object
) -> _MonkeyPatchedResponse:
    """POST an email with `attachments` to one Student."""
    body = {
        "subject": "Your papers",
        "body": "Dear Sita,\n\nHere they are.",
        "attachments": attachments,
    }
    return office.client.post(messages_url(student), body, format="json")


def send_bulk(
    office: ConsultancyContext, students: list[Student], attachments: object
) -> _MonkeyPatchedResponse:
    """POST one email with `attachments` to many Students."""
    body = {
        "subject": "Office news",
        "body": "Dear Student,\n\nHave a look.",
        "students": [str(student.public_id) for student in students],
        "attachments": attachments,
    }
    return office.client.post(BULK_URL, body, format="json")


def course_file(
    office: ConsultancyContext,
    name: str = "Brochure",
    university: str = "Deakin University",
    *,
    archived: bool = False,
    university_archived: bool = False,
) -> CourseFile:
    """A Course File in use, on a Course (Master of IT) of `university`."""
    course = make_course(
        office, university, archived=archived, university_archived=university_archived
    )
    return make_course_file(office, course, name, binned=False)


def example(
    office: ConsultancyContext, type_name: str = "Bank Statement", file_name: str = "sample.pdf"
) -> DocumentExample:
    """A Document Example in use."""
    with tenant_context(office.consultancy):
        document_type = DocumentType.objects.get(name=type_name)
    return make_example(office, document_type, file_name)


def png_example(office: ConsultancyContext) -> DocumentExample:
    """A PNG Document Example in use."""
    found = example(office, file_name="sample.png")
    with tenant_context(office.consultancy):
        DocumentExample.objects.filter(pk=found.pk).update(
            content_type="image/png", size=len(PNG_BYTES)
        )
        found.refresh_from_db()
    FakeDocumentStorage.files[found.storage_key] = StoredFile(PNG_BYTES, "image/png")
    return found


def document_file(
    office: ConsultancyContext, student: Student, type_name: str = "Passport"
) -> DocumentFile:
    """A file of one of the Student's Documents, in use, with its bytes in storage."""
    with tenant_context(office.consultancy):
        document = Document.objects.create(
            student=student,
            document_type=DocumentType.objects.get(name=type_name),
            verification=Verification.PENDING,
        )
        [file] = add_stored_files(office, document, first_name=f"{type_name.lower()}.pdf")
    FakeDocumentStorage.files[file.storage_key] = StoredFile(PDF_BYTES, "application/pdf")
    return file


def sized(
    office: ConsultancyContext, row: CourseFile | DocumentExample | DocumentFile, size: int
) -> None:
    """Store `size` as the file's size (the sizes are checked from what was stored at upload)."""
    with tenant_context(office.consultancy):
        type(row).objects.filter(pk=row.pk).update(size=size)


def binned(
    office: ConsultancyContext, row: CourseFile | DocumentExample | DocumentFile | Document
) -> None:
    """Put the file (or Document) in the Bin."""
    with tenant_context(office.consultancy):
        type(row).objects.filter(pk=row.pk).update(
            binned_at="2026-11-20T10:00:00Z", binned_by_name="Someone", bin_reason="Old"
        )


def picked(*rows: CourseFile | DocumentExample | DocumentFile) -> list[dict[str, object]]:
    """The attachments as the composer names them."""
    kinds = {
        CourseFile: "course_file",
        DocumentExample: "document_example",
        DocumentFile: "document_file",
    }
    return [{"kind": kinds[type(row)], "id": outside_id(row)} for row in rows]


def outside_id(row: CourseFile | DocumentExample | DocumentFile) -> int | str:
    """The id the picker gives a file: a Document file's UUID; the rest by number (ticket 18)."""
    return str(row.public_id) if isinstance(row, DocumentFile) else row.pk


def stored_messages(office: ConsultancyContext) -> list[Message]:
    """Every stored email, oldest first."""
    with tenant_context(office.consultancy):
        return list(Message.objects.order_by("pk"))


def parts_of(part: EmailMessage | Message_) -> list[Message_]:
    """The parts directly inside a multipart part, in order."""
    payload = part.get_payload()
    assert isinstance(payload, list)
    return [item for item in payload if isinstance(item, Message_)]


def files_of(sent: EmailMultiAlternatives) -> list[tuple[str, str, bytes]]:
    """Each file attached to the email (never the inline logo): name, type and bytes."""
    found = []
    for part in sent.message().walk():
        if part.get_content_disposition() == "attachment":
            payload = part.get_payload(decode=True)
            assert isinstance(payload, bytes)
            found.append((str(part.get_filename()), part.get_content_type(), payload))
    return found


class TestAttachingToOneStudent:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_each_kind_of_file_arrives_with_its_name_and_type(
        self, abc: ConsultancyContext, sita: Student, role: Role
    ) -> None:
        abc.log_in_as(role)
        brochure = course_file(abc)
        sample = png_example(abc)
        passport = document_file(abc, sita)

        response = send(abc, sita, picked(brochure, sample, passport))

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        [sent] = outbox()
        assert files_of(sent) == [
            ("brochure.pdf", "application/pdf", PDF_BYTES),
            ("sample.png", "image/png", PNG_BYTES),
            ("passport.pdf", "application/pdf", PDF_BYTES),
        ]

    def test_the_email_keeps_its_html_and_logo_beside_the_files(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        send(abc, sita, picked(course_file(abc)))

        [sent] = outbox()
        [(html, kind)] = sent.alternatives
        assert kind == "text/html"
        assert "Here they are." in str(html)
        assert "Here they are." in sent.body

    def test_a_png_logo_stays_inside_the_email_beside_the_files(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        set_logo(abc.consultancy, CheckedLogo(PNG, PNG_LOGO))

        send(abc, sita, picked(course_file(abc)))

        [sent] = outbox()
        email = sent.message()
        assert email.get_content_type() == "multipart/mixed"
        body, file = parts_of(email)
        assert body.get_content_type() == "multipart/related"
        text_and_html, logo = parts_of(body)
        assert text_and_html.get_content_type() == "multipart/alternative"
        assert (logo.get_content_type(), logo.get("Content-ID")) == ("image/png", "<logo>")
        assert (file.get_content_disposition(), file.get_filename()) == (
            "attachment",
            "brochure.pdf",
        )

    def test_without_a_logo_the_files_sit_beside_the_text_and_html(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        send(abc, sita, picked(course_file(abc)))

        [sent] = outbox()
        body, _ = parts_of(sent.message())
        assert body.get_content_type() == "multipart/alternative"

    def test_the_email_records_the_names_and_the_api_shows_them(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        brochure = course_file(abc)
        passport = document_file(abc, sita)

        response = send(abc, sita, picked(brochure, passport))

        assert response.json()["attachments"] == ["brochure.pdf", "passport.pdf"]
        [message] = stored_messages(abc)
        assert message.attachments == ["brochure.pdf", "passport.pdf"]
        listed = abc.client.get(messages_url(sita)).json()["results"]
        assert [row["attachments"] for row in listed] == [["brochure.pdf", "passport.pdf"]]
        everyone = abc.client.get("/api/messages/").json()["results"]
        assert [row["attachments"] for row in everyone] == [["brochure.pdf", "passport.pdf"]]

    def test_without_attachments_nothing_is_attached(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        body = {"subject": "Hello", "body": "Dear Sita,\n\nHi."}

        response = abc.client.post(messages_url(sita), body, format="json")

        assert response.json()["attachments"] == []
        [sent] = outbox()
        assert files_of(sent) == []

    def test_an_empty_list_attaches_nothing(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        response = send(abc, sita, [])

        assert response.json()["attachments"] == []

    def test_a_file_named_twice_is_attached_once(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        brochure = course_file(abc)

        response = send(abc, sita, picked(brochure, brochure))

        assert response.json()["attachments"] == ["brochure.pdf"]

    def test_ten_mb_in_all_is_sent(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        brochure, passport = course_file(abc), document_file(abc, sita)
        sized(abc, brochure, 6 * MB)
        sized(abc, passport, 4 * MB)

        response = send(abc, sita, picked(brochure, passport))

        assert response.status_code == status.HTTP_201_CREATED

    def test_more_than_ten_mb_is_refused_naming_the_biggest_file(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        brochure, passport = course_file(abc), document_file(abc, sita)
        sized(abc, brochure, 5 * MB)
        sized(abc, passport, 5 * MB + MB // 2)

        response = send(abc, sita, picked(brochure, passport))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "attachments": [
                "The files add up to 10.5 MB, more than the 10 MB one email can carry. "
                "Remove passport.pdf or another file."
            ]
        }
        assert outbox() == []
        assert stored_messages(abc) == []

    def test_another_students_document_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        ram = make_student(abc, name="Ram Thapa", email="ram@example.com")
        his = document_file(abc, ram)

        response = send(abc, sita, picked(his))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == GONE
        assert outbox() == []
        assert stored_messages(abc) == []

    def test_a_document_file_or_document_in_the_bin_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        loose = document_file(abc, sita)
        binned(abc, loose)
        whole = document_file(abc, sita, "Citizenship")
        binned(abc, whole.document)

        answers = [send(abc, sita, picked(loose)), send(abc, sita, picked(whole))]

        assert [answer.json() for answer in answers] == [GONE, GONE]
        assert outbox() == []

    def test_a_course_file_or_example_in_the_bin_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        brochure, sample = course_file(abc), example(abc)
        binned(abc, brochure)
        binned(abc, sample)

        answers = [send(abc, sita, picked(brochure)), send(abc, sita, picked(sample))]

        assert [answer.json() for answer in answers] == [GONE, GONE]

    @pytest.mark.parametrize("kind", ["course_file", "document_example", "document_file"])
    def test_an_unknown_id_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, kind: str
    ) -> None:
        unknown = str(uuid.uuid4()) if kind == "document_file" else 999_999
        response = send(abc, sita, [{"kind": kind, "id": unknown}])

        assert response.json() == GONE

    @pytest.mark.parametrize(
        "attachments",
        [
            "brochure.pdf",
            {"kind": "course_file", "id": 1},
            [1],
            [None],
            [{"kind": "course_file"}],
            [{"id": 1}],
            [{"kind": "computer_file", "id": 1}],
            [{"kind": "course_file", "id": "1"}],
            [{"kind": "course_file", "id": True}],
            [{"kind": "course_file", "id": 0}],
            [{"kind": "course_file", "id": -1}],
            [{"kind": "course_file", "id": 2**63}],
            [{"kind": "course_file", "id": 1.5}],
            [{"kind": "document_file", "id": 1}],
            [{"kind": "document_file", "id": "not-a-uuid"}],
            [{"kind": "course_file", "id": 1, "path": "/etc/passwd"}],
            [{"kind": "course_file", "id": 1, "url": "https://evil.example/x.pdf"}],
            *[[{"kind": text, "id": 1}] for text in INJECTION_STRINGS],
            [{"kind": "x" * 10_000, "id": 1}],
        ],
    )
    def test_bad_attachments_are_refused_and_nothing_is_sent(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, attachments: object
    ) -> None:
        response = send(abc, sita, attachments)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_FILES
        assert outbox() == []
        assert stored_messages(abc) == []

    def test_null_attachments_are_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        assert send(abc, sita, None).json() == CHOOSE_FILES

    def test_at_most_twenty_files(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        attachments = [{"kind": "course_file", "id": number} for number in range(1, 22)]

        response = send(abc, sita, attachments)

        assert response.json() == TOO_MANY_FILES

    def test_storage_down_sends_and_records_nothing(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        brochure = course_file(abc)
        FakeDocumentStorage.is_down = True

        response = send(abc, sita, picked(brochure))

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json() == NOT_FETCHED
        assert outbox() == []
        assert stored_messages(abc) == []

    def test_without_a_contact_email_nothing_is_sent(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        set_contact_email(abc, "")

        response = send(abc, sita, picked(course_file(abc)))

        assert response.json() == NO_CONTACT_EMAIL
        assert outbox() == []

    def test_a_teacher_is_refused(self, abc: ConsultancyContext, sita: Student) -> None:
        abc.log_in_as(Role.TEACHER)

        response = send(abc, sita, picked(course_file(abc)))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS
        assert outbox() == []

    def test_logs_carry_counts_never_file_names(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, json_logs: JsonLogs
    ) -> None:
        send(abc, sita, picked(course_file(abc), document_file(abc, sita)))

        [entry] = json_logs.with_message("Email sent: ")
        assert "attachments=2" in entry["message"]
        for private in ("brochure.pdf", "passport.pdf", "sita@example.com", CONTACT_EMAIL):
            assert private not in json_logs.text()


class TestAttachingToManyStudents:
    def test_course_files_and_examples_go_in_every_copy(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        ram = make_student(abc, name="Ram Thapa", email="ram@example.com")
        brochure, sample = course_file(abc), example(abc)

        response = send_bulk(abc, [sita, ram], picked(brochure, sample))

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert response.json()["sent"] == 2
        names = [[name for name, _, _ in files_of(sent)] for sent in outbox()]
        assert names == [["brochure.pdf", "sample.pdf"]] * 2
        assert [m.attachments for m in stored_messages(abc)] == [["brochure.pdf", "sample.pdf"]] * 2

    def test_a_students_own_document_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        response = send_bulk(abc, [sita], picked(document_file(abc, sita)))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NOT_TO_MANY
        assert outbox() == []
        assert stored_messages(abc) == []

    def test_more_than_ten_mb_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        big, small = course_file(abc, "Big"), example(abc)
        sized(abc, big, 10 * MB)
        sized(abc, small, 1)

        response = send_bulk(abc, [sita], picked(big, small))

        assert response.json() == {
            "attachments": [
                "The files add up to 10.0 MB, more than the 10 MB one email can carry. "
                "Remove brochure.pdf or another file."
            ]
        }
        assert outbox() == []

    def test_bad_attachments_are_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        response = send_bulk(abc, [sita], [{"kind": "<script>", "id": 1}])

        assert response.json() == CHOOSE_FILES

    def test_storage_down_sends_nothing(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        brochure = course_file(abc)
        FakeDocumentStorage.is_down = True

        response = send_bulk(abc, [sita], picked(brochure))

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert stored_messages(abc) == []


def attachable_row(
    row: CourseFile | DocumentExample | DocumentFile, **expected: object
) -> dict[str, object]:
    """One file as the picker lists it."""
    return {"id": outside_id(row), "size": row.size, **expected}


class TestWhatCanBeAttached:
    def test_one_student_gets_course_files_examples_and_their_own_documents(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        brochure = course_file(abc, "Master of IT brochure")
        sample = example(abc)
        passport = document_file(abc, sita)
        ram = make_student(abc, name="Ram Thapa", email="ram@example.com")
        document_file(abc, ram, "Citizenship")
        binned(abc, course_file(abc, "Old", university="Old University"))
        binned(abc, example(abc, file_name="old.pdf"))
        binned(abc, document_file(abc, sita, "Citizenship"))

        response = abc.client.get(attachable_url(sita))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "course_files": [
                attachable_row(
                    brochure,
                    kind="course_file",
                    name="Master of IT brochure",
                    file_name="brochure.pdf",
                    group="Deakin University, Master of IT",
                )
            ],
            "document_examples": [
                attachable_row(
                    sample,
                    kind="document_example",
                    name="sample.pdf",
                    file_name="sample.pdf",
                    group="Bank Statement",
                )
            ],
            "documents": [
                attachable_row(
                    passport,
                    kind="document_file",
                    name="passport.pdf",
                    file_name="passport.pdf",
                    group="Passport",
                )
            ],
            "max_bytes": 10 * MB,
        }

    def test_archived_courses_and_universities_are_left_out(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        course_file(abc, "Archived course", university="A University", archived=True)
        course_file(abc, "Archived university", university="B University", university_archived=True)

        assert abc.client.get(attachable_url(sita)).json()["course_files"] == []

    def test_many_students_get_no_documents(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        brochure = course_file(abc)
        document_file(abc, sita)

        answer = abc.client.get(BULK_ATTACHABLE_URL).json()

        assert [row["id"] for row in answer["course_files"]] == [brochure.pk]
        assert answer["documents"] == []

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_counsellors_and_front_desk_see_them(
        self, abc: ConsultancyContext, sita: Student, role: Role
    ) -> None:
        abc.log_in_as(role)
        document_file(abc, sita)

        assert len(abc.client.get(attachable_url(sita)).json()["documents"]) == 1
        assert abc.client.get(BULK_ATTACHABLE_URL).status_code == status.HTTP_200_OK

    def test_a_teacher_is_refused(self, abc: ConsultancyContext, sita: Student) -> None:
        abc.log_in_as(Role.TEACHER)

        answers = [abc.client.get(attachable_url(sita)), abc.client.get(BULK_ATTACHABLE_URL)]

        assert [answer.status_code for answer in answers] == [403, 403]

    def test_logged_out_is_refused(self, abc: ConsultancyContext, sita: Student) -> None:
        answers = [abc.client.get(attachable_url(sita)), abc.client.get(BULK_ATTACHABLE_URL)]

        assert [answer.status_code for answer in answers] == [401, 401]

    def test_with_email_off_they_are_not_found(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        switch_modules(abc.consultancy, set())

        answers = [abc.client.get(attachable_url(sita)), abc.client.get(BULK_ATTACHABLE_URL)]

        assert [answer.status_code for answer in answers] == [404, 404]

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext, admin: Staff) -> None:
        assert abc.client.get(attachable_url(999_999)).status_code == status.HTTP_404_NOT_FOUND

    def test_a_query_is_refused(self, abc: ConsultancyContext, admin: Staff, sita: Student) -> None:
        answers = [
            abc.client.get(attachable_url(sita), {"student": "<script>"}),
            abc.client.get(BULK_ATTACHABLE_URL, {"q": "x"}),
        ]

        assert [answer.status_code for answer in answers] == [400, 400]

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, method: str
    ) -> None:
        answers = [
            getattr(abc.client, method)(attachable_url(sita), {}, format="json"),
            getattr(abc.client, method)(BULK_ATTACHABLE_URL, {}, format="json"),
        ]

        assert [answer.status_code for answer in answers] == [405, 405]

    def test_names_are_kept_as_words(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        brochure = course_file(abc, INJECTION_STRINGS[0])

        [row] = abc.client.get(attachable_url(sita)).json()["course_files"]

        assert (row["id"], row["name"]) == (brochure.pk, INJECTION_STRINGS[0])


class TestSuggestedAttachments:
    def test_documents_needed_suggests_the_missing_documents_examples(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        sample = example(abc, "Passport", "sample-passport.pdf")

        response = abc.client.get(draft_url(sita), {"kind": "documents_needed"})

        answer = response.json()
        assert answer["attachments"] == [
            attachable_row(
                sample,
                kind="document_example",
                name="sample-passport.pdf",
                file_name="sample-passport.pdf",
                group="Passport",
            )
        ]
        assert "We've attached examples" in answer["body"]

    def test_examples_of_documents_already_in_are_not_suggested(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        example(abc, "Passport", "sample-passport.pdf")
        with tenant_context(abc.consultancy):
            Document.objects.create(
                student=sita,
                document_type=DocumentType.objects.get(name="Passport"),
                verification=Verification.VERIFIED,
            )

        answer = abc.client.get(draft_url(sita), {"kind": "documents_needed"}).json()

        assert answer["attachments"] == []
        assert "We've attached examples" not in answer["body"]

    def test_other_emails_suggest_nothing(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        example(abc, "Passport")

        answers = [
            abc.client.get(draft_url(sita), {"kind": kind}).json() for kind in ("welcome", "plain")
        ]
        many = abc.client.get("/api/messages/bulk/draft/", {"kind": "plain"}).json()

        assert [answer["attachments"] for answer in [*answers, many]] == [[], [], []]
