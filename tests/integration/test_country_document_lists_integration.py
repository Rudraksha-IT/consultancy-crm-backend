"""Country document lists in Applying, Visa and Statements (spec 64, 120, 121; issue 80).

An Admin sets in Configuration which Document Types each Country asks for, each in a Document
Group. Before any Application the preferred country's list applies; while applying, the Applying
list of every Country applied to; with a Chosen Offer, the Visa and Statements lists come only
from that offer's Country. Uploaded Documents never go away: off the checklist they are listed
as "Other documents" (not required, no group).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application
from applications.statuses import ApplicationStatus
from catalogue.models import Course, CourseLevel, University
from documents.models import DocumentRequirement, DocumentType
from documents.services import MAX_DOCUMENT_TYPES
from documents.verification import Verification
from staff.models import Role
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs
from tests.integration.test_applications_integration import make_application
from tests.integration.test_documents_integration import DEFAULT_TYPES, make_document
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    make_student,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

LISTS_URL = "/api/configuration/document-lists/"
CONFIG_TYPES_URL = "/api/configuration/document-types/"
ONLY_ADMIN_CONFIG = {"detail": "Only an Admin can change the Configuration."}
CHOOSE_TYPES = "Choose documents from the list."
CHOOSE_GROUP = "Choose Applying, Visa or Statements."
LISTED_COUNTRIES = ["australia", "uk", "canada", "usa", "new_zealand", "japan"]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def list_url(country: str) -> str:
    """Where one Country's document list is set."""
    return f"{LISTS_URL}{country}/"


def optional_type(office: ConsultancyContext, name: str, usual_group: str = "applying") -> int:
    """Store a Document Type asked for only when needed, and return its id."""
    with tenant_context(office.consultancy):
        return int(
            DocumentType.objects.create(
                name=name, needed_by_everyone=False, usual_group=usual_group
            ).pk
        )


def type_id(office: ConsultancyContext, name: str) -> int:
    """The id of one of the office's Document Types."""
    with tenant_context(office.consultancy):
        return int(DocumentType.objects.get(name=name).pk)


def put_list(
    office: ConsultancyContext, country: str, rows: list[tuple[int, str]]
) -> _MonkeyPatchedResponse:
    """PUT a Country's whole list as (type id, group) rows."""
    body = {"documents": [{"document_type": pk, "group": group} for pk, group in rows]}
    return office.client.put(list_url(country), body, format="json")


def stored_lists(office: ConsultancyContext) -> list[tuple[str, str, str]]:
    """Every stored Country row as (country, type name, group), oldest type first."""
    with tenant_context(office.consultancy):
        rows = (
            DocumentRequirement.objects.exclude(country="")
            .select_related("document_type")
            .order_by("country", "document_type_id")
        )
        return [(row.country, row.document_type.name, row.group) for row in rows]


def set_list(office: ConsultancyContext, country: str, rows: list[tuple[int, str]]) -> None:
    """Store a Country's list directly."""
    with tenant_context(office.consultancy):
        DocumentRequirement.objects.bulk_create(
            DocumentRequirement(document_type_id=pk, country=country, group=group)
            for pk, group in rows
        )


def course_in(office: ConsultancyContext, country: str, university: str = "") -> Course:
    """Store a Course at a University in `country`."""
    with tenant_context(office.consultancy):
        uni = University.objects.create(
            name=university or f"University in {country}", country=country
        )
        return Course.objects.create(
            university=uni, name=f"Course in {country}", level=CourseLevel.MASTER
        )


def choose(office: ConsultancyContext, student: Student, application: Application) -> None:
    """Make the Application an offer and the Student's Chosen Offer (directly)."""
    with tenant_context(office.consultancy):
        Application.objects.filter(pk=application.pk).update(status=ApplicationStatus.OFFER)
        Student.objects.filter(pk=student.pk).update(chosen_offer=application)


def checklist(office: ConsultancyContext, student: Student) -> dict[str, object]:
    """The Student's checklist as the API returns it."""
    response = office.client.get(f"/api/students/{student.public_id}/documents/")
    assert response.status_code == status.HTTP_200_OK
    body: dict[str, object] = response.json()
    return body


def extra(body: dict[str, object]) -> list[tuple[str, str, str | None, bool]]:
    """Each item after everyone's types: name, reason, group, and whether it is required."""
    found = body["items"]
    assert isinstance(found, list)
    return [
        (i["document_type"]["name"], i["reason"], i["group"], i["is_required"])
        for i in found
        if i["document_type"]["name"] not in DEFAULT_TYPES
    ]


