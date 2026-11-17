"""Reports: Students each month, where visitors came from, and Staff Progress (issues 28, 53, 85).

Spec 93-95, 93b, 99, 99a. Admin only; the Reports Module off answers 404.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from itertools import count

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application, Visa
from applications.statuses import ApplicationStatus, VisaStatus
from catalogue.models import Course, CourseLevel, University
from history.models import HistoryEntry, HistoryKind
from modules.services import switch_modules
from modules.types import Module
from reports.calculations import months_back
from staff.models import Role, Staff, StaffStatus
from students.models import Country, Source, Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)
from visits.models import Visit, VisitKind

STUDENTS_URL = "/api/reports/students/"
SOURCES_URL = "/api/reports/sources/"
# Staff Progress has its own tests in test_reports_staff_progress_integration.py (issue 85).
STAFF_PROGRESS_URL = "/api/reports/staff-progress/"
REPORT_URLS = [STUDENTS_URL, SOURCES_URL, STAFF_PROGRESS_URL]
ADMINS_ONLY = {"detail": "Only an Admin can see reports."}
SIX = 6
TWELVE = 12


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """An office with the Reports Module on and its Admin logged in."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.REPORTS})
    office.log_in_as(Role.ADMIN)
    return office


def today() -> date:
    """The office's day today."""
    return timezone.localdate()


def at(day: date, hour: int = 10) -> datetime:
    """A moment on the office's local day."""
    return timezone.make_aware(datetime.combine(day, time(hour)))


def months_ago(count: int) -> date:
    """The first day of the month `count` months before this one."""
    year, month = (int(part) for part in months_back(today(), count + 1)[0].split("-"))
    return date(year, month, 1)


def visit(office: ConsultancyContext, student: Student, day: date, kind: VisitKind) -> None:
    """Store a Visit on a day."""
    with tenant_context(office.consultancy):
        Visit.objects.create(student=student, at=at(day), kind=kind, recorded_by_name="Desk")


def moved(
    office: ConsultancyContext,
    student: Student,
    day: date,
    before: Stage | str,
    after: Stage | str,
) -> None:
    """Store a Stage change in the Student's History on a day; text for Stages that are gone."""
    with tenant_context(office.consultancy):
        HistoryEntry.objects.create(
            student=student,
            kind=HistoryKind.STAGE_CHANGED,
            at=at(day),
            by_name="Someone",
            before=str(before),
            after=str(after),
        )


def month_row(body: dict[str, list[dict[str, object]]], month: str) -> dict[str, object]:
    """One month's counts from the Students report."""
    return next(row for row in body["months"] if row["month"] == month)


def this_month() -> str:
    """This month as "YYYY-MM"."""
    return months_back(today(), 1)[0]


def date_range(start: date, end: date) -> dict[str, str]:
    """The query for a date range."""
    return {"from": start.isoformat(), "to": end.isoformat()}


def good_query(url: str) -> dict[str, str]:
    """A query each report accepts: months for the Students report, a range for the others."""
    return {"months": "6"} if url == STUDENTS_URL else date_range(today(), today())


