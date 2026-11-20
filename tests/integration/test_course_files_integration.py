"""Course Files: PDF brochures on each Course of the Catalogue (spec 53-61; issue 10).

An Admin uploads PDFs (checked by their bytes, at most 5 MB) to a Course in Configuration →
Universities, renames them and deletes them to the Bin, where they are restored or deleted for
good. Counsellors and Front Desk see them with the Course (Add application, a Student's
Applications) and open them through short signed links; Teachers never see them. A Course or
University that keeps Course Files, even in the Bin, is never deleted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django_tenants.utils import tenant_context
from rest_framework import status

from catalogue.models import Course
from course_files.models import CourseFile
from course_files.services import MAX_FILES_PER_COURSE
from staff.models import Role, Staff, StaffStatus
from students.models import Stage
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.fakes import FakeDocumentStorage, StoredFile
from tests.integration.test_applications_integration import make_application, make_course
from tests.integration.test_documents_integration import PDF_BYTES
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)
from tests.support_access import open_as_platform_admin

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

FIVE_MB = 5 * 1024 * 1024
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
BIN_URL = "/api/documents/bin/"
UNIVERSITIES_URL = "/api/catalogue/universities/"
ONLY_ADMIN = {"detail": "Only an Admin can change the Catalogue."}
NOT_FOR_TEACHERS = {"detail": "Teachers can't see this."}
ONLY_ADMIN_BIN = {"detail": "Only an Admin can do this."}
NOT_A_PDF = {"file": ["Only PDF files can be Course Files. That one isn't a PDF."]}
TOO_BIG = {"file": ["The file is too big. A Course File can be at most 5 MB."]}
CHOOSE_PDF = {"file": ["Choose a PDF file to upload."]}
NAME_IT = {"name": ["Give it a name."]}
UNKNOWN_FIELD = {"detail": ["Unknown field."]}
NOT_LIVE = {"detail": "This course is archived. Restore it before adding files."}
NOT_STORED = {"detail": "We couldn't store the file. Try again."}
NOT_DELETED = {"detail": "We couldn't delete the file. Try again."}
READERS = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)
NOT_ADMINS = (Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER)


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def files_url(course: Course | int) -> str:
    """Where a Course's Course Files are uploaded."""
    pk = course if isinstance(course, int) else course.pk
    return f"/api/catalogue/courses/{pk}/files/"


def file_url(file: CourseFile | int) -> str:
    """One Course File in use."""
    pk = file if isinstance(file, int) else file.pk
    return f"/api/catalogue/course-files/{pk}/"


def bin_url(file: CourseFile | int) -> str:
    """One Course File in the Bin."""
    pk = file if isinstance(file, int) else file.pk
    return f"/api/documents/bin/course-files/{pk}/"


def a_pdf(
    content: bytes = PDF_BYTES, name: str = "deakin-master-it-2027.pdf"
) -> SimpleUploadedFile:
    """An uploaded file as the browser sends it."""
    return SimpleUploadedFile(name, content, content_type="application/pdf")


def upload(
    client: APIClient, course: Course | int, name: object = "Master of IT brochure", **more: object
) -> _MonkeyPatchedResponse:
    """POST a PDF with its name to a Course."""
    body: dict[str, object] = {"file": a_pdf(), "name": name, **more}
    return client.post(files_url(course), body, format="multipart")


def make_course_file(
    office: ConsultancyContext, course: Course, name: str = "Brochure", *, binned: bool = False
) -> CourseFile:
    """Store a Course File (and its bytes) directly."""
    with tenant_context(office.consultancy):
        file = CourseFile.objects.create(
            course=course,
            name=name,
            file_name="brochure.pdf",
            size=len(PDF_BYTES),
            storage_key=f"{office.consultancy.schema_name}/course-files/{course.pk}/{name}.pdf",
        )
        if binned:
            CourseFile.objects.filter(pk=file.pk).update(
                binned_at=file.uploaded_at, binned_by_name="Someone", bin_reason="Old"
            )
            file.refresh_from_db()
    FakeDocumentStorage.files[file.storage_key] = StoredFile(PDF_BYTES, "application/pdf")
    return file


