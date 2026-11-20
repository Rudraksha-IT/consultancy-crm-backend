"""The Bin for Documents (spec 118, 119; issue 79).

When anyone but an Admin deletes a Document or one of its files, it goes to the Bin with a reason:
it stays on the Student's list, nobody can open, check or add to it, and the checklist counts it
as missing. The person who put it there can take it out; an Admin restores it or deletes it for
good (the stored files too). An Admin's own deletes skip the Bin. History and the bell follow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django_tenants.utils import schema_context, tenant_context
from rest_framework import status

from documents.models import Document, DocumentFile
from documents.verification import Verification
from history.models import HistoryEntry, HistoryKind
from home.waiting import waiting_on
from modules.services import switch_modules
from modules.types import SWITCHABLE_MODULES
from notifications.models import Notification, NotificationKind
from staff.models import Role, Staff, StaffStatus
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.fakes import FakeDocumentStorage, StoredFile
from tests.integration.test_applications_integration import make_application, make_course
from tests.integration.test_documents_integration import (
    COUNSELLOR_OR_ADMIN,
    PDF_BYTES,
    checklist,
    files_of,
    make_document,
)
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    NOT_FOR_TEACHERS,
    NOT_YOUR_STUDENT,
    log_in,
    make_student,
    named_staff,
)
from tests.support_access import Visit, open_as_platform_admin

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

REASON = "Blurry scan"
BIN_URL = "/api/documents/bin/"
SAY_WHY = {"reason": ["Say why it should be deleted."]}
TOO_LONG = {"reason": ["Ensure this field has no more than 200 characters."]}
ADMINS_DELETE = {"detail": "As an Admin, delete it for good instead."}
ALREADY_IN_BIN = {"detail": "This is already in the Bin."}
NOT_IN_BIN = {"detail": "This isn't in the Bin."}
ONLY_FILE = {"detail": "This is the document's only file. Delete the document instead."}
IN_BIN = {"detail": "This document is in the Bin."}
ONLY_WHO_PUT_IT = {"detail": "Only the person who put it in the Bin can take it out."}
ONLY_ADMINS = {"detail": "Only an Admin can do this."}
PUT_IT_IN_THE_BIN = {"detail": "Put it in the Bin instead."}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def document_url(student: Student | int | str, document: Document | int | str) -> str:
    """One of a Student's Documents."""
    pk = student if isinstance(student, int | str) else student.public_id
    document_pk = document if isinstance(document, int | str) else document.public_id
    return f"/api/students/{pk}/documents/{document_pk}/"


def file_url(
    student: Student | int | str, document: Document | int | str, file: DocumentFile | int | str
) -> str:
    """One file of a Student's Document."""
    file_pk = file if isinstance(file, int | str) else file.public_id
    return f"{document_url(student, document)}files/{file_pk}/"


def put_in_bin(
    client: APIClient, url: str, reason: object = REASON, **extra: object
) -> _MonkeyPatchedResponse:
    """POST the item at `url` to the Bin with a reason."""
    return client.post(f"{url}bin/", {"reason": reason, **extra}, format="json")


def take_out(client: APIClient, url: str) -> _MonkeyPatchedResponse:
    """Take the item at `url` back out of the Bin."""
    return client.post(f"{url}take-out/", {}, format="json")


def restore(client: APIClient, url: str) -> _MonkeyPatchedResponse:
    """Restore the item at `url` from the Bin (an Admin)."""
    return client.post(f"{url}restore/", {}, format="json")


def with_stored_files(office: ConsultancyContext, document: Document) -> list[DocumentFile]:
    """Put the Document's files into the fake storage, and return them."""
    files = files_of(office, document)
    for file in files:
        FakeDocumentStorage.files[file.storage_key] = StoredFile(PDF_BYTES, "application/pdf")
    return files


def reloaded(office: ConsultancyContext, document: Document) -> Document | None:
    """The Document as stored now, or None once deleted."""
    with tenant_context(office.consultancy):
        return Document.objects.filter(pk=document.pk).first()


