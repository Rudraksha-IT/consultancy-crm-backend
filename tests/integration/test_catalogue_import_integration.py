"""Admins import Universities and Courses from a CSV file (spec 48-49; issue 13)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.db import IntegrityError
from django_tenants.utils import tenant_context
from rest_framework import status

from catalogue.models import Course, Intake, University
from staff.models import Role
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

IMPORT_URL = "/api/catalogue/import/"
HEADER = "university,country,course,level,intakes"
DEAKIN_DATA_SCIENCE = (
    'Deakin University,Australia,Master of Data Science,Master,"Mar 2027;Jul 2027"'
)
DEAKIN_NURSING = "Deakin University,Australia,Bachelor of Nursing,Bachelor,Feb 2027"
MONASH_IT = "Monash University,Australia,Master of IT,Master,2027-02"
NOT_ADMINS = (Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER)
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE catalogue_university; --",
    "}body{background:url(javascript:alert(1))}",
]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def csv_of(*rows: str) -> str:
    """A CSV file with the column names and these rows."""
    return "\n".join((HEADER, *rows)) + "\n"


def import_csv(client: APIClient, text: str) -> _MonkeyPatchedResponse:
    """POST a CSV file's text to the import."""
    return client.post(IMPORT_URL, {"csv": text}, format="json")


def catalogue_in(office: ConsultancyContext) -> dict[str, dict[str, list[str]]]:
    """Every University, its Courses and their Intakes, as stored."""
    with tenant_context(office.consultancy):
        return {
            university.name: {
                course.name: [intake.month for intake in course.intakes.all()]
                for course in university.courses.all()
            }
            for university in University.objects.prefetch_related("courses__intakes")
        }


