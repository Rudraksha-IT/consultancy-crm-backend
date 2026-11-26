"""The office-wide Visas list (spec 62b, 64; issues 48 and 75)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application, Visa
from applications.statuses import ApplicationStatus, VisaStatus
from documents.models import Document, DocumentRequirement, DocumentType
from documents.verification import Verification
from staff.models import Role, Staff
from students.models import Student
from tests.conftest import ConsultancyContext, ConsultancyFactory
from tests.integration.test_applications_list_integration import applied, course_at, student_named
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    NOT_FOR_TEACHERS,
    log_in,
    named_staff,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rest_framework.response import _MonkeyPatchedResponse

LIST_URL = "/api/visas/"
CHOICES_URL = "/api/visas/filter-choices/"
KATHMANDU = ZoneInfo("Asia/Kathmandu")
# How many Document Types every Student needs in a new office.
STARTING_TYPES = 8


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def visa_for(
    office: ConsultancyContext,
    student: Student,
    university: str = "Deakin University",
    country: str = "australia",
    intake: str = "2027-02",
    **fields: object,
) -> Visa:
    """A Visa for the Student, from a new Application at CoE Received; `fields` set the Visa's."""
    application = applied(
        office,
        student,
        course_at(office, university, country, intakes=(intake,)),
        intake,
        status=ApplicationStatus.OFFER,
    )
    with tenant_context(office.consultancy):
        return Visa.objects.create(student=student, application=application, **fields)


def in_kathmandu(days: int, hour: int = 10, minute: int = 0) -> datetime:
    """`hour:minute` in Kathmandu, `days` from today."""
    day = timezone.localdate() + timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KATHMANDU)


def local_text(at: datetime) -> str:
    """How the API writes a moment: Kathmandu's "YYYY-MM-DDTHH:MM"."""
    return at.astimezone(KATHMANDU).strftime("%Y-%m-%dT%H:%M")


def listed(
    office: ConsultancyContext, query: Mapping[str, str | list[str]] | None = None
) -> _MonkeyPatchedResponse:
    """GET the Visas list."""
    return office.client.get(LIST_URL, query or {})


def names_in(response: _MonkeyPatchedResponse) -> list[str]:
    """The Student of each listed row, in order."""
    assert response.status_code == status.HTTP_200_OK, response.json()
    return [row["student"]["name"] for row in response.json()["results"]]


