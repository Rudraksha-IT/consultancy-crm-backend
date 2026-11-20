"""One edit page per University, Intakes on a month grid, delete once archived (issue 92).

Spec 142-145: the Catalogue shows every Course; Intakes are added and removed on the list; a
University's edit page saves it and every Course at once, or nothing; an archived University or
Course can be deleted unless an Application uses it; a Student's preferred Intake is one of the
Catalogue's upcoming Intakes.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application
from catalogue.edit_serializers import UniversityEditSerializer
from catalogue.intakes import intake_label, month_of
from catalogue.models import Course, Intake, University
from commission.models import Commission
from documents.models import DocumentRequirement, DocumentType
from staff.models import Role
from students.models import Source, Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

UNIVERSITIES_URL = "/api/catalogue/universities/"
INTAKES_URL = "/api/catalogue/intakes/"
ONLY_ADMIN = {"detail": "Only an Admin can change the Catalogue."}
NOT_FOR_TEACHERS = {"detail": "Teachers can't see this."}
NOT_ADMINS = (Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER)
READERS = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE catalogue_course; --",
    "}body{background:url(javascript:alert(1))}",
]
# A year every test can use as "upcoming", whatever today is.
NEXT_YEAR = timezone.localdate().year + 1
FEB = f"{NEXT_YEAR}-02"
JUL = f"{NEXT_YEAR}-07"
NOV = f"{NEXT_YEAR}-11"
# The past-month rule, checked against today's month and the one before it.
TODAY = timezone.localdate()
THIS_MONTH = month_of(TODAY)
LAST_MONTH = month_of(TODAY.replace(day=1) - timedelta(days=1))
EARLIER = month_of(TODAY.replace(day=1) - timedelta(days=40))
LATER = f"{TODAY.year + 2}-03"


def university_url(university_id: object) -> str:
    """One University."""
    return f"{UNIVERSITIES_URL}{university_id}/"


def course_url(course_id: object) -> str:
    """One Course."""
    return f"/api/catalogue/courses/{course_id}/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


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
    intakes: tuple[str, ...] = (FEB,),
) -> Course:
    """Store a Course and its Intakes directly."""
    with tenant_context(office.consultancy):
        course = Course.objects.create(
            university=university, name=name, level="bachelor", archived=archived
        )
        Intake.objects.bulk_create(Intake(course=course, month=month) for month in intakes)
        return course


def make_student(office: ConsultancyContext, **fields: object) -> Student:
    """Store a Student directly."""
    details: dict[str, object] = {
        "name": "Sita Gurung",
        "phone": "9841000001",
        "country": "australia",
        "source": Source.WALK_IN,
        "highest_qualification": "plus_two",
        **fields,
    }
    with tenant_context(office.consultancy):
        return Student.objects.create(stage=Stage.COUNSELLING, **details)


def make_application(office: ConsultancyContext, course: Course, intake: str = FEB) -> Application:
    """Store an Application for a new Student on `course`."""
    student = make_student(office)
    with tenant_context(office.consultancy):
        return Application.objects.create(student=student, course=course, intake=intake)


def make_type(office: ConsultancyContext, name: str, *, everyone: bool = False) -> DocumentType:
    """Store a Document Type directly."""
    with tenant_context(office.consultancy):
        return DocumentType.objects.create(name=name, needed_by_everyone=everyone)


def course_row(course: Course | None = None, **changes: object) -> dict[str, object]:
    """One Course as the edit page sends it; a new one has no id."""
    row: dict[str, object] = {
        "name": "Bachelor of Nursing" if course is None else course.name,
        "level": "bachelor",
        "intakes": [FEB],
        "archived": False if course is None else course.archived,
        "documents": [],
    }
    if course is not None:
        row["id"] = str(course.public_id)
    return {**row, **changes}


def page_body(
    courses: list[dict[str, object]] | None = None,
    deleted: list[str] | None = None,
    **changes: object,
) -> dict[str, object]:
    """Everything the edit page sends."""
    return {
        "name": "Deakin University",
        "country": "australia",
        "archived": False,
        "courses": courses or [],
        "deleted_courses": deleted or [],
        **changes,
    }


def save_page(
    office: ConsultancyContext, university: University, body: dict[str, object]
) -> _MonkeyPatchedResponse:
    """PUT the whole edit page."""
    return office.client.put(university_url(university.public_id), body, format="json")


def stored_courses(office: ConsultancyContext, university: University) -> list[tuple[str, bool]]:
    """The University's Courses as stored: name and archived."""
    with tenant_context(office.consultancy):
        return list(
            Course.objects.filter(university=university)
            .order_by("name")
            .values_list("name", "archived")
        )