class TestAccess:
    @pytest.mark.parametrize("url", REPORT_URLS)
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_an_admin_sees_reports(
        self, abc: ConsultancyContext, url: str, role: Role
    ) -> None:
        abc.log_in_as(role)

        response = abc.client.get(url, good_query(url))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ADMINS_ONLY

    @pytest.mark.parametrize("url", REPORT_URLS)
    def test_an_admin_sees_every_report(self, abc: ConsultancyContext, url: str) -> None:
        response = abc.client.get(url, good_query(url))

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("url", REPORT_URLS)
    @pytest.mark.parametrize("role", list(Role))
    def test_with_the_module_off_every_role_gets_not_found(
        self, abc: ConsultancyContext, url: str, role: Role
    ) -> None:
        switch_modules(abc.consultancy, {Module.BATCHES, Module.ACCOUNTS})
        if role != Role.ADMIN:  # The fixture's Admin is already logged in.
            abc.log_in_as(role)

        response = abc.client.get(url, good_query(url))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("url", REPORT_URLS)
    def test_switching_the_module_back_on_brings_the_numbers_back(
        self, abc: ConsultancyContext, url: str
    ) -> None:
        student = make_student(abc)
        visit(abc, student, today(), VisitKind.NEW)
        switch_modules(abc.consultancy, set())
        hidden = abc.client.get(url, good_query(url))
        switch_modules(abc.consultancy, {Module.REPORTS})

        shown = abc.client.get(url, good_query(url))

        assert hidden.status_code == status.HTTP_404_NOT_FOUND
        assert shown.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("url", REPORT_URLS)
    def test_logged_out_is_refused(self, abc: ConsultancyContext, url: str) -> None:
        response = abc.new_client().get(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("url", REPORT_URLS)
    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(self, abc: ConsultancyContext, url: str, method: str) -> None:
        response = getattr(abc.client, method)(url, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("url", REPORT_URLS)
    def test_deactivated_admin_is_refused(self, abc: ConsultancyContext, url: str) -> None:
        admin = named_staff(abc, "Old Admin", Role.ADMIN)
        log_in(abc, admin)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        response = abc.client.get(url, good_query(url))

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}


class TestStudentsReport:
    def test_an_empty_office_shows_six_months_of_zeros_ending_this_month(
        self, abc: ConsultancyContext
    ) -> None:
        response = abc.client.get(STUDENTS_URL)

        assert response.status_code == status.HTTP_200_OK
        rows = response.json()["months"]
        assert [row["month"] for row in rows] == months_back(today(), SIX)
        assert rows[-1] == {
            "month": this_month(),
            "new_visitors": 0,
            "signed_up": 0,
            "applied": 0,
            "visas_granted": 0,
            "visas_refused": 0,
            "lost": 0,
        }

    def test_twelve_months_can_be_asked_for(self, abc: ConsultancyContext) -> None:
        response = abc.client.get(STUDENTS_URL, {"months": "12"})

        assert [row["month"] for row in response.json()["months"]] == months_back(today(), TWELVE)

    def test_new_visitors_count_first_visits_only_in_their_month(
        self, abc: ConsultancyContext
    ) -> None:
        sita, ram = make_student(abc), make_student(abc, name="Ram Thapa")
        visit(abc, sita, today(), VisitKind.NEW)
        visit(abc, sita, today(), VisitKind.FOLLOW_UP)
        visit(abc, ram, months_ago(1), VisitKind.NEW)
        visit(abc, ram, months_ago(SIX), VisitKind.NEW)

        body = abc.client.get(STUDENTS_URL).json()

        assert month_row(body, this_month())["new_visitors"] == 1
        assert month_row(body, months_back(today(), 2)[0])["new_visitors"] == 1
        assert sum(int(str(row["new_visitors"])) for row in body["months"]) == 2

    def test_a_stage_change_made_in_the_app_counts_as_signing_up(
        self, abc: ConsultancyContext
    ) -> None:
        admin = named_staff(abc, "Maya Admin", Role.ADMIN)
        student = make_student(abc, admin, Stage.LEAD)
        log_in(abc, admin)
        changed = abc.client.post(
            f"/api/students/{student.pk}/stage/", {"stage": "counselling"}, format="json"
        )

        body = abc.client.get(STUDENTS_URL).json()

        assert changed.status_code == status.HTTP_200_OK
        assert month_row(body, this_month())["signed_up"] == 1

    def test_signing_up_applying_visas_and_lost_come_from_history(
        self, abc: ConsultancyContext
    ) -> None:
        sita, ram, hari = (
            make_student(abc, name=name) for name in ("Sita Gurung", "Ram Thapa", "Hari KC")
        )
        moved(abc, sita, today(), Stage.LEAD, Stage.COUNSELLING)
        moved(abc, sita, today(), Stage.COUNSELLING, Stage.APPLYING)
        moved(abc, ram, months_ago(TWELVE), Stage.LEAD, Stage.COUNSELLING)
        moved(abc, ram, today(), Stage.COUNSELLING, Stage.LOST)
        moved(abc, ram, today(), Stage.LOST, Stage.COUNSELLING)
        moved(abc, hari, months_ago(TWELVE), Stage.COUNSELLING, Stage.APPLYING)
        moved(abc, hari, today(), Stage.VISA, Stage.OFFER)
        visa_for(abc, hari, Country.AUSTRALIA, today())

        row = month_row(abc.client.get(STUDENTS_URL).json(), this_month())

        assert row == {
            "month": this_month(),
            "new_visitors": 0,
            "signed_up": 1,
            "applied": 1,
            "visas_granted": 1,
            "visas_refused": 1,
            "lost": 1,
        }

    def test_history_naming_stages_that_are_gone_still_counts(
        self, abc: ConsultancyContext
    ) -> None:
        sita, ram = (make_student(abc, name=name) for name in ("Sita Gurung", "Ram Thapa"))
        moved(abc, sita, today(), "counselling", "onboarded")
        moved(abc, sita, today(), "onboarded", "applying")
        moved(abc, ram, months_ago(TWELVE), "lead", "applying")
        moved(abc, ram, today(), "visa", "completed")

        row = month_row(abc.client.get(STUDENTS_URL).json(), this_month())

        assert (row["signed_up"], row["applied"]) == (1, 1)
        assert row["visas_granted"] == 0

    @pytest.mark.parametrize(
        "query",
        [
            {"months": "7"},
            {"months": "six"},
            {"months": "6.0"},
            {"months": "6", "extra": "1"},
            {"stage": "lead"},
            *({"months": text} for text in INJECTION_STRINGS),
        ],
    )
    def test_a_bad_query_is_refused_without_echoing_it(
        self, abc: ConsultancyContext, query: dict[str, str]
    ) -> None:
        response = abc.client.get(STUDENTS_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        for text in INJECTION_STRINGS:
            assert text not in response.content.decode()


_university_numbers = count(1)


def visa_for(
    office: ConsultancyContext,
    student: Student,
    country: Country,
    granted_on: date | None,
    visa_status: VisaStatus = VisaStatus.GRANTED,
) -> Visa:
    """Store a Visa for a course at a new University in `country`."""
    with tenant_context(office.consultancy):
        university = University.objects.create(
            name=f"University {next(_university_numbers)}", country=country
        )
        course = Course.objects.create(
            university=university, name="Master of IT", level=CourseLevel.MASTER
        )
        application = Application.objects.create(
            student=student,
            course=course,
            intake="2027-03",
            status=ApplicationStatus.OFFER,
        )
        return Visa.objects.create(
            student=student, application=application, status=visa_status, granted_on=granted_on
        )


def visit_at(office: ConsultancyContext, student: Student, moment: datetime) -> None:
    """Store a New Visit at a moment."""
    with tenant_context(office.consultancy):
        Visit.objects.create(student=student, at=moment, kind=VisitKind.NEW, recorded_by_name="D")


def counted(rows: list[dict[str, object]]) -> list[tuple[object, object]]:
    """A country list as (country, count) pairs."""
    return [(row["country"], row["count"]) for row in rows]


class TestCountries:
    """Issue 53: Visas granted by country and the countries Students want, for the months shown."""

    def test_an_empty_office_lists_no_countries(self, abc: ConsultancyContext) -> None:
        body = abc.client.get(STUDENTS_URL).json()

        assert body["visas_granted_by_country"] == []
        assert body["countries_wanted"] == []

    def test_visas_granted_in_the_months_count_by_the_universitys_country(
        self, abc: ConsultancyContext
    ) -> None:
        student = make_student(abc, country="japan")
        visa_for(abc, student, Country.UK, today())
        visa_for(abc, student, Country.CANADA, months_ago(SIX - 1))
        visa_for(abc, student, Country.CANADA, months_ago(1))
        visa_for(abc, student, Country.USA, months_ago(SIX))
        visa_for(abc, student, Country.AUSTRALIA, today(), VisaStatus.REFUSED)
        visa_for(abc, student, Country.NEW_ZEALAND, None)

        six = abc.client.get(STUDENTS_URL).json()["visas_granted_by_country"]
        twelve = abc.client.get(STUDENTS_URL, {"months": "12"}).json()["visas_granted_by_country"]

        assert six == [{"country": "canada", "count": 2}, {"country": "uk", "count": 1}]
        assert counted(twelve) == [("canada", 2), ("uk", 1), ("usa", 1)]

    def test_a_visa_granted_in_the_app_is_counted(self, abc: ConsultancyContext) -> None:
        admin = named_staff(abc, "Maya Admin", Role.ADMIN)
        student = make_student(abc, admin, Stage.VISA)
        visa = visa_for(abc, student, Country.CANADA, None, VisaStatus.APPLIED)
        log_in(abc, admin)
        changed = abc.client.patch(
            f"/api/students/{student.pk}/visas/{visa.pk}/", {"status": "granted"}, format="json"
        )

        body = abc.client.get(STUDENTS_URL).json()

        assert changed.status_code == status.HTTP_200_OK
        assert counted(body["visas_granted_by_country"]) == [("canada", 1)]

    def test_new_visitors_in_the_months_count_once_by_the_country_they_want(
        self, abc: ConsultancyContext
    ) -> None:
        sita = make_student(abc, country="canada", other_countries=["uk", "usa"])
        ram = make_student(abc, name="Ram Thapa", country="canada")
        hari = make_student(abc, name="Hari KC", country="uk")
        old = make_student(abc, name="Old Visitor", country="japan")
        returning = make_student(abc, name="Returning", country="usa")
        visit(abc, sita, today(), VisitKind.NEW)
        visit(abc, sita, today(), VisitKind.NEW)
        visit(abc, ram, months_ago(SIX - 1), VisitKind.NEW)
        visit(abc, hari, months_ago(2), VisitKind.NEW)
        visit(abc, old, months_ago(SIX), VisitKind.NEW)
        visit(abc, returning, today(), VisitKind.FOLLOW_UP)

        body = abc.client.get(STUDENTS_URL).json()

        assert body["countries_wanted"] == [
            {"country": "canada", "count": 2},
            {"country": "uk", "count": 1},
        ]

    def test_the_first_day_is_the_offices_local_day(self, abc: ConsultancyContext) -> None:
        first_day = months_ago(SIX - 1)
        early, late = (
            make_student(abc, name=name, country=country)
            for name, country in (("Early Bird", "uk"), ("Late Owl", "usa"))
        )
        # 00:30 in Kathmandu is still the day before in UTC, and 23:30 the day before is outside.
        visit_at(abc, early, at(first_day, 0) + timedelta(minutes=30))
        visit_at(abc, late, at(first_day - timedelta(days=1), 23) + timedelta(minutes=30))

        body = abc.client.get(STUDENTS_URL).json()

        assert counted(body["countries_wanted"]) == [("uk", 1)]

    @pytest.mark.parametrize(
        "query",
        [
            {"from": "2026-01-01", "to": "2026-12-31"},
            {"months": "6", "from": "2026-01-01"},
            {"months": "0"},
            {"months": "-6"},
            {"months": "120"},
        ],
    )
    def test_any_other_range_is_refused(
        self, abc: ConsultancyContext, query: dict[str, str]
    ) -> None:
        response = abc.client.get(STUDENTS_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "visas_granted_by_country" not in response.json()

    def test_country_names_are_never_shown_to_other_roles(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, country="canada")
        visit(abc, student, today(), VisitKind.NEW)
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.get(STUDENTS_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ADMINS_ONLY


class TestSourcesReport:
    def test_new_visitors_between_the_dates_are_counted_by_source(
        self, abc: ConsultancyContext
    ) -> None:
        start, end = today() - timedelta(days=30), today() - timedelta(days=1)
        for name, source, moment, kind in [
            ("A", Source.FACEBOOK, at(start, 0), VisitKind.NEW),
            ("B", Source.FACEBOOK, at(end, 23), VisitKind.NEW),
            ("C", Source.WALK_IN, at(end), VisitKind.NEW),
            ("D", Source.WALK_IN, at(start - timedelta(days=1), 23), VisitKind.NEW),
            ("E", Source.REFERRAL, at(end + timedelta(days=1), 0), VisitKind.NEW),
            ("F", Source.REFERRAL, at(end), VisitKind.FOLLOW_UP),
        ]:
            student = make_student(abc, name=name, source=source)
            with tenant_context(abc.consultancy):
                Visit.objects.create(student=student, at=moment, kind=kind, recorded_by_name="D")

        response = abc.client.get(SOURCES_URL, date_range(start, end))

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["total"] == 3
        assert body["sources"][:2] == [
            {"source": "facebook", "count": 2},
            {"source": "walk_in", "count": 1},
        ]
        assert len(body["sources"]) == len(Source)
        assert {row["source"] for row in body["sources"]} == {source.value for source in Source}

    @pytest.mark.parametrize(
        "query",
        [
            {},
            {"from": "2026-10-01"},
            {"to": "2026-10-01"},
            {"from": "2026-10-02", "to": "2026-10-01"},
            {"from": "2026/10/01", "to": "2026-10-31"},
            {"from": "2026-02-30", "to": "2026-03-01"},
            {"from": "1999-12-31", "to": "2026-03-01"},
            {"from": "2026-01-01", "to": "9999-12-31"},
            {"from": "2026-01-01", "to": "2026-01-31", "source": "facebook"},
            *({"from": text, "to": "2026-01-31"} for text in INJECTION_STRINGS),
        ],
    )
    @pytest.mark.parametrize("url", [SOURCES_URL, STAFF_PROGRESS_URL])
    def test_a_bad_range_is_refused_without_echoing_it(
        self, abc: ConsultancyContext, url: str, query: dict[str, str]
    ) -> None:
        response = abc.client.get(url, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        for text in INJECTION_STRINGS:
            assert text not in response.content.decode()