class TestRows:
    def test_a_row_carries_everything_the_list_shows(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        bikash = named_staff(abc, "Bikash Thapa")
        student = student_named(
            abc, "Sita Karki", bikash, email="sita@gmail.test", phone="9841234567"
        )
        when = in_kathmandu(5, 14, 0)
        visa = visa_for(
            abc,
            student,
            "University of Toronto",
            "canada",
            "2027-09",
            biometrics_at=in_kathmandu(-2),
            interview_at=when,
        )
        with tenant_context(abc.consultancy):
            application = Application.objects.get(pk=visa.application_id)
            course = application.course

        [row] = listed(abc).json()["results"]

        assert row == {
            "id": str(visa.public_id),
            "student": {
                "id": str(student.public_id),
                "name": "Sita Karki",
                "phone": "9841234567",
                "email": "sita@gmail.test",
            },
            "university": {
                "id": str(course.university.public_id),
                "name": "University of Toronto",
                "country": "canada",
            },
            "course": {"id": str(course.public_id), "name": course.name, "level": "master"},
            "intake": "2027-09",
            "status": "applied",
            "next_appointment": {"kind": "interview", "at": local_text(when)},
            "started_on": timezone.localdate().isoformat(),
            # A new office starts with the Document Types every Student needs.
            "documents": {"done": 0, "total": STARTING_TYPES, "rejected": 0, "waiting": 0},
            "assigned_counsellor": {"id": str(bikash.public_id), "name": "Bikash Thapa"},
        }

    def test_no_appointment_and_no_counsellor_are_empty(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        visa_for(abc, student_named(abc, "Sita Karki"))

        [row] = listed(abc).json()["results"]

        assert (row["next_appointment"], row["assigned_counsellor"]) == (None, None)

    def test_with_every_appointment_past_the_latest_is_shown(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        visa_for(
            abc,
            student_named(abc, "Sita Karki"),
            biometrics_at=in_kathmandu(-9),
            medical_at=in_kathmandu(-3, 8),
        )

        [row] = listed(abc).json()["results"]

        assert row["next_appointment"] == {"kind": "medical", "at": local_text(in_kathmandu(-3, 8))}

    def test_the_document_count_comes_from_the_checklist(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = student_named(abc, "Sita Karki")
        visa = visa_for(abc, student, "Federation University")
        with tenant_context(abc.consultancy):
            DocumentType.objects.all().delete()
            passport = DocumentType.objects.create(name="Passport", needed_by_everyone=True)
            transcript = DocumentType.objects.create(name="Transcript", needed_by_everyone=True)
            DocumentType.objects.create(name="Bank statement", needed_by_everyone=True)
            statement = DocumentType.objects.create(name="Genuine Student statement")
            DocumentRequirement.objects.create(
                document_type=statement, course=visa.application.course
            )
            for kind, verification in [
                (passport, Verification.VERIFIED),
                (transcript, Verification.REJECTED),
                (statement, Verification.PENDING),
            ]:
                Document.objects.create(
                    student=student,
                    document_type=kind,
                    verification=verification,
                )

        [row] = listed(abc).json()["results"]

        assert row["documents"] == {"done": 2, "total": 4, "rejected": 1, "waiting": 1}

    def test_it_is_paged_twenty_five_at_a_time(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for number in range(26):
            visa_for(abc, student_named(abc, f"Student {number}"), f"University {number}")

        first = listed(abc).json()
        second = listed(abc, {"page": "2"}).json()

        assert (first["count"], first["page"], first["page_size"]) == (26, 1, 25)
        assert (len(first["results"]), len(second["results"])) == (25, 1)
        assert listed(abc, {"page": "3"}).status_code == status.HTTP_404_NOT_FOUND

    def test_the_query_count_stays_flat_for_one_row_or_fifty(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        visa_for(abc, student_named(abc, "Student 0", named_staff(abc, "Bikash Thapa")))
        with CaptureQueriesContext(connection) as one:
            assert len(listed(abc).json()["results"]) == 1
        for number in range(1, 50):
            counsellor = named_staff(abc, f"Counsellor{number} Rai")
            visa_for(abc, student_named(abc, f"Student {number}", counsellor), f"Uni {number}")

        with CaptureQueriesContext(connection) as fifty:
            assert len(listed(abc, {"page": "1"}).json()["results"]) == 25
            assert len(listed(abc, {"page": "2"}).json()["results"]) == 25

        assert len(fifty.captured_queries) == 2 * len(one.captured_queries)


class TestOrder:
    @pytest.fixture
    def office(self, abc: ConsultancyContext) -> ConsultancyContext:
        abc.log_in_as(Role.ADMIN)
        visa_for(abc, student_named(abc, "Past"), "U1", medical_at=in_kathmandu(-3))
        visa_for(
            abc,
            student_named(abc, "Later"),
            "U2",
            biometrics_at=in_kathmandu(-1),
            interview_at=in_kathmandu(20),
        )
        visa_for(abc, student_named(abc, "No date old"), "U3")
        visa_for(abc, student_named(abc, "Soon"), "U4", biometrics_at=in_kathmandu(1))
        visa_for(abc, student_named(abc, "Long past"), "U5", interview_at=in_kathmandu(-30))
        visa_for(abc, student_named(abc, "No date new"), "U6")
        return abc

    def test_upcoming_appointments_first_then_no_date_then_past(
        self, office: ConsultancyContext
    ) -> None:
        assert names_in(listed(office)) == [
            "Soon",
            "Later",
            "No date new",
            "No date old",
            "Past",
            "Long past",
        ]

    @pytest.mark.parametrize(
        ("sort", "expected"),
        [
            ("appointment", ["Long past", "Past", "Soon", "Later", "No date new", "No date old"]),
            ("-appointment", ["Later", "Soon", "Past", "Long past", "No date new", "No date old"]),
        ],
    )
    def test_sorts_by_next_appointment(
        self, office: ConsultancyContext, sort: str, expected: list[str]
    ) -> None:
        assert names_in(listed(office, {"sort": sort})) == expected


class TestFilters:
    @pytest.fixture
    def office(self, abc: ConsultancyContext) -> ConsultancyContext:
        """Sita's two Visas (Bikash's Student) and Gita's (no counsellor)."""
        bikash = named_staff(abc, "Bikash Thapa")
        sita = student_named(abc, "Sita Karki", bikash)
        gita = student_named(abc, "Gita Rai")
        visa_for(
            abc,
            sita,
            "Deakin University",
            "australia",
            "2027-02",
            status=VisaStatus.APPLIED,
            medical_at=in_kathmandu(3, 9),
        )
        visa_for(
            abc,
            sita,
            "University of Toronto",
            "canada",
            "2027-09",
            status=VisaStatus.REFUSED,
            interview_at=in_kathmandu(-10),
        )
        visa_for(abc, gita, "Monash University", "australia", "2027-09")
        log_in(abc, abc.add_staff(Role.ADMIN))
        return abc

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            (
                {"status": "applied"},
                ["Sita Karki/Deakin University", "Gita Rai/Monash University"],
            ),
            (
                {"status": ["applied", "refused"]},
                [
                    "Sita Karki/Deakin University",
                    "Gita Rai/Monash University",
                    "Sita Karki/University of Toronto",
                ],
            ),
            ({"country": "canada"}, ["Sita Karki/University of Toronto"]),
            (
                {"intake": "2027-09"},
                ["Gita Rai/Monash University", "Sita Karki/University of Toronto"],
            ),
            ({"counsellor": "none"}, ["Gita Rai/Monash University"]),
            ({"has_appointment": "false"}, ["Gita Rai/Monash University"]),
            (
                {"has_appointment": "true"},
                ["Sita Karki/Deakin University", "Sita Karki/University of Toronto"],
            ),
            ({"status": "granted"}, []),
        ],
    )
    def test_filters_narrow_the_list(
        self, office: ConsultancyContext, query: dict[str, str | list[str]], expected: list[str]
    ) -> None:
        response = listed(office, query)

        assert response.status_code == status.HTTP_200_OK, response.json()
        rows = [
            f"{r['student']['name']}/{r['university']['name']}" for r in response.json()["results"]
        ]
        assert rows == expected

    def test_appointment_dates_from_and_to_include_both_days(
        self, office: ConsultancyContext
    ) -> None:
        today = timezone.localdate()
        within = {
            "appointment_from": today.isoformat(),
            "appointment_to": (today + timedelta(days=3)).isoformat(),
        }
        before = {"appointment_to": (today - timedelta(days=10)).isoformat()}
        after = {"appointment_from": (today + timedelta(days=4)).isoformat()}

        assert names_in(listed(office, within)) == ["Sita Karki"]
        assert names_in(listed(office, before)) == ["Sita Karki"]
        assert names_in(listed(office, after)) == []

    def test_appointment_days_are_kathmandu_days(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        # 00:30 in Kathmandu is still the day before in UTC.
        visa_for(abc, student_named(abc, "Sita Karki"), interview_at=in_kathmandu(2, 0, 30))
        day = (timezone.localdate() + timedelta(days=2)).isoformat()

        assert names_in(listed(abc, {"appointment_from": day, "appointment_to": day})) == [
            "Sita Karki"
        ]

    @pytest.mark.parametrize("field", ["biometrics_at", "medical_at", "interview_at"])
    def test_any_of_the_three_appointments_counts_for_the_range(
        self, abc: ConsultancyContext, field: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        soon: dict[str, object] = {field: in_kathmandu(5)}
        later: dict[str, object] = {field: in_kathmandu(9)}
        visa_for(abc, student_named(abc, "Sita Karki"), "Deakin", "australia", "2027-02", **soon)
        visa_for(abc, student_named(abc, "Gita Rai"), "Monash", "australia", "2027-02", **later)
        day = (timezone.localdate() + timedelta(days=5)).isoformat()

        assert names_in(listed(abc, {"appointment_from": day, "appointment_to": day})) == [
            "Sita Karki"
        ]

    def test_filter_by_a_counsellor_and_my_students(self, office: ConsultancyContext) -> None:
        with tenant_context(office.consultancy):
            bikash = Staff.objects.get(name="Bikash Thapa")

        by_id = names_in(listed(office, {"counsellor": str(bikash.public_id)}))
        log_in(office, bikash)
        mine = names_in(listed(office, {"counsellor": "me"}))

        assert by_id == mine == ["Sita Karki", "Sita Karki"]

    @pytest.mark.parametrize(
        "query",
        [
            {"status": "approved"},
            {"status": "Applied"},
            {"status": "lodged"},
            {"status": "preparing"},
            {"status": "withdrawn"},
            {"status": ["applied", "nope"]},
            {"status": ""},
            {"intake": "2027-13"},
            {"intake": "Feb 2027"},
            {"country": "nepal"},
            {"counsellor": "1; DROP TABLE"},
            {"counsellor": "everyone"},
            {"appointment_from": "2027-02-30"},
            {"appointment_from": "tomorrow"},
            {"appointment_to": "01/02/2027"},
            {"appointment_from": "2027-03-01", "appointment_to": "2027-02-01"},
            {"has_appointment": "yes"},
            {"has_appointment": "1"},
            {"interview_from": "2027-02-01"},
            {"has_interview": "true"},
            {"sort": "interview"},
            {"sort": "name"},
            {"sort": "-started_on"},
            {"page": "0"},
            {"page": "two"},
            {"q": "a" * 101},
            {"search": "sita"},
            {"colour": "red"},
            *({"status": text} for text in INJECTION_STRINGS),
            *({"country": text} for text in INJECTION_STRINGS),
            *({"appointment_from": text} for text in INJECTION_STRINGS),
        ],
    )
    def test_refused_filter_values(
        self, office: ConsultancyContext, query: dict[str, str | list[str]]
    ) -> None:
        response = listed(office, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "DROP" not in str(response.json())
        assert "<script>" not in str(response.json())


class TestSearch:
    """Ticket 24 (UI revamp): `q` finds a Visa by its Student or its University."""

    @pytest.fixture
    def office(self, abc: ConsultancyContext) -> ConsultancyContext:
        """Sita's two Visas (Bikash's Student) and Gita's (no counsellor)."""
        bikash = named_staff(abc, "Bikash Thapa")
        sita = student_named(abc, "Sita Karki", bikash, email="sita@gmail.test", phone="9841234567")
        gita = student_named(abc, "Gita Rai", email="gita@yahoo.test", phone="9801112222")
        visa_for(abc, sita, "Deakin University", medical_at=in_kathmandu(3, 9))
        visa_for(abc, sita, "University of Toronto", "canada", "2027-09", status=VisaStatus.REFUSED)
        visa_for(abc, gita, "Monash University", "australia", "2027-09")
        log_in(abc, abc.add_staff(Role.ADMIN))
        return abc

    @staticmethod
    def rows(response: _MonkeyPatchedResponse) -> list[str]:
        """Each listed row as Student/University, in order."""
        assert response.status_code == status.HTTP_200_OK, response.json()
        return [
            f"{row['student']['name']}/{row['university']['name']}"
            for row in response.json()["results"]
        ]

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("gita", ["Gita Rai/Monash University"]),
            ("SITA", ["Sita Karki/Deakin University", "Sita Karki/University of Toronto"]),
            ("9801-11", ["Gita Rai/Monash University"]),
            ("yahoo", ["Gita Rai/Monash University"]),
            ("toronto", ["Sita Karki/University of Toronto"]),
            ("nobody", []),
        ],
    )
    def test_finds_by_student_name_phone_email_or_university(
        self, office: ConsultancyContext, query: str, expected: list[str]
    ) -> None:
        response = listed(office, {"q": query})

        assert self.rows(response) == expected
        assert response.json()["count"] == len(expected)

    @pytest.mark.parametrize(
        ("filters", "expected"),
        [
            ({"status": "refused"}, ["Sita Karki/University of Toronto"]),
            ({"country": "australia"}, ["Sita Karki/Deakin University"]),
            ({"intake": "2027-09"}, ["Sita Karki/University of Toronto"]),
            ({"has_appointment": "true"}, ["Sita Karki/Deakin University"]),
            ({"counsellor": "none"}, []),
        ],
    )
    def test_it_works_together_with_the_other_filters(
        self, office: ConsultancyContext, filters: dict[str, str], expected: list[str]
    ) -> None:
        assert self.rows(listed(office, {"q": "sita", **filters})) == expected

    def test_it_works_together_with_a_counsellor_and_appointment_days(
        self, office: ConsultancyContext
    ) -> None:
        with tenant_context(office.consultancy):
            bikash = Staff.objects.get(name="Bikash Thapa")
        day = (timezone.localdate() + timedelta(days=3)).isoformat()

        by_counsellor = listed(office, {"q": "university", "counsellor": str(bikash.public_id)})
        by_day = listed(office, {"q": "university", "appointment_from": day, "appointment_to": day})

        assert self.rows(by_counsellor) == [
            "Sita Karki/Deakin University",
            "Sita Karki/University of Toronto",
        ]
        assert self.rows(by_day) == ["Sita Karki/Deakin University"]

    def test_a_blank_search_changes_nothing(self, office: ConsultancyContext) -> None:
        assert listed(office, {"q": ""}).json() == listed(office).json()

    @pytest.mark.parametrize("query", [*INJECTION_STRINGS, "%", "_", "\\"])
    def test_injection_shaped_searches_find_nothing_and_break_nothing(
        self, office: ConsultancyContext, query: str
    ) -> None:
        assert self.rows(listed(office, {"q": query})) == []
        assert listed(office).json()["count"] == 3

    def test_an_over_long_search_is_refused(self, office: ConsultancyContext) -> None:
        response = listed(office, {"q": "a" * 101})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "q" in response.json()

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_every_role_that_sees_the_list_can_search_it(
        self, office: ConsultancyContext, role: Role
    ) -> None:
        office.log_in_as(role)

        assert self.rows(listed(office, {"q": "gita"})) == ["Gita Rai/Monash University"]

    def test_a_teacher_is_still_refused(self, office: ConsultancyContext) -> None:
        office.log_in_as(Role.TEACHER)

        response = listed(office, {"q": "sita"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_the_query_count_does_not_grow_with_a_search(self, office: ConsultancyContext) -> None:
        with CaptureQueriesContext(connection) as plain:
            listed(office)
        with CaptureQueriesContext(connection) as searched:
            listed(office, {"q": "university"})

        assert len(searched) == len(plain)


class TestWhoMaySee:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_everyone_but_teachers_sees_every_visa(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        visa_for(abc, student_named(abc, "Sita Karki", named_staff(abc, "Bikash Thapa")))
        visa_for(abc, student_named(abc, "Gita Rai"), "Monash")
        abc.log_in_as(role)

        assert len(names_in(listed(abc))) == 2

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        visa_for(abc, student_named(abc, "Sita Karki"))
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
    def test_lists_the_intakes_and_countries_visas_use(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        student = student_named(abc, "Sita")
        visa_for(abc, student, "Toronto", "canada", "2028-02")
        visa_for(abc, student, "Deakin", "australia", "2027-07")
        # An Application without a Visa adds nothing.
        applied(abc, student, course_at(abc, "Otago", "new_zealand", ("2027-02",)), "2027-02")

        response = abc.client.get(CHOICES_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "intakes": ["2027-07", "2028-02"],
            "countries": ["australia", "canada"],
        }

    def test_unknown_query_fields_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(CHOICES_URL, {"x": "1"}).status_code == status.HTTP_400_BAD_REQUEST
