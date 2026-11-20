"""Document Examples on each Document Type (spec 62-64, 66; issue 11).

In Configuration → Documents an Admin gives a Document Type a short instruction (up to 500
characters) and up to 3 example files (PDF, JPEG or PNG by their bytes, at most 5 MB each).
Replacing or removing a file puts the old one in the Bin, where an Admin restores it or deletes
it for good. On a Student's checklist, Counsellors and Front Desk see each type's instruction and
open its examples through short signed links. Examples are never a Student's Document: they are
stored in their own folder and never count on a checklist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django_tenants.utils import tenant_context
from rest_framework import status

from document_examples.models import DocumentExample
from documents.models import DocumentType
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff, StaffStatus
from students.models import Stage
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.fakes import FakeDocumentStorage, StoredFile
from tests.integration.test_documents_integration import (
    DEFAULT_TYPES,
    JPEG_BYTES,
    PDF_BYTES,
    PNG_BYTES,
    item_states,
    make_document,
)
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    make_student,
)
from tests.support_access import open_as_platform_admin

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

FIVE_MB = 5 * 1024 * 1024
TYPES_URL = "/api/configuration/document-types/"
BIN_URL = "/api/documents/bin/"
ONLY_ADMIN = {"detail": "Only an Admin can do this."}
NOT_FOR_TEACHERS = {"detail": "Teachers can't see this."}
CHOOSE_FILE = {"file": ["Choose a PDF, JPEG or PNG file."]}
WRONG_KIND = {"file": ["An example must be a PDF, JPEG or PNG file. That one isn't."]}
TOO_BIG = {"file": ["The file is too big. An example can be at most 5 MB."]}
TOO_MANY = {"detail": "A document can have at most 3 example files. Remove one to add another."}
FULL_ON_RESTORE = {"detail": "This document already has 3 example files. Remove one first."}
UNKNOWN_FIELD = {"detail": ["Unknown field."]}
NOT_STORED = {"detail": "We couldn't store the file. Try again."}
NOT_DELETED = {"detail": "We couldn't delete the file. Try again."}
KEEPS_EXAMPLES = {
    "detail": "This document has example files (counting the Bin), so it can't be removed."
}
READERS = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)
NOT_ADMINS = (Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER)


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def type_of(office: ConsultancyContext, name: str = "Bank Statement") -> DocumentType:
    """One of the office's Document Types."""
    with tenant_context(office.consultancy):
        return DocumentType.objects.get(name=name)


def type_url(document_type: DocumentType | int) -> str:
    """One Document Type in Configuration."""
    pk = document_type if isinstance(document_type, int) else document_type.pk
    return f"{TYPES_URL}{pk}/"


def examples_url(document_type: DocumentType | int) -> str:
    """Where a Document Type's example files are added."""
    return f"{type_url(document_type)}examples/"


def instruction_url(document_type: DocumentType | int) -> str:
    """Where a Document Type's instruction is set."""
    return f"{type_url(document_type)}instruction/"


def example_url(example: DocumentExample | int) -> str:
    """One example file in use, in Configuration."""
    pk = example if isinstance(example, int) else example.pk
    return f"/api/configuration/document-examples/{pk}/"


def link_url(example: DocumentExample | int) -> str:
    """Where Staff ask for a short link to an example file in use."""
    pk = example if isinstance(example, int) else example.pk
    return f"/api/documents/examples/{pk}/link/"


def bin_url(example: DocumentExample | int) -> str:
    """One example file in the Bin."""
    pk = example if isinstance(example, int) else example.pk
    return f"/api/documents/bin/document-examples/{pk}/"


def a_file(content: bytes = PDF_BYTES, name: str = "sample-statement.pdf") -> SimpleUploadedFile:
    """A file as the browser sends it (its stated type is never believed)."""
    return SimpleUploadedFile(name, content, content_type="application/pdf")


def add_example(
    client: APIClient, document_type: DocumentType | int, file: object = None, **more: object
) -> _MonkeyPatchedResponse:
    """POST an example file to a Document Type."""
    body: dict[str, object] = {"file": a_file() if file is None else file, **more}
    return client.post(examples_url(document_type), body, format="multipart")


def replace(
    client: APIClient, example: DocumentExample | int, file: object = None
) -> _MonkeyPatchedResponse:
    """POST a new file in place of an example file."""
    body = {"file": a_file(name="new-sample.pdf") if file is None else file}
    return client.post(f"{example_url(example)}replace/", body, format="multipart")