class TestSettingCountryLists:
    def test_an_admin_sees_every_countrys_list_but_other(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        gs = optional_type(abc, "Genuine Student statement", "statements")
        set_list(abc, "australia", [(gs, "statements")])

        response = abc.client.get(LISTS_URL)

        assert response.status_code == status.HTTP_200_OK
        results = response.json()["results"]
        assert [one["country"] for one in results] == LISTED_COUNTRIES
        assert results[0]["documents"] == [
            {
                "document_type": {"id": gs, "name": "Genuine Student statement"},
                "group": "statements",
            }
        ]
        assert all(one["documents"] == [] for one in results[1:])

    def test_an_admin_sets_a_list_with_a_group_for_each_type(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        gs = optional_type(abc, "Genuine Student statement", "statements")
        police = optional_type(abc, "Police report", "visa")

        response = put_list(abc, "australia", [(police, "visa"), (gs, "statements")])

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "country": "australia",
            "documents": [
                {
                    "document_type": {"id": gs, "name": "Genuine Student statement"},
                    "group": "statements",
                },
                {"document_type": {"id": police, "name": "Police report"}, "group": "visa"},
            ],
        }
        assert stored_lists(abc) == [
            ("australia", "Genuine Student statement", "statements"),
            ("australia", "Police report", "visa"),
        ]

    def test_setting_again_moves_groups_takes_off_and_clears(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        gs = optional_type(abc, "Genuine Student statement", "statements")
        police = optional_type(abc, "Police report", "visa")
        put_list(abc, "uk", [(gs, "statements"), (police, "visa")])
        with tenant_context(abc.consultancy):
            kept_row = DocumentRequirement.objects.get(country="uk", document_type_id=police).pk

        put_list(abc, "uk", [(police, "applying")])
        moved = stored_lists(abc)
        with tenant_context(abc.consultancy):
            still_there = DocumentRequirement.objects.filter(pk=kept_row, group="applying")
            kept = still_there.exists()
        cleared = put_list(abc, "uk", [])

        assert moved == [("uk", "Police report", "applying")]
        assert kept
        assert cleared.json() == {"country": "uk", "documents": []}
        assert stored_lists(abc) == []

    def test_one_countrys_list_leaves_the_others_alone(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        cv = optional_type(abc, "CV")
        put_list(abc, "canada", [(cv, "applying")])

        put_list(abc, "japan", [(cv, "visa")])

        assert stored_lists(abc) == [("canada", "CV", "applying"), ("japan", "CV", "visa")]

    @pytest.mark.parametrize("country", ["other", "nepal", "Australia", "1"])
    def test_a_country_without_a_list_is_not_found(
        self, abc: ConsultancyContext, country: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        cv = optional_type(abc, "CV")

        response = put_list(abc, country, [(cv, "applying")])

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored_lists(abc) == []

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_see_or_change_them(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        cv = optional_type(abc, "CV")

        responses = [abc.client.get(LISTS_URL), put_list(abc, "australia", [(cv, "applying")])]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 2
        assert all(r.json() == ONLY_ADMIN_CONFIG for r in responses)
        assert stored_lists(abc) == []

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        responses = [abc.client.get(LISTS_URL), put_list(abc, "australia", [])]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 2

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_the_lists_only_allow_get(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(LISTS_URL)

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_one_list_only_allows_put(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(list_url("australia"))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_it_is_logged_with_ids_and_counts_only(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        cv = optional_type(abc, "Curriculum vitae")

        put_list(abc, "australia", [(cv, "applying")])

        [entry] = json_logs.with_message("Country document list set")
        assert "Curriculum" not in entry["message"]
        assert "country=australia" in entry["message"]
        assert "document_types=1" in entry["message"]


class TestRefusedCountryLists:
    @pytest.mark.parametrize(
        ("body", "refused"),
        [
            ({}, {"documents": ["This field is required."]}),
            ({"documents": None}, {"documents": ["This field may not be null."]}),
            ({"documents": "1"}, None),
            ({"documents": [1]}, None),
            ({"documents": [{"document_type": "1", "group": "visa"}]}, None),
            ({"documents": [{"document_type": True, "group": "visa"}]}, None),
            ({"documents": [{"document_type": 1.5, "group": "visa"}]}, None),
            ({"documents": [{"document_type": None, "group": "visa"}]}, None),
            ({"documents": [{"document_type": -1, "group": "visa"}]}, None),
            ({"documents": [{"document_type": 10**30, "group": "visa"}]}, None),
            ({"documents": [{"group": "visa"}]}, None),
            ({"documents": [{"document_type": 999_999, "group": "visa"}]}, None),
            ({"documents": [{"document_type": 999_999}]}, None),
            ({"documents": [], "country": "uk"}, {"detail": ["Unknown field."]}),
        ],
    )
    def test_bad_input_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, body: dict[str, object], refused: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(list_url("australia"), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        if refused is not None:
            assert response.json() == refused
        assert "documents" in response.json() or "detail" in response.json()
        assert stored_lists(abc) == []

    @pytest.mark.parametrize(
        "group", ["", None, 1, "Visa", "VISA", "university", ["visa"], *INJECTION_STRINGS]
    )
    def test_a_group_that_isnt_one_of_the_three_is_refused(
        self, abc: ConsultancyContext, group: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        cv = optional_type(abc, "CV")
        body = {"documents": [{"document_type": cv, "group": group}]}

        response = abc.client.put(list_url("australia"), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"documents": {"0": {"group": [CHOOSE_GROUP]}}}
        assert stored_lists(abc) == []

    def test_an_unknown_field_on_a_row_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        cv = optional_type(abc, "CV")
        body = {"documents": [{"document_type": cv, "group": "visa", "student": 1}]}

        response = abc.client.put(list_url("australia"), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"documents": {"0": {"detail": ["Unknown field."]}}}

    def test_a_type_listed_twice_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        cv = optional_type(abc, "CV")

        response = put_list(abc, "australia", [(cv, "applying"), (cv, "visa")])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"documents": ["Choose each document only once."]}

    def test_an_unknown_type_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        cv = optional_type(abc, "CV")

        response = put_list(abc, "australia", [(cv, "applying"), (999_999, "visa")])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"documents": [CHOOSE_TYPES]}
        assert stored_lists(abc) == []

    def test_a_type_every_student_needs_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = put_list(abc, "australia", [(type_id(abc, "Passport"), "applying")])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"documents": ["Passport is already needed by every Student."]}

    def test_a_list_longer_than_the_most_types_an_office_has_is_refused(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        rows = [(pk, "visa") for pk in range(1, MAX_DOCUMENT_TYPES + 2)]

        response = put_list(abc, "australia", rows)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"documents": [f"Choose at most {MAX_DOCUMENT_TYPES} documents."]}

    def test_a_body_that_isnt_an_object_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(list_url("australia"), [1, 2], format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(list_url("australia"), "documents=1", content_type="text/plain")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


class TestUsualGroup:
    def test_a_new_type_takes_the_usual_group_sent(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            CONFIG_TYPES_URL,
            {"name": "Police report", "needed_by_everyone": False, "usual_group": "visa"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["usual_group"] == "visa"
        with tenant_context(abc.consultancy):
            assert DocumentType.objects.get(name="Police report").usual_group == "visa"

    def test_without_one_it_is_applying(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            CONFIG_TYPES_URL, {"name": "CV", "needed_by_everyone": False}, format="json"
        )

        assert response.json()["usual_group"] == "applying"

    @pytest.mark.parametrize("group", [None, 2, "Visa", "university", *INJECTION_STRINGS])
    def test_a_bad_usual_group_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, group: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            CONFIG_TYPES_URL,
            {"name": "CV", "needed_by_everyone": False, "usual_group": group},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"usual_group": [CHOOSE_GROUP]}
        with tenant_context(abc.consultancy):
            assert not DocumentType.objects.filter(name="CV").exists()

    def test_the_default_types_keep_their_usual_groups(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        results = abc.client.get(CONFIG_TYPES_URL).json()["results"]

        groups = {t["name"]: t["usual_group"] for t in results}
        assert groups["Passport"] == "applying"
        assert groups["Bank Statement"] == "visa"
        assert groups["Statement of Purpose"] == "statements"


@pytest.fixture
def lists(abc: ConsultancyContext) -> dict[str, int]:
    """Australia, Canada and Japan lists, and a Counsellor logged in. Returns the type ids."""
    ids = {
        "GS": optional_type(abc, "Genuine Student statement", "statements"),
        "LOE": optional_type(abc, "Letter of explanation", "statements"),
        "Police": optional_type(abc, "Police report", "visa"),
        "Birth": optional_type(abc, "Birth certificate", "visa"),
        "CV": optional_type(abc, "CV"),
    }
    set_list(
        abc,
        "australia",
        [(ids["GS"], "statements"), (ids["Police"], "visa"), (ids["CV"], "applying")],
    )
    set_list(abc, "canada", [(ids["LOE"], "statements"), (ids["Police"], "visa")])
    set_list(abc, "japan", [(ids["Birth"], "applying")])
    return ids


def own_student(office: ConsultancyContext, country: str = "australia") -> Student:
    """Log in as a Counsellor and give them a Student who wants to study in `country`."""
    me = office.log_in_as(Role.COUNSELLOR)
    return make_student(office, me, Stage.COUNSELLING, country=country)


@pytest.mark.usefixtures("lists")
class TestWhichListsApply:
    def test_before_any_application_the_preferred_countrys_whole_list_applies(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc, "australia")

        body = checklist(abc, student)

        assert extra(body) == [
            ("Genuine Student statement", "For Australia", "statements", True),
            ("Police report", "For Australia", "visa", True),
            ("CV", "For Australia", "applying", True),
        ]
        assert body["total"] == len(DEFAULT_TYPES) + 3

    def test_everyones_types_sit_in_their_usual_groups(self, abc: ConsultancyContext) -> None:
        student = own_student(abc, "other")

        items = checklist(abc, student)["items"]

        assert isinstance(items, list)
        groups = {i["document_type"]["name"]: i["group"] for i in items}
        assert groups["Passport"] == "applying"
        assert groups["Bank Statement"] == "visa"
        assert groups["Statement of Purpose"] == "statements"

    def test_while_applying_only_the_applying_lists_of_each_country_apply(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc, "canada")
        make_application(abc, student, course_in(abc, "japan"))
        make_application(abc, student, course_in(abc, "australia"))

        assert extra(checklist(abc, student)) == [
            ("Birth certificate", "For Japan", "applying", True),
            ("CV", "For Australia", "applying", True),
        ]

    def test_a_rejected_application_doesnt_count(self, abc: ConsultancyContext) -> None:
        student = own_student(abc, "canada")
        make_application(abc, student, course_in(abc, "japan"), ApplicationStatus.REJECTED)

        assert [name for name, *_ in extra(checklist(abc, student))] == [
            "Letter of explanation",
            "Police report",
        ]

    def test_with_a_chosen_offer_visa_and_statements_come_only_from_its_country(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc, "canada")
        australia = make_application(abc, student, course_in(abc, "australia"))
        make_application(abc, student, course_in(abc, "japan"))
        choose(abc, student, australia)

        assert extra(checklist(abc, student)) == [
            ("CV", "For Australia", "applying", True),
            ("Birth certificate", "For Japan", "applying", True),
            ("Genuine Student statement", "For Australia", "statements", True),
            ("Police report", "For Australia", "visa", True),
        ]

    def test_changing_the_chosen_offer_changes_the_lists(self, abc: ConsultancyContext) -> None:
        student = own_student(abc, "australia")
        australia = make_application(abc, student, course_in(abc, "australia"))
        canada = make_application(abc, student, course_in(abc, "canada"))
        choose(abc, student, australia)

        choose(abc, student, canada)
        body = checklist(abc, student)

        required = [(n, r, g) for n, r, g, needed in extra(body) if needed]
        assert required == [
            ("CV", "For Australia", "applying"),
            ("Letter of explanation", "For Canada", "statements"),
            ("Police report", "For Canada", "visa"),
        ]
        assert ("Genuine Student statement", "For Australia") not in [r[:2] for r in required]

    def test_a_document_off_the_checklist_is_kept_under_other_documents_and_moves_back(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc, "australia")
        make_document(abc, student, "Genuine Student statement", Verification.VERIFIED)
        make_application(abc, student, course_in(abc, "canada"))
        before = checklist(abc, student)
        choose(abc, student, make_application(abc, student, course_in(abc, "australia")))
        after = checklist(abc, student)

        assert ("Genuine Student statement", "", None, False) in extra(before)
        assert before["total"] == len(DEFAULT_TYPES)
        assert ("Genuine Student statement", "For Australia", "statements", True) in extra(after)

    def test_one_upload_counts_for_every_list_that_asks_for_it(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc, "canada")
        canada = make_application(abc, student, course_in(abc, "canada", "Canada One"))
        make_application(abc, student, course_in(abc, "australia", "Australia One"))
        choose(abc, student, canada)
        make_document(abc, student, "Police report", Verification.VERIFIED)

        body = checklist(abc, student)

        police = [row for row in extra(body) if row[0] == "Police report"]
        assert police == [("Police report", "For Canada", "visa", True)]
        assert body["done"] == 1

    def test_the_profile_documents_page_and_home_use_the_same_rule(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc, "australia")
        make_document(abc, student, "CV")

        profile = abc.client.get(f"/api/students/{student.public_id}/").json()
        paperwork = abc.client.get("/api/documents/students/?scope=mine").json()
        home = abc.client.get("/api/home/progress/?counsellor=me").json()

        expected = {"done": 1, "total": len(DEFAULT_TYPES) + 3}
        assert profile["documents"] == expected
        row = paperwork["results"][0]
        assert {"done": row["done"], "total": row["total"]} == expected
        [mine] = [r for r in home["results"] if r["student"]["id"] == str(student.public_id)]
        assert {k: mine["documents"][k] for k in expected} == expected
