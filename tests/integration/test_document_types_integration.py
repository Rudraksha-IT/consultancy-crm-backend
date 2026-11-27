"""Document Types in Configuration, and the Documents page (spec 63, 64a; issue 16)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from documents.models import DocumentType
from documents.services import MAX_DOCUMENT_TYPES
from documents.verification import Verification
from staff.models import Role
from students.models import Stage
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_documents_integration import DEFAULT_TYPES, make_document
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

TYPES_URL = "/api/configuration/document-types/"
PAPERWORK_URL = "/api/documents/students/"
ONLY_ADMIN = {"detail": "Only an Admin can change the Configuration."}
ONLY_COUNSELLORS = {"detail": "Only counsellors and Admins can see this."}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def type_url(type_id: object) -> str:
    """One Document Type."""
    return f"{TYPES_URL}{type_id}/"


def add_type(office: ConsultancyContext, **body: object) -> _MonkeyPatchedResponse:
    """POST a new Document Type."""
    return office.client.post(TYPES_URL, body, format="json")


def stored_types(office: ConsultancyContext) -> list[tuple[str, bool]]:
    """Every stored Document Type's name and flag, oldest first."""
    with tenant_context(office.consultancy):
        return list(DocumentType.objects.order_by("pk").values_list("name", "needed_by_everyone"))


def type_id(office: ConsultancyContext, name: str) -> str:
    """The id of one of the office's Document Types."""
    with tenant_context(office.consultancy):
        return str(DocumentType.objects.get(name=name).public_id)