def make_example(
    office: ConsultancyContext,
    document_type: DocumentType,
    file_name: str = "sample.pdf",
    *,
    binned: bool = False,
) -> DocumentExample:
    """Store an example file (and its bytes) directly."""
    with tenant_context(office.consultancy):
        example = DocumentExample.objects.create(
            document_type=document_type,
            file_name=file_name,
            content_type="application/pdf",
            size=len(PDF_BYTES),
            storage_key=(
                f"{office.consultancy.schema_name}/document-examples/"
                f"{document_type.pk}/{file_name}-{DocumentExample.objects.count()}.pdf"
            ),
        )
        if binned:
            DocumentExample.objects.filter(pk=example.pk).update(
                binned_at=example.uploaded_at, binned_by_name="Someone", bin_reason="Old"
            )
            example.refresh_from_db()
    FakeDocumentStorage.files[example.storage_key] = StoredFile(PDF_BYTES, "application/pdf")
    return example


def stored(office: ConsultancyContext) -> list[DocumentExample]:
    """Every example file kept, in or out of the Bin, oldest first."""
    with tenant_context(office.consultancy):
        return list(DocumentExample.objects.order_by("pk"))


def configured(office: ConsultancyContext, name: str = "Bank Statement") -> dict[str, Any]:
    """The Document Type as Configuration lists it."""
    results = office.client.get(TYPES_URL).json()["results"]
    found: dict[str, Any] = next(t for t in results if t["name"] == name)
    return found


def example_answer(example: DocumentExample) -> dict[str, object]:
    """An example file as the API answers it."""
    return {
        "id": example.pk,
        "file_name": example.file_name,
        "content_type": example.content_type,
        "size": example.size,
        "uploaded_at": example.uploaded_at.isoformat(),
    }


