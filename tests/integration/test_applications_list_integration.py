"""The office-wide Applications list and the day each was first Submitted (spec 57a, 57b)."""

from __future__ import annotations

import itertools
from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application
from applications.statuses import ApplicationStatus
from catalogue.models import Course, CourseLevel, Intake, University
from staff.models import Role, Staff
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    NOT_FOR_TEACHERS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

LIST_URL = "/api/applications/"
CHOICES_URL = "/api/applications/filter-choices/"
KATHMANDU = ZoneInfo("Asia/Kathmandu")
_PHONES = itertools.count(10_000_000)


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def course_at(
    office: ConsultancyContext,
    university: str,
    country: str = "australia",
    intakes: tuple[str, ...] = ("2027-02", "2027-07", "2028-02"),
) -> Course:
    """A Course at `university` (made if new) that runs `intakes`."""
    with tenant_context(office.consultancy):
        uni, _ = University.objects.get_or_create(name=university, defaults={"country": country})
        course = Course.objects.create(
            university=uni, name=f"Master of IT {Course.objects.count()}", level=CourseLevel.MASTER
        )
        Intake.objects.bulk_create(Intake(course=course, month=month) for month in intakes)
        return course


def applied(
    office: ConsultancyContext,
    student: Student,
    course: Course,
    intake: str = "2027-02",
    **fields: object,
) -> Application:
    """Store an Application directly; `fields` set its `status` or `applied_on`."""
    with tenant_context(office.consultancy):
        return Application.objects.create(student=student, course=course, intake=intake, **fields)


def student_named(
    office: ConsultancyContext, name: str, counsellor: Staff | None = None, **fields: object
) -> Student:
    """A Student at Applying with this name; `fields` override the other details."""
    phone = fields.pop("phone", f"98{next(_PHONES):08d}")
    return make_student(office, counsellor, Stage.APPLYING, name=name, phone=phone, **fields)


def listed(
    office: ConsultancyContext, query: dict[str, str | list[str]] | None = None
) -> _MonkeyPatchedResponse:
    """GET the Applications list."""
    return office.client.get(LIST_URL, query or {})


def rows_of(response: _MonkeyPatchedResponse) -> list[tuple[str, str]]:
    """(Student, University) of each listed row, in order."""
    assert response.status_code == status.HTTP_200_OK, response.json()
    return [
        (row["student"]["name"], row["university"]["name"]) for row in response.json()["results"]
    ]


def set_added_at(office: ConsultancyContext, application: Application, at: datetime) -> None:
    """Pretend the Application was added at `at`."""
    with tenant_context(office.consultancy):
        Application.objects.filter(pk=application.pk).update(created_at=at)


