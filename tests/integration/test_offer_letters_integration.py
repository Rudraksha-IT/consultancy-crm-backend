"""Offer Letters and the Chosen Offer (spec 114, 115, 58; issue 76)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import pytest
from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application, Visa
from applications.statuses import ApplicationStatus, VisaStatus
from documents.files import MAX_DOCUMENT_BYTES
from documents.models import Document, DocumentType
from documents.services import MAX_DOCUMENTS_PER_STUDENT
from history.models import HistoryEntry, HistoryKind
from staff.models import Role
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.fakes import FakeDocumentStorage
from tests.integration.test_applications_integration import (
    COUNSELLOR_OR_ADMIN,
    counselled_student,
    make_application,
    make_course,
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

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

PDF_BYTES = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
WAITING_FOR_OFFER = {"application": ["Choose an application waiting for an offer."]}
NOT_THIS_STUDENTS = {"application": ["Choose one of this Student's applications."]}
CHOOSE_KIND = {"kind": ["Choose Conditional or Unconditional."]}
CHOOSE_FILE = {"file": ["Choose a file to upload."]}
FILE_KIND = {"file": ["Upload a PDF, JPG or PNG file."]}
CHOOSE_OFFER = {"application": ["Choose an application that has an offer."]}
ALREADY_CHOSEN = {"application": ["This offer is already chosen."]}
CHOICE_LOCKED = {
    "application": [
        "The visa for the chosen offer is waiting for an answer, so the choice can't change now."
    ]
}
NOT_CHOSEN = {"application": ["A visa can only be applied for on the chosen offer."]}
VISA_NOT_CHOSEN = {"status": ["This visa isn't for the chosen offer."]}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def letters_url(student: Student | int | str) -> str:
    """Where a Student's Offer Letters are added."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/offer-letters/"


def chosen_url(student: Student | int | str) -> str:
    """A Student's Chosen Offer."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/chosen-offer/"


def a_letter(content: bytes = PDF_BYTES, name: str = "deakin-offer.pdf") -> SimpleUploadedFile:
    """A letter as the browser would send it (its stated type is never believed)."""
    return SimpleUploadedFile(name, content, content_type="application/pdf")


def add_letter(
    office: ConsultancyContext, student: Student | int | str, **fields: object
) -> _MonkeyPatchedResponse:
    """POST an Offer Letter as a form: a conditional PDF unless `fields` say otherwise."""
    body: dict[str, object] = {"kind": "conditional", "file": a_letter(), **fields}
    body = {key: value for key, value in body.items() if value is not None}
    return office.client.post(letters_url(student), body, format="multipart")


def choose(
    office: ConsultancyContext, student: Student | int | str, body: object
) -> _MonkeyPatchedResponse:
    """PUT the Chosen Offer."""
    return office.client.put(chosen_url(student), body, format="json")


def applied(office: ConsultancyContext, student: Student, university: str) -> Application:
    """An Application of the Student, waiting for an offer."""
    return make_application(office, student, make_course(office, university))


def with_offer(
    office: ConsultancyContext, student: Student, university: str = "Deakin University"
) -> Application:
    """An Application of the Student that already has an offer."""
    return make_application(
        office, student, make_course(office, university), ApplicationStatus.OFFER
    )


def set_chosen(office: ConsultancyContext, student: Student, application: Application) -> None:
    """Store the Chosen Offer directly."""
    with tenant_context(office.consultancy):
        Student.objects.filter(pk=student.pk).update(chosen_offer=application)


def chosen_of(office: ConsultancyContext, student: Student) -> int | None:
    """The stored Chosen Offer's id."""
    with tenant_context(office.consultancy):
        return Student.objects.get(pk=student.pk).chosen_offer_id


def entries(office: ConsultancyContext, student: Student, kind: HistoryKind) -> list[HistoryEntry]:
    """The Student's stored History of one kind, oldest first."""
    with tenant_context(office.consultancy):
        return list(HistoryEntry.objects.filter(student=student, kind=kind).order_by("at", "pk"))


def documents_of(office: ConsultancyContext, student: Student) -> list[Document]:
    """The Student's stored Documents, oldest first."""
    with tenant_context(office.consultancy):
        return list(
            Document.objects.filter(student=student).select_related("document_type").order_by("pk")
        )