class TestListingTypes:
    def test_a_new_office_has_the_default_types_needed_by_everyone(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(TYPES_URL)

        assert response.status_code == status.HTTP_200_OK
        results = response.json()["results"]
        assert [(t["name"], t["needed_by_everyone"]) for t in results] == [
            (name, True) for name in DEFAULT_TYPES
        ]
        assert set(results[0]) == {
            "id",
            "name",
            "needed_by_everyone",
            "usual_group",
            "used_by",
            "instruction",
            "examples",
            "example_count",
        }

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_may_manage_them(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        passport = type_id(abc, "Passport")

        responses = [
            abc.client.get(TYPES_URL),
            add_type(abc, name="Police report", needed_by_everyone=False),
            abc.client.patch(type_url(passport), {"needed_by_everyone": False}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 3
        assert all(r.json() == ONLY_ADMIN for r in responses)
        assert stored_types(abc) == [(name, True) for name in DEFAULT_TYPES]

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.client.get(TYPES_URL).status_code == status.HTTP_401_UNAUTHORIZED


class TestAddingTypes:
    def test_an_admin_adds_one_asked_for_only_when_needed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add_type(abc, name="Police report", needed_by_everyone=False)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {
            "id": type_id(abc, "Police report"),
            "name": "Police report",
            "needed_by_everyone": False,
            "usual_group": "applying",
            "used_by": {"countries": 0, "courses": 0, "students": 0, "documents": 0},
            "instruction": "",
            "examples": [],
            "example_count": 0,
        }

    def test_one_needed_by_everyone_joins_every_checklist(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.COUNSELLING)

        add_type(abc, name="Birth certificate", needed_by_everyone=True)

        body = abc.client.get(f"/api/students/{student.public_id}/documents/").json()
        assert body["items"][-1]["document_type"]["name"] == "Birth certificate"
        assert body["total"] == len(DEFAULT_TYPES) + 1

    def test_the_name_is_trimmed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        add_type(abc, name="  Police report  ", needed_by_everyone=False)

        assert stored_types(abc)[-1] == ("Police report", False)

    @pytest.mark.parametrize("name", ["passport", "PASSPORT", " Passport "])
    def test_a_name_already_used_in_any_case_is_refused(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add_type(abc, name=name, needed_by_everyone=False)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"name": ["There is already a document with this name."]}

    @pytest.mark.parametrize(
        ("body", "refused"),
        [
            ({"needed_by_everyone": False}, {"name": ["Enter the document's name."]}),
            ({"name": "", "needed_by_everyone": False}, {"name": ["Enter the document's name."]}),
            (
                {"name": "   ", "needed_by_everyone": False},
                {"name": ["Enter the document's name."]},
            ),
            ({"name": 12, "needed_by_everyone": False}, {"name": ["Not a valid string."]}),
            (
                {"name": None, "needed_by_everyone": False},
                {"name": ["This field may not be null."]},
            ),
            ({"name": "CV"}, {"needed_by_everyone": ["This field is required."]}),
            (
                {"name": "CV", "needed_by_everyone": "true"},
                {"needed_by_everyone": ["Must be true or false."]},
            ),
            (
                {"name": "CV", "needed_by_everyone": 1},
                {"needed_by_everyone": ["Must be true or false."]},
            ),
            (
                {"name": "x" * 101, "needed_by_everyone": False},
                {"name": ["Ensure this field has no more than 100 characters."]},
            ),
            (
                {"name": "CV\nletter", "needed_by_everyone": False},
                {"name": ["Use letters, numbers and punctuation only."]},
            ),
            ({"name": "CV", "needed_by_everyone": False, "id": 1}, {"detail": ["Unknown field."]}),
        ],
    )
    def test_bad_input_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, body: dict[str, object], refused: dict[str, list[str]]
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(TYPES_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == refused
        assert len(stored_types(abc)) == len(DEFAULT_TYPES)

    @pytest.mark.parametrize("body", [[], "Police report", 5])
    def test_a_body_that_isnt_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(TYPES_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("name", INJECTION_STRINGS)
    def test_injection_shaped_names_are_kept_as_text(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add_type(abc, name=name, needed_by_everyone=False)

        assert response.status_code == status.HTTP_201_CREATED
        assert stored_types(abc)[-1] == (name, False)

    def test_there_is_a_limit(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            DocumentType.objects.bulk_create(
                DocumentType(name=f"Type {number}")
                for number in range(MAX_DOCUMENT_TYPES - len(DEFAULT_TYPES))
            )

        response = add_type(abc, name="One too many", needed_by_everyone=False)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": f"An office can have at most {MAX_DOCUMENT_TYPES} document types."
        }

    def test_adding_is_logged_with_ids_only(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        add_type(abc, name="Police report", needed_by_everyone=False)

        [entry] = json_logs.with_message("Document type added")
        assert "Police" not in entry["message"]


class TestMovingTypes:
    def test_an_admin_moves_one_off_every_checklist(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sponsor = type_id(abc, "Sponsor Letter")

        response = abc.client.patch(type_url(sponsor), {"needed_by_everyone": False}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "id": sponsor,
            "name": "Sponsor Letter",
            "needed_by_everyone": False,
            "usual_group": "visa",
            "used_by": {"countries": 0, "courses": 0, "students": 0, "documents": 0},
            "instruction": "",
            "examples": [],
            "example_count": 0,
        }
        assert ("Sponsor Letter", False) in stored_types(abc)

    def test_and_back_onto_them(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        sponsor = type_id(abc, "Sponsor Letter")
        abc.client.patch(type_url(sponsor), {"needed_by_everyone": False}, format="json")

        abc.client.patch(type_url(sponsor), {"needed_by_everyone": True}, format="json")

        assert ("Sponsor Letter", True) in stored_types(abc)

    @pytest.mark.parametrize(
        ("body", "refused"),
        [
            ({}, {"needed_by_everyone": ["This field is required."]}),
            ({"needed_by_everyone": "false"}, {"needed_by_everyone": ["Must be true or false."]}),
            ({"needed_by_everyone": None}, {"needed_by_everyone": ["Must be true or false."]}),
            ({"needed_by_everyone": False, "name": "Visa"}, {"detail": ["Unknown field."]}),
        ],
    )
    def test_bad_input_is_refused(
        self, abc: ConsultancyContext, body: dict[str, object], refused: dict[str, list[str]]
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.patch(type_url(type_id(abc, "Passport")), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == refused
        assert ("Passport", True) in stored_types(abc)

    def test_an_unknown_type_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.patch(type_url(999_999), {"needed_by_everyone": False}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestTypeMethods:
    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_the_list_answers_only_get_and_post(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        assert getattr(abc.client, method)(TYPES_URL).status_code == 405

    @pytest.mark.parametrize("method", ["get", "post", "put"])
    def test_one_type_answers_only_patch_and_delete(
        self, abc: ConsultancyContext, method: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(type_url(type_id(abc, "Passport")))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_not_on_the_platform_host(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        assert platform_site.client.get(TYPES_URL).status_code == status.HTTP_404_NOT_FOUND


def names_in_order(body: dict[str, object]) -> list[str]:
    """The Students' names as the Documents page lists them."""
    results = body["results"]
    assert isinstance(results, list)
    return [row["student"]["name"] for row in results]


class TestDocumentsPage:
    def test_students_are_ordered_by_how_much_attention_they_need(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        missing_all = make_student(abc, me, Stage.COUNSELLING, name="Asha Missing")
        one_rejected = make_student(abc, me, Stage.APPLYING, name="Bina Rejected")
        all_correct = make_student(abc, me, Stage.VISA, name="Chandra Correct")
        for name in DEFAULT_TYPES:
            make_document(abc, all_correct, name, Verification.VERIFIED)
            make_document(abc, one_rejected, name, Verification.VERIFIED)
        make_document(abc, one_rejected, "Passport", Verification.REJECTED)

        body = abc.client.get(PAPERWORK_URL).json()

        assert names_in_order(body) == ["Asha Missing", "Bina Rejected", "Chandra Correct"]
        assert body["results"][1] == {
            "student": {
                "id": str(one_rejected.public_id),
                "name": "Bina Rejected",
                "stage": "applying",
                "country": "australia",
                "assigned_counsellor": {"id": str(me.public_id), "name": "Bikash Thapa"},
            },
            "done": 7,
            "total": 8,
            "rejected": 1,
            "waiting": 0,
            "missing": 0,
            "verified": 7,
            "items": [
                {"name": "Passport", "state": "rejected"},
                *({"name": name, "state": "verified"} for name in DEFAULT_TYPES[1:]),
            ],
        }
        assert body["totals"] == {"rejected": 1, "waiting": 0, "missing": 8, "verified": 15}
        assert (body["count"], body["page"], body["page_size"]) == (3, 1, 25)
        assert str(missing_all.public_id) == body["results"][0]["student"]["id"]

    def test_each_student_lists_their_checklist_documents_by_name_and_state(
        self, abc: ConsultancyContext
    ) -> None:
        """UI revamp 12: a pill per document; Other documents and the Bin never count."""
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.APPLYING, name="Asha Mixed")
        make_document(abc, student, "Passport", Verification.REJECTED)
        make_document(abc, student, "Citizenship", Verification.PENDING)
        make_document(abc, student, "SEE Marksheet", Verification.VERIFIED)
        binned = make_document(abc, student, "+2 Transcript", Verification.VERIFIED)
        with tenant_context(abc.consultancy):
            binned.binned_at = timezone.now()
            binned.save(update_fields=["binned_at"])
            DocumentType.objects.create(name="Gap year letter", needed_by_everyone=False)
        make_document(abc, student, "Gap year letter", Verification.PENDING)

        row = abc.client.get(PAPERWORK_URL).json()["results"][0]

        assert row["items"] == [
            {"name": "Passport", "state": "rejected"},
            {"name": "Citizenship", "state": "waiting"},
            {"name": "SEE Marksheet", "state": "verified"},
            *({"name": name, "state": "missing"} for name in DEFAULT_TYPES[3:]),
        ]
        states = [item["state"] for item in row["items"]]
        assert len(states) == row["total"]
        for state in ("rejected", "waiting", "verified", "missing"):
            assert states.count(state) == row[state]

    def test_items_follow_the_counsellors_scope_and_the_waiting_filter(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        mine = make_student(abc, me, Stage.COUNSELLING, name="Asha Mine")
        make_student(abc, me, Stage.COUNSELLING, name="Bina Nothing")
        theirs = make_student(abc, named_staff(abc, "Gita Rai"), Stage.VISA, name="Dipa Theirs")
        make_document(abc, mine, "Passport")
        make_document(abc, theirs, "Citizenship")

        body = abc.client.get(PAPERWORK_URL, {"scope": "mine", "waiting": "true"}).json()

        assert names_in_order(body) == ["Asha Mine"]
        assert body["results"][0]["items"] == [
            {"name": "Passport", "state": "waiting"},
            *({"name": name, "state": "missing"} for name in DEFAULT_TYPES[1:]),
        ]

    def test_a_document_name_shaped_like_an_attack_is_sent_as_plain_text(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, stage=Stage.COUNSELLING, name="Asha Plain")
        with tenant_context(abc.consultancy):
            DocumentType.objects.create(name=INJECTION_STRINGS[0], needed_by_everyone=True)

        row = abc.client.get(PAPERWORK_URL).json()["results"][0]

        assert row["items"][-1] == {"name": INJECTION_STRINGS[0], "state": "missing"}
        assert set(row["items"][0]) == {"name", "state"}

    def test_home_counts_waiting_as_the_page_does_checklist_documents_only(
        self, abc: ConsultancyContext
    ) -> None:
        """Home said 14, the page 10: Other documents aren't counted (list tidy-ups 22)."""
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.APPLYING, name="Asha Waiting")
        other = make_student(abc, stage=Stage.OFFER, name="Bina Other Only")
        make_document(abc, student, "Passport", Verification.PENDING)
        with tenant_context(abc.consultancy):
            DocumentType.objects.create(name="Gap year letter", needed_by_everyone=False)
        make_document(abc, student, "Gap year letter", Verification.PENDING)
        make_document(abc, other, "Gap year letter", Verification.PENDING)

        totals = abc.client.get(PAPERWORK_URL).json()["totals"]
        home = abc.client.get("/api/home/waiting/").json()
        only_waiting = abc.client.get(f"{PAPERWORK_URL}?waiting=true").json()

        assert totals["waiting"] == 1
        assert home["documents_waiting"] == 1
        assert names_in_order(only_waiting) == ["Asha Waiting"]
        assert other.name not in names_in_order(only_waiting)

    def test_rejected_weighs_more_than_waiting_which_weighs_more_than_missing(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        waiting = make_student(abc, stage=Stage.COUNSELLING, name="Waiting Two")
        rejected = make_student(abc, stage=Stage.COUNSELLING, name="Rejected One")
        for name in DEFAULT_TYPES[2:]:
            make_document(abc, waiting, name, Verification.VERIFIED)
            make_document(abc, rejected, name, Verification.VERIFIED)
        make_document(abc, waiting, "Passport")
        make_document(abc, waiting, "Citizenship")
        make_document(abc, rejected, "Passport", Verification.REJECTED)
        make_document(abc, rejected, "Citizenship", Verification.REJECTED)

        assert names_in_order(abc.client.get(PAPERWORK_URL).json()) == [
            "Rejected One",
            "Waiting Two",
        ]

    def test_leads_and_lost_students_are_left_out(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for stage in Stage:
            make_student(abc, stage=stage, name=f"{stage.label} Student")

        names = names_in_order(abc.client.get(PAPERWORK_URL).json())

        assert sorted(names) == sorted(
            f"{stage.label} Student" for stage in Stage if stage not in (Stage.LEAD, Stage.LOST)
        )

    def test_a_counsellor_can_see_only_their_own(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        make_student(abc, me, Stage.COUNSELLING, name="Mine")
        make_student(abc, named_staff(abc, "Gita Rai"), Stage.COUNSELLING, name="Theirs")

        mine = abc.client.get(PAPERWORK_URL, {"scope": "mine"}).json()
        everyone = abc.client.get(PAPERWORK_URL, {"scope": "everyone"}).json()

        assert names_in_order(mine) == ["Mine"]
        assert sorted(names_in_order(everyone)) == ["Mine", "Theirs"]
        assert mine["totals"]["missing"] == len(DEFAULT_TYPES)

    def test_waiting_to_check_lists_only_students_with_a_pending_document(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        waiting = make_student(abc, me, Stage.COUNSELLING, name="Asha Waiting")
        checked = make_student(abc, me, Stage.COUNSELLING, name="Bina Checked")
        replaced = make_student(abc, me, Stage.COUNSELLING, name="Chandra Replaced")
        theirs = make_student(abc, named_staff(abc, "Gita Rai"), Stage.VISA, name="Dipa Theirs")
        make_student(abc, me, Stage.COUNSELLING, name="Esha Nothing")
        make_document(abc, waiting, "Passport")
        make_document(abc, checked, "Passport", Verification.VERIFIED)
        make_document(abc, checked, "Citizenship", Verification.REJECTED)
        make_document(abc, replaced, "Passport", Verification.REJECTED)
        make_document(abc, replaced, "Passport")
        make_document(abc, theirs, "Passport")

        mine = abc.client.get(PAPERWORK_URL, {"scope": "mine", "waiting": "true"}).json()
        everyone = abc.client.get(PAPERWORK_URL, {"waiting": "true"}).json()
        unfiltered = abc.client.get(PAPERWORK_URL, {"scope": "mine", "waiting": "false"}).json()

        assert names_in_order(mine) == ["Asha Waiting", "Chandra Replaced"]
        assert mine["totals"]["waiting"] == 2
        assert sorted(names_in_order(everyone)) == [
            "Asha Waiting",
            "Chandra Replaced",
            "Dipa Theirs",
        ]
        assert unfiltered["count"] == 4

    def test_waiting_to_check_matches_the_home_count(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for number in range(3):
            student = make_student(
                abc, stage=Stage.COUNSELLING, name=f"S {number}", phone=f"984100000{number}"
            )
            make_document(abc, student, "Passport")
        lead = make_student(abc, stage=Stage.LEAD, name="A lead", phone="9841000009")
        make_document(abc, lead, "Passport")

        page = abc.client.get(PAPERWORK_URL, {"waiting": "true"}).json()
        home = abc.client.get("/api/home/waiting/").json()

        assert page["count"] == home["documents_waiting"] == 3

    def test_students_are_paged(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for number in range(26):
            make_student(abc, stage=Stage.COUNSELLING, name=f"Student {number:02}")

        second = abc.client.get(PAPERWORK_URL, {"page": "2"}).json()

        assert (second["count"], second["page"], len(second["results"])) == (26, 2, 1)
        assert second["totals"]["missing"] == 26 * len(DEFAULT_TYPES)
        assert abc.client.get(PAPERWORK_URL, {"page": "3"}).status_code == 404

    @pytest.mark.parametrize(
        "query",
        [
            {"scope": "all"},
            {"scope": "MINE"},
            {"page": "0"},
            {"page": "x"},
            {"counsellor": "1"},
            {"waiting": "yes"},
            {"waiting": "1"},
            {"waiting": "TRUE"},
            *({"waiting": text} for text in INJECTION_STRINGS),
        ],
    )
    def test_bad_query_parameters_are_refused(
        self, abc: ConsultancyContext, query: dict[str, str]
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(PAPERWORK_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize(("role", "code"), [(Role.FRONT_DESK, 403), (Role.TEACHER, 403)])
    def test_front_desk_and_teachers_are_refused(
        self, abc: ConsultancyContext, role: Role, code: int
    ) -> None:
        abc.log_in_as(role)

        response = abc.client.get(PAPERWORK_URL)

        assert response.status_code == code
        assert response.json() == ONLY_COUNSELLORS

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.client.get(PAPERWORK_URL).status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_it_answers_only_get(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        assert getattr(abc.client, method)(PAPERWORK_URL).status_code == 405