class TestAppliedOn:
    def change(
        self, office: ConsultancyContext, student: Student, application: Application, to: str
    ) -> None:
        response = office.client.patch(
            f"/api/students/{student.public_id}/applications/{application.public_id}/",
            {"status": to},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()

    def stored_on(self, office: ConsultancyContext, application: Application) -> date | None:
        with tenant_context(office.consultancy):
            return Application.objects.get(pk=application.pk).applied_on

    def test_adding_one_records_today_in_kathmandu(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = student_named(abc, "Sita Karki", me)
        course = course_at(abc, "Deakin University")

        response = abc.client.post(
            f"/api/students/{student.public_id}/applications/",
            {"course": str(course.public_id), "intake": "2027-02"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        with tenant_context(abc.consultancy):
            assert Application.objects.get().applied_on == timezone.localdate()

    def test_one_without_a_day_gets_today_when_set_back_to_applied(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = student_named(abc, "Sita Karki", me)
        application = applied(
            abc, student, course_at(abc, "Deakin University"), status=ApplicationStatus.REJECTED
        )

        self.change(abc, student, application, "applied")

        assert self.stored_on(abc, application) == timezone.localdate()

    def test_it_never_changes_after_the_first_time(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = student_named(abc, "Sita Karki", me)
        first = date(2026, 9, 1)
        application = applied(abc, student, course_at(abc, "Deakin University"), applied_on=first)

        self.change(abc, student, application, "rejected")
        self.change(abc, student, application, "applied")

        assert self.stored_on(abc, application) == first


class TestRows:
    def test_a_row_carries_everything_the_list_shows(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        bikash = named_staff(abc, "Bikash Thapa")
        student = student_named(
            abc, "Sita Karki", bikash, email="sita@gmail.test", phone="9841234567"
        )
        course = course_at(abc, "Deakin University", "australia")
        application = applied(
            abc,
            student,
            course,
            "2027-07",
            status=ApplicationStatus.OFFER,
            offer_kind="conditional",
            applied_on=date(2026, 10, 2),
        )
        set_added_at(abc, application, datetime(2026, 9, 30, 20, 0, tzinfo=KATHMANDU))

        [row] = listed(abc).json()["results"]

        assert row == {
            "id": str(application.public_id),
            "student": {
                "id": str(student.public_id),
                "name": "Sita Karki",
                "phone": "9841234567",
                "email": "sita@gmail.test",
            },
            "university": {
                "id": str(course.university.public_id),
                "name": "Deakin University",
                "country": "australia",
            },
            "course": {"id": str(course.public_id), "name": course.name, "level": "master"},
            "intake": "2027-07",
            "status": "offer",
            "offer_kind": "conditional",
            "assigned_counsellor": {"id": str(bikash.public_id), "name": "Bikash Thapa"},
            "applied_on": "2026-10-02",
            "added_on": "2026-09-30",
        }

    def test_an_older_application_has_no_applied_day_and_an_unassigned_student_no_counsellor(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        applied(abc, student_named(abc, "Sita Karki"), course_at(abc, "Deakin University"))

        [row] = listed(abc).json()["results"]

        assert row["applied_on"] is None
        assert row["assigned_counsellor"] is None

    def test_added_on_is_the_day_in_kathmandu(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        application = applied(abc, student_named(abc, "Sita"), course_at(abc, "Deakin"))
        # 20:00 UTC on 1 Oct is 01:45 on 2 Oct in Kathmandu.
        set_added_at(abc, application, datetime(2026, 10, 1, 20, 0, tzinfo=ZoneInfo("UTC")))

        [row] = listed(abc).json()["results"]

        assert row["added_on"] == "2026-10-02"

    def test_it_is_paged_twenty_five_at_a_time(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = student_named(abc, "Sita")
        course = course_at(abc, "Deakin", intakes=tuple(f"2027-{m:02d}" for m in range(1, 13)))
        other = course_at(abc, "Monash", intakes=tuple(f"2028-{m:02d}" for m in range(1, 13)))
        for month in range(1, 13):
            applied(abc, student, course, f"2027-{month:02d}")
            applied(abc, student, other, f"2028-{month:02d}")
        applied(abc, student_named(abc, "Gita"), course, "2027-01")
        applied(abc, student_named(abc, "Rita"), course, "2027-01")

        first = listed(abc).json()
        second = listed(abc, {"page": "2"}).json()

        assert (first["count"], first["page"], first["page_size"]) == (26, 1, 25)
        assert len(first["results"]) == 25
        assert len(second["results"]) == 1
        assert listed(abc, {"page": "3"}).status_code == status.HTTP_404_NOT_FOUND

    def test_the_query_count_stays_flat_for_one_row_or_fifty(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        course = course_at(abc, "Deakin")
        applied(abc, student_named(abc, "Student 0", named_staff(abc, "Bikash Thapa")), course)
        with CaptureQueriesContext(connection) as one:
            assert len(listed(abc).json()["results"]) == 1
        for number in range(1, 50):
            counsellor = named_staff(abc, f"Counsellor{number} Rai")
            other = course_at(abc, f"University {number}", "canada")
            applied(abc, student_named(abc, f"Student {number}", counsellor), other)

        with CaptureQueriesContext(connection) as fifty:
            assert len(listed(abc, {"page": "1"}).json()["results"]) == 25
            assert len(listed(abc, {"page": "2"}).json()["results"]) == 25

        assert len(fifty.captured_queries) == 2 * len(one.captured_queries)


class TestOrder:
    def test_newest_submitted_first_then_never_submitted_by_newest_added(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        course = course_at(abc, "Deakin")
        early = applied(abc, student_named(abc, "Early"), course, applied_on=date(2026, 9, 1))
        draft_old = applied(abc, student_named(abc, "Old draft"), course)
        late = applied(abc, student_named(abc, "Late"), course, applied_on=date(2026, 10, 1))
        draft_new = applied(abc, student_named(abc, "New draft"), course)
        for application, day in ((early, 1), (draft_old, 2), (late, 3), (draft_new, 4)):
            set_added_at(abc, application, datetime(2026, 8, day, 10, tzinfo=KATHMANDU))

        assert [name for name, _ in rows_of(listed(abc))] == [
            "Late",
            "Early",
            "New draft",
            "Old draft",
        ]

    @pytest.mark.parametrize(
        ("sort", "expected"),
        [
            ("applied_on", ["Early", "Late", "Undated"]),
            ("-applied_on", ["Late", "Early", "Undated"]),
            ("added_on", ["Late", "Undated", "Early"]),
            ("-added_on", ["Early", "Undated", "Late"]),
            ("intake", ["Undated", "Late", "Early"]),
            ("-intake", ["Early", "Late", "Undated"]),
        ],
    )
    def test_sorts_by_submitted_added_or_intake(
        self, abc: ConsultancyContext, sort: str, expected: list[str]
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        course = course_at(abc, "Deakin")
        late = applied(
            abc, student_named(abc, "Late"), course, "2027-07", applied_on=date(2026, 10, 1)
        )
        undated = applied(abc, student_named(abc, "Undated"), course, "2027-02")
        early = applied(
            abc, student_named(abc, "Early"), course, "2028-02", applied_on=date(2026, 9, 1)
        )
        for application, day in ((late, 1), (undated, 2), (early, 3)):
            set_added_at(abc, application, datetime(2026, 8, day, 10, tzinfo=KATHMANDU))

        assert [name for name, _ in rows_of(listed(abc, {"sort": sort}))] == expected


class TestSearch:
    @pytest.fixture
    def office(self, abc: ConsultancyContext) -> ConsultancyContext:
        abc.log_in_as(Role.ADMIN)
        sita = student_named(abc, "Sita Karki", email="sita@gmail.test", phone="9841234567")
        gita = student_named(abc, "Gita Rai", email="gita@yahoo.test", phone="9801112222")
        applied(abc, sita, course_at(abc, "Deakin University"))
        applied(abc, gita, course_at(abc, "University of Toronto", "canada"))
        return abc

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("sita", [("Sita Karki", "Deakin University")]),
            ("yahoo", [("Gita Rai", "University of Toronto")]),
            ("9841-23", [("Sita Karki", "Deakin University")]),
            ("toronto", [("Gita Rai", "University of Toronto")]),
            ("nobody", []),
        ],
    )
    def test_finds_by_student_name_phone_email_or_university(
        self, office: ConsultancyContext, query: str, expected: list[tuple[str, str]]
    ) -> None:
        assert rows_of(listed(office, {"q": query})) == expected

    @pytest.mark.parametrize("query", [*INJECTION_STRINGS, "%", "_", "\\"])
    def test_injection_shaped_searches_find_nothing_and_break_nothing(
        self, office: ConsultancyContext, query: str
    ) -> None:
        assert rows_of(listed(office, {"q": query})) == []

    def test_an_over_long_search_is_refused(self, office: ConsultancyContext) -> None:
        response = listed(office, {"q": "a" * 101})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "q" in response.json()


class TestFilters:
    @pytest.fixture
    def office(self, abc: ConsultancyContext) -> ConsultancyContext:
        """Three Applications: Sita's two (Bikash's Student) and Gita's (no counsellor)."""
        bikash = named_staff(abc, "Bikash Thapa")
        sita = student_named(abc, "Sita Karki", bikash)
        gita = student_named(abc, "Gita Rai")
        deakin = course_at(abc, "Deakin University", "australia")
        toronto = course_at(abc, "University of Toronto", "canada")
        applied(
            abc,
            sita,
            deakin,
            "2027-02",
            status=ApplicationStatus.OFFER,
            applied_on=date(2026, 10, 3),
        )
        applied(
            abc,
            sita,
            toronto,
            "2027-07",
            status=ApplicationStatus.REJECTED,
            applied_on=date(2026, 10, 2),
        )
        applied(abc, gita, deakin, "2028-02")
        log_in(abc, abc.add_staff(Role.ADMIN))
        return abc

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ({"status": "offer"}, ["Sita Karki/Deakin University"]),
            (
                {"status": ["offer", "applied"]},
                ["Sita Karki/Deakin University", "Gita Rai/Deakin University"],
            ),
            ({"intake": "2027-07"}, ["Sita Karki/University of Toronto"]),
            (
                {"year": "2027"},
                ["Sita Karki/Deakin University", "Sita Karki/University of Toronto"],
            ),
            ({"year": "2028"}, ["Gita Rai/Deakin University"]),
            ({"country": "canada"}, ["Sita Karki/University of Toronto"]),
            ({"counsellor": "none"}, ["Gita Rai/Deakin University"]),
            ({"country": "australia", "year": "2027"}, ["Sita Karki/Deakin University"]),
            ({"status": "rejected", "country": "australia"}, []),
        ],
    )
    def test_filters_narrow_the_list(
        self,
        office: ConsultancyContext,
        query: dict[str, str | list[str]],
        expected: list[str],
    ) -> None:
        assert [f"{s}/{u}" for s, u in rows_of(listed(office, query))] == expected

    def test_filter_by_a_counsellor(self, office: ConsultancyContext) -> None:
        with tenant_context(office.consultancy):
            bikash = Staff.objects.get(name="Bikash Thapa")

        rows = rows_of(listed(office, {"counsellor": str(bikash.public_id)}))

        assert rows == [
            ("Sita Karki", "Deakin University"),
            ("Sita Karki", "University of Toronto"),
        ]

    def test_my_students_are_those_assigned_to_me(self, office: ConsultancyContext) -> None:
        with tenant_context(office.consultancy):
            bikash = Staff.objects.get(name="Bikash Thapa")
        log_in(office, bikash)

        rows = rows_of(listed(office, {"counsellor": "me"}))

        assert [s for s, _ in rows] == ["Sita Karki", "Sita Karki"]

    @pytest.mark.parametrize(
        "query",
        [
            {"status": "approved"},
            {"status": "Applied"},
            {"status": "submitted"},
            {"status": "withdrawn"},
            {"status": ["applied", "nope"]},
            {"status": ""},
            {"year": "27"},
            {"year": "abcd"},
            {"year": "1999"},
            {"year": "2027.0"},
            {"intake": "2027-13"},
            {"intake": "Feb 2027"},
            {"country": "nepal"},
            {"counsellor": "1; DROP TABLE"},
            {"counsellor": "everyone"},
            {"sort": "name"},
            {"sort": "-created_at"},
            {"page": "0"},
            {"page": "two"},
            {"colour": "red"},
            *({"status": text} for text in INJECTION_STRINGS),
            *({"country": text} for text in INJECTION_STRINGS),
            *({"intake": text} for text in INJECTION_STRINGS),
        ],
    )
    def test_refused_filter_values(
        self, office: ConsultancyContext, query: dict[str, str | list[str]]
    ) -> None:
        response = listed(office, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "DROP" not in str(response.json())
        assert "<script>" not in str(response.json())


class TestWhoMaySee:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_everyone_but_teachers_sees_every_application(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        someone = named_staff(abc, "Bikash Thapa")
        applied(abc, student_named(abc, "Sita Karki", someone), course_at(abc, "Deakin"))
        applied(abc, student_named(abc, "Gita Rai"), course_at(abc, "Monash"))
        abc.log_in_as(role)

        assert len(rows_of(listed(abc))) == 2

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        applied(abc, student_named(abc, "Sita Karki"), course_at(abc, "Deakin"))
        abc.log_in_as(Role.TEACHER)

        responses = [listed(abc), abc.client.get(CHOICES_URL)]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 2
        assert [r.json() for r in responses] == [NOT_FOR_TEACHERS] * 2

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        responses = [abc.new_client().get(LIST_URL), abc.new_client().get(CHOICES_URL)]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 2

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_reading_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        responses = [
            getattr(abc.client, method)(url, {}, format="json") for url in (LIST_URL, CHOICES_URL)
        ]

        assert [r.status_code for r in responses] == [status.HTTP_405_METHOD_NOT_ALLOWED] * 2


class TestFilterChoices:
    def test_lists_the_intakes_years_and_countries_in_use(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        student = student_named(abc, "Sita")
        applied(abc, student, course_at(abc, "Toronto", "canada"), "2028-02")
        applied(abc, student, course_at(abc, "Deakin", "australia"), "2027-07")
        applied(abc, student, course_at(abc, "Monash", "australia"), "2027-02")
        applied(abc, student_named(abc, "Gita"), course_at(abc, "Otago", "new_zealand"), "2027-02")

        response = abc.client.get(CHOICES_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "intakes": ["2027-02", "2027-07", "2028-02"],
            "years": ["2027", "2028"],
            "countries": ["australia", "canada", "new_zealand"],
        }

    def test_with_no_applications_there_are_none(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.get(CHOICES_URL)

        assert response.json() == {"intakes": [], "years": [], "countries": []}

    def test_unknown_query_fields_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(CHOICES_URL, {"x": "1"}).status_code == status.HTTP_400_BAD_REQUEST
