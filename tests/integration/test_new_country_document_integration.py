"""A new document made from a Country's list (issue 97; spec 121, 121a).

One request creates the Document Type (not needed by everyone, its usual group the list's) and
puts it on that Country's list, in one transaction. A name already used, ignoring capitals and
extra spaces, is refused with words that say why, and nothing is created.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from documents.models import DocumentRequirement, DocumentType
from documents.services import MAX_DOCUMENT_TYPES
from staff.models import Role
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_country_document_lists_integration import (
    CHOOSE_GROUP,
    LISTED_COUNTRIES,
    ONLY_ADMIN_CONFIG,
    optional_type,
    set_list,
    stored_lists,
)
from tests.integration.test_documents_integration import DEFAULT_TYPES
from tests.integration.test_stage_and_counsellor_integration import INJECTION_STRINGS
from tests.support_access import open_as_platform_admin

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

CONFIG_TYPES_URL = "/api/configuration/document-types/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def new_url(country: str) -> str:
    """Where a new document is made from one Country's list."""
    return f"/api/configuration/document-lists/{country}/new-document/"


def create(client: APIClient, country: str = "uk", **body: object) -> _MonkeyPatchedResponse:
    """POST a new document for a Country's list."""
    return client.post(new_url(country), body, format="json")


def type_count(office: ConsultancyContext) -> int:
    """How many Document Types the office has."""
    with tenant_context(office.consultancy):
        return DocumentType.objects.count()


