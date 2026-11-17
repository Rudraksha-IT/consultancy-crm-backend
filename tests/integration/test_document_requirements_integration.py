"""Document Requirements per Course and Student (spec 63a-63c, 64; issues 40, 80).

Country lists are in test_country_document_lists_integration.py; University lists are gone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from catalogue.models import Course
from documents.models import DocumentRequirement, DocumentType
from documents.serializers import DocumentTypeListField
from documents.services import MAX_DOCUMENT_TYPES
from documents.verification import Verification
from staff.models import Role
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_applications_integration import make_application, make_course
from tests.integration.test_documents_integration import (
    COUNSELLOR_OR_ADMIN,
    DEFAULT_TYPES,
    make_document,
)
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    NOT_FOR_TEACHERS,
    NOT_YOUR_STUDENT,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

PICKER_URL = "/api/documents/types/"
CONFIG_TYPES_URL = "/api/configuration/document-types/"
CATALOGUE_URL = "/api/catalogue/universities/"
ONLY_ADMIN_CATALOGUE = {"detail": "Only an Admin can change the Catalogue."}
TEACHERS_NO_CATALOGUE = {"detail": "Teachers can't see this."}
ONLY_COUNSELLORS = {"detail": "Only counsellors and Admins can see this."}
ONLY_ADMIN_CONFIG = {"detail": "Only an Admin can change the Configuration."}
CHOOSE_TYPES = "Choose documents from the list."
ADDED_FOR_STUDENT = "Added for this Student"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def course_documents_url(course: Course | int) -> str:
    """Where a Course's extra documents are set."""
    pk = course if isinstance(course, int) else course.pk
    return f"/api/catalogue/courses/{pk}/documents/"


def asked_url(student: Student | int, type_id: int | None = None) -> str:
    """Where the documents asked for one Student are set, or one is taken off."""
    pk = student if isinstance(student, int) else student.pk
    one = "" if type_id is None else f"{type_id}/"
    return f"/api/students/{pk}/documents/asked/{one}"


def checklist_url(student: Student) -> str:
    """The Student's checklist."""
    return f"/api/students/{student.pk}/documents/"


def optional_type(office: ConsultancyContext, name: str) -> DocumentType:
    """Store a Document Type asked for only when needed."""
    with tenant_context(office.consultancy):
        return DocumentType.objects.create(name=name, needed_by_everyone=False)


def type_id(office: ConsultancyContext, name: str) -> int:
    """The id of one of the office's Document Types."""
    with tenant_context(office.consultancy):
        return int(DocumentType.objects.get(name=name).pk)


def put(office: ConsultancyContext, url: str, ids: object) -> _MonkeyPatchedResponse:
    """PUT a list of Document Type ids."""
    return office.client.put(url, {"document_types": ids}, format="json")


def stored_requirements(office: ConsultancyContext) -> list[tuple[str, str]]:
    """Every stored requirement as (what asks for it, type name), oldest first."""
    with tenant_context(office.consultancy):
        rows = DocumentRequirement.objects.order_by("pk").select_related(
            "course", "student", "document_type"
        )
        return [(_target_name(row), row.document_type.name) for row in rows]


def _target_name(row: DocumentRequirement) -> str:
    if row.country:
        return row.get_country_display()
    target = row.course or row.student
    assert target is not None
    return str(target.name)


def items(body: dict[str, object]) -> list[tuple[str, str]]:
    """Each checklist item's type name and reason."""
    found = body["items"]
    assert isinstance(found, list)
    return [(item["document_type"]["name"], item["reason"]) for item in found]


def own_student(office: ConsultancyContext, stage: Stage = Stage.COUNSELLING) -> Student:
    """Log in as a Counsellor and give them a Student."""
    me = office.log_in_as(Role.COUNSELLOR)
    return make_student(office, me, stage)


