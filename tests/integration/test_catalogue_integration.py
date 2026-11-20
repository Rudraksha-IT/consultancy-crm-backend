"""Admins keep the Catalogue: Universities, Courses and Intakes (spec 46-47, 50-51; issue 12)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from catalogue.models import Course, Intake, University
from catalogue.services import UNIVERSITIES_PER_PAGE
from staff.models import Role
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext

# A record UUID that no office has (ADR 0027).
SOME_UUID = "00000000-0000-4000-8000-000000000001"

if TYPE_CHECKING:
    from pytest_django import DjangoAssertNumQueries
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

UNIVERSITIES_URL = "/api/catalogue/universities/"
# Upcoming years, so new Intakes are never in a month gone by (issue 92).
Y1 = timezone.localdate().year + 1
Y2 = Y1 + 1
ONLY_ADMIN = {"detail": "Only an Admin can change the Catalogue."}
NOT_FOR_TEACHERS = {"detail": "Teachers can't see this."}
READERS = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)
NOT_ADMINS = (Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER)
# Login checks, the Consultancy and its Modules, then the count, the page, courses and intakes, and
# the extra documents the Universities and the Courses ask for.
MOST_QUERIES_FOR_A_PAGE = 16
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE catalogue_university; --",
    "}body{background:url(javascript:alert(1))}",
]


def university_url(university_id: object) -> str:
    """One University."""
    return f"{UNIVERSITIES_URL}{university_id}/"


def courses_url(university_id: object) -> str:
    """Where a University's Courses are added."""
    return f"{UNIVERSITIES_URL}{university_id}/courses/"


def course_url(course_id: object) -> str:
    """One Course."""
    return f"/api/catalogue/courses/{course_id}/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def add_university(
    client: APIClient, name: str = "Deakin University", country: str = "australia"
) -> _MonkeyPatchedResponse:
    """POST a new University."""
    return client.post(UNIVERSITIES_URL, {"name": name, "country": country}, format="json")


def course_body(**changes: object) -> dict[str, object]:
    """What the course dialog sends."""
    return {
        "name": "Master of Information Technology",
        "level": "master",
        "intakes": [f"{Y1}-03", f"{Y1}-07"],
        **changes,
    }


def add_course(
    client: APIClient, university_id: object, **changes: object
) -> _MonkeyPatchedResponse:
    """POST a new Course to a University."""
    return client.post(courses_url(university_id), course_body(**changes), format="json")


def make_university(
    office: ConsultancyContext, name: str = "Deakin University", *, archived: bool = False
) -> University:
    """Store a University directly."""
    with tenant_context(office.consultancy):
        return University.objects.create(name=name, country="australia", archived=archived)


def make_course(
    office: ConsultancyContext,
    university: University,
    name: str = "Bachelor of Nursing",
    *,
    archived: bool = False,
    intakes: tuple[str, ...] = (f"{Y1}-02",),
) -> Course:
    """Store a Course and its Intakes directly."""
    with tenant_context(office.consultancy):
        course = Course.objects.create(
            university=university, name=name, level="bachelor", archived=archived
        )
        Intake.objects.bulk_create(Intake(course=course, month=month) for month in intakes)
        return course


def names_in(response: _MonkeyPatchedResponse) -> list[str]:
    """The names on a list page."""
    return [row["name"] for row in response.json()["results"]]