def reloaded_file(office: ConsultancyContext, file: DocumentFile) -> DocumentFile | None:
    """The file as stored now, or None once deleted."""
    with tenant_context(office.consultancy):
        return DocumentFile.objects.filter(pk=file.pk).first()


def history_of(office: ConsultancyContext, student: Student) -> list[HistoryEntry]:
    """The Student's History, oldest first."""
    with tenant_context(office.consultancy):
        return list(HistoryEntry.objects.filter(student=student).order_by("at", "pk"))


def notifications(office: ConsultancyContext) -> list[Notification]:
    """Every notification stored, oldest first."""
    with tenant_context(office.consultancy):
        return list(Notification.objects.order_by("pk"))


def passport_item(body: dict[str, Any]) -> dict[str, Any]:
    """The Passport line of a checklist answer."""
    [item] = [one for one in body["items"] if one["document_type"]["name"] == "Passport"]
    assert isinstance(item, dict)
    return item


def binned(office: ConsultancyContext, student: Student, files: int = 1) -> tuple[Staff, Document]:
    """A Counsellor's Student with a Passport they have put in the Bin; the client is theirs."""
    me = student.assigned_counsellor
    assert me is not None
    document = make_document(office, student, files=files)
    log_in(office, me)
    assert put_in_bin(office.client, document_url(student, document)).status_code == 200
    return me, document


@pytest.fixture
def mine(abc: ConsultancyContext) -> tuple[Staff, Student]:
    """A logged-in Counsellor and their counselled Student."""
    me = named_staff(abc, "Bikash Thapa")
    log_in(abc, me)
    return me, make_student(abc, me, Stage.COUNSELLING)