def stored(office: ConsultancyContext) -> list[CourseFile]:
    """Every Course File kept, in or out of the Bin."""
    with tenant_context(office.consultancy):
        return list(CourseFile.objects.all())


def as_role(office: ConsultancyContext, role: Role) -> Staff:
    """Log the office's client in as a new Staff member of this Role."""
    return office.log_in_as(role)


class TestUpload:
    def test_an_admin_uploads_a_pdf_to_a_course(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc)

        response = upload(abc.client, course)

        assert response.status_code == status.HTTP_201_CREATED
        [file] = stored(abc)
        assert response.json() == {
            "id": file.pk,
            "name": "Master of IT brochure",
            "file_name": "deakin-master-it-2027.pdf",
            "size": len(PDF_BYTES),
            "uploaded_at": file.uploaded_at.isoformat(),
        }
        assert file.course_id == course.pk
        assert file.uploaded_by_name == "Test Admin"
        assert FakeDocumentStorage.files[file.storage_key] == StoredFile(
            PDF_BYTES, "application/pdf"
        )

    def test_it_is_kept_privately_in_its_own_folder_of_the_office(
        self, abc: ConsultancyContext
    ) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc)

        upload(abc.client, course)

        [file] = stored(abc)
        assert file.storage_key.startswith(f"abcedu/course-files/{course.pk}/")
        assert file.storage_key.endswith(".pdf")
        assert "storage_key" not in str(upload(abc.client, course).json())

    def test_the_file_name_is_cleaned_and_always_ends_in_pdf(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc)
        body = {"file": a_pdf(name='../../<b>"x".html'), "name": "Brochure"}

        response = abc.client.post(files_url(course), body, format="multipart")

        assert response.json()["file_name"] == "bx.html.pdf"

    def test_the_name_is_trimmed(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)

        response = upload(abc.client, make_course(abc), name="  Brochure 2027  ")

        assert response.json()["name"] == "Brochure 2027"

    @pytest.mark.parametrize("injection", INJECTION_STRINGS)
    def test_an_injection_shaped_name_is_kept_as_words(
        self, abc: ConsultancyContext, injection: str
    ) -> None:
        as_role(abc, Role.ADMIN)

        response = upload(abc.client, make_course(abc), name=injection)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["name"] == injection

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (PNG_BYTES, NOT_A_PDF),
            (b"<html><script>alert(1)</script></html>", NOT_A_PDF),
            (b"", CHOOSE_PDF),
        ],
    )
    def test_a_file_that_isnt_a_pdf_by_its_bytes_is_refused(
        self, abc: ConsultancyContext, content: bytes, expected: dict[str, list[str]]
    ) -> None:
        as_role(abc, Role.ADMIN)
        body = {"file": a_pdf(content, "brochure.pdf"), "name": "Brochure"}

        response = abc.client.post(files_url(make_course(abc)), body, format="multipart")

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, expected)
        assert stored(abc) == []
        assert FakeDocumentStorage.files == {}

    def test_five_mb_is_accepted_and_more_is_refused(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc)
        exactly = PDF_BYTES + b"0" * (FIVE_MB - len(PDF_BYTES))

        fits = abc.client.post(
            files_url(course), {"file": a_pdf(exactly), "name": "A"}, format="multipart"
        )
        too_big = abc.client.post(
            files_url(course), {"file": a_pdf(exactly + b"0"), "name": "B"}, format="multipart"
        )

        assert fits.status_code == status.HTTP_201_CREATED
        assert (too_big.status_code, too_big.json()) == (status.HTTP_400_BAD_REQUEST, TOO_BIG)

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"name": "Brochure"}, CHOOSE_PDF),
            ({"file": "not a file", "name": "Brochure"}, CHOOSE_PDF),
            ({"file": "FILE"}, NAME_IT),
            ({"file": "FILE", "name": "   "}, NAME_IT),
            ({"file": "FILE", "name": "x" * 121}, None),
            ({"file": "FILE", "name": "Line\nbreak"}, None),
            ({"file": "FILE", "name": "Brochure", "storage_key": "abcedu/x"}, UNKNOWN_FIELD),
        ],
    )
    def test_bad_forms_are_refused_and_nothing_is_kept(
        self, abc: ConsultancyContext, body: dict[str, Any], expected: object
    ) -> None:
        as_role(abc, Role.ADMIN)
        sent = {key: a_pdf() if value == "FILE" else value for key, value in body.items()}

        response = abc.client.post(files_url(make_course(abc)), sent, format="multipart")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        if expected is not None:
            assert response.json() == expected
        assert stored(abc) == []

    def test_a_field_sent_twice_is_refused(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        body = {"file": [a_pdf(), a_pdf()], "name": "Brochure"}

        response = abc.client.post(files_url(make_course(abc)), body, format="multipart")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc) == []

    def test_json_is_refused(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)

        response = abc.client.post(files_url(make_course(abc)), {"name": "Brochure"}, format="json")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize(("archived", "university_archived"), [(True, False), (False, True)])
    def test_an_archived_course_or_university_takes_no_new_files(
        self, abc: ConsultancyContext, archived: bool, university_archived: bool
    ) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc, archived=archived, university_archived=university_archived)

        response = upload(abc.client, course)

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, NOT_LIVE)

    def test_a_course_keeps_at_most_twenty_counting_the_bin(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc)
        for number in range(MAX_FILES_PER_COURSE):
            make_course_file(abc, course, f"File {number}", binned=number == 0)

        response = upload(abc.client, course)

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            {"detail": "A course can have at most 20 Course Files."},
        )

    def test_storage_down_keeps_nothing(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        FakeDocumentStorage.is_down = True

        response = upload(abc.client, make_course(abc))

        assert (response.status_code, response.json()) == (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            NOT_STORED,
        )
        assert stored(abc) == []

    def test_an_unknown_course_is_not_found(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)

        assert upload(abc.client, 999_999).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_uploads(self, abc: ConsultancyContext, role: Role) -> None:
        as_role(abc, role)

        response = upload(abc.client, make_course(abc))

        expected = NOT_FOR_TEACHERS if role == Role.TEACHER else ONLY_ADMIN
        assert (response.status_code, response.json()) == (status.HTTP_403_FORBIDDEN, expected)
        assert stored(abc) == []

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        response = upload(abc.new_client(), make_course(abc))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_deactivated_admin_is_refused(self, abc: ConsultancyContext) -> None:
        admin = as_role(abc, Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        response = upload(abc.client, make_course(abc))

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        as_role(abc, Role.ADMIN)

        response = getattr(abc.client, method)(files_url(make_course(abc)))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_it_is_logged_with_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc)

        upload(abc.client, course, name="Secret brochure name")

        [line] = [e for e in json_logs.entries() if "Course File added" in e["message"]]
        assert f"course_id={course.pk}" in line["message"]
        assert "Secret brochure name" not in json_logs.text()
        assert "deakin-master-it-2027" not in json_logs.text()

    def test_a_platform_admin_inside_uploads_as_themselves(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)

        response = upload(visit.client, make_course(abc))

        assert response.status_code == status.HTTP_201_CREATED
        [file] = stored(abc)
        assert file.uploaded_by_id is None
        assert file.uploaded_by_name.endswith("(Platform Admin)")


class TestRename:
    def test_an_admin_renames_it_and_the_file_keeps_its_own_name(
        self, abc: ConsultancyContext
    ) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc))

        response = abc.client.patch(file_url(file), {"name": "  New name  "}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert (response.json()["name"], response.json()["file_name"]) == (
            "New name",
            "brochure.pdf",
        )

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"name": ""},
            {"name": None},
            {"name": 12},
            {"name": ["x"]},
            {"name": "x" * 121},
            {"name": "Tab\there"},
            {"name": "Brochure", "course": 3},
        ],
    )
    def test_bad_bodies_are_refused(self, abc: ConsultancyContext, body: dict[str, Any]) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc))

        response = abc.client.patch(file_url(file), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc)[0].name == "Brochure"

    @pytest.mark.parametrize("injection", INJECTION_STRINGS)
    def test_an_injection_shaped_name_is_kept_as_words(
        self, abc: ConsultancyContext, injection: str
    ) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc))

        response = abc.client.patch(file_url(file), {"name": injection}, format="json")

        assert response.json()["name"] == injection

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_renames(self, abc: ConsultancyContext, role: Role) -> None:
        as_role(abc, role)
        file = make_course_file(abc, make_course(abc))

        response = abc.client.patch(file_url(file), {"name": "Mine"}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert stored(abc)[0].name == "Brochure"

    def test_one_in_the_bin_or_unknown_is_not_found(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        binned = make_course_file(abc, make_course(abc), binned=True)

        for url in (file_url(binned), file_url(999_999)):
            response = abc.client.patch(url, {"name": "X"}, format="json")
            assert response.status_code == status.HTTP_404_NOT_FOUND


class TestLink:
    @pytest.mark.parametrize("role", READERS)
    def test_staff_who_work_with_students_open_it_by_a_short_link(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        as_role(abc, role)
        file = make_course_file(abc, make_course(abc), "Master of IT brochure")

        response = abc.client.get(f"{file_url(file)}link/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "url": (
                f"https://files.test/{file.storage_key}?disposition=inline"
                "&name=Master%20of%20IT%20brochure.pdf&type=application%2Fpdf&expires=300"
            ),
            "expires_in": 300,
        }

    def test_it_can_be_saved_instead(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.COUNSELLOR)
        file = make_course_file(abc, make_course(abc))

        response = abc.client.get(f"{file_url(file)}link/", {"action": "download"})

        assert "disposition=attachment" in response.json()["url"]

    @pytest.mark.parametrize("query", [{"action": "<script>"}, {"page": "1"}])
    def test_a_bad_query_is_refused(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        as_role(abc, Role.COUNSELLOR)
        file = make_course_file(abc, make_course(abc))

        response = abc.client.get(f"{file_url(file)}link/", query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_teachers_never_see_course_files(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.TEACHER)
        file = make_course_file(abc, make_course(abc))

        response = abc.client.get(f"{file_url(file)}link/")

        assert (response.status_code, response.json()) == (
            status.HTTP_403_FORBIDDEN,
            NOT_FOR_TEACHERS,
        )

    def test_one_in_the_bin_cant_be_opened_by_anyone(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc), binned=True)

        response = abc.client.get(f"{file_url(file)}link/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc))

        response = getattr(abc.client, method)(f"{file_url(file)}link/")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestBin:
    def test_an_admins_delete_puts_it_in_the_bin(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc))

        response = abc.client.delete(file_url(file))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        [kept] = stored(abc)
        assert kept.is_in_bin
        assert kept.bin_reason == "Deleted from the Catalogue"
        assert kept.binned_by_name == "Test Admin"
        assert file.storage_key in FakeDocumentStorage.files

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_deletes(self, abc: ConsultancyContext, role: Role) -> None:
        as_role(abc, role)
        file = make_course_file(abc, make_course(abc))

        response = abc.client.delete(file_url(file))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not stored(abc)[0].is_in_bin

    def test_the_bin_lists_it_beside_documents_with_its_kind(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc)
        file = make_course_file(abc, course, "Old brochure")
        abc.client.delete(file_url(file))

        response = abc.client.get(BIN_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1
        [row] = response.json()["results"]
        assert row == {
            "kind": "course_file",
            "course_file": {"id": file.pk, "name": "Old brochure", "file_name": "brochure.pdf"},
            "university": {"id": course.university_id, "name": "Deakin University"},
            "course": {"id": course.pk, "name": "Master of IT"},
            "by": {"id": row["by"]["id"], "name": "Test Admin"},
            "at": row["at"],
            "reason": "Deleted from the Catalogue",
        }

    def test_the_home_count_includes_it(self, abc: ConsultancyContext) -> None:
        from documents.bin import count_in_bin  # noqa: PLC0415

        make_course_file(abc, make_course(abc), binned=True)

        with tenant_context(abc.consultancy):
            assert count_in_bin() == 1

    def test_an_admin_opens_it_from_the_bin(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc), binned=True)

        response = abc.client.get(f"{bin_url(file)}link/")

        assert response.status_code == status.HTTP_200_OK
        assert file.storage_key in response.json()["url"]

    def test_an_admin_restores_it_to_its_course(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc), binned=True)

        response = abc.client.post(f"{bin_url(file)}restore/", {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["id"] == file.pk
        [kept] = stored(abc)
        assert not kept.is_in_bin
        assert (kept.bin_reason, kept.binned_by_name) == ("", "")

    def test_restoring_takes_nothing(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc), binned=True)

        response = abc.client.post(f"{bin_url(file)}restore/", {"name": "x"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc)[0].is_in_bin

    def test_an_admin_deletes_it_for_good_with_its_stored_file(
        self, abc: ConsultancyContext
    ) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc), binned=True)

        response = abc.client.delete(bin_url(file))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert stored(abc) == []
        assert file.storage_key not in FakeDocumentStorage.files

    def test_storage_down_deletes_nothing(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc), binned=True)
        FakeDocumentStorage.is_down = True

        response = abc.client.delete(bin_url(file))

        assert (response.status_code, response.json()) == (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            NOT_DELETED,
        )
        assert len(stored(abc)) == 1

    def test_one_in_use_is_never_deleted_for_good_or_restored(
        self, abc: ConsultancyContext
    ) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc))

        deleted = abc.client.delete(bin_url(file))
        restored = abc.client.post(f"{bin_url(file)}restore/", {}, format="json")
        opened = abc.client.get(f"{bin_url(file)}link/")

        assert {deleted.status_code, restored.status_code, opened.status_code} == {
            status.HTTP_404_NOT_FOUND
        }
        assert stored(abc) == [file]

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_uses_the_bin(self, abc: ConsultancyContext, role: Role) -> None:
        as_role(abc, role)
        file = make_course_file(abc, make_course(abc), binned=True)

        answers = [
            abc.client.delete(bin_url(file)),
            abc.client.post(f"{bin_url(file)}restore/", {}, format="json"),
            abc.client.get(f"{bin_url(file)}link/"),
        ]

        assert [a.status_code for a in answers] == [status.HTTP_403_FORBIDDEN] * 3
        assert stored(abc)[0].is_in_bin

    def test_a_platform_admin_inside_restores_and_deletes(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)
        course = make_course(abc)
        first = make_course_file(abc, course, "First", binned=True)
        second = make_course_file(abc, course, "Second", binned=True)

        restored = visit.client.post(f"{bin_url(first)}restore/", {}, format="json")
        deleted = visit.client.delete(bin_url(second))

        assert (restored.status_code, deleted.status_code) == (
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
        )

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        as_role(abc, Role.ADMIN)
        file = make_course_file(abc, make_course(abc), "Secret name")

        abc.client.delete(file_url(file))
        abc.client.post(f"{bin_url(file)}restore/", {}, format="json")

        text = json_logs.text()
        assert f"course_file_id={file.pk}" in text
        assert "Secret name" not in text