def application_of(office: ConsultancyContext, application: Application) -> Application:
    """The Application as stored now."""
    with tenant_context(office.consultancy):
        return Application.objects.get(pk=application.pk)


class TestAddingAnOfferLetter:
    def test_the_application_becomes_offer_and_the_student_moves_to_offer(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        student = make_student(abc, me, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")

        response = add_letter(abc, student, application=str(application.public_id))

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["stage"] == "offer"
        [answer] = body["applications"]
        assert answer["status"] == "offer"
        assert answer["offer_kind"] == "conditional"
        assert answer["offered_on"] == timezone.localdate().isoformat()
        [document] = documents_of(abc, student)
        [file] = files_of(abc, document)
        assert answer["offer_letter"] == {
            "id": str(document.public_id),
            "files": [{"id": str(file.public_id), "file_name": "deakin-offer.pdf"}],
        }
        assert document.document_type.name == "Offer letter"
        assert document.application_id == application.pk
        assert document.verification == "pending"
        assert fake_document_storage.files[file.storage_key].content == PDF_BYTES
        assert body["chosen_offer_id"] is None
        [letter] = entries(abc, student, HistoryKind.OFFER_LETTER_ADDED)
        assert (letter.after, letter.by_name) == ("Deakin University, Conditional", "Bikash Thapa")
        [moved] = stage_entries(abc, student)
        assert (moved.before, moved.after) == ("applying", "offer")
        assert moved.detail == "Automatic: offer letter from Deakin University added."

    def test_a_second_offer_keeps_the_student_at_offer(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        with_offer(abc, student)
        other = applied(abc, student, "RMIT University")

        response = add_letter(abc, student, application=str(other.public_id), kind="unconditional")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["stage"] == "offer"
        assert application_of(abc, other).offer_kind == "unconditional"
        assert stage_entries(abc, student) == []
        [letter] = entries(abc, student, HistoryKind.OFFER_LETTER_ADDED)
        assert letter.after == "RMIT University, Unconditional"

    def test_the_offer_letter_type_is_made_once_and_reused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        with tenant_context(abc.consultancy):
            DocumentType.objects.create(name="OFFER LETTER")
        first = applied(abc, student, "Deakin University")
        second = applied(abc, student, "RMIT University")

        add_letter(abc, student, application=str(first.public_id))
        add_letter(abc, student, application=str(second.public_id))

        with tenant_context(abc.consultancy):
            assert DocumentType.objects.filter(name__iexact="offer letter").count() == 1
        assert {d.document_type.name for d in documents_of(abc, student)} == {"OFFER LETTER"}

    def test_a_photo_letter_is_kept_with_its_real_type(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")
        photo = a_letter(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00rest", "scan.html")

        add_letter(abc, student, application=str(application.public_id), file=photo)

        [document] = documents_of(abc, student)
        [file] = files_of(abc, document)
        assert (file.file_name, file.content_type) == ("scan.html.jpg", "image/jpeg")

    def test_an_admin_can_add_one_for_anyones_student(self, abc: ConsultancyContext) -> None:
        student = make_student(
            abc, abc.add_staff(Role.COUNSELLOR, email="other@abc.test"), Stage.APPLYING
        )
        application = applied(abc, student, "Deakin University")
        abc.log_in_as(Role.ADMIN)

        response = add_letter(abc, student, application=str(application.public_id))

        assert response.status_code == status.HTTP_201_CREATED

    def test_another_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(
            abc, abc.add_staff(Role.COUNSELLOR, email="other@abc.test"), Stage.APPLYING
        )
        application = applied(abc, student, "Deakin University")
        abc.log_in_as(Role.COUNSELLOR)

        response = add_letter(abc, student, application=str(application.public_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        assert application_of(abc, application).status == ApplicationStatus.APPLIED
        assert documents_of(abc, student) == []

    @pytest.mark.parametrize(
        ("role", "refusal"),
        [(Role.FRONT_DESK, COUNSELLOR_OR_ADMIN), (Role.TEACHER, NOT_FOR_TEACHERS)],
    )
    def test_front_desk_and_teachers_are_refused(
        self, abc: ConsultancyContext, role: Role, refusal: dict[str, str]
    ) -> None:
        student = make_student(
            abc, abc.add_staff(Role.COUNSELLOR, email="other@abc.test"), Stage.APPLYING
        )
        application = applied(abc, student, "Deakin University")
        abc.log_in_as(role)

        response = add_letter(abc, student, application=str(application.public_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == refusal
        assert documents_of(abc, student) == []

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(
            abc, abc.add_staff(Role.COUNSELLOR, email="other@abc.test"), Stage.APPLYING
        )
        application = applied(abc, student, "Deakin University")

        response = abc.new_client().post(
            letters_url(student),
            {"application": str(application.public_id), "kind": "conditional", "file": a_letter()},
            format="multipart",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("at", [ApplicationStatus.OFFER, ApplicationStatus.REJECTED])
    def test_only_an_application_waiting_for_an_offer_takes_one(
        self,
        abc: ConsultancyContext,
        fake_document_storage: type[FakeDocumentStorage],
        at: ApplicationStatus,
    ) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        application = make_application(abc, student, make_course(abc), at)

        response = add_letter(abc, student, application=str(application.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == WAITING_FOR_OFFER
        assert application_of(abc, application).status == at
        assert documents_of(abc, student) == []
        assert fake_document_storage.files == {}
        assert entries(abc, student, HistoryKind.OFFER_LETTER_ADDED) == []

    def test_another_students_application_is_refused(self, abc: ConsultancyContext) -> None:
        me, student = counselled_student(abc, Stage.APPLYING)
        someone_else = make_student(abc, me, Stage.APPLYING, name="Ram Thapa")
        theirs = applied(abc, someone_else, "Deakin University")

        response = add_letter(abc, student, application=str(theirs.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NOT_THIS_STUDENTS
        assert application_of(abc, theirs).status == ApplicationStatus.APPLIED

    @pytest.mark.parametrize(
        "value", ["", "0", "-1", "1.5", "abc", "true", "9" * 30, *INJECTION_STRINGS]
    )
    def test_bad_applications_are_refused(self, abc: ConsultancyContext, value: str) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        applied(abc, student, "Deakin University")

        response = add_letter(abc, student, application=value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NOT_THIS_STUDENTS
        assert documents_of(abc, student) == []

    def test_a_missing_application_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)

        response = add_letter(abc, student)

        assert response.json() == NOT_THIS_STUDENTS

    @pytest.mark.parametrize(
        "value",
        ["", "Conditional", "offer", "accepted", "x" * 10_000, *INJECTION_STRINGS],
    )
    def test_bad_kinds_are_refused(self, abc: ConsultancyContext, value: str) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")

        response = add_letter(abc, student, application=str(application.public_id), kind=value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_KIND
        assert application_of(abc, application).status == ApplicationStatus.APPLIED

    def test_a_missing_kind_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")
        body = {"application": str(application.public_id), "file": a_letter()}

        response = abc.client.post(letters_url(student), body, format="multipart")

        assert response.json() == CHOOSE_KIND

    def test_a_missing_file_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")
        body = {"application": str(application.public_id), "kind": "conditional"}

        response = abc.client.post(letters_url(student), body, format="multipart")

        assert response.json() == CHOOSE_FILE

    @pytest.mark.parametrize(
        "content", [b"<html><script>alert(1)</script></html>", b"GIF89a....", b"PK\x03\x04zip"]
    )
    def test_files_that_are_not_a_pdf_or_photo_are_refused(
        self, abc: ConsultancyContext, content: bytes
    ) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")

        response = add_letter(
            abc,
            student,
            application=str(application.public_id),
            file=a_letter(content, "offer.pdf"),
        )

        assert response.json() == FILE_KIND
        assert application_of(abc, application).status == ApplicationStatus.APPLIED

    def test_a_file_over_the_limit_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")
        big = a_letter(PDF_BYTES + b"0" * MAX_DOCUMENT_BYTES)

        response = add_letter(abc, student, application=str(application.public_id), file=big)

        assert response.json() == {"file": ["The file is too big. Upload at most 10 MB."]}
        assert documents_of(abc, student) == []

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")

        response = add_letter(
            abc,
            student,
            application=str(application.public_id),
            document_type="1",
            verification="ok",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert documents_of(abc, student) == []
        assert application_of(abc, application).status == ApplicationStatus.APPLIED

    def test_a_field_sent_twice_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")

        response = add_letter(abc, student, application=[str(application.pk), str(application.pk)])

        assert response.json() == {"application": ["Send this only once."]}

    def test_json_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")

        response = abc.client.post(
            letters_url(student),
            {"application": str(application.public_id), "kind": "conditional"},
            format="json",
        )

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_storage_failing_changes_nothing(
        self, abc: ConsultancyContext, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")
        fake_document_storage.is_down = True

        response = add_letter(abc, student, application=str(application.public_id))

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json() == {"detail": "We couldn't store the file. Try again."}
        assert application_of(abc, application).status == ApplicationStatus.APPLIED
        assert stored(abc, student).stage == Stage.APPLYING
        assert documents_of(abc, student) == []
        assert entries(abc, student, HistoryKind.OFFER_LETTER_ADDED) == []
        assert stage_entries(abc, student) == []

    def test_there_is_a_limit_of_documents_per_student(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")
        with tenant_context(abc.consultancy):
            passport = DocumentType.objects.get(name="Passport")
            Document.objects.bulk_create(
                Document(
                    student=student,
                    document_type=passport,
                )
                for number in range(MAX_DOCUMENTS_PER_STUDENT)
            )

        response = add_letter(abc, student, application=str(application.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": f"A Student can have at most {MAX_DOCUMENTS_PER_STUDENT} documents."
        }
        assert application_of(abc, application).status == ApplicationStatus.APPLIED

    def test_a_student_who_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert add_letter(abc, 999_999, application="1").status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)

        response = getattr(abc.client, method)(letters_url(student))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_the_log_names_ids_never_the_file(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        _, student = counselled_student(abc, Stage.APPLYING)
        application = applied(abc, student, "Deakin University")

        add_letter(
            abc, student, application=str(application.public_id), file=a_letter(name="sita.pdf")
        )

        text = json_logs.text()
        assert f"Offer letter added: application_id={application.pk}" in text
        assert "sita" not in text

    def test_the_platform_host_has_no_offer_letters_endpoint(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        student = make_student(abc)
        platform_site.log_in()

        response = platform_site.client.post(letters_url(student), {}, format="multipart")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestChoosingAnOffer:
    def test_the_assigned_counsellor_chooses_one(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        student = make_student(abc, me, Stage.OFFER)
        deakin = with_offer(abc, student, "Deakin University")
        with_offer(abc, student, "RMIT University")

        response = choose(abc, student, {"application": str(deakin.public_id)})

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["chosen_offer_id"] == str(deakin.public_id)
        assert body["stage"] == "offer"
        assert body["progress"] == {
            "percent": 76,
            "next_step": "Prepare the visa papers, Deakin University",
            "is_done": False,
        }
        assert chosen_of(abc, student) == deakin.pk
        [entry] = entries(abc, student, HistoryKind.CHOSEN_OFFER_CHANGED)
        assert (entry.before, entry.after, entry.by_name) == (
            "",
            "Deakin University",
            "Bikash Thapa",
        )

    def test_choosing_another_replaces_it(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        deakin = with_offer(abc, student, "Deakin University")
        rmit = with_offer(abc, student, "RMIT University")
        set_chosen(abc, student, deakin)

        response = choose(abc, student, {"application": str(rmit.public_id)})

        assert response.json()["chosen_offer_id"] == str(rmit.public_id)
        [entry] = entries(abc, student, HistoryKind.CHOSEN_OFFER_CHANGED)
        assert (entry.before, entry.after) == ("Deakin University", "RMIT University")

    def test_the_one_already_chosen_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        deakin = with_offer(abc, student)
        set_chosen(abc, student, deakin)

        response = choose(abc, student, {"application": str(deakin.public_id)})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ALREADY_CHOSEN
        assert entries(abc, student, HistoryKind.CHOSEN_OFFER_CHANGED) == []

    @pytest.mark.parametrize("at", [ApplicationStatus.APPLIED, ApplicationStatus.REJECTED])
    def test_an_application_without_an_offer_is_refused(
        self, abc: ConsultancyContext, at: ApplicationStatus
    ) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        application = make_application(abc, student, make_course(abc), at)

        response = choose(abc, student, {"application": str(application.public_id)})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_OFFER
        assert chosen_of(abc, student) is None

    def test_another_students_offer_is_refused(self, abc: ConsultancyContext) -> None:
        me, student = counselled_student(abc, Stage.OFFER)
        someone_else = make_student(abc, me, Stage.OFFER, name="Ram Thapa")
        theirs = with_offer(abc, someone_else)

        response = choose(abc, student, {"application": str(theirs.public_id)})

        assert response.json() == CHOOSE_OFFER
        assert chosen_of(abc, student) is None
        assert chosen_of(abc, someone_else) is None

    def test_changing_is_refused_while_the_chosen_offers_visa_waits(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        deakin = with_offer(abc, student, "Deakin University")
        rmit = with_offer(abc, student, "RMIT University")
        set_chosen(abc, student, deakin)
        with tenant_context(abc.consultancy):
            Visa.objects.create(student=student, application=deakin, status=VisaStatus.APPLIED)

        response = choose(abc, student, {"application": str(rmit.public_id)})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOICE_LOCKED
        assert chosen_of(abc, student) == deakin.pk

    def test_a_refused_visa_lets_them_choose_again(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        student = make_student(abc, me, Stage.OFFER)
        deakin = with_offer(abc, student, "Deakin University")
        rmit = with_offer(abc, student, "RMIT University")
        set_chosen(abc, student, deakin)
        visa = abc.client.post(
            f"/api/students/{student.public_id}/visas/",
            {"application": str(deakin.public_id)},
            format="json",
        ).json()["visas"][0]

        refused = abc.client.patch(
            f"/api/students/{student.public_id}/visas/{visa['id']}/",
            {"status": "refused"},
            format="json",
        )

        assert refused.json()["chosen_offer_id"] is None
        assert refused.json()["stage"] == "offer"
        cleared = entries(abc, student, HistoryKind.CHOSEN_OFFER_CHANGED)[-1]
        assert (cleared.before, cleared.after) == ("Deakin University", "")
        assert cleared.detail == "Automatic: Deakin University visa is Refused."
        assert (
            choose(abc, student, {"application": str(deakin.public_id)}).status_code
            == status.HTTP_200_OK
        )
        set_chosen(abc, student, rmit)
        assert chosen_of(abc, student) == rmit.pk

    def test_an_admin_can_choose_for_anyones_student(self, abc: ConsultancyContext) -> None:
        student = make_student(
            abc, abc.add_staff(Role.COUNSELLOR, email="other@abc.test"), Stage.OFFER
        )
        deakin = with_offer(abc, student)
        abc.log_in_as(Role.ADMIN)

        assert (
            choose(abc, student, {"application": str(deakin.public_id)}).status_code
            == status.HTTP_200_OK
        )

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
        student = make_student(
            abc, abc.add_staff(Role.COUNSELLOR, email="other@abc.test"), Stage.OFFER
        )
        deakin = with_offer(abc, student)
        abc.log_in_as(role)

        response = choose(abc, student, {"application": str(deakin.public_id)})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == refusal
        assert chosen_of(abc, student) is None

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(
            abc, abc.add_staff(Role.COUNSELLOR, email="other@abc.test"), Stage.OFFER
        )
        deakin = with_offer(abc, student)

        response = abc.new_client().put(
            chosen_url(student), {"application": str(deakin.public_id)}, format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize(
        "value", ["1", 1.5, 0, -1, True, None, [1], {"id": 1}, "x" * 10_000, *INJECTION_STRINGS]
    )
    def test_bad_ids_are_refused(self, abc: ConsultancyContext, value: object) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        with_offer(abc, student)

        response = choose(abc, student, {"application": value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_OFFER
        assert chosen_of(abc, student) is None

    def test_a_missing_application_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.OFFER)

        assert choose(abc, student, {}).json() == CHOOSE_OFFER

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        deakin = with_offer(abc, student)

        response = choose(abc, student, {"application": str(deakin.public_id), "stage": "visa"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert chosen_of(abc, student) is None
        assert stored(abc, student).stage == Stage.OFFER

    @pytest.mark.parametrize("body", [[1], "1", 1])
    def test_a_body_that_is_not_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        _, student = counselled_student(abc, Stage.OFFER)

        assert choose(abc, student, body).status_code == status.HTTP_400_BAD_REQUEST

    def test_a_student_who_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert choose(abc, 999_999, {"application": 1}).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_only_put_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        _, student = counselled_student(abc, Stage.OFFER)

        response = getattr(abc.client, method)(chosen_url(student), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestAVisaFollowsTheChosenOffer:
    def test_a_visa_on_an_offer_that_is_not_chosen_is_refused(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        deakin = with_offer(abc, student, "Deakin University")
        rmit = with_offer(abc, student, "RMIT University")
        set_chosen(abc, student, deakin)

        response = abc.client.post(
            f"/api/students/{student.public_id}/visas/",
            {"application": str(rmit.public_id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NOT_CHOSEN
        with tenant_context(abc.consultancy):
            assert not Visa.objects.exists()

    def test_with_no_chosen_offer_a_visa_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        deakin = with_offer(abc, student)

        response = abc.client.post(
            f"/api/students/{student.public_id}/visas/",
            {"application": str(deakin.public_id)},
            format="json",
        )

        assert response.json() == NOT_CHOSEN

    def test_a_refused_visa_set_back_to_applied_chooses_its_offer_again(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        deakin = with_offer(abc, student)
        with tenant_context(abc.consultancy):
            visa = Visa.objects.create(
                student=student, application=deakin, status=VisaStatus.REFUSED
            )

        response = abc.client.patch(
            f"/api/students/{student.public_id}/visas/{visa.public_id}/",
            {"status": "applied"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["chosen_offer_id"] == str(deakin.public_id)
        [entry] = entries(abc, student, HistoryKind.CHOSEN_OFFER_CHANGED)
        assert (entry.before, entry.after) == ("", "Deakin University")

    def test_a_visa_set_back_to_applied_for_another_offer_is_refused(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counselled_student(abc, Stage.OFFER)
        deakin = with_offer(abc, student, "Deakin University")
        rmit = with_offer(abc, student, "RMIT University")
        set_chosen(abc, student, rmit)
        with tenant_context(abc.consultancy):
            visa = Visa.objects.create(
                student=student, application=deakin, status=VisaStatus.REFUSED
            )

        response = abc.client.patch(
            f"/api/students/{student.public_id}/visas/{visa.public_id}/",
            {"status": "applied"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == VISA_NOT_CHOSEN
        assert chosen_of(abc, student) == rmit.pk
        with tenant_context(abc.consultancy):
            assert Visa.objects.get().status == VisaStatus.REFUSED

    def test_a_granted_visa_keeps_the_chosen_offer(self, abc: ConsultancyContext) -> None:
        _, student = counselled_student(abc, Stage.VISA)
        deakin = with_offer(abc, student)
        set_chosen(abc, student, deakin)
        with tenant_context(abc.consultancy):
            visa = Visa.objects.create(student=student, application=deakin)

        response = abc.client.patch(
            f"/api/students/{student.public_id}/visas/{visa.public_id}/",
            {"status": "granted"},
            format="json",
        )

        assert response.json()["chosen_offer_id"] == str(deakin.public_id)
        assert entries(abc, student, HistoryKind.CHOSEN_OFFER_CHANGED) == []


class TestTheConversion:
    """Students whose Visa is waiting or granted go ahead with that Visa's offer."""

    def test_the_newest_waiting_or_granted_visa_decides_the_chosen_offer(
        self, abc: ConsultancyContext
    ) -> None:
        migration = importlib.import_module("students.migrations.0006_chosen_offer")
        me = abc.add_staff(Role.COUNSELLOR)
        waiting = make_student(abc, me, Stage.VISA)
        granted = make_student(abc, me, Stage.VISA, name="Ram Thapa")
        refused_only = make_student(abc, me, Stage.OFFER, name="Gita Rai")
        no_visa = make_student(abc, me, Stage.OFFER, name="Hari Shah")
        with tenant_context(abc.consultancy):
            old = with_offer(abc, waiting, "Deakin University")
            new = with_offer(abc, waiting, "RMIT University")
            Visa.objects.create(student=waiting, application=old, status=VisaStatus.REFUSED)
            Visa.objects.create(student=waiting, application=new, status=VisaStatus.APPLIED)
            done = with_offer(abc, granted, "Monash University")
            Visa.objects.create(student=granted, application=done, status=VisaStatus.GRANTED)
            lost = with_offer(abc, refused_only, "Torrens University")
            Visa.objects.create(student=refused_only, application=lost, status=VisaStatus.REFUSED)
            with_offer(abc, no_visa, "Flinders University")
            migration.choose_from_visas(django_apps, None)

        assert chosen_of(abc, waiting) == new.pk
        assert chosen_of(abc, granted) == done.pk
        assert chosen_of(abc, refused_only) is None
        assert chosen_of(abc, no_visa) is None