class TestPuttingADocumentInTheBin:
    def test_it_stays_on_the_list_marked_in_the_bin_and_counts_as_missing(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        me, student = mine
        document = make_document(abc, student, files=2)
        total_before = checklist(abc, student).json()["done"]

        response = put_in_bin(abc.client, document_url(student, document))

        assert response.status_code == status.HTTP_200_OK
        item = passport_item(response.json())
        assert item["state"] == "not_received"
        assert item["document"] is None
        [shown] = item["in_bin"]
        assert shown["id"] == str(document.public_id)
        assert [f["file_name"] for f in shown["files"]] == ["passport.pdf", "page-2.pdf"]
        assert shown["bin"]["reason"] == REASON
        assert shown["bin"]["by"] == {"id": str(me.public_id), "name": "Bikash Thapa"}
        assert response.json()["done"] == total_before - 1
        stored = reloaded(abc, document)
        assert stored is not None
        assert stored.binned_at is not None
        assert (stored.bin_reason, stored.binned_by_id) == (REASON, me.pk)

    def test_an_older_copy_in_use_shows_again_while_the_newest_is_in_the_bin(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        older = make_document(abc, student, verification=Verification.VERIFIED)
        newer = make_document(abc, student)

        response = put_in_bin(abc.client, document_url(student, newer))

        item = passport_item(response.json())
        assert item["state"] == "verified"
        assert item["document"]["id"] == str(older.public_id)
        assert [one["id"] for one in item["in_bin"]] == [str(newer.public_id)]

    def test_nobody_can_open_check_or_add_to_it(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student)
        [file] = files_of(abc, document)
        put_in_bin(abc.client, document_url(student, document))
        admin = abc.log_in_as(Role.ADMIN)
        assert admin.role == Role.ADMIN

        link = abc.client.get(f"{file_url(student, document, file)}link/")
        check = abc.client.post(
            f"{document_url(student, document)}verification/",
            {"verification": "verified"},
            format="json",
        )
        added = abc.client.post(
            f"{document_url(student, document)}files/",
            {"file": [_pdf("page-2.pdf")]},
            format="multipart",
        )

        assert link.status_code == status.HTTP_404_NOT_FOUND
        assert "url" not in link.json()
        assert (check.status_code, check.json()) == (status.HTTP_400_BAD_REQUEST, IN_BIN)
        assert (added.status_code, added.json()) == (status.HTTP_400_BAD_REQUEST, IN_BIN)
        stored = reloaded(abc, document)
        assert stored is not None
        assert stored.verification == Verification.PENDING
        assert len(files_of(abc, document)) == 1

    def test_a_new_copy_can_be_uploaded_while_one_is_in_the_bin(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student)
        put_in_bin(abc.client, document_url(student, document))
        newer = make_document(abc, student)

        response = abc.client.post(
            f"{document_url(student, newer)}verification/",
            {"verification": "verified"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert passport_item(response.json())["state"] == "verified"

    def test_it_is_not_waiting_for_a_check_on_home_or_the_documents_page(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        me, student = mine
        document = make_document(abc, student)
        put_in_bin(abc.client, document_url(student, document))

        page = abc.client.get("/api/documents/students/?waiting=true").json()

        assert page["count"] == 0
        with tenant_context(abc.consultancy):
            assert waiting_on(me).documents == 0

    def test_it_is_not_listed_on_its_application(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        application = make_application(abc, student, make_course(abc, "Federation University"))
        document = make_document(abc, student, files=2)
        kept = make_document(abc, student, "+2 Transcript", files=2)
        _, page = files_of(abc, kept)
        with tenant_context(abc.consultancy):
            Document.objects.filter(pk__in=[document.pk, kept.pk]).update(application=application)
        put_in_bin(abc.client, document_url(student, document))
        put_in_bin(abc.client, file_url(student, kept, page))

        answer = abc.client.get(f"/api/students/{student.public_id}/").json()

        [shown] = answer["applications"][0]["documents"]
        assert shown["id"] == str(kept.public_id)
        assert len(shown["files"]) == 1
        assert page.pk not in [f["id"] for f in shown["files"]]

    def test_history_records_it_with_the_reason_and_who(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student)

        put_in_bin(abc.client, document_url(student, document), "Wrong Student")

        [entry] = history_of(abc, student)
        assert entry.kind == HistoryKind.DOCUMENT_BINNED
        assert (entry.after, entry.detail, entry.by_name) == (
            "Passport",
            "Wrong Student",
            "Bikash Thapa",
        )

    def test_every_active_admin_is_told(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        first = named_staff(abc, "Anita Rai", Role.ADMIN)
        second = named_staff(abc, "Binod Shah", Role.ADMIN)
        gone = named_staff(abc, "Chandra Lal", Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=gone.pk).update(status=StaffStatus.DEACTIVATED)
        document = make_document(abc, student)

        put_in_bin(abc.client, document_url(student, document))

        told = notifications(abc)
        assert sorted(n.recipient_id for n in told) == sorted([first.pk, second.pk])
        assert {n.kind for n in told} == {NotificationKind.BIN_ADDED}
        log_in(abc, first)
        [shown] = abc.client.get("/api/notifications/").json()["results"]
        assert shown["kind"] == "bin_added"
        assert shown["by"] == {"id": str(mine[0].public_id), "name": "Bikash Thapa"}
        assert shown["student"] == {"id": str(student.public_id), "name": student.name}
        assert shown["bin"] == {"item": "Passport", "reason": REASON}

    def test_it_is_logged_with_ids_only(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student], json_logs: JsonLogs
    ) -> None:
        _, student = mine
        document = make_document(abc, student)

        put_in_bin(abc.client, document_url(student, document), "Belongs to her brother")

        [line] = [e for e in json_logs.entries() if "Put in the Bin" in e["message"]]
        assert f"document_id={document.pk}" in line["message"]
        assert "brother" not in json_logs.text()
        assert "passport.pdf" not in json_logs.text()


def _pdf(name: str) -> object:
    from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: PLC0415

    return SimpleUploadedFile(name, PDF_BYTES, content_type="application/pdf")


class TestPuttingOneFileInTheBin:
    def test_the_file_goes_to_the_bin_and_the_document_stays(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        me, student = mine
        document = make_document(abc, student, files=2)
        first, second = files_of(abc, document)

        response = put_in_bin(abc.client, file_url(student, document, second))

        assert response.status_code == status.HTTP_200_OK
        item = passport_item(response.json())
        assert item["state"] == "waiting"
        shown = item["document"]
        assert [f["id"] for f in shown["files"]] == [str(first.public_id)]
        [in_bin] = shown["binned_files"]
        assert (in_bin["id"], in_bin["file_name"]) == (str(second.public_id), "page-2.pdf")
        assert in_bin["bin"]["reason"] == REASON
        assert in_bin["bin"]["by"] == {"id": str(me.public_id), "name": "Bikash Thapa"}
        [entry] = history_of(abc, student)
        assert (entry.kind, entry.after) == (HistoryKind.DOCUMENT_BINNED, "page-2.pdf of Passport")

    def test_its_link_is_refused(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student, files=2)
        _, second = files_of(abc, document)
        put_in_bin(abc.client, file_url(student, document, second))
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(f"{file_url(student, document, second)}link/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_only_file_left_cannot_be_binned_alone(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student, files=2)
        first, second = files_of(abc, document)
        put_in_bin(abc.client, file_url(student, document, second))

        response = put_in_bin(abc.client, file_url(student, document, first))

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, ONLY_FILE)

    def test_a_file_already_in_the_bin_is_refused(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student, files=3)
        _, second, _ = files_of(abc, document)
        put_in_bin(abc.client, file_url(student, document, second))

        response = put_in_bin(abc.client, file_url(student, document, second))

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            ALREADY_IN_BIN,
        )

    def test_a_document_already_in_the_bin_is_refused(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student, files=2)
        _, second = files_of(abc, document)
        put_in_bin(abc.client, document_url(student, document))

        responses = [
            put_in_bin(abc.client, document_url(student, document)),
            put_in_bin(abc.client, file_url(student, document, second)),
        ]

        assert [(r.status_code, r.json()) for r in responses] == [
            (status.HTTP_400_BAD_REQUEST, ALREADY_IN_BIN)
        ] * 2


class TestWhoMayPutInTheBin:
    @pytest.mark.parametrize(
        ("role", "refusal"),
        [(Role.FRONT_DESK, COUNSELLOR_OR_ADMIN), (Role.COUNSELLOR, NOT_YOUR_STUDENT)],
    )
    def test_someone_who_may_not_change_the_students_documents_is_refused(
        self, abc: ConsultancyContext, role: Role, refusal: dict[str, str]
    ) -> None:
        student = make_student(abc, named_staff(abc, "Other Counsellor"), Stage.COUNSELLING)
        document = make_document(abc, student)
        abc.log_in_as(role)

        response = put_in_bin(abc.client, document_url(student, document))

        assert (response.status_code, response.json()) == (status.HTTP_403_FORBIDDEN, refusal)
        stored = reloaded(abc, document)
        assert stored is not None
        assert stored.binned_at is None

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        switch = abc.consultancy
        with schema_context("public"):
            switch_modules(switch, SWITCHABLE_MODULES)
        student = make_student(abc, named_staff(abc, "Other Counsellor"), Stage.COUNSELLING)
        document = make_document(abc, student)
        abc.log_in_as(Role.TEACHER)

        response = put_in_bin(abc.client, document_url(student, document))

        assert (response.status_code, response.json()) == (
            status.HTTP_403_FORBIDDEN,
            NOT_FOR_TEACHERS,
        )

    def test_an_admin_deletes_instead(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, named_staff(abc, "Other Counsellor"), Stage.COUNSELLING)
        document = make_document(abc, student)
        abc.log_in_as(Role.ADMIN)

        response = put_in_bin(abc.client, document_url(student, document))

        assert (response.status_code, response.json()) == (
            status.HTTP_403_FORBIDDEN,
            ADMINS_DELETE,
        )

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, named_staff(abc, "Other Counsellor"), Stage.COUNSELLING)
        document = make_document(abc, student)

        response = put_in_bin(abc.new_client(), document_url(student, document))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_another_students_or_unknown_document_is_not_found(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        me, student = mine
        other = make_student(abc, me, Stage.COUNSELLING, name="Ram Thapa")
        document = make_document(abc, other)
        [file] = files_of(abc, document)

        responses = [
            put_in_bin(abc.client, document_url(student, document)),
            put_in_bin(abc.client, document_url(student, 9999)),
            put_in_bin(abc.client, file_url(student, document, file)),
            put_in_bin(abc.client, file_url(other, document, 9999)),
            put_in_bin(abc.client, document_url(9999, document)),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 5


class TestRefusedReasons:
    @pytest.mark.parametrize("reason", ["", "   ", None, 5, True, ["Blurry"], {"a": 1}])
    def test_a_missing_or_wrong_type_of_reason_is_refused(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student], reason: object
    ) -> None:
        _, student = mine
        document = make_document(abc, student)

        response = put_in_bin(abc.client, document_url(student, document), reason)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "reason" in response.json()
        stored = reloaded(abc, document)
        assert stored is not None
        assert stored.binned_at is None

    def test_no_reason_at_all_is_refused(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student)

        response = abc.client.post(f"{document_url(student, document)}bin/", {}, format="json")

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, SAY_WHY)

    def test_an_over_long_reason_is_refused(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student)

        response = put_in_bin(abc.client, document_url(student, document), "x" * 201)

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, TOO_LONG)

    def test_line_breaks_and_control_characters_are_refused(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student)

        response = put_in_bin(abc.client, document_url(student, document), "Blurry\nscan")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unknown_fields_are_refused_and_never_stored(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student)

        response = put_in_bin(abc.client, document_url(student, document), binned_by=1)

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            {"detail": ["Unknown field."]},
        )

    def test_a_form_is_refused(self, abc: ConsultancyContext, mine: tuple[Staff, Student]) -> None:
        _, student = mine
        document = make_document(abc, student)

        response = abc.client.post(
            f"{document_url(student, document)}bin/", {"reason": REASON}, format="multipart"
        )

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("reason", INJECTION_STRINGS)
    def test_injection_shaped_reasons_are_kept_as_plain_text(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student], reason: str
    ) -> None:
        _, student = mine
        document = make_document(abc, student)

        response = put_in_bin(abc.client, document_url(student, document), reason)

        assert response.status_code == status.HTTP_200_OK
        [shown] = passport_item(response.json())["in_bin"]
        assert shown["bin"]["reason"] == reason
        stored = reloaded(abc, document)
        assert stored is not None
        assert stored.bin_reason == reason

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student], method: str
    ) -> None:
        _, student = mine
        document = make_document(abc, student)
        url = f"{document_url(student, document)}bin/"

        response = getattr(abc.client, method)(url, {"reason": REASON}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestTakingItOut:
    def test_who_put_it_in_takes_the_document_out(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        _, document = binned(abc, student)

        response = take_out(abc.client, document_url(student, document))

        assert response.status_code == status.HTTP_200_OK
        item = passport_item(response.json())
        assert (item["state"], item["document"]["id"], item["in_bin"]) == (
            "waiting",
            str(document.public_id),
            [],
        )
        stored = reloaded(abc, document)
        assert stored is not None
        assert (stored.binned_at, stored.bin_reason, stored.binned_by_id) == (None, "", None)
        kinds = [entry.kind for entry in history_of(abc, student)]
        assert kinds == [HistoryKind.DOCUMENT_BINNED, HistoryKind.DOCUMENT_TAKEN_OUT]

    def test_who_put_it_in_takes_a_file_out(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student, files=2)
        _, second = files_of(abc, document)
        put_in_bin(abc.client, file_url(student, document, second))

        response = take_out(abc.client, file_url(student, document, second))

        assert response.status_code == status.HTTP_200_OK
        shown = passport_item(response.json())["document"]
        assert len(shown["files"]) == 2
        assert shown["binned_files"] == []

    def test_anyone_else_is_refused_even_an_admin(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        _, document = binned(abc, student)
        abc.log_in_as(Role.ADMIN)

        response = take_out(abc.client, document_url(student, document))

        assert (response.status_code, response.json()) == (
            status.HTTP_403_FORBIDDEN,
            ONLY_WHO_PUT_IT,
        )

    def test_something_not_in_the_bin_is_refused(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student)

        response = take_out(abc.client, document_url(student, document))

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            NOT_IN_BIN,
        )

    def test_unknown_fields_are_refused(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        _, document = binned(abc, student)

        response = abc.client.post(
            f"{document_url(student, document)}take-out/", {"x": 1}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestTheBinTab:
    def test_an_admin_sees_every_item_newest_first(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        me, student = mine
        whole = make_document(abc, student, files=2)
        put_in_bin(abc.client, document_url(student, whole), "Uploaded twice")
        other = make_document(abc, student, "+2 Transcript", files=2)
        _, page = files_of(abc, other)
        put_in_bin(abc.client, file_url(student, other, page))
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(BIN_URL)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["count"] == 2
        first, second = body["results"]
        assert first["kind"] == "file"
        assert first["document"] == {"id": str(other.public_id), "name": "+2 Transcript"}
        assert [f["id"] for f in first["files"]] == [str(page.public_id)]
        assert first["student"] == {"id": str(student.public_id), "name": student.name}
        assert first["by"] == {"id": str(me.public_id), "name": "Bikash Thapa"}
        assert first["reason"] == REASON
        assert second["kind"] == "document"
        assert [f["file_name"] for f in second["files"]] == ["passport.pdf", "page-2.pdf"]
        assert second["reason"] == "Uploaded twice"

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_every_other_role_is_refused(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = abc.client.get(BIN_URL)

        assert (response.status_code, response.json()) == (status.HTTP_403_FORBIDDEN, ONLY_ADMINS)

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        with schema_context("public"):
            switch_modules(abc.consultancy, SWITCHABLE_MODULES)
        abc.log_in_as(Role.TEACHER)

        assert abc.client.get(BIN_URL).status_code == status.HTTP_403_FORBIDDEN

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.new_client().get(BIN_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_unknown_query_or_page_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        responses = [
            abc.client.get(f"{BIN_URL}?x=1"),
            abc.client.get(f"{BIN_URL}?page=abc"),
            abc.client.get(f"{BIN_URL}?page=5"),
        ]

        assert [r.status_code for r in responses] == [400, 400, 404]

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(BIN_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_an_admin_opens_a_file_in_the_bin_from_the_bin_tab(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        _, document = binned(abc, student)
        [file] = files_of(abc, document)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(f"{BIN_URL}files/{file.public_id}/link/?action=download")

        assert response.status_code == status.HTTP_200_OK
        assert file.storage_key in response.json()["url"]
        assert "disposition=attachment" in response.json()["url"]

    def test_a_file_not_in_the_bin_has_no_bin_link(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student)
        [file] = files_of(abc, document)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(f"{BIN_URL}files/{file.public_id}/link/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_nobody_but_an_admin_gets_a_bin_link(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        _, document = binned(abc, student)
        [file] = files_of(abc, document)

        response = abc.client.get(f"{BIN_URL}files/{file.public_id}/link/")

        assert (response.status_code, response.json()) == (status.HTTP_403_FORBIDDEN, ONLY_ADMINS)

    def test_admin_home_counts_the_bin(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        binned(abc, student)
        abc.log_in_as(Role.ADMIN)

        admin_home = abc.client.get("/api/home/waiting/").json()
        log_in(abc, mine[0])
        counsellor_home = abc.client.get("/api/home/waiting/").json()

        assert admin_home["documents_in_bin"] == 1
        assert counsellor_home["documents_in_bin"] == 0


class TestRestoring:
    def test_an_admin_restores_a_document_and_who_put_it_in_is_told(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        me, student = mine
        _, document = binned(abc, student)
        admin = named_staff(abc, "Anita Rai", Role.ADMIN)
        log_in(abc, admin)

        response = restore(abc.client, document_url(student, document))

        assert response.status_code == status.HTTP_200_OK
        assert passport_item(response.json())["document"]["id"] == str(document.public_id)
        entry = history_of(abc, student)[-1]
        assert (entry.kind, entry.after, entry.detail, entry.by_name) == (
            HistoryKind.DOCUMENT_RESTORED,
            "Passport",
            REASON,
            "Anita Rai",
        )
        told = notifications(abc)[-1]
        assert (told.recipient_id, told.kind) == (me.pk, NotificationKind.BIN_RESTORED)
        assert abc.client.get(BIN_URL).json()["count"] == 0

    def test_an_admin_restores_a_file(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student, files=2)
        _, second = files_of(abc, document)
        put_in_bin(abc.client, file_url(student, document, second))
        abc.log_in_as(Role.ADMIN)

        response = restore(abc.client, file_url(student, document, second))

        assert response.status_code == status.HTTP_200_OK
        assert len(passport_item(response.json())["document"]["files"]) == 2

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_nobody_else_restores(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student], role: Role
    ) -> None:
        _, student = mine
        _, document = binned(abc, student)
        abc.log_in_as(role)

        response = restore(abc.client, document_url(student, document))

        assert (response.status_code, response.json()) == (status.HTTP_403_FORBIDDEN, ONLY_ADMINS)
        stored = reloaded(abc, document)
        assert stored is not None
        assert stored.binned_at is not None

    def test_something_not_in_the_bin_is_refused(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student)
        abc.log_in_as(Role.ADMIN)

        response = restore(abc.client, document_url(student, document))

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, NOT_IN_BIN)


class TestDeletingForGood:
    def test_an_admin_deletes_a_binned_document_and_its_stored_files(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        me, student = mine
        _, document = binned(abc, student, files=2)
        files = with_stored_files(abc, document)
        log_in(abc, named_staff(abc, "Anita Rai", Role.ADMIN))

        response = abc.client.delete(document_url(student, document))

        assert response.status_code == status.HTTP_200_OK
        assert reloaded(abc, document) is None
        assert all(f.storage_key not in FakeDocumentStorage.files for f in files)
        assert passport_item(response.json())["in_bin"] == []
        entry = history_of(abc, student)[-1]
        assert (entry.kind, entry.after, entry.detail) == (
            HistoryKind.DOCUMENT_DELETED_FROM_BIN,
            "Passport",
            REASON,
        )
        told = notifications(abc)[-1]
        assert (told.recipient_id, told.kind) == (me.pk, NotificationKind.BIN_DELETED)
        log_in(abc, me)
        shown = abc.client.get("/api/notifications/").json()["results"][0]
        assert shown["by"]["name"] == "Anita Rai"
        assert shown["bin"] == {"item": "Passport", "reason": REASON}

    def test_an_admin_deletes_a_binned_file(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        document = make_document(abc, student, files=2)
        first, second = with_stored_files(abc, document)
        put_in_bin(abc.client, file_url(student, document, second))
        abc.log_in_as(Role.ADMIN)

        response = abc.client.delete(file_url(student, document, second))

        assert response.status_code == status.HTTP_200_OK
        assert reloaded_file(abc, second) is None
        assert reloaded_file(abc, first) is not None
        assert second.storage_key not in FakeDocumentStorage.files
        assert first.storage_key in FakeDocumentStorage.files

    def test_an_admins_own_delete_skips_the_bin(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, named_staff(abc, "Other Counsellor"), Stage.COUNSELLING)
        document = make_document(abc, student)
        [file] = with_stored_files(abc, document)
        log_in(abc, named_staff(abc, "Anita Rai", Role.ADMIN))

        response = abc.client.delete(document_url(student, document))

        assert response.status_code == status.HTTP_200_OK
        assert reloaded(abc, document) is None
        assert file.storage_key not in FakeDocumentStorage.files
        [entry] = history_of(abc, student)
        assert (entry.kind, entry.after, entry.by_name) == (
            HistoryKind.DOCUMENT_DELETED,
            "Passport",
            "Anita Rai",
        )
        assert notifications(abc) == []

    def test_an_admin_deletes_one_file_but_never_the_only_one(
        self, abc: ConsultancyContext
    ) -> None:
        student = make_student(abc, named_staff(abc, "Other Counsellor"), Stage.COUNSELLING)
        document = make_document(abc, student, files=2)
        first, second = files_of(abc, document)
        abc.log_in_as(Role.ADMIN)

        deleted = abc.client.delete(file_url(student, document, second))
        last = abc.client.delete(file_url(student, document, first))

        assert deleted.status_code == status.HTTP_200_OK
        assert (last.status_code, last.json()) == (status.HTTP_400_BAD_REQUEST, ONLY_FILE)
        assert reloaded_file(abc, first) is not None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_nobody_else_deletes(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student], role: Role
    ) -> None:
        _, student = mine
        _, document = binned(abc, student)
        if role == Role.FRONT_DESK:
            abc.log_in_as(role)

        response = abc.client.delete(document_url(student, document))

        assert (response.status_code, response.json()) == (status.HTTP_403_FORBIDDEN, ONLY_ADMINS)
        assert reloaded(abc, document) is not None

    def test_storage_failing_keeps_the_item(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        _, student = mine
        _, document = binned(abc, student)
        with_stored_files(abc, document)
        abc.log_in_as(Role.ADMIN)
        FakeDocumentStorage.is_down = True

        response = abc.client.delete(document_url(student, document))

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json() == {"detail": "We couldn't delete the files. Try again."}
        assert reloaded(abc, document) is not None
        assert [e.kind for e in history_of(abc, student)] == [HistoryKind.DOCUMENT_BINNED]


class TestPlatformAdminInside:
    def test_a_platform_admins_delete_goes_to_the_bin_under_their_own_name(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visit: Visit = open_as_platform_admin(platform_site, abc)
        student = make_student(abc, named_staff(abc, "Other Counsellor"), Stage.COUNSELLING)
        document = make_document(abc, student)

        direct = visit.client.delete(document_url(student, document))
        response = put_in_bin(visit.client, document_url(student, document))

        assert (direct.status_code, direct.json()) == (
            status.HTTP_403_FORBIDDEN,
            PUT_IT_IN_THE_BIN,
        )
        assert response.status_code == status.HTTP_200_OK
        stored = reloaded(abc, document)
        assert stored is not None
        assert stored.binned_by_id is None
        assert stored.binned_by_name.endswith("(Platform Admin)")
        [entry] = history_of(abc, student)
        assert entry.by_platform_admin

    def test_a_platform_admin_deletes_for_good_from_the_bin_and_nobody_is_told(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visit: Visit = open_as_platform_admin(platform_site, abc)
        student = make_student(abc, named_staff(abc, "Other Counsellor"), Stage.COUNSELLING)
        document = make_document(abc, student, files=2)
        _, second = with_stored_files(abc, document)
        put_in_bin(visit.client, file_url(student, document, second))

        restored = restore(visit.client, file_url(student, document, second))
        put_in_bin(visit.client, file_url(student, document, second))
        deleted = visit.client.delete(file_url(student, document, second))

        assert (restored.status_code, deleted.status_code) == (200, 200)
        assert reloaded_file(abc, second) is None
        assert [n.kind for n in notifications(abc)] == []


class TestWhatTheChecklistTellsTheViewer:
    def test_a_counsellor_puts_in_the_bin_and_is_named(
        self, abc: ConsultancyContext, mine: tuple[Staff, Student]
    ) -> None:
        me, student = mine

        body = checklist(abc, student).json()

        assert (body["deletes_for_good"], body["viewer_id"]) == (False, str(me.public_id))

    def test_an_admin_deletes_for_good(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, named_staff(abc, "Other Counsellor"), Stage.COUNSELLING)
        admin = abc.log_in_as(Role.ADMIN)

        body = checklist(abc, student).json()

        assert (body["deletes_for_good"], body["viewer_id"]) == (True, str(admin.public_id))

    def test_a_platform_admin_puts_in_the_bin_and_has_no_staff_id(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visit: Visit = open_as_platform_admin(platform_site, abc)
        student = make_student(abc, named_staff(abc, "Other Counsellor"), Stage.COUNSELLING)

        body = visit.client.get(f"/api/students/{student.public_id}/documents/").json()

        assert (body["deletes_for_good"], body["viewer_id"]) == (False, None)