def stored_intakes(office: ConsultancyContext, course: Course) -> list[str]:
    """The Course's Intakes as stored."""
    with tenant_context(office.consultancy):
        return list(Intake.objects.filter(course=course).values_list("month", flat=True))


class TestOneUniversity:
    def test_an_admin_opens_a_university_with_every_course_and_how_often_it_is_used(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        used = make_course(abc, university, "Bachelor of Nursing", intakes=(FEB, JUL))
        make_course(abc, university, "Master of Nursing", archived=True)
        make_application(abc, used)

        response = abc.client.get(university_url(university.public_id))

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["name"] == "Deakin University"
        assert [(c["name"], c["archived"], c["applications"]) for c in body["courses"]] == [
            ("Bachelor of Nursing", False, 1),
            ("Master of Nursing", True, 0),
        ]
        assert body["courses"][0]["intakes"] == [FEB, JUL]

    @pytest.mark.parametrize("role", READERS)
    def test_roles_that_pick_courses_can_read_it(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        university = make_university(abc)

        assert (
            abc.client.get(university_url(university.public_id)).status_code == status.HTTP_200_OK
        )

    def test_a_teacher_cannot(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)
        university = make_university(abc)

        response = abc.client.get(university_url(university.public_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_an_unknown_university_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(university_url(999_999)).status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        university = make_university(abc)

        response = abc.new_client().get(university_url(university.public_id))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestSavingTheEditPage:
    def test_one_save_stores_the_university_and_every_course(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university, intakes=(FEB,))
        reference = make_type(abc, "Work Reference")
        body = page_body(
            name="Deakin University Melbourne",
            country="canada",
            courses=[
                course_row(nursing, name="Bachelor of Nursing (Honours)", intakes=[NOV, FEB, NOV]),
                course_row(
                    None, name="Master of IT", level="master", documents=[str(reference.public_id)]
                ),
            ],
        )

        response = save_page(abc, university, body)

        assert response.status_code == status.HTTP_200_OK
        answer = response.json()
        assert answer["name"] == "Deakin University Melbourne"
        assert answer["country"] == "canada"
        assert [(c["name"], c["level"], c["intakes"]) for c in answer["courses"]] == [
            ("Bachelor of Nursing (Honours)", "bachelor", [FEB, NOV]),
            ("Master of IT", "master", [FEB]),
        ]
        assert answer["courses"][1]["documents"] == [
            {"id": str(reference.public_id), "name": "Work Reference"}
        ]
        assert stored_intakes(abc, nursing) == [FEB, NOV]

    def test_nothing_is_stored_when_one_course_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university)
        body = page_body(
            name="Renamed University",
            courses=[course_row(nursing, name="Renamed course"), course_row(None, name="")],
        )

        response = save_page(abc, university, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"courses": {"1": {"name": ["Enter the course's name."]}}}
        with tenant_context(abc.consultancy):
            assert University.objects.get().name == "Deakin University"
            assert Course.objects.get().name == "Bachelor of Nursing"

    def test_nothing_is_stored_when_the_database_refuses_a_name(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        make_university(abc, "Monash University")
        university = make_university(abc)
        nursing = make_course(abc, university)

        response = save_page(
            abc,
            university,
            page_body(name="monash university", courses=[course_row(nursing, name="Renamed")]),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"name": ["There's already a university with this name."]}
        assert stored_courses(abc, university) == [("Bachelor of Nursing", False)]

    def test_a_course_left_out_is_left_as_it_is(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        make_course(abc, university, "Bachelor of Nursing")

        response = save_page(abc, university, page_body(name="Deakin Uni"))

        assert response.status_code == status.HTTP_200_OK
        assert stored_courses(abc, university) == [("Bachelor of Nursing", False)]

    def test_archiving_and_restoring_take_effect_on_save(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university, "Bachelor of Nursing")
        old = make_course(abc, university, "Diploma of Old Things", archived=True)

        response = save_page(
            abc,
            university,
            page_body(
                archived=True,
                courses=[course_row(nursing, archived=True), course_row(old, archived=False)],
            ),
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["archived"] is True
        assert stored_courses(abc, university) == [
            ("Bachelor of Nursing", True),
            ("Diploma of Old Things", False),
        ]

    def test_an_archived_course_may_have_no_intakes(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university)

        response = save_page(
            abc, university, page_body(courses=[course_row(nursing, archived=True, intakes=[])])
        )

        assert response.status_code == status.HTTP_200_OK
        assert stored_intakes(abc, nursing) == []

    def test_an_active_course_needs_an_intake(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university)

        response = save_page(abc, university, page_body(courses=[course_row(nursing, intakes=[])]))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "courses": {"0": {"intakes": ["Add at least one intake, or archive the course."]}}
        }
        assert stored_intakes(abc, nursing) == [FEB]

    def test_two_courses_with_one_name_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university)

        response = save_page(
            abc,
            university,
            page_body(courses=[course_row(nursing), course_row(None, name="BACHELOR of nursing")]),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "courses": {"1": {"name": ["This university already has a course with this name."]}}
        }

    def test_a_new_course_cannot_take_the_name_of_one_left_out(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        make_course(abc, university, "Bachelor of Nursing")

        response = save_page(
            abc, university, page_body(courses=[course_row(None, name="Bachelor of Nursing")])
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_courses(abc, university) == [("Bachelor of Nursing", False)]

    def test_two_courses_can_swap_names(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        first = make_course(abc, university, "Course A")
        second = make_course(abc, university, "Course B")

        response = save_page(
            abc,
            university,
            page_body(
                courses=[course_row(first, name="Course B"), course_row(second, name="Course A")]
            ),
        )

        assert response.status_code == status.HTTP_200_OK
        with tenant_context(abc.consultancy):
            assert Course.objects.get(pk=first.pk).name == "Course B"
            assert Course.objects.get(pk=second.pk).name == "Course A"

    @pytest.mark.parametrize(
        "rows",
        [
            pytest.param("other", id="another universitys course"),
            pytest.param("twice", id="the same course twice"),
            pytest.param("unknown", id="no such course"),
        ],
    )
    def test_a_course_id_must_be_one_of_this_universitys_once(
        self, abc: ConsultancyContext, rows: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university)
        elsewhere = make_course(abc, make_university(abc, "Monash University"), "Elsewhere")
        sent = {
            "other": [course_row(elsewhere)],
            "twice": [course_row(nursing), course_row(nursing, name="Copy")],
            "unknown": [{**course_row(nursing), "id": str(uuid.uuid4())}],
        }[rows]

        response = save_page(abc, university, page_body(courses=sent))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "courses": ["A course here isn't one of this university's. Refresh the page."]
        }
        with tenant_context(abc.consultancy):
            assert Course.objects.get(pk=elsewhere.pk).name == "Elsewhere"

    def test_required_documents_are_tick_boxes_of_types_not_everyone_needs(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university)
        everyone = make_type(abc, "Citizenship Card", everyone=True)

        response = save_page(
            abc,
            university,
            page_body(courses=[course_row(nursing, documents=[str(everyone.public_id)])]),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "courses": {
                "0": {"documents": ["Citizenship Card is already needed by every Student."]}
            }
        }

    def test_documents_taken_off_are_no_longer_asked(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university)
        reference = make_type(abc, "Work Reference")
        with tenant_context(abc.consultancy):
            DocumentRequirement.objects.create(document_type=reference, course=nursing)

        response = save_page(abc, university, page_body(courses=[course_row(nursing)]))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["courses"][0]["documents"] == []

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_can_save(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        university = make_university(abc)

        response = save_page(abc, university, page_body(name="Taken over"))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        expected = NOT_FOR_TEACHERS if role == Role.TEACHER else ONLY_ADMIN
        assert response.json() == expected
        with tenant_context(abc.consultancy):
            assert University.objects.get().name == "Deakin University"

    def test_an_unknown_university_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(university_url(999_999), page_body(), format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        ("changes", "field"),
        [
            ({"name": ""}, "name"),
            ({"name": "x" * 201}, "name"),
            ({"name": 5}, "name"),
            ({"name": "Deakin\u0000"}, "name"),
            ({"country": "mars"}, "country"),
            ({"archived": "yes"}, "archived"),
            ({"courses": "none"}, "courses"),
            ({"deleted_courses": ["1"]}, "deleted_courses"),
            ({"deleted_courses": "1"}, "deleted_courses"),
            ({"is_admin": True}, "detail"),
        ],
    )
    def test_bad_university_fields_are_refused(
        self, abc: ConsultancyContext, changes: dict[str, object], field: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = save_page(abc, university, {**page_body(), **changes})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        with tenant_context(abc.consultancy):
            assert University.objects.get().name == "Deakin University"

    @pytest.mark.parametrize(
        "missing", ["name", "country", "archived", "courses", "deleted_courses"]
    )
    def test_every_field_is_needed(self, abc: ConsultancyContext, missing: str) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        body = page_body()
        del body[missing]

        response = save_page(abc, university, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert missing in response.json()

    @pytest.mark.parametrize(
        ("changes", "field"),
        [
            ({"name": "x" * 201}, "name"),
            ({"name": None}, "name"),
            ({"level": "phd"}, "level"),
            ({"intakes": ["Feb 2027"]}, "intakes"),
            ({"intakes": ["2027-13"]}, "intakes"),
            ({"intakes": [202702]}, "intakes"),
            ({"intakes": "2027-02"}, "intakes"),
            ({"intakes": [f"{2040 + n // 12}-{n % 12 + 1:02d}" for n in range(25)]}, "intakes"),
            ({"archived": 1}, "archived"),
            ({"documents": ["1"]}, "documents"),
            ({"documents": [999_999]}, "documents"),
            ({"id": "1"}, "id"),
            ({"secret": "x"}, "detail"),
        ],
    )
    def test_bad_course_fields_are_refused(
        self, abc: ConsultancyContext, changes: dict[str, object], field: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = save_page(abc, university, page_body(courses=[course_row(None, **changes)]))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()["courses"]["0"]
        assert stored_courses(abc, university) == []

    def test_too_many_courses_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        rows = [course_row(None, name=f"Course {n}") for n in range(201)]

        response = save_page(abc, university, page_body(courses=rows))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "courses" in response.json()

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_names_are_stored_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = save_page(
            abc, university, page_body(name=text, courses=[course_row(None, name=text)])
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == text
        assert response.json()["courses"][0]["name"] == text

    def test_only_put_get_patch_and_delete_are_allowed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = abc.client.post(university_url(university.public_id), page_body(), format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_the_log_has_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        save_page(abc, university, page_body(name="Secret Name University"))

        json_logs.only(
            f"University edit saved: university_id={university.pk}, courses=0, deleted=0, "
            f"by_staff_id={admin.pk}"
        )
        assert "Secret Name" not in json_logs.text()


class TestRacesTheChecksMiss:
    """What the database refuses after the checks passed (another save got in first)."""

    def test_a_name_clash_saves_nothing(
        self, abc: ConsultancyContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university, "Bachelor of Nursing")
        monkeypatch.setattr(UniversityEditSerializer, "_name_taken", lambda *_: False)

        response = save_page(
            abc,
            university,
            page_body(
                name="Renamed",
                courses=[course_row(nursing), course_row(None, name="Bachelor of Nursing")],
            ),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": ["A name was taken meanwhile. Refresh the page and save again."]
        }
        with tenant_context(abc.consultancy):
            assert University.objects.get().name == "Deakin University"
        assert stored_courses(abc, university) == [("Bachelor of Nursing", False)]

    def test_a_course_an_application_started_using_is_not_deleted(
        self, abc: ConsultancyContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        old = make_course(abc, university, archived=True)
        make_application(abc, old)
        monkeypatch.setattr(UniversityEditSerializer, "_check_deleted", lambda *_: None)

        response = save_page(
            abc, university, page_body(name="Renamed", deleted=[str(old.public_id)])
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "deleted_courses": ["Used by 1 application, so it stays archived."]
        }
        with tenant_context(abc.consultancy):
            assert University.objects.get().name == "Deakin University"
            assert Course.objects.filter(pk=old.pk).exists()


class TestPastMonths:
    def test_a_new_intake_in_a_month_gone_by_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university, intakes=(LATER,))

        response = save_page(
            abc,
            university,
            page_body(courses=[course_row(nursing, intakes=[LAST_MONTH, LATER])]),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "courses": {
                "0": {
                    "intakes": [
                        f"{intake_label(LAST_MONTH)} has gone by. Pick this month or a later one."
                    ]
                }
            }
        }

    def test_this_month_can_be_added(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = save_page(
            abc, university, page_body(courses=[course_row(None, intakes=[THIS_MONTH])])
        )

        assert response.status_code == status.HTTP_200_OK

    def test_an_intake_the_course_already_has_is_kept_though_gone_by(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university, intakes=(LAST_MONTH, LATER))

        response = save_page(
            abc,
            university,
            page_body(courses=[course_row(nursing, intakes=[LAST_MONTH, LATER])]),
        )

        assert response.status_code == status.HTTP_200_OK
        assert stored_intakes(abc, nursing) == [LAST_MONTH, LATER]

    def test_adding_a_course_on_the_list_refuses_a_month_gone_by(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = abc.client.post(
            f"{university_url(university.public_id)}courses/",
            {"name": "Master of IT", "level": "master", "intakes": [EARLIER]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "intakes": [f"{intake_label(EARLIER)} has gone by. Pick this month or a later one."]
        }

    def test_changing_intakes_on_the_list_refuses_a_month_gone_by(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        nursing = make_course(abc, make_university(abc), intakes=(EARLIER, LATER))

        refused = abc.client.patch(
            course_url(nursing.public_id), {"intakes": [EARLIER, LAST_MONTH, LATER]}, format="json"
        )
        kept = abc.client.patch(
            course_url(nursing.public_id), {"intakes": [EARLIER]}, format="json"
        )

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert kept.status_code == status.HTTP_200_OK
        assert stored_intakes(abc, nursing) == [EARLIER]


class TestIntakesOnTheList:
    def test_an_admin_adds_an_intake_in_date_order(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        nursing = make_course(abc, make_university(abc), intakes=(NOV,))

        response = abc.client.patch(
            course_url(nursing.public_id), {"intakes": [NOV, FEB]}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["intakes"] == [FEB, NOV]

    def test_removing_an_intake_leaves_applications_made_with_it(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        nursing = make_course(abc, make_university(abc), intakes=(FEB, JUL))
        application = make_application(abc, nursing, FEB)

        response = abc.client.patch(
            course_url(nursing.public_id), {"intakes": [JUL]}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert stored_intakes(abc, nursing) == [JUL]
        with tenant_context(abc.consultancy):
            assert Application.objects.get(pk=application.pk).intake == FEB

    def test_the_last_intake_of_an_active_course_cannot_be_removed(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        nursing = make_course(abc, make_university(abc), intakes=(FEB,))

        response = abc.client.patch(course_url(nursing.public_id), {"intakes": []}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"intakes": ["Add at least one intake."]}

    def test_the_answer_says_how_many_applications_use_the_course(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        nursing = make_course(abc, make_university(abc), intakes=(FEB, JUL))
        make_application(abc, nursing, FEB)
        make_application(abc, nursing, JUL)

        response = abc.client.patch(
            course_url(nursing.public_id), {"intakes": [FEB, JUL]}, format="json"
        )

        assert response.json()["applications"] == 2


class TestAddingACourseOnTheList:
    def test_a_course_is_added_with_its_required_documents_at_once(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        reference = make_type(abc, "Work Reference")

        response = abc.client.post(
            f"{university_url(university.public_id)}courses/",
            {
                "name": "Master of IT",
                "level": "master",
                "intakes": [FEB],
                "documents": [str(reference.public_id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["documents"] == [
            {"id": str(reference.public_id), "name": "Work Reference"}
        ]

    def test_a_refused_document_adds_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        everyone = make_type(abc, "Citizenship Card", everyone=True)

        response = abc.client.post(
            f"{university_url(university.public_id)}courses/",
            {
                "name": "Master of IT",
                "level": "master",
                "intakes": [FEB],
                "documents": [str(everyone.public_id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "documents": ["Citizenship Card is already needed by every Student."]
        }
        assert stored_courses(abc, university) == []


class TestDeletingCourses:
    def test_an_archived_course_no_application_uses_is_deleted_on_save(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        keep = make_course(abc, university, "Bachelor of Nursing")
        old = make_course(abc, university, "Diploma of Old Things", archived=True)
        reference = make_type(abc, "Work Reference")
        with tenant_context(abc.consultancy):
            DocumentRequirement.objects.create(document_type=reference, course=old)

        response = save_page(
            abc, university, page_body(courses=[course_row(keep)], deleted=[str(old.public_id)])
        )

        assert response.status_code == status.HTTP_200_OK
        assert [c["name"] for c in response.json()["courses"]] == ["Bachelor of Nursing"]
        with tenant_context(abc.consultancy):
            assert not Course.objects.filter(pk=old.pk).exists()
            assert not Intake.objects.filter(course_id=old.pk).exists()
            assert not DocumentRequirement.objects.filter(course_id=old.pk).exists()

    def test_a_course_not_yet_saved_as_archived_cannot_be_deleted(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        nursing = make_course(abc, university)

        response = save_page(abc, university, page_body(deleted=[str(nursing.public_id)]))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "deleted_courses": ["Archive the course and save before deleting it."]
        }
        assert stored_courses(abc, university) == [("Bachelor of Nursing", False)]

    def test_a_course_an_application_uses_stays_archived(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        old = make_course(abc, university, archived=True)
        make_application(abc, old)

        response = save_page(abc, university, page_body(deleted=[str(old.public_id)]))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "deleted_courses": ["Used by 1 application, so it stays archived."]
        }
        assert stored_courses(abc, university) == [("Bachelor of Nursing", True)]

    @pytest.mark.parametrize("which", ["kept_too", "other_university", "twice"])
    def test_a_deleted_course_must_be_one_of_this_universitys_once(
        self, abc: ConsultancyContext, which: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)
        old = make_course(abc, university, archived=True)
        elsewhere = make_course(abc, make_university(abc, "Monash"), "Elsewhere", archived=True)
        courses, deleted = {
            "kept_too": ([course_row(old)], [str(old.public_id)]),
            "other_university": ([], [str(elsewhere.public_id)]),
            "twice": ([], [str(old.public_id), str(old.public_id)]),
        }[which]

        response = save_page(abc, university, page_body(courses=courses, deleted=deleted))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "deleted_courses" in response.json() or "courses" in response.json()
        with tenant_context(abc.consultancy):
            assert Course.objects.count() == 2


class TestDeletingAUniversity:
    def test_an_archived_university_no_application_uses_is_deleted_with_its_courses(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc, archived=True)
        nursing = make_course(abc, university, archived=False)
        make_course(abc, university, "Master of Nursing", archived=True)
        reference = make_type(abc, "Work Reference")
        with tenant_context(abc.consultancy):
            DocumentRequirement.objects.create(document_type=reference, course=nursing)

        response = abc.client.delete(university_url(university.public_id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        with tenant_context(abc.consultancy):
            assert not University.objects.exists()
            assert not Course.objects.exists()
            assert not Intake.objects.exists()
            assert not DocumentRequirement.objects.filter(course__isnull=False).exists()
            assert DocumentType.objects.filter(pk=reference.pk).exists()

    def test_an_active_university_cannot_be_deleted(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc)

        response = abc.client.delete(university_url(university.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Archive the university and save before deleting it."}
        with tenant_context(abc.consultancy):
            assert University.objects.exists()

    def test_a_university_whose_course_an_application_uses_stays_archived(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        university = make_university(abc, archived=True)
        first = make_course(abc, university, "Bachelor of Nursing", archived=True)
        second = make_course(abc, university, "Master of Nursing", archived=True)
        make_application(abc, first)
        make_application(abc, second)

        response = abc.client.delete(university_url(university.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Used by 2 applications, so it stays archived."}
        with tenant_context(abc.consultancy):
            assert Course.objects.count() == 2

    def test_a_university_with_commission_stays_archived(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        university = make_university(abc, archived=True)
        student = make_student(abc)
        with tenant_context(abc.consultancy):
            Commission.objects.create(
                student=student,
                university=university,
                intake=FEB,
                amount=1000,
                recorded_by=admin,
                recorded_by_name=admin.name,
            )

        response = abc.client.delete(university_url(university.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Used by 1 commission, so it stays archived."}

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_can_delete(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        university = make_university(abc, archived=True)

        response = abc.client.delete(university_url(university.public_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        with tenant_context(abc.consultancy):
            assert University.objects.exists()

    def test_an_unknown_university_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.delete(university_url(999_999))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_log_has_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        university = make_university(abc, "Secret Name University", archived=True)

        abc.client.delete(university_url(university.public_id))

        json_logs.only(
            f"University deleted: university_id={university.pk}, courses=0, by_staff_id={admin.pk}"
        )
        assert "Secret Name" not in json_logs.text()


class TestSearch:
    def test_a_course_name_finds_its_university_once(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        deakin = make_university(abc, "Deakin University")
        make_course(abc, deakin, "Master of Nursing")
        make_course(abc, deakin, "Bachelor of Nursing")
        make_course(abc, make_university(abc, "Monash University"), "Master of IT")

        response = abc.client.get(UNIVERSITIES_URL, {"q": "nursing"})

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["count"] == 1
        assert [u["name"] for u in body["results"]] == ["Deakin University"]
        # The card still shows every Course of the University.
        assert len(body["results"][0]["courses"]) == 2


class TestUpcomingIntakes:
    def test_the_catalogues_intakes_from_this_month_on_each_once(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        deakin = make_university(abc)
        make_course(abc, deakin, "A", intakes=(LAST_MONTH, THIS_MONTH, LATER))
        make_course(abc, deakin, "B", intakes=(LATER, NOV))
        make_course(abc, deakin, "C archived", archived=True, intakes=(JUL,))
        archived_university = make_university(abc, "Old University", archived=True)
        make_course(abc, archived_university, "D", intakes=(FEB,))

        response = abc.client.get(INTAKES_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"intakes": [THIS_MONTH, NOV, LATER]}

    @pytest.mark.parametrize("role", READERS)
    def test_roles_that_work_with_students_can_read_them(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)

        assert abc.client.get(INTAKES_URL).status_code == status.HTTP_200_OK

    def test_a_teacher_cannot(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)

        assert abc.client.get(INTAKES_URL).status_code == status.HTTP_403_FORBIDDEN

    def test_only_get_is_allowed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(INTAKES_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_unknown_query_fields_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(INTAKES_URL, {"x": "1"}).status_code == status.HTTP_400_BAD_REQUEST


class TestPreferredIntake:
    def edit(
        self, office: ConsultancyContext, student: Student, value: object
    ) -> _MonkeyPatchedResponse:
        return office.client.patch(
            f"/api/students/{student.public_id}/", {"preferred_intake": value}, format="json"
        )

    def test_one_of_the_catalogues_upcoming_intakes_can_be_chosen(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        make_course(abc, make_university(abc), intakes=(JUL,))
        student = make_student(abc)

        response = self.edit(abc, student, JUL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["preferred_intake"] == JUL

    def test_an_intake_the_catalogue_does_not_offer_is_refused(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        make_course(abc, make_university(abc), intakes=(JUL,))
        student = make_student(abc)

        response = self.edit(abc, student, NOV)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "preferred_intake": ["Choose one of the Catalogue's intakes, or Not decided."]
        }

    def test_an_intake_gone_by_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_course(abc, make_university(abc), intakes=(LAST_MONTH, LATER))
        student = make_student(abc)

        response = self.edit(abc, student, LAST_MONTH)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_intake_already_stored_can_be_kept(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, preferred_intake="2024-02")

        response = self.edit(abc, student, "2024-02")

        assert response.status_code == status.HTTP_200_OK

    def test_not_decided_is_always_fine(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, preferred_intake=FEB)

        response = self.edit(abc, student, "")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["preferred_intake"] == ""