class TestWithTheCatalogue:
    def test_a_course_lists_its_files_in_use_and_counts_the_bin(
        self, abc: ConsultancyContext
    ) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc)
        kept = make_course_file(abc, course, "Brochure")
        make_course_file(abc, course, "Old", binned=True)

        response = abc.client.get(f"{UNIVERSITIES_URL}{course.university_id}/")

        [answer] = response.json()["courses"]
        assert answer["files"] == [
            {
                "id": kept.pk,
                "name": "Brochure",
                "file_name": "brochure.pdf",
                "size": len(PDF_BYTES),
                "uploaded_at": kept.uploaded_at.isoformat(),
            }
        ]
        assert answer["file_count"] == 2

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_the_picker_shows_a_courses_files_and_hides_archived_courses(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        as_role(abc, role)
        live = make_course(abc)
        make_course_file(abc, live, "Brochure")
        archived = make_course(abc, "Archived University", university_archived=True)
        make_course_file(abc, archived, "Hidden")

        response = abc.client.get(UNIVERSITIES_URL)

        [university] = response.json()["results"]
        assert [f["name"] for f in university["courses"][0]["files"]] == ["Brochure"]
        assert "Hidden" not in str(response.json())

    def test_a_course_with_files_even_in_the_bin_is_never_deleted(
        self, abc: ConsultancyContext
    ) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc, archived=True)
        make_course_file(abc, course, binned=True)
        university = abc.client.get(f"{UNIVERSITIES_URL}{course.university_id}/").json()
        body = {
            "name": university["name"],
            "country": university["country"],
            "archived": False,
            "courses": [],
            "deleted_courses": [course.pk],
        }

        response = abc.client.put(f"{UNIVERSITIES_URL}{course.university_id}/", body, format="json")

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            {"deleted_courses": ["It has 1 Course File (counting the Bin), so it stays archived."]},
        )
        with tenant_context(abc.consultancy):
            assert Course.objects.filter(pk=course.pk).exists()

    def test_a_university_whose_courses_keep_files_is_never_deleted(
        self, abc: ConsultancyContext
    ) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc, university_archived=True)
        make_course_file(abc, course)
        make_course_file(abc, course, "Second", binned=True)

        response = abc.client.delete(f"{UNIVERSITIES_URL}{course.university_id}/")

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            {"detail": "Its courses have 2 Course Files (counting the Bin), so it stays archived."},
        )
        with tenant_context(abc.consultancy):
            assert Course.objects.filter(pk=course.pk).exists()

    def test_an_archived_course_keeps_its_files(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.ADMIN)
        course = make_course(abc, archived=True)
        make_course_file(abc, course, "Kept")

        response = abc.client.get(f"{UNIVERSITIES_URL}{course.university_id}/")

        assert [f["name"] for f in response.json()["courses"][0]["files"]] == ["Kept"]


class TestUnderAStudentsApplications:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_each_application_shows_its_courses_files_in_use(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        staff = as_role(abc, role)
        student = make_student(abc, named_staff(abc, "Ram Counsellor"), Stage.APPLYING)
        course = make_course(abc)
        make_course_file(abc, course, "Brochure")
        make_course_file(abc, course, "Old", binned=True)
        make_application(abc, student, course)
        log_in(abc, staff)

        response = abc.client.get(f"/api/students/{student.pk}/")

        [application] = response.json()["applications"]
        assert [f["name"] for f in application["course_files"]] == ["Brochure"]

    def test_an_application_without_files_shows_none(self, abc: ConsultancyContext) -> None:
        as_role(abc, Role.COUNSELLOR)
        student = make_student(abc, None, Stage.APPLYING)
        make_application(abc, student, make_course(abc))

        response = abc.client.get(f"/api/students/{student.pk}/")

        assert response.json()["applications"][0]["course_files"] == []