class TestAddUniversity:
    def test_an_admin_adds_a_university(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add_university(abc.client)

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body == {
            "id": body["id"],
            "name": "Deakin University",
            "country": "australia",
            "archived": False,
            "courses": [],
        }
        with tenant_context(abc.consultancy):
            assert University.objects.get(public_id=body["id"]).name == "Deakin University"

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_can_add_one(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = add_university(abc.client)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        with tenant_context(abc.consultancy):
            assert not University.objects.exists()

    def test_the_name_is_trimmed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add_university(abc.client, name="  Deakin University  ")

        assert response.json()["name"] == "Deakin University"

    def test_the_same_name_twice_is_refused_whatever_its_case(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        add_university(abc.client)

        response = add_university(abc.client, name="deakin university")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"name": ["There's already a university with this name."]}

    def test_a_name_and_a_country_are_needed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(UNIVERSITIES_URL, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.json()) == {"name", "country"}

    @pytest.mark.parametrize(
        "changes",
        [
            {"name": ""},
            {"name": "   "},
            {"name": 12},
            {"name": None},
            {"name": ["Deakin"]},
            {"name": "x" * 201},
            {"name": "Deakin\nUniversity"},
            {"country": "mars"},
            {"country": 1},
            {"country": ""},
        ],
    )
    def test_bad_input_is_refused(
        self, abc: ConsultancyContext, changes: dict[str, object]
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        body = {"name": "Deakin University", "country": "australia", **changes}

        response = abc.client.post(UNIVERSITIES_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(abc.consultancy):
            assert not University.objects.exists()

    @pytest.mark.parametrize("field", ["archived", "id", "courses", "tenant"])
    def test_unknown_or_read_only_fields_are_refused(
        self, abc: ConsultancyContext, field: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        body = {"name": "Deakin University", "country": "australia", field: True}

        response = abc.client.post(UNIVERSITIES_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_names_are_stored_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add_university(abc.client, name=text)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["name"] == text
        with tenant_context(abc.consultancy):
            assert University.objects.get().name == text

    def test_adding_is_logged_without_the_name(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)

        university_id = add_university(abc.client).json()["id"]
        with tenant_context(abc.consultancy):  # logs keep the number
            university_id = University.objects.get(public_id=university_id).pk

        line = json_logs.only(
            f"University added: university_id={university_id}, by_staff_id={admin.pk}"
        )
        assert line["level"] == "INFO"


class TestChangeUniversity:
    def test_an_admin_renames_and_moves_a_university(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = abc.client.patch(
            university_url(university.public_id),
            {"name": "Deakin University Melbourne", "country": "uk"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == "Deakin University Melbourne"
        assert response.json()["country"] == "uk"

    def test_only_the_fields_sent_change(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = abc.client.patch(
            university_url(university.public_id), {"country": "uk"}, format="json"
        )

        assert response.json()["name"] == "Deakin University"
        assert response.json()["country"] == "uk"

    def test_archiving_and_restoring(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        archived = abc.client.patch(
            university_url(university.public_id), {"archived": True}, format="json"
        )
        restored = abc.client.patch(
            university_url(university.public_id), {"archived": False}, format="json"
        )

        assert archived.json()["archived"] is True
        assert restored.json()["archived"] is False
        with tenant_context(abc.consultancy):
            assert University.objects.filter(pk=university.pk).exists()

    def test_renaming_to_another_universitys_name_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_university(abc, "Deakin University")
        other = make_university(abc, "Federation University")

        response = abc.client.patch(
            university_url(other.public_id), {"name": "DEAKIN UNIVERSITY"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"name": ["There's already a university with this name."]}

    def test_keeping_its_own_name_in_another_case_is_fine(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc, "Deakin university")

        response = abc.client.patch(
            university_url(university.public_id), {"name": "Deakin University"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_can_change_one(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        university = make_university(abc)

        response = abc.client.patch(
            university_url(university.public_id), {"archived": True}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        with tenant_context(abc.consultancy):
            assert University.objects.get().archived is False

    @pytest.mark.parametrize(
        "body",
        [
            {"archived": "yes"},
            {"archived": 1},
            {"archived": None},
            {"name": ""},
            {"country": "mars"},
            {"courses": []},
            {"id": 9},
        ],
    )
    def test_bad_input_is_refused(self, abc: ConsultancyContext, body: dict[str, object]) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = abc.client.patch(university_url(university.public_id), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_unknown_university_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.patch(university_url(999), {"archived": True}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_post_is_not_allowed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        method = "post"

        response = getattr(abc.client, method)(
            university_url(university.public_id), {}, format="json"
        )

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        with tenant_context(abc.consultancy):
            assert University.objects.exists()

    def test_archiving_is_logged(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        abc.client.patch(university_url(university.public_id), {"archived": True}, format="json")

        json_logs.only(f"University changed: university_id={university.pk}, by_staff_id={admin.pk}")


class TestAddCourse:
    def test_an_admin_adds_a_course_with_its_intakes(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = add_course(abc.client, university.public_id)

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body == {
            "id": body["id"],
            "university_id": str(university.public_id),
            "name": "Master of Information Technology",
            "level": "master",
            "intakes": [f"{Y1}-03", f"{Y1}-07"],
            "archived": False,
            "documents": [],
            "applications": 0,
            "files": [],
            "file_count": 0,
        }

    def test_intakes_are_kept_in_order_without_repeats(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = add_course(
            abc.client,
            university.public_id,
            intakes=[f"{Y1}-11", f"{Y1}-03", f"{Y1}-11", f"{Y1}-07"],
        )

        assert response.json()["intakes"] == [f"{Y1}-03", f"{Y1}-07", f"{Y1}-11"]

    @pytest.mark.parametrize(
        "level", ["bachelor", "master", "diploma", "graduate_certificate", "foundation"]
    )
    def test_every_level_can_be_chosen(self, abc: ConsultancyContext, level: str) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = add_course(abc.client, university.public_id, level=level)

        assert response.status_code == status.HTTP_201_CREATED

    def test_the_same_course_name_twice_at_one_university_is_refused(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        add_course(abc.client, university.public_id)

        response = add_course(
            abc.client, university.public_id, name="master of information technology"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"name": ["This university already has a course with this name."]}

    def test_the_same_course_name_at_another_university_is_fine(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        deakin = make_university(abc, "Deakin University")
        federation = make_university(abc, "Federation University")
        add_course(abc.client, deakin.public_id)

        response = add_course(abc.client, federation.public_id)

        assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_can_add_one(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        university = make_university(abc)

        response = add_course(abc.client, university.public_id)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        with tenant_context(abc.consultancy):
            assert not Course.objects.exists()

    def test_a_course_for_an_unknown_university_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add_course(abc.client, 999)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_at_least_one_intake_is_needed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = add_course(abc.client, university.public_id, intakes=[])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"intakes": ["Add at least one intake."]}

    def test_too_many_intakes_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        intakes = [f"{2027 + n // 12}-{n % 12 + 1:02d}" for n in range(25)]

        response = add_course(abc.client, university.public_id, intakes=intakes)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"intakes": ["A course can have at most 24 intakes."]}

    @pytest.mark.parametrize(
        "changes",
        [
            {"name": ""},
            {"name": "x" * 201},
            {"name": 5},
            {"name": "Master\tof IT"},
            {"level": "bachelors"},
            {"level": 1},
            {"intakes": f"{Y1}-03"},
            {"intakes": ["Mar 2027"]},
            {"intakes": [f"{Y1}-13"]},
            {"intakes": ["1999-01"]},
            {"intakes": [f"{Y1}-3"]},
            {"intakes": [202703]},
            {"intakes": [None]},
            {"intakes": [f"{Y1}-03\n"]},
            {"intakes": None},
        ],
    )
    def test_bad_input_is_refused(
        self, abc: ConsultancyContext, changes: dict[str, object]
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = add_course(abc.client, university.public_id, **changes)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(abc.consultancy):
            assert not Course.objects.exists()
            assert not Intake.objects.exists()

    @pytest.mark.parametrize("field", ["archived", "university", "university_id", "docs"])
    def test_unknown_or_read_only_fields_are_refused(
        self, abc: ConsultancyContext, field: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        body = course_body(**{field: 1})

        response = abc.client.post(courses_url(university.public_id), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_names_are_stored_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = add_course(abc.client, university.public_id, name=text)

        assert response.json()["name"] == text

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = getattr(abc.client, method)(courses_url(university.public_id), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_adding_is_logged(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        course_id = add_course(abc.client, university.public_id).json()["id"]
        with tenant_context(abc.consultancy):  # logs keep the number
            course_id = Course.objects.get(public_id=course_id).pk

        json_logs.only(
            f"Course added: course_id={course_id}, university_id={university.pk}, "
            f"by_staff_id={admin.pk}"
        )
        assert "Information Technology" not in json_logs.text()


class TestChangeCourse:
    def test_an_admin_changes_a_courses_name_level_and_intakes(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc, make_university(abc), intakes=(f"{Y1}-02", f"{Y1}-07"))

        response = abc.client.patch(
            course_url(course.public_id),
            {"name": "Bachelor of Nursing (Honours)", "level": "master", "intakes": [f"{Y2}-02"]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == "Bachelor of Nursing (Honours)"
        assert response.json()["level"] == "master"
        assert response.json()["intakes"] == [f"{Y2}-02"]
        with tenant_context(abc.consultancy):
            assert list(Intake.objects.values_list("month", flat=True)) == [f"{Y2}-02"]

    def test_intakes_kept_are_not_recreated(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc, make_university(abc), intakes=(f"{Y1}-02", f"{Y1}-07"))
        with tenant_context(abc.consultancy):
            kept_id = Intake.objects.get(month=f"{Y1}-02").pk

        abc.client.patch(
            course_url(course.public_id), {"intakes": [f"{Y1}-02", f"{Y1}-11"]}, format="json"
        )

        with tenant_context(abc.consultancy):
            assert Intake.objects.get(month=f"{Y1}-02").pk == kept_id
            assert sorted(Intake.objects.values_list("month", flat=True)) == [
                f"{Y1}-02",
                f"{Y1}-11",
            ]

    def test_only_the_fields_sent_change(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc, make_university(abc), intakes=(f"{Y1}-02",))

        response = abc.client.patch(
            course_url(course.public_id), {"level": "diploma"}, format="json"
        )

        assert response.json()["name"] == "Bachelor of Nursing"
        assert response.json()["intakes"] == [f"{Y1}-02"]

    def test_archiving_and_restoring(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc, make_university(abc))

        archived = abc.client.patch(course_url(course.public_id), {"archived": True}, format="json")
        restored = abc.client.patch(
            course_url(course.public_id), {"archived": False}, format="json"
        )

        assert archived.json()["archived"] is True
        assert restored.json()["archived"] is False

    def test_renaming_to_a_sibling_courses_name_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        make_course(abc, university, "Bachelor of Nursing")
        other = make_course(abc, university, "Bachelor of IT")

        response = abc.client.patch(
            course_url(other.public_id), {"name": "Bachelor of nursing"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"name": ["This university already has a course with this name."]}

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_can_change_one(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        course = make_course(abc, make_university(abc))

        response = abc.client.patch(course_url(course.public_id), {"archived": True}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize(
        "body",
        [
            {"intakes": []},
            {"intakes": ["Feb 2027"]},
            {"level": "phd"},
            {"archived": "true"},
            {"university_id": 2},
            {"name": None},
        ],
    )
    def test_bad_input_is_refused_and_nothing_changes(
        self, abc: ConsultancyContext, body: dict[str, object]
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc, make_university(abc), intakes=(f"{Y1}-02",))

        response = abc.client.patch(course_url(course.public_id), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        with tenant_context(abc.consultancy):
            assert list(Intake.objects.values_list("month", flat=True)) == [f"{Y1}-02"]

    def test_an_unknown_course_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.patch(course_url(999), {"archived": True}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "put", "delete", "post"])
    def test_only_patch_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        course = make_course(abc, make_university(abc))

        response = getattr(abc.client, method)(course_url(course.public_id), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        with tenant_context(abc.consultancy):
            assert Course.objects.exists()

    def test_changing_is_logged(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        course = make_course(abc, make_university(abc))

        abc.client.patch(course_url(course.public_id), {"archived": True}, format="json")

        json_logs.only(f"Course changed: course_id={course.pk}, by_staff_id={admin.pk}")


class TestCatalogueList:
    @pytest.mark.parametrize("role", READERS)
    def test_every_role_but_teacher_can_read_it_for_picking(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)
        make_course(abc, make_university(abc))

        response = abc.client.get(UNIVERSITIES_URL)

        assert response.status_code == status.HTTP_200_OK
        assert names_in(response) == ["Deakin University"]

    def test_a_teacher_cannot_read_it(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(UNIVERSITIES_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_universities_come_with_their_courses_and_intakes(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        university = make_university(abc)
        course = make_course(abc, university, intakes=(f"{Y1}-07", f"{Y1}-02"))

        body = abc.client.get(UNIVERSITIES_URL).json()

        assert body == {
            "count": 1,
            "page": 1,
            "page_size": UNIVERSITIES_PER_PAGE,
            "results": [
                {
                    "id": str(university.public_id),
                    "name": "Deakin University",
                    "country": "australia",
                    "archived": False,
                    "courses": [
                        {
                            "id": str(course.public_id),
                            "university_id": str(university.public_id),
                            "name": "Bachelor of Nursing",
                            "level": "bachelor",
                            "intakes": [f"{Y1}-02", f"{Y1}-07"],
                            "archived": False,
                            "documents": [],
                            "applications": 0,
                            "files": [],
                            "file_count": 0,
                        }
                    ],
                }
            ],
        }

    def test_universities_and_courses_are_in_name_order(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        wollongong = make_university(abc, "University of Wollongong")
        make_university(abc, "deakin University")
        make_course(abc, wollongong, "Master of IT")
        make_course(abc, wollongong, "bachelor of IT")

        body = abc.client.get(UNIVERSITIES_URL).json()

        assert names_in_body(body) == ["deakin University", "University of Wollongong"]
        assert [c["name"] for c in body["results"][1]["courses"]] == [
            "bachelor of IT",
            "Master of IT",
        ]

    def test_archived_universities_and_courses_are_hidden_from_pickers(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        deakin = make_university(abc, "Deakin University")
        make_course(abc, deakin, "Bachelor of Nursing")
        make_course(abc, deakin, "Old course", archived=True)
        kaplan = make_university(abc, "Kaplan Business School", archived=True)
        make_course(abc, kaplan, "Bachelor of Business")

        body = abc.client.get(UNIVERSITIES_URL).json()

        assert names_in_body(body) == ["Deakin University"]
        assert [c["name"] for c in body["results"][0]["courses"]] == ["Bachelor of Nursing"]

    def test_archived_entries_are_shown_when_asked_for(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        deakin = make_university(abc, "Deakin University")
        make_course(abc, deakin, "Old course", archived=True)
        make_university(abc, "Kaplan Business School", archived=True)

        body = abc.client.get(UNIVERSITIES_URL, {"include_archived": "true"}).json()

        assert names_in_body(body) == ["Deakin University", "Kaplan Business School"]
        assert body["results"][0]["courses"][0]["archived"] is True
        assert body["results"][1]["archived"] is True

    def test_a_search_matches_part_of_a_university_name(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_university(abc, "Deakin University")
        make_university(abc, "Federation University")

        response = abc.client.get(UNIVERSITIES_URL, {"q": "feder"})

        assert names_in(response) == ["Federation University"]

    def test_the_list_is_paginated(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for number in range(UNIVERSITIES_PER_PAGE + 2):
            make_university(abc, f"University {number:02d}")

        first = abc.client.get(UNIVERSITIES_URL).json()
        second = abc.client.get(UNIVERSITIES_URL, {"page": 2}).json()
        beyond = abc.client.get(UNIVERSITIES_URL, {"page": 3})

        assert first["count"] == UNIVERSITIES_PER_PAGE + 2
        assert len(first["results"]) == UNIVERSITIES_PER_PAGE
        assert names_in_body(second) == ["University 25", "University 26"]
        assert beyond.status_code == status.HTTP_404_NOT_FOUND

    def test_the_list_needs_few_queries(
        self, abc: ConsultancyContext, django_assert_max_num_queries: DjangoAssertNumQueries
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        for number in range(UNIVERSITIES_PER_PAGE):
            make_course(abc, make_university(abc, f"University {number}"))

        with django_assert_max_num_queries(MOST_QUERIES_FOR_A_PAGE):
            response = abc.client.get(UNIVERSITIES_URL)

        assert len(response.json()["results"]) == UNIVERSITIES_PER_PAGE

    @pytest.mark.parametrize(
        "query",
        [
            {"q": "x" * 101},
            {"include_archived": "maybe"},
            {"page": "0"},
            {"page": "two"},
            {"sort": "name"},
        ],
    )
    def test_bad_queries_are_refused(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(UNIVERSITIES_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_searches_find_nothing_and_break_nothing(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        make_university(abc)

        response = abc.client.get(UNIVERSITIES_URL, {"q": text})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["results"] == []

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_only_get_and_post_are_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(UNIVERSITIES_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


def names_in_body(body: dict[str, list[dict[str, object]]]) -> list[object]:
    """The names in a list answer."""
    return [row["name"] for row in body["results"]]


class TestCatalogueAccess:
    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("get", UNIVERSITIES_URL),
            ("post", UNIVERSITIES_URL),
            ("patch", university_url(SOME_UUID)),
            ("post", courses_url(SOME_UUID)),
            ("patch", course_url(SOME_UUID)),
        ],
    )
    def test_nobody_logged_in_is_refused(
        self, abc: ConsultancyContext, method: str, url: str
    ) -> None:
        response = getattr(abc.client, method)(url, {}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_non_admin_is_told_why(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = add_university(abc.client)

        assert response.json() == ONLY_ADMIN

    def test_the_catalogue_is_not_served_on_the_platform_host(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        response = platform_site.client.get(UNIVERSITIES_URL)

        assert response.status_code == status.HTTP_404_NOT_FOUND
