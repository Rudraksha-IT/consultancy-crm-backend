"""The Students board's cards (UI revamp, ticket 08).

`GET /api/students/?progress=true` adds each Student's next step and how many of their checklist
Documents are in, so a board card can show them. Without it the list answers as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from rest_framework import status

from applications.statuses import ApplicationStatus
from documents.verification import Verification
from staff.models import Role
from students.models import Stage
from tests.conftest import ConsultancyContext, ConsultancyFactory
from tests.integration.test_home_integration import EVERYONE_NEEDS, apply, course_in, hand_in
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    make_student,
)

if TYPE_CHECKING:
    from pytest_django import DjangoAssertNumQueries

LIST = "/api/students/"
BOARD = {"progress": "true"}
# The list's own queries (the domain, the Staff member, the count, the page), then the page's
# Applications, Visas and checklists: a fixed number however many Students are on the page.
MOST_QUERIES = 24


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def rows(office: ConsultancyContext, **query: str) -> list[dict[str, object]]:
    """The Students list's rows for `query`."""
    response = office.client.get(LIST, query)
    assert response.status_code == status.HTTP_200_OK, response.json()
    found: list[dict[str, object]] = response.json()["results"]
    return found


class TestProgressOnTheList:
    def test_a_row_gains_the_next_step_and_documents(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc, admin, Stage.APPLYING)
        apply(abc, student, course_in(abc), ApplicationStatus.APPLIED)
        hand_in(abc, student, "Passport", Verification.VERIFIED)
        hand_in(abc, student, "Citizenship", Verification.PENDING)
        hand_in(abc, student, "SEE Marksheet", Verification.REJECTED)

        [row] = rows(abc, **BOARD)

        assert row["next_step"] == "Waiting for an offer, Deakin University"
        assert row["documents"] == {"done": 2, "total": EVERYONE_NEEDS}
        assert row["name"] == "Sita Gurung"
        assert row["stage"] == "applying"

    def test_without_it_the_row_is_as_before(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc)

        [row] = rows(abc)

        assert "next_step" not in row
        assert "documents" not in row

    def test_every_stage_has_a_next_step(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, None, Stage.LEAD, name="New", phone="9841000001")
        make_student(abc, None, Stage.COUNSELLING, name="Talking", phone="9841000002")
        make_student(
            abc, None, Stage.LOST, name="Gone", phone="9841000003", lost_reason="Chose another"
        )

        steps = {row["name"]: row["next_step"] for row in rows(abc, **BOARD)}

        assert steps == {
            "New": "Have the first counselling session",
            "Talking": "Choose a course and apply",
            "Gone": "Lost: Chose another",
        }

    def test_it_works_with_the_filters_and_the_count(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(abc, None, Stage.LEAD, name="Asha Rai", phone="9841000001")
        make_student(abc, None, Stage.COUNSELLING, name="Asha Lama", phone="9841000002")
        make_student(abc, None, Stage.LEAD, name="Bina KC", phone="9841000003")

        response = abc.client.get(LIST, {**BOARD, "stage": "lead", "q": "Asha"})

        body = response.json()
        assert body["count"] == 1
        assert [row["name"] for row in body["results"]] == ["Asha Rai"]
        assert body["results"][0]["documents"] == {"done": 0, "total": EVERYONE_NEEDS}

    def test_a_page_needs_few_queries_however_many_students(
        self, abc: ConsultancyContext, django_assert_max_num_queries: DjangoAssertNumQueries
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        course = course_in(abc)
        for number in range(12):
            student = make_student(
                abc, admin, Stage.APPLYING, name=f"Student {number}", phone=f"9841000{number:03}"
            )
            apply(abc, student, course, ApplicationStatus.APPLIED)
            hand_in(abc, student, "Passport", Verification.VERIFIED)

        with django_assert_max_num_queries(MOST_QUERIES):
            found = rows(abc, **BOARD)

        assert len(found) == 12


class TestWhoMayAsk:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_everyone_who_sees_the_list_may(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)
        make_student(abc)

        [row] = rows(abc, **BOARD)

        assert row["next_step"] == "Have the first counselling session"

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)

        assert abc.client.get(LIST, BOARD).status_code == status.HTTP_403_FORBIDDEN

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.client.get(LIST, BOARD).status_code == status.HTTP_401_UNAUTHORIZED


class TestBadInput:
    @pytest.mark.parametrize("value", ["false", "1", "yes", "TRUE", *INJECTION_STRINGS])
    def test_anything_but_true_is_refused(self, abc: ConsultancyContext, value: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST, {"progress": value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "progress" in response.json()

    def test_a_name_typed_as_html_comes_back_as_plain_text(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        make_student(
            abc, None, Stage.LOST, name="<b>Sita</b>", lost_reason="<script>alert(1)</script>"
        )

        [row] = rows(abc, **BOARD)

        assert row["name"] == "<b>Sita</b>"
        assert row["next_step"] == "Lost: <script>alert(1)</script>"


class TestIsolation:
    def test_another_offices_students_and_documents_never_show(
        self, abc: ConsultancyContext, make_consultancy: ConsultancyFactory
    ) -> None:
        other = make_consultancy("XYZ Education", "xyzedu")
        theirs = make_student(other, None, Stage.APPLYING, name="Their Student")
        apply(other, theirs, course_in(other, "Their University"), ApplicationStatus.APPLIED)
        hand_in(other, theirs, "Passport", Verification.VERIFIED)
        make_student(abc, None, Stage.APPLYING, name="Our Student")
        abc.log_in_as(Role.ADMIN)

        [row] = rows(abc, **BOARD)

        assert row["name"] == "Our Student"
        assert row["next_step"] == "Find new options"
        assert row["documents"] == {"done": 0, "total": EVERYONE_NEEDS}