class TestCourseDocuments:
    def test_an_admin_sets_what_a_course_also_needs(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc)
        cv = optional_type(abc, "CV")

        response = put(abc, course_documents_url(course), [cv.pk])

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["documents"] == [{"id": cv.pk, "name": "CV"}]
        assert response.json()["name"] == "Master of IT"
        assert stored_requirements(abc) == [("Master of IT", "CV")]

    def test_the_catalogue_shows_what_each_course_also_needs_and_universities_have_no_list(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc, "Federation University")
        cv = optional_type(abc, "CV")
        put(abc, course_documents_url(course), [cv.pk])

        [university] = abc.client.get(CATALOGUE_URL).json()["results"]

        assert "documents" not in university
        assert university["courses"][0]["documents"] == [{"id": cv.pk, "name": "CV"}]

    def test_the_university_list_endpoint_is_gone(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc)
        cv = optional_type(abc, "CV")

        response = put(abc, f"{CATALOGUE_URL}{course.university_id}/documents/", [cv.pk])

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored_requirements(abc) == []

    def test_setting_again_replaces_the_list_and_an_empty_list_clears_it(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc)
        gs = optional_type(abc, "Genuine Student statement")
        cv = optional_type(abc, "CV")
        url = course_documents_url(course)
        put(abc, url, [gs.pk, cv.pk])

        put(abc, url, [cv.pk])
        kept = stored_requirements(abc)
        cleared = put(abc, url, [])

        assert kept == [("Master of IT", "CV")]
        assert cleared.json()["documents"] == []
        assert stored_requirements(abc) == []

    def test_an_archived_course_can_still_be_changed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc, archived=True)
        cv = optional_type(abc, "CV")

        response = put(abc, course_documents_url(course), [cv.pk])

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        ("role", "refused"),
        [
            (Role.COUNSELLOR, ONLY_ADMIN_CATALOGUE),
            (Role.FRONT_DESK, ONLY_ADMIN_CATALOGUE),
            (Role.TEACHER, TEACHERS_NO_CATALOGUE),
        ],
    )
    def test_only_admins_may_change_them(
        self, abc: ConsultancyContext, role: Role, refused: dict[str, str]
    ) -> None:
        abc.log_in_as(role)
        course = make_course(abc)
        cv = optional_type(abc, "CV")

        response = put(abc, course_documents_url(course), [cv.pk])

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == refused
        assert stored_requirements(abc) == []

    def test_an_unknown_course_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        cv = optional_type(abc, "CV")

        response = put(abc, course_documents_url(999_999), [cv.pk])

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_only_put_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc)

        response = getattr(abc.client, method)(course_documents_url(course))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        course = make_course(abc)

        response = put(abc, course_documents_url(course), [])

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_it_is_logged_with_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc, "Federation University")
        cv = optional_type(abc, "CV")

        put(abc, course_documents_url(course), [cv.pk])

        [entry] = json_logs.with_message("Document requirements set")
        assert "Master" not in entry["message"]
        assert f"course_id={course.pk}" in entry["message"]