class TestNewDocumentFromACountrysList:
    def test_it_creates_the_type_and_puts_it_on_the_list(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = create(abc.client, name="Police clearance", group="statements")

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["document_type"] == {
            "id": body["document_type"]["id"],
            "name": "Police clearance",
            "needed_by_everyone": False,
            "usual_group": "statements",
            "used_by": {"countries": 1, "courses": 0, "students": 0, "documents": 0},
            "instruction": "",
            "examples": [],
            "example_count": 0,
        }
        assert body["list"] == {
            "country": "uk",
            "documents": [
                {
                    "document_type": {
                        "id": body["document_type"]["id"],
                        "name": "Police clearance",
                    },
                    "group": "statements",
                }
            ],
        }
        assert stored_lists(abc) == [("uk", "Police clearance", "statements")]

    @pytest.mark.parametrize("group", ["applying", "visa", "statements"])
    def test_the_group_stored_is_the_one_sent(self, abc: ConsultancyContext, group: str) -> None:
        abc.log_in_as(Role.ADMIN)

        create(abc.client, "australia", name="Police clearance", group=group)

        assert stored_lists(abc) == [("australia", "Police clearance", group)]
        with tenant_context(abc.consultancy):
            assert DocumentType.objects.get(name="Police clearance").usual_group == group

    def test_the_rest_of_the_list_is_kept(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        cv = optional_type(abc, "CV")
        set_list(abc, "uk", [(cv, "visa")])

        response = create(abc.client, name="Police clearance", group="applying")

        assert [row["document_type"]["name"] for row in response.json()["list"]["documents"]] == [
            "CV",
            "Police clearance",
        ]
        assert stored_lists(abc) == [("uk", "CV", "visa"), ("uk", "Police clearance", "applying")]

    def test_it_shows_under_extra_documents_asked_for_by_one_country(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        create(abc.client, name="Police clearance", group="statements")

        results = abc.client.get(CONFIG_TYPES_URL).json()["results"]

        [made] = [one for one in results if one["name"] == "Police clearance"]
        assert made["needed_by_everyone"] is False
        assert made["used_by"]["countries"] == 1

    def test_extra_spaces_in_the_name_are_tidied(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = create(abc.client, name="  Police   clearance ", group="visa")

        assert response.json()["document_type"]["name"] == "Police clearance"

    def test_it_is_logged_with_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        abc.log_in_as(Role.ADMIN)

        create(abc.client, name="Police clearance", group="visa")

        [entry] = json_logs.with_message("New document added to a country's list")
        assert "Police" not in entry["message"]
        assert "country=uk" in entry["message"]
        assert "group=visa" in entry["message"]

    def test_a_platform_admin_inside_the_office_may_do_it(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visitor = open_as_platform_admin(platform_site, abc).client

        response = create(visitor, name="Police clearance", group="visa")

        assert response.status_code == status.HTTP_201_CREATED
        assert stored_lists(abc) == [("uk", "Police clearance", "visa")]


class TestANameAlreadyUsed:
    @pytest.mark.parametrize("name", ["passport", "PASSPORT", "  Passport "])
    def test_an_always_needed_type_is_refused(self, abc: ConsultancyContext, name: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = create(abc.client, name=name, group="visa")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"name": ["Passport already exists and is Always needed."]}
        assert type_count(abc) == len(DEFAULT_TYPES)
        assert stored_lists(abc) == []

    @pytest.mark.parametrize("name", ["police clearance", "POLICE  CLEARANCE", " Police clearance"])
    def test_one_already_on_this_countrys_list_is_refused(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        police = optional_type(abc, "Police clearance")
        set_list(abc, "uk", [(police, "statements")])

        response = create(abc.client, name=name, group="visa")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"name": ["Police clearance is already on UK's list."]}
        assert type_count(abc) == len(DEFAULT_TYPES) + 1
        assert stored_lists(abc) == [("uk", "Police clearance", "statements")]

    def test_an_extra_document_not_on_the_list_says_to_pick_it(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        optional_type(abc, "Police clearance")

        response = create(abc.client, name="police clearance", group="visa")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "name": ['Police clearance already exists. Pick it from "Add an existing document".']
        }
        assert stored_lists(abc) == []

    def test_a_name_stored_with_extra_spaces_still_matches(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        optional_type(abc, "Police  clearance")

        response = create(abc.client, name="Police clearance", group="visa")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert type_count(abc) == len(DEFAULT_TYPES) + 1

    def test_the_configuration_add_also_ignores_extra_spaces(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        optional_type(abc, "Police clearance")

        response = abc.client.post(
            CONFIG_TYPES_URL,
            {"name": "Police   CLEARANCE", "needed_by_everyone": False},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert type_count(abc) == len(DEFAULT_TYPES) + 1

    def test_there_is_a_limit_and_nothing_is_created(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            DocumentType.objects.bulk_create(
                DocumentType(name=f"Type {number}")
                for number in range(MAX_DOCUMENT_TYPES - len(DEFAULT_TYPES))
            )

        response = create(abc.client, name="Police clearance", group="visa")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": f"An office can have at most {MAX_DOCUMENT_TYPES} document types."
        }
        assert stored_lists(abc) == []


class TestRefusedNewDocuments:
    @pytest.mark.parametrize(
        ("body", "refused"),
        [
            ({"group": "visa"}, {"name": ["Enter the document's name."]}),
            ({"name": "", "group": "visa"}, {"name": ["Enter the document's name."]}),
            ({"name": "   ", "group": "visa"}, {"name": ["Enter the document's name."]}),
            ({"name": 12, "group": "visa"}, {"name": ["Not a valid string."]}),
            ({"name": None, "group": "visa"}, {"name": ["This field may not be null."]}),
            ({"name": ["CV"], "group": "visa"}, {"name": ["Not a valid string."]}),
            (
                {"name": "x" * 101, "group": "visa"},
                {"name": ["Ensure this field has no more than 100 characters."]},
            ),
            (
                {"name": "CV\nletter", "group": "visa"},
                {"name": ["Use letters, numbers and punctuation only."]},
            ),
            ({"name": "CV"}, {"group": [CHOOSE_GROUP]}),
            ({"name": "CV", "group": None}, {"group": [CHOOSE_GROUP]}),
            ({"name": "CV", "group": "Visa"}, {"group": [CHOOSE_GROUP]}),
            ({"name": "CV", "group": 1}, {"group": [CHOOSE_GROUP]}),
            ({"name": "CV", "group": "university"}, {"group": [CHOOSE_GROUP]}),
            ({"name": "CV", "group": "visa", "needed_by_everyone": True}, None),
            ({"name": "CV", "group": "visa", "country": "usa"}, None),
        ],
    )
    def test_bad_input_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, body: dict[str, object], refused: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(new_url("uk"), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == (refused or {"detail": ["Unknown field."]})
        assert type_count(abc) == len(DEFAULT_TYPES)
        assert stored_lists(abc) == []

    @pytest.mark.parametrize("group", INJECTION_STRINGS)
    def test_an_injection_shaped_group_is_refused(
        self, abc: ConsultancyContext, group: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = create(abc.client, name="CV", group=group)

        assert response.json() == {"group": [CHOOSE_GROUP]}
        assert type_count(abc) == len(DEFAULT_TYPES)

    @pytest.mark.parametrize("name", INJECTION_STRINGS)
    def test_injection_shaped_names_are_kept_as_text(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = create(abc.client, name=name, group="visa")

        assert response.status_code == status.HTTP_201_CREATED
        assert stored_lists(abc) == [("uk", " ".join(name.split()), "visa")]

    @pytest.mark.parametrize("body", [[], "Police clearance", 5])
    def test_a_body_that_isnt_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(new_url("uk"), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert type_count(abc) == len(DEFAULT_TYPES)

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(new_url("uk"), "name=CV&group=visa", content_type="text/plain")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("country", ["other", "france", "UK", "<script>"])
    def test_a_country_without_a_list_is_not_found(
        self, abc: ConsultancyContext, country: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            f"/api/configuration/document-lists/{country}/new-document/",
            {"name": "CV", "group": "visa"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert type_count(abc) == len(DEFAULT_TYPES)

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_may_do_it(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = create(abc.client, name="CV", group="visa")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN_CONFIG
        assert type_count(abc) == len(DEFAULT_TYPES)

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        response = create(abc.client, name="CV", group="visa")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert type_count(abc) == len(DEFAULT_TYPES)

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_it_only_allows_post(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(new_url("uk"))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_every_listed_country_has_it(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        codes = {
            create(abc.client, country, name=f"Letter for {country}", group="visa").status_code
            for country in LISTED_COUNTRIES
        }

        assert codes == {status.HTTP_201_CREATED}
        with tenant_context(abc.consultancy):
            assert DocumentRequirement.objects.exclude(country="").count() == len(LISTED_COUNTRIES)