class TestInstruction:
    def test_an_admin_sets_the_instruction(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)
        text = "Last 6 months, with the bank's stamp on every page."

        response = abc.client.put(instruction_url(statement), {"instruction": text}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["instruction"] == text
        assert response.json()["id"] == statement.pk
        assert type_of(abc).instruction == text

    def test_every_type_starts_without_one_and_configuration_shows_it(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        listed = configured(abc)

        assert (listed["instruction"], listed["examples"], listed["example_count"]) == ("", [], 0)

    def test_it_is_trimmed_and_can_be_cleared(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)
        abc.client.put(instruction_url(statement), {"instruction": "  Six months  "}, format="json")
        assert type_of(abc).instruction == "Six months"

        response = abc.client.put(instruction_url(statement), {"instruction": ""}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert type_of(abc).instruction == ""

    def test_five_hundred_characters_fit(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(
            instruction_url(type_of(abc)), {"instruction": "x" * 500}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("injection", INJECTION_STRINGS)
    def test_an_injection_shaped_instruction_is_kept_as_words(
        self, abc: ConsultancyContext, injection: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(
            instruction_url(type_of(abc)), {"instruction": injection}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["instruction"] == injection

    @pytest.mark.parametrize(
        ("body", "refused"),
        [
            ({}, {"instruction": ["This field is required."]}),
            (
                {"instruction": "x" * 501},
                {"instruction": ["Ensure this field has no more than 500 characters."]},
            ),
            ({"instruction": 5}, {"instruction": ["Not a valid string."]}),
            ({"instruction": None}, {"instruction": ["This field may not be null."]}),
            (
                {"instruction": "Line\nbreak"},
                {"instruction": ["Use letters, numbers and punctuation only."]},
            ),
            ({"instruction": "Fine", "name": "Visa"}, UNKNOWN_FIELD),
        ],
    )
    def test_bad_input_is_refused_and_nothing_changes(
        self, abc: ConsultancyContext, body: dict[str, object], refused: dict[str, object]
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(instruction_url(type_of(abc)), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == refused
        assert type_of(abc).instruction == ""

    @pytest.mark.parametrize("body", [[], "Six months", 5])
    def test_a_body_that_isnt_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(instruction_url(type_of(abc)), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_sets_it(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = abc.client.put(
            instruction_url(type_of(abc)), {"instruction": "Six months"}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert type_of(abc).instruction == ""

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        response = abc.new_client().put(
            instruction_url(type_of(abc)), {"instruction": "Six months"}, format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_an_unknown_type_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(instruction_url(999_999), {"instruction": "x"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_only_put_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(instruction_url(type_of(abc)))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_a_platform_admin_inside_sets_it(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)

        response = visit.client.put(
            instruction_url(type_of(abc)), {"instruction": "Six months"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

    def test_it_is_logged_with_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)

        abc.client.put(instruction_url(statement), {"instruction": "Secret words"}, format="json")

        [line] = [e for e in json_logs.entries() if "instruction set" in e["message"]]
        assert f"document_type_id={statement.pk}" in line["message"]
        assert "Secret words" not in json_logs.text()


class TestAddExample:
    @pytest.mark.parametrize(
        ("content", "name", "content_type", "file_name"),
        [
            (PDF_BYTES, "sample-statement.pdf", "application/pdf", "sample-statement.pdf"),
            (JPEG_BYTES, "passport.jpeg", "image/jpeg", "passport.jpg"),
            (PNG_BYTES, "letter.png", "image/png", "letter.png"),
        ],
    )
    def test_an_admin_adds_a_pdf_jpeg_or_png(
        self,
        abc: ConsultancyContext,
        content: bytes,
        name: str,
        content_type: str,
        file_name: str,
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)

        response = add_example(abc.client, statement, a_file(content, name))

        assert response.status_code == status.HTTP_201_CREATED
        [example] = stored(abc)
        assert response.json() == example_answer(example)
        assert (example.file_name, example.content_type) == (file_name, content_type)
        assert example.document_type_id == statement.pk
        assert example.uploaded_by_name == "Test Admin"
        assert FakeDocumentStorage.files[example.storage_key] == StoredFile(content, content_type)

    def test_it_is_kept_privately_in_its_own_folder_apart_from_documents(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)

        response = add_example(abc.client, statement)

        [example] = stored(abc)
        assert example.storage_key.startswith(f"abcedu/document-examples/{statement.pk}/")
        assert example.storage_key.endswith(".pdf")
        assert "storage_key" not in str(response.json())

    def test_the_file_name_is_cleaned_and_ends_in_what_it_really_is(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add_example(abc.client, type_of(abc), a_file(name='../../<b>"x".html'))

        assert response.json()["file_name"] == "bx.html.pdf"

    def test_configuration_lists_the_examples_in_use(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)
        first = make_example(abc, statement, "first.pdf")
        make_example(abc, statement, "old.pdf", binned=True)
        second = make_example(abc, statement, "second.pdf")

        listed = configured(abc)

        assert listed["examples"] == [example_answer(first), example_answer(second)]
        assert listed["example_count"] == 3

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (b"GIF89a-not-allowed", WRONG_KIND),
            (b"<html><script>alert(1)</script></html>", WRONG_KIND),
            (b"", CHOOSE_FILE),
        ],
    )
    def test_a_file_of_the_wrong_kind_by_its_bytes_is_refused(
        self, abc: ConsultancyContext, content: bytes, expected: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add_example(abc.client, type_of(abc), a_file(content, "looks-fine.pdf"))

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, expected)
        assert stored(abc) == []

    def test_five_mb_is_accepted_and_more_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)
        just_fits = PDF_BYTES + b"0" * (FIVE_MB - len(PDF_BYTES))

        fits = add_example(abc.client, statement, a_file(just_fits))
        too_big = add_example(abc.client, statement, a_file(just_fits + b"0"))

        assert fits.status_code == status.HTTP_201_CREATED
        assert (too_big.status_code, too_big.json()) == (status.HTTP_400_BAD_REQUEST, TOO_BIG)
        assert len(stored(abc)) == 1

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({}, CHOOSE_FILE),
            ({"file": "not a file"}, CHOOSE_FILE),
            ({"file": "FILE", "storage_key": "abcedu/x"}, UNKNOWN_FIELD),
            ({"file": "FILE", "document_type": "1"}, UNKNOWN_FIELD),
        ],
    )
    def test_bad_forms_are_refused_and_nothing_is_kept(
        self, abc: ConsultancyContext, body: dict[str, Any], expected: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        sent = {key: a_file() if value == "FILE" else value for key, value in body.items()}

        response = abc.client.post(examples_url(type_of(abc)), sent, format="multipart")

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, expected)
        assert stored(abc) == []

    def test_a_field_sent_twice_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            examples_url(type_of(abc)), {"file": [a_file(), a_file()]}, format="multipart"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc) == []

    def test_json_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(examples_url(type_of(abc)), {"file": "x"}, format="json")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_a_type_keeps_at_most_three_in_use(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)
        for number in range(3):
            make_example(abc, statement, f"sample-{number}.pdf")

        response = add_example(abc.client, statement)

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, TOO_MANY)
        assert len(stored(abc)) == 3

    def test_ones_in_the_bin_dont_count_towards_three(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)
        for number in range(2):
            make_example(abc, statement, f"sample-{number}.pdf")
        make_example(abc, statement, "old.pdf", binned=True)

        response = add_example(abc.client, statement)

        assert response.status_code == status.HTTP_201_CREATED

    def test_storage_down_keeps_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        FakeDocumentStorage.is_down = True

        response = add_example(abc.client, type_of(abc))

        assert (response.status_code, response.json()) == (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            NOT_STORED,
        )
        assert stored(abc) == []

    def test_an_unknown_type_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert add_example(abc.client, 999_999).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_adds(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = add_example(abc.client, type_of(abc))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert stored(abc) == []

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        response = add_example(abc.new_client(), type_of(abc))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_deactivated_admin_is_refused(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        response = add_example(abc.client, type_of(abc))

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
        assert stored(abc) == []

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(examples_url(type_of(abc)))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_it_is_logged_with_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)

        add_example(abc.client, statement, a_file(name="secret-file-name.pdf"))

        [line] = [e for e in json_logs.entries() if "Document Example added" in e["message"]]
        assert f"document_type_id={statement.pk}" in line["message"]
        assert "secret-file-name" not in json_logs.text()

    def test_a_platform_admin_inside_adds_as_themselves(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)

        response = add_example(visit.client, type_of(abc))

        assert response.status_code == status.HTTP_201_CREATED
        [example] = stored(abc)
        assert example.uploaded_by_id is None
        assert example.uploaded_by_name.endswith("(Platform Admin)")


class TestReplaceAndRemove:
    def test_replacing_puts_the_old_file_in_the_bin(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        old = make_example(abc, type_of(abc), "old-sample.pdf")

        response = replace(abc.client, old, a_file(PNG_BYTES, "new-sample.png"))

        assert response.status_code == status.HTTP_201_CREATED
        kept_old, new = stored(abc)
        assert response.json() == example_answer(new)
        assert (new.file_name, new.content_type) == ("new-sample.png", "image/png")
        assert kept_old.is_in_bin
        assert kept_old.bin_reason == "Replaced"
        assert kept_old.binned_by_name == "Test Admin"
        assert old.storage_key in FakeDocumentStorage.files

    def test_a_full_type_can_still_replace_one(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)
        first, *_ = [make_example(abc, statement, f"s{number}.pdf") for number in range(3)]

        response = replace(abc.client, first)

        assert response.status_code == status.HTTP_201_CREATED
        assert len(configured(abc)["examples"]) == 3

    def test_a_bad_replacement_changes_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        old = make_example(abc, type_of(abc))

        response = replace(abc.client, old, a_file(b"GIF89a", "x.gif"))

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, WRONG_KIND)
        assert stored(abc) == [old]
        assert not stored(abc)[0].is_in_bin

    def test_storage_down_replaces_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        old = make_example(abc, type_of(abc))
        FakeDocumentStorage.is_down = True

        response = replace(abc.client, old)

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert stored(abc) == [old]
        assert not stored(abc)[0].is_in_bin

    def test_one_in_the_bin_or_unknown_cant_be_replaced(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        binned = make_example(abc, type_of(abc), binned=True)

        answers = [replace(abc.client, binned), replace(abc.client, 999_999)]

        assert [a.status_code for a in answers] == [status.HTTP_404_NOT_FOUND] * 2

    def test_removing_puts_it_in_the_bin(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc))

        response = abc.client.delete(example_url(example))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        [kept] = stored(abc)
        assert kept.is_in_bin
        assert kept.bin_reason == "Removed from the examples"
        assert configured(abc)["examples"] == []

    def test_one_already_in_the_bin_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc), binned=True)

        assert abc.client.delete(example_url(example)).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_replaces_or_removes(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        example = make_example(abc, type_of(abc))

        answers = [replace(abc.client, example), abc.client.delete(example_url(example))]

        assert [a.status_code for a in answers] == [status.HTTP_403_FORBIDDEN] * 2
        assert stored(abc) == [example]
        assert not stored(abc)[0].is_in_bin

    @pytest.mark.parametrize("method", ["get", "put", "patch", "post"])
    def test_only_delete_is_allowed_on_one(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc))

        response = getattr(abc.client, method)(example_url(example))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_a_type_with_examples_even_in_the_bin_is_never_removed(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        added = abc.client.post(
            TYPES_URL, {"name": "Police report", "needed_by_everyone": False}, format="json"
        ).json()
        make_example(abc, type_of(abc, "Police report"), binned=True)

        response = abc.client.delete(type_url(added["id"]))

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            KEEPS_EXAMPLES,
        )
        assert configured(abc, "Police report")["example_count"] == 1


class TestLink:
    @pytest.mark.parametrize("role", READERS)
    def test_staff_who_work_with_students_open_it_by_a_short_link(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)
        example = make_example(abc, type_of(abc), "sample-statement.pdf")

        response = abc.client.get(link_url(example))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "url": (
                f"https://files.test/{example.storage_key}?disposition=inline"
                "&name=sample-statement.pdf&type=application%2Fpdf&expires=300"
            ),
            "expires_in": 300,
        }

    def test_it_can_be_saved_instead(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        example = make_example(abc, type_of(abc))

        response = abc.client.get(link_url(example), {"action": "download"})

        assert "disposition=attachment" in response.json()["url"]

    @pytest.mark.parametrize("query", [{"action": "<script>"}, {"page": "1"}])
    def test_a_bad_query_is_refused(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        example = make_example(abc, type_of(abc))

        response = abc.client.get(link_url(example), query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_teachers_never_see_examples(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)
        example = make_example(abc, type_of(abc))

        response = abc.client.get(link_url(example))

        assert (response.status_code, response.json()) == (
            status.HTTP_403_FORBIDDEN,
            NOT_FOR_TEACHERS,
        )

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        example = make_example(abc, type_of(abc))

        assert abc.new_client().get(link_url(example)).status_code == (status.HTTP_401_UNAUTHORIZED)

    def test_one_in_the_bin_cant_be_opened_from_here(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc), binned=True)

        assert abc.client.get(link_url(example)).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc))

        response = getattr(abc.client, method)(link_url(example))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_it_is_logged_with_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        example = make_example(abc, type_of(abc), "secret-name.pdf")

        abc.client.get(link_url(example))

        assert f"document_example_id={example.pk}" in json_logs.text()
        assert "secret-name" not in json_logs.text()


class TestBin:
    def test_the_bin_lists_it_beside_documents_with_its_kind(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)
        example = make_example(abc, statement, "old-sample.pdf")
        abc.client.delete(example_url(example))

        response = abc.client.get(BIN_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1
        [row] = response.json()["results"]
        assert row == {
            "kind": "document_example",
            "document_example": {"id": example.pk, "file_name": "old-sample.pdf"},
            "document_type": {"id": statement.pk, "name": "Bank Statement"},
            "by": {"id": row["by"]["id"], "name": "Test Admin"},
            "at": row["at"],
            "reason": "Removed from the examples",
        }

    def test_the_home_count_includes_it(self, abc: ConsultancyContext) -> None:
        from documents.bin import count_in_bin  # noqa: PLC0415

        make_example(abc, type_of(abc), binned=True)

        with tenant_context(abc.consultancy):
            assert count_in_bin() == 1

    def test_an_admin_opens_it_from_the_bin(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc), binned=True)

        response = abc.client.get(f"{bin_url(example)}link/", {"action": "download"})

        assert response.status_code == status.HTTP_200_OK
        assert example.storage_key in response.json()["url"]
        assert "disposition=attachment" in response.json()["url"]

    def test_an_admin_restores_it_to_its_type(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc), binned=True)

        response = abc.client.post(f"{bin_url(example)}restore/", {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["id"] == example.pk
        [kept] = stored(abc)
        assert not kept.is_in_bin
        assert (kept.bin_reason, kept.binned_by_name) == ("", "")

    def test_a_full_type_takes_none_back(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        statement = type_of(abc)
        binned = make_example(abc, statement, "old.pdf", binned=True)
        for number in range(3):
            make_example(abc, statement, f"s{number}.pdf")

        response = abc.client.post(f"{bin_url(binned)}restore/", {}, format="json")

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            FULL_ON_RESTORE,
        )
        assert stored(abc)[0].is_in_bin

    def test_restoring_takes_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc), binned=True)

        response = abc.client.post(f"{bin_url(example)}restore/", {"x": 1}, format="json")

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            UNKNOWN_FIELD,
        )

    def test_an_admin_deletes_it_for_good_with_its_stored_file(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc), binned=True)

        response = abc.client.delete(bin_url(example))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert stored(abc) == []
        assert example.storage_key not in FakeDocumentStorage.files

    def test_storage_down_deletes_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc), binned=True)
        FakeDocumentStorage.is_down = True

        response = abc.client.delete(bin_url(example))

        assert (response.status_code, response.json()) == (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            NOT_DELETED,
        )
        assert len(stored(abc)) == 1

    def test_one_in_use_is_never_deleted_for_good_or_restored(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc))

        answers = [
            abc.client.delete(bin_url(example)),
            abc.client.post(f"{bin_url(example)}restore/", {}, format="json"),
            abc.client.get(f"{bin_url(example)}link/"),
        ]

        assert {a.status_code for a in answers} == {status.HTTP_404_NOT_FOUND}
        assert stored(abc) == [example]

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_uses_the_bin(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        example = make_example(abc, type_of(abc), binned=True)

        answers = [
            abc.client.delete(bin_url(example)),
            abc.client.post(f"{bin_url(example)}restore/", {}, format="json"),
            abc.client.get(f"{bin_url(example)}link/"),
        ]

        assert [a.status_code for a in answers] == [status.HTTP_403_FORBIDDEN] * 3
        assert all(a.json() == ONLY_ADMIN for a in answers)
        assert stored(abc)[0].is_in_bin

    def test_a_platform_admin_inside_restores_and_deletes(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        first = make_example(abc, type_of(abc), "first.pdf", binned=True)
        second = make_example(abc, type_of(abc), "second.pdf", binned=True)

        restored = visit.client.post(f"{bin_url(first)}restore/", {}, format="json")
        deleted = visit.client.delete(bin_url(second))

        assert (restored.status_code, deleted.status_code) == (
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
        )

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        abc.log_in_as(Role.ADMIN)
        example = make_example(abc, type_of(abc), "secret-name.pdf")

        abc.client.delete(example_url(example))
        abc.client.post(f"{bin_url(example)}restore/", {}, format="json")

        text = json_logs.text()
        assert f"document_example_id={example.pk}" in text
        assert "secret-name" not in text


class TestOnTheChecklist:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_each_item_shows_its_instruction_and_examples_in_use(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        me = abc.log_in_as(role)
        student = make_student(abc, me if role == Role.COUNSELLOR else None, Stage.COUNSELLING)
        statement = type_of(abc)
        with tenant_context(abc.consultancy):
            DocumentType.objects.filter(pk=statement.pk).update(instruction="Six months")
        example = make_example(abc, statement, "sample.pdf")
        make_example(abc, statement, "old.pdf", binned=True)

        body = abc.client.get(f"/api/students/{student.public_id}/documents/").json()

        items = {item["document_type"]["name"]: item for item in body["items"]}
        assert items["Bank Statement"]["instruction"] == "Six months"
        assert items["Bank Statement"]["examples"] == [example_answer(example)]
        assert (items["Passport"]["instruction"], items["Passport"]["examples"]) == ("", [])

    def test_examples_never_count_or_show_as_the_students_documents(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)
        make_example(abc, type_of(abc))

        body = abc.client.get(f"/api/students/{student.public_id}/documents/").json()

        assert item_states(body) == [(name, "not_received") for name in DEFAULT_TYPES]
        assert (body["done"], body["total"]) == (0, len(DEFAULT_TYPES))

    def test_a_received_document_still_shows_its_types_instruction(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)
        with tenant_context(abc.consultancy):
            DocumentType.objects.filter(name="Passport").update(instruction="Every page")
        make_document(abc, student, "Passport")

        body = abc.client.get(f"/api/students/{student.public_id}/documents/").json()

        assert body["items"][0]["instruction"] == "Every page"


class TestInTheDocumentsNeededEmail:
    def test_the_draft_lists_each_missing_types_instruction(self, abc: ConsultancyContext) -> None:
        switch_modules(abc.consultancy, {Module.COMMUNICATION})
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)
        with tenant_context(abc.consultancy):
            DocumentType.objects.filter(name="Bank Statement").update(
                instruction="Last 6 months, stamped"
            )

        response = abc.client.get(
            f"/api/students/{student.public_id}/emails/draft/", {"kind": "documents_needed"}
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()["body"]
        assert "- Bank Statement: Last 6 months, stamped\n" in body
        assert "- Passport\n" in body