class TestRefusedLists:
    """The same list rules hold for a Course and a Student."""

    @pytest.mark.parametrize(
        ("body", "refused"),
        [
            ({}, {"document_types": ["This field is required."]}),
            ({"document_types": None}, {"document_types": ["This field may not be null."]}),
            ({"document_types": "1"}, {"document_types": [CHOOSE_TYPES]}),
            ({"document_types": {"id": 1}}, {"document_types": [CHOOSE_TYPES]}),
            ({"document_types": ["1"]}, {"document_types": [CHOOSE_TYPES]}),
            ({"document_types": [True]}, {"document_types": [CHOOSE_TYPES]}),
            ({"document_types": [1.5]}, {"document_types": [CHOOSE_TYPES]}),
            ({"document_types": [None]}, {"document_types": [CHOOSE_TYPES]}),
            ({"document_types": [999_999]}, {"document_types": [CHOOSE_TYPES]}),
            ({"document_types": [10**30]}, {"document_types": [CHOOSE_TYPES]}),
            ({"document_types": [-1]}, {"document_types": [CHOOSE_TYPES]}),
            ({"document_types": [], "student": 1}, {"detail": ["Unknown field."]}),
        ],
    )
    @pytest.mark.parametrize("target", ["course", "student"])
    def test_bad_input_is_refused_and_nothing_stored(
        self,
        abc: ConsultancyContext,
        target: str,
        body: dict[str, object],
        refused: dict[str, list[str]],
    ) -> None:
        url = _target_url(abc, target)

        response = abc.client.put(url, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == refused
        assert stored_requirements(abc) == []

    @pytest.mark.parametrize("target", ["course", "student"])
    def test_a_type_listed_twice_is_refused(self, abc: ConsultancyContext, target: str) -> None:
        url = _target_url(abc, target)
        cv = optional_type(abc, "CV")

        response = put(abc, url, [cv.pk, cv.pk])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"document_types": ["Choose each document only once."]}
        assert stored_requirements(abc) == []

    @pytest.mark.parametrize("target", ["course", "student"])
    def test_a_type_every_student_needs_is_refused(
        self, abc: ConsultancyContext, target: str
    ) -> None:
        url = _target_url(abc, target)

        response = put(abc, url, [type_id(abc, "Passport")])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "document_types": ["Passport is already needed by every Student."]
        }
        assert stored_requirements(abc) == []

    def test_a_list_longer_than_the_most_types_an_office_has_is_refused(
        self, abc: ConsultancyContext
    ) -> None:
        url = _target_url(abc, "course")

        response = put(abc, url, list(range(1, MAX_DOCUMENT_TYPES + 2)))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "document_types": [f"Choose at most {MAX_DOCUMENT_TYPES} documents."]
        }

    def test_a_body_that_isnt_an_object_is_refused(self, abc: ConsultancyContext) -> None:
        url = _target_url(abc, "course")

        response = abc.client.put(url, [1, 2], format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        url = _target_url(abc, "student")

        response = abc.client.put(url, "document_types=1", content_type="text/plain")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


def _target_url(office: ConsultancyContext, target: str) -> str:
    """Log in as an Admin and return where one Course's or Student's list is set."""
    office.log_in_as(Role.ADMIN)
    if target == "student":
        return asked_url(make_student(office, stage=Stage.COUNSELLING))
    return course_documents_url(make_course(office))


class TestChecklistWithRequirements:
    def test_a_student_is_asked_for_what_their_courses_need(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        course = make_course(abc, "Federation University")
        make_application(abc, student, course)
        gs = optional_type(abc, "Genuine Student statement")
        cv = optional_type(abc, "CV")
        with tenant_context(abc.consultancy):
            DocumentRequirement.objects.create(document_type=gs, course=course)
            DocumentRequirement.objects.create(document_type=cv, course=course)

        body = abc.client.get(checklist_url(student)).json()

        assert items(body)[len(DEFAULT_TYPES) :] == [
            ("Genuine Student statement", "For Master of IT"),
            ("CV", "For Master of IT"),
        ]
        assert body["total"] == len(DEFAULT_TYPES) + 2
        assert [item["added_for_student"] for item in body["items"]][-2:] == [False, False]
        assert body["asked"] == []

    def test_a_student_without_applications_there_is_not_asked(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        course = make_course(abc)
        cv = optional_type(abc, "CV")
        with tenant_context(abc.consultancy):
            DocumentRequirement.objects.create(document_type=cv, course=course)

        body = abc.client.get(checklist_url(student)).json()

        assert body["total"] == len(DEFAULT_TYPES)

    def test_a_type_asked_for_by_two_applications_is_counted_once(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        first = make_course(abc, "Federation University")
        second = make_course(abc, "Deakin University")
        make_application(abc, student, first)
        make_application(abc, student, second)
        gs = optional_type(abc, "Genuine Student statement")
        with tenant_context(abc.consultancy):
            for course in (first, second):
                DocumentRequirement.objects.create(document_type=gs, course=course)

        body = abc.client.get(checklist_url(student)).json()

        assert items(body)[-1] == ("Genuine Student statement", "For Master of IT")
        assert body["total"] == len(DEFAULT_TYPES) + 1

    def test_the_counts_on_the_profile_documents_page_and_home_include_them(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        course = make_course(abc)
        make_application(abc, student, course)
        cv = optional_type(abc, "CV")
        with tenant_context(abc.consultancy):
            DocumentRequirement.objects.create(document_type=cv, course=course)
        make_document(abc, student, "CV")

        profile = abc.client.get(f"/api/students/{student.pk}/").json()
        paperwork = abc.client.get("/api/documents/students/?scope=mine").json()
        home = abc.client.get("/api/home/progress/?counsellor=me").json()

        assert profile["documents"] == {"done": 1, "total": len(DEFAULT_TYPES) + 1}
        assert paperwork["results"][0]["total"] == len(DEFAULT_TYPES) + 1
        assert paperwork["results"][0]["waiting"] == 1
        assert _home_documents(home, student)["total"] == len(DEFAULT_TYPES) + 1
        assert _home_documents(home, student)["done"] == 1


def _home_documents(home: dict[str, object], student: Student) -> dict[str, object]:
    """The documents count the Counsellor's Home shows for the Student."""
    progress = home["results"]
    assert isinstance(progress, list)
    [row] = [row for row in progress if row["student"]["id"] == student.pk]
    documents = row["documents"]
    assert isinstance(documents, dict)
    return documents


class TestAskingOneStudent:
    def test_the_assigned_counsellor_asks_for_another_document(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        portfolio = optional_type(abc, "Portfolio")

        response = put(abc, asked_url(student), [portfolio.pk])

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert items(body)[-1] == ("Portfolio", ADDED_FOR_STUDENT)
        assert body["items"][-1]["added_for_student"] is True
        assert body["asked"] == [portfolio.pk]
        assert body["total"] == len(DEFAULT_TYPES) + 1
        assert stored_requirements(abc) == [("Sita Gurung", "Portfolio")]

    def test_an_admin_may_ask_too(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Rai"), Stage.COUNSELLING)
        abc.log_in_as(Role.ADMIN)
        portfolio = optional_type(abc, "Portfolio")

        response = put(abc, asked_url(student), [portfolio.pk])

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        ("role", "refused"),
        [
            (Role.COUNSELLOR, NOT_YOUR_STUDENT),
            (Role.FRONT_DESK, COUNSELLOR_OR_ADMIN),
            (Role.TEACHER, NOT_FOR_TEACHERS),
        ],
    )
    def test_nobody_else_may(
        self, abc: ConsultancyContext, role: Role, refused: dict[str, str]
    ) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Rai"), Stage.COUNSELLING)
        portfolio = optional_type(abc, "Portfolio")
        with tenant_context(abc.consultancy):
            DocumentRequirement.objects.create(document_type=portfolio, student=student)
        abc.log_in_as(role)

        responses = [
            put(abc, asked_url(student), []),
            abc.client.delete(asked_url(student, portfolio.pk)),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 2
        assert all(r.json() == refused for r in responses)
        assert stored_requirements(abc) == [("Sita Gurung", "Portfolio")]

    def test_not_needed_takes_it_off_the_checklist(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        portfolio = optional_type(abc, "Portfolio")
        put(abc, asked_url(student), [portfolio.pk])

        response = abc.client.delete(asked_url(student, portfolio.pk))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["total"] == len(DEFAULT_TYPES)
        assert response.json()["asked"] == []
        assert stored_requirements(abc) == []

    def test_not_needed_is_refused_once_a_file_is_uploaded(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        portfolio = optional_type(abc, "Portfolio")
        put(abc, asked_url(student), [portfolio.pk])
        make_document(abc, student, "Portfolio")

        responses = [
            abc.client.delete(asked_url(student, portfolio.pk)),
            put(abc, asked_url(student), []),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_400_BAD_REQUEST] * 2
        assert responses[0].json() == {
            "detail": "A file is already uploaded for Portfolio, so it stays on the checklist."
        }
        assert responses[1].json() == {
            "document_types": [
                "A file is already uploaded for Portfolio, so it stays on the checklist."
            ]
        }
        assert stored_requirements(abc) == [("Sita Gurung", "Portfolio")]

    def test_a_rejected_file_counts_as_uploaded_too(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        portfolio = optional_type(abc, "Portfolio")
        put(abc, asked_url(student), [portfolio.pk])
        make_document(abc, student, "Portfolio", Verification.REJECTED)

        response = abc.client.delete(asked_url(student, portfolio.pk))

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_type_not_asked_for_them_is_not_found(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        portfolio = optional_type(abc, "Portfolio")

        responses = [
            abc.client.delete(asked_url(student, portfolio.pk)),
            abc.client.delete(asked_url(student, 999_999)),
            abc.client.delete(asked_url(999_999, portfolio.pk)),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 3

    def test_a_type_asked_for_by_a_course_is_not_taken_off_here(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        course = make_course(abc)
        make_application(abc, student, course)
        cv = optional_type(abc, "CV")
        with tenant_context(abc.consultancy):
            DocumentRequirement.objects.create(document_type=cv, course=course)

        response = abc.client.delete(asked_url(student, cv.pk))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored_requirements(abc) == [("Master of IT", "CV")]

    @pytest.mark.parametrize("method", ["get", "post", "patch"])
    def test_only_put_and_delete_are_allowed(self, abc: ConsultancyContext, method: str) -> None:
        student = own_student(abc)

        responses = [
            getattr(abc.client, method)(asked_url(student)),
            getattr(abc.client, method)(asked_url(student, 1)),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_405_METHOD_NOT_ALLOWED] * 2

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        responses = [put(abc, asked_url(student), []), abc.client.delete(asked_url(student, 1))]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 2


class TestPickingTypes:
    """The list every document picker shows, and adding a name not on it (spec 63c)."""

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR])
    def test_admins_and_counsellors_see_every_type(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)
        optional_type(abc, "CV")

        response = abc.client.get(PICKER_URL)

        assert response.status_code == status.HTTP_200_OK
        results = response.json()["results"]
        assert [(t["name"], t["needed_by_everyone"]) for t in results] == [
            *[(name, True) for name in DEFAULT_TYPES],
            ("CV", False),
        ]
        assert set(results[0]) == {"id", "name", "needed_by_everyone", "usual_group"}

    @pytest.mark.parametrize("role", [Role.FRONT_DESK, Role.TEACHER])
    def test_nobody_else_does(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        responses = [abc.client.get(PICKER_URL), abc.client.post(PICKER_URL, {"name": "CV"})]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 2
        assert all(r.json() == ONLY_COUNSELLORS for r in responses)

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR])
    def test_a_new_name_is_added_as_asked_for_only_when_needed(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)

        response = abc.client.post(PICKER_URL, {"name": "  Portfolio of work "}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {
            "id": type_id(abc, "Portfolio of work"),
            "name": "Portfolio of work",
            "needed_by_everyone": False,
            "usual_group": "applying",
        }

    @pytest.mark.parametrize("name", ["passport", "CV", "cv", " CV "])
    def test_a_name_already_on_the_list_in_any_case_is_refused(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        optional_type(abc, "CV")

        response = abc.client.post(PICKER_URL, {"name": name}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"name": ["There is already a document with this name."]}

    @pytest.mark.parametrize(
        ("body", "refused"),
        [
            ({}, {"name": ["Enter the document's name."]}),
            ({"name": ""}, {"name": ["Enter the document's name."]}),
            ({"name": "   "}, {"name": ["Enter the document's name."]}),
            ({"name": 12}, {"name": ["Not a valid string."]}),
            ({"name": None}, {"name": ["This field may not be null."]}),
            ({"name": ["CV"]}, {"name": ["Not a valid string."]}),
            ({"name": "x" * 81}, {"name": ["Ensure this field has no more than 80 characters."]}),
            ({"name": "CV\nletter"}, {"name": ["Use letters, numbers and punctuation only."]}),
            (
                {"name": "CV", "needed_by_everyone": True},
                {"detail": ["Unknown field."]},
            ),
        ],
    )
    def test_bad_input_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, body: dict[str, object], refused: dict[str, list[str]]
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.post(PICKER_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == refused
        with tenant_context(abc.consultancy):
            assert DocumentType.objects.count() == len(DEFAULT_TYPES)

    def test_eighty_characters_is_allowed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.post(PICKER_URL, {"name": "x" * 80}, format="json")

        assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("name", INJECTION_STRINGS)
    def test_injection_shaped_names_are_kept_as_text(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.post(PICKER_URL, {"name": name}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["name"] == name

    def test_there_is_a_limit(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            DocumentType.objects.bulk_create(
                DocumentType(name=f"Type {number}")
                for number in range(MAX_DOCUMENT_TYPES - len(DEFAULT_TYPES))
            )

        response = abc.client.post(PICKER_URL, {"name": "One too many"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": f"An office can have at most {MAX_DOCUMENT_TYPES} document types."
        }

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_only_get_and_post_are_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(PICKER_URL)

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.client.get(PICKER_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_platform_host_has_none_of_these(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        responses = [
            platform_site.client.get(PICKER_URL),
            platform_site.client.put(course_documents_url(1), {"document_types": []}),
            platform_site.client.put(asked_url(1), {"document_types": []}),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 3


class TestConfigurationUses:
    def test_each_type_says_where_it_is_used(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc)
        student = make_student(abc, stage=Stage.COUNSELLING)
        cv = optional_type(abc, "CV")
        optional_type(abc, "Unused")
        with tenant_context(abc.consultancy):
            for country in ("australia", "canada"):
                DocumentRequirement.objects.create(
                    document_type=cv, country=country, group="applying"
                )
            DocumentRequirement.objects.create(document_type=cv, course=course)
            DocumentRequirement.objects.create(document_type=cv, student=student)
        make_document(abc, student, "CV")

        results = abc.client.get(CONFIG_TYPES_URL).json()["results"]

        uses = {t["name"]: t["used_by"] for t in results}
        assert uses["CV"] == {"countries": 2, "courses": 1, "students": 1, "documents": 1}
        assert uses["Unused"] == {"countries": 0, "courses": 0, "students": 0, "documents": 0}

    def test_an_unused_type_is_removed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        unused = optional_type(abc, "Unused")

        response = abc.client.delete(f"{CONFIG_TYPES_URL}{unused.pk}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        with tenant_context(abc.consultancy):
            assert not DocumentType.objects.filter(pk=unused.pk).exists()

    @pytest.mark.parametrize("use", ["country", "course", "student", "document"])
    def test_a_type_in_use_is_kept(self, abc: ConsultancyContext, use: str) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc)
        student = make_student(abc, stage=Stage.COUNSELLING)
        cv = optional_type(abc, "CV")
        if use == "document":
            make_document(abc, student, "CV")
        with tenant_context(abc.consultancy):
            if use == "country":
                DocumentRequirement.objects.create(document_type=cv, country="uk", group="visa")
            if use == "course":
                DocumentRequirement.objects.create(document_type=cv, course=course)
            if use == "student":
                DocumentRequirement.objects.create(document_type=cv, student=student)

        response = abc.client.delete(f"{CONFIG_TYPES_URL}{cv.pk}/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": "This document is asked for or has files, so it can't be removed."
        }
        with tenant_context(abc.consultancy):
            assert DocumentType.objects.filter(pk=cv.pk).exists()

    def test_a_type_every_student_needs_is_kept(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.delete(f"{CONFIG_TYPES_URL}{type_id(abc, 'Passport')}/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": "Every Student needs this document, so it can't be removed."
        }

    def test_an_unknown_type_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.delete(f"{CONFIG_TYPES_URL}999999/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_may_remove(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        unused = optional_type(abc, "Unused")

        response = abc.client.delete(f"{CONFIG_TYPES_URL}{unused.pk}/")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN_CONFIG
        with tenant_context(abc.consultancy):
            assert DocumentType.objects.filter(pk=unused.pk).exists()

    def test_removing_is_logged_with_ids_only(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        unused = optional_type(abc, "Unused")

        abc.client.delete(f"{CONFIG_TYPES_URL}{unused.pk}/")

        [entry] = json_logs.with_message("Document type removed")
        assert "Unused" not in entry["message"]


class TestPlumbing:
    def test_the_list_field_shows_ids_and_a_requirement_names_its_type(
        self, abc: ConsultancyContext
    ) -> None:
        cv = optional_type(abc, "CV")
        course = make_course(abc)
        with tenant_context(abc.consultancy):
            requirement = DocumentRequirement.objects.create(document_type=cv, course=course)

            assert DocumentTypeListField().to_representation([cv]) == [cv.pk]
            assert str(requirement) == str(cv.pk)