class TestImport:
    def test_an_admin_imports_universities_and_courses(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = import_csv(abc.client, csv_of(DEAKIN_DATA_SCIENCE, DEAKIN_NURSING, MONASH_IT))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"imported": 3, "already_there": 0, "failed": 0, "problems": []}
        assert catalogue_in(abc) == {
            "Deakin University": {
                "Bachelor of Nursing": ["2027-02"],
                "Master of Data Science": ["2027-03", "2027-07"],
            },
            "Monash University": {"Master of IT": ["2027-02"]},
        }
        with tenant_context(abc.consultancy):
            deakin = University.objects.get(name="Deakin University")
            assert deakin.country == "australia"
            assert not deakin.archived
            assert Course.objects.get(name="Master of IT").level == "master"

    def test_bad_rows_are_reported_and_good_rows_are_still_imported(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = import_csv(
            abc.client,
            csv_of(
                DEAKIN_DATA_SCIENCE,
                "Monash University,Australia,Bachelor of IT,Bachelors,Feb 2027",
                ",Australia,Master of IT,Master,Fall 2027",
                MONASH_IT,
            ),
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "imported": 2,
            "already_there": 0,
            "failed": 2,
            "problems": [
                {
                    "row": 3,
                    "problem": "Level “Bachelors” isn't one of Bachelor, Master, Diploma, "
                    "Graduate Certificate, Foundation.",
                },
                {"row": 4, "problem": "University name is empty."},
                {"row": 4, "problem": "Intake “Fall 2027” must look like “Sep 2027”."},
            ],
        }
        assert set(catalogue_in(abc)) == {"Deakin University", "Monash University"}

    def test_importing_again_creates_no_duplicates(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        import_csv(abc.client, csv_of(DEAKIN_DATA_SCIENCE, MONASH_IT))

        response = import_csv(
            abc.client,
            csv_of(
                DEAKIN_DATA_SCIENCE.upper()
                .replace("MASTER,", "Master,")
                .replace("AUSTRALIA", "australia"),
                "monash university,Australia,master of it,Master,2027-02",
                DEAKIN_NURSING,
            ),
        )

        assert response.json() == {"imported": 1, "already_there": 2, "failed": 0, "problems": []}
        with tenant_context(abc.consultancy):
            assert University.objects.count() == 2
            assert Course.objects.count() == 3

    def test_a_course_already_there_is_left_as_it_is(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        import_csv(abc.client, csv_of(DEAKIN_DATA_SCIENCE))

        import_csv(
            abc.client,
            csv_of("Deakin University,Australia,Master of Data Science,Diploma,Feb 2029"),
        )

        assert catalogue_in(abc) == {
            "Deakin University": {"Master of Data Science": ["2027-03", "2027-07"]}
        }
        with tenant_context(abc.consultancy):
            assert Course.objects.get().level == "master"

    def test_the_same_course_twice_in_one_file_is_added_once(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = import_csv(abc.client, csv_of(MONASH_IT, MONASH_IT))

        assert response.json()["imported"] == 1
        assert response.json()["already_there"] == 1
        with tenant_context(abc.consultancy):
            assert Course.objects.count() == 1

    def test_archived_entries_already_there_stay_archived(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            monash = University.objects.create(
                name="Monash University", country="australia", archived=True
            )
            Course.objects.create(university=monash, name="Master of IT", level="master")

        response = import_csv(abc.client, csv_of(MONASH_IT))

        assert response.json()["already_there"] == 1
        with tenant_context(abc.consultancy):
            assert University.objects.get().archived

    def test_a_university_already_there_in_another_country_is_reported(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        import_csv(abc.client, csv_of(MONASH_IT))

        response = import_csv(
            abc.client, csv_of("Monash University,UK,Master of Law,Master,Feb 2027")
        )

        assert response.json() == {
            "imported": 0,
            "already_there": 0,
            "failed": 1,
            "problems": [
                {
                    "row": 2,
                    "problem": (
                        "Monash University is already in the Catalogue in Australia, not UK."
                    ),
                }
            ],
        }
        with tenant_context(abc.consultancy):
            assert not Course.objects.filter(name="Master of Law").exists()

    def test_a_file_with_only_the_column_names_imports_nothing(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = import_csv(abc.client, csv_of())

        assert response.json() == {"imported": 0, "already_there": 0, "failed": 0, "problems": []}

    def test_a_row_clashing_with_a_name_added_meanwhile_is_reported(
        self, abc: ConsultancyContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        create = Course.objects.create

        def clash(**fields: object) -> Course:
            if fields["name"] == "Master of IT":
                raise IntegrityError
            return create(**fields)

        monkeypatch.setattr(Course.objects, "create", clash)

        response = import_csv(abc.client, csv_of(DEAKIN_NURSING, MONASH_IT))

        assert response.json() == {
            "imported": 1,
            "already_there": 0,
            "failed": 1,
            "problems": [
                {
                    "row": 3,
                    "problem": (
                        "This row clashes with a name added meanwhile. Import the file again."
                    ),
                }
            ],
        }
        # The row is saved whole or not at all: no University without its Course.
        assert set(catalogue_in(abc)) == {"Deakin University"}

    def test_a_full_file_is_imported_in_one_go(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        rows = [
            f"University {n // 10},UK,Course {n},Diploma,Feb 2027;Sep 2027" for n in range(2000)
        ]

        response = import_csv(abc.client, csv_of(*rows))

        assert response.json()["imported"] == 2000
        with tenant_context(abc.consultancy):
            assert University.objects.count() == 200
            assert Intake.objects.count() == 4000

    @pytest.mark.parametrize("name", INJECTION_STRINGS)
    def test_injection_shaped_names_are_stored_as_plain_text(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        quoted = name.replace('"', '""')

        response = import_csv(
            abc.client, csv_of(f'"{quoted}",Australia,"{quoted}",Master,Feb 2027')
        )

        assert response.json()["imported"] == 1
        assert catalogue_in(abc) == {name: {name: ["2027-02"]}}

    def test_importing_is_logged_with_counts_only(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)

        import_csv(abc.client, csv_of(DEAKIN_DATA_SCIENCE, ",Australia,x,Master,Feb 2027"))

        line = json_logs.only(
            f"Catalogue imported: imported=1, already_there=0, failed=1, by_staff_id={admin.pk}"
        )
        assert line["level"] == "INFO"
        assert "Deakin" not in json_logs.text()


class TestRefusedFiles:
    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("", "The file is empty."),
            ("\n\n", "The file is empty."),
            (
                f"university,country,course\n{MONASH_IT}\n",
                "The first row must name the columns: university,country,course,level,intakes.",
            ),
            (csv_of('"Monash,Australia,x,Master,Feb 2027'), "We couldn't read this file as CSV."),
            (
                csv_of(*[MONASH_IT] * 2001),
                "A file can have at most 2,000 rows. Split it and import each part.",
            ),
        ],
    )
    def test_a_file_that_cant_be_imported_is_refused_with_a_reason(
        self, abc: ConsultancyContext, text: str, message: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = import_csv(abc.client, text)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"csv": [message]}
        with tenant_context(abc.consultancy):
            assert not University.objects.exists()

    def test_an_over_long_file_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        text = csv_of(f"{'x' * 500_000},Australia,x,Master,Feb 2027")

        response = import_csv(abc.client, text)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"csv": ["The file is too big. Import at most 500 KB at a time."]}

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({}, "csv"),
            ({"csv": 123}, "csv"),
            ({"csv": ["a"]}, "csv"),
            ({"csv": None}, "csv"),
            ({"csv": csv_of(MONASH_IT), "replace": True}, "detail"),  # unknown field
        ],
    )
    def test_bad_input_is_refused_and_nothing_is_stored(
        self, abc: ConsultancyContext, body: dict[str, object], field: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(IMPORT_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        with tenant_context(abc.consultancy):
            assert not University.objects.exists()
            assert not Intake.objects.exists()


class TestImportAccess:
    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_can_import(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = import_csv(abc.client, csv_of(MONASH_IT))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        with tenant_context(abc.consultancy):
            assert not University.objects.exists()

    def test_a_counsellor_is_told_why(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = import_csv(abc.client, csv_of(MONASH_IT))

        assert response.json() == {"detail": "Only an Admin can change the Catalogue."}

    def test_nobody_logged_in_is_refused(self, abc: ConsultancyContext) -> None:
        response = import_csv(abc.client, csv_of(MONASH_IT))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(IMPORT_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_the_import_is_not_served_on_the_platform_host(
        self, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        response = import_csv(platform_site.client, csv_of(MONASH_IT))

        assert response.status_code == status.HTTP_404_NOT_FOUND
