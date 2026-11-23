"""A Student's Priority: High, Normal or Low (first live fixes, ticket 10; spec 82-86, 95-97).

Every Student starts at Normal. Anyone who can open a Student may change it (Teachers can't), and
each real change is recorded in the Student's History, which their Activity shows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django_tenants.utils import tenant_context
from rest_framework import status

from staff.models import Role, Staff
from students.models import Priority, Stage, Student
from tasks.models import TaskPriority
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs
from tests.integration.test_student_profile_integration import (
    INJECTION_STRINGS,
    history_rows,
    make_student,
    profile_url,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

CHOOSE_PRIORITY = {"priority": ["Choose High, Normal or Low."]}
NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def priority_url(student: Student | str) -> str:
    """Where a Student's Priority is set."""
    return f"{profile_url(student)}priority/"


def set_priority(
    office: ConsultancyContext, student: Student | str, body: object
) -> _MonkeyPatchedResponse:
    """PUT the body to the Student's Priority."""
    return office.client.put(priority_url(student), body, format="json")


def stored_priority(office: ConsultancyContext, student: Student) -> str:
    """The Student's Priority as stored now."""
    with tenant_context(office.consultancy):
        return Student.objects.get(pk=student.pk).priority


class TestStartsAtNormal:
    def test_a_new_student_is_normal_and_the_profile_says_so(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.get(profile_url(student))

        assert response.json()["priority"] == "normal"
        assert stored_priority(abc, student) == Priority.NORMAL

    def test_a_visitor_added_at_the_front_desk_is_normal(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.post(
            "/api/students/",
            {
                "name": "Ram Thapa",
                "phone": "9841000002",
                "country": "australia",
                "source": "walk_in",
                "highest_qualification": "plus_two",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        with tenant_context(abc.consultancy):
            assert Student.objects.get(name="Ram Thapa").priority == Priority.NORMAL

    def test_the_words_are_the_same_as_a_tasks(self) -> None:
        assert (
            Priority.choices
            == TaskPriority.choices
            == [
                ("high", "High"),
                ("normal", "Normal"),
                ("low", "Low"),
            ]
        )


class TestChangingIt:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_anyone_who_can_open_the_student_changes_it(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.APPLYING)
        abc.log_in_as(role)

        response = set_priority(abc, student, {"priority": "high"})

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["priority"] == "high"
        assert stored_priority(abc, student) == Priority.HIGH

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_the_profile_says_they_may_set_it(self, abc: ConsultancyContext, role: Role) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.APPLYING)
        abc.log_in_as(role)

        assert abc.client.get(profile_url(student)).json()["can_set_priority"] is True

    def test_a_lost_student_can_be_changed_too(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, None, Stage.LOST)

        assert set_priority(abc, student, {"priority": "low"}).json()["priority"] == "low"

    def test_each_change_is_recorded_with_who_and_the_words(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)

        set_priority(abc, student, {"priority": "high"})
        set_priority(abc, student, {"priority": "low"})

        first, second = history_rows(abc, student)
        assert (first.kind, first.before, first.after) == ("priority_changed", "Normal", "High")
        assert (second.before, second.after) == ("High", "Low")
        assert (first.by_staff_id, first.by_name, first.by_platform_admin) == (
            me.pk,
            me.name,
            False,
        )

    def test_the_history_answer_shows_the_change(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        set_priority(abc, student, {"priority": "high"})

        [entry] = abc.client.get(f"{profile_url(student)}history/").json()["results"]

        assert (entry["kind"], entry["before"], entry["after"]) == (
            "priority_changed",
            "Normal",
            "High",
        )

    def test_the_same_priority_again_records_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = set_priority(abc, student, {"priority": "normal"})

        assert response.status_code == status.HTTP_200_OK
        assert history_rows(abc, student) == []

    def test_it_stays_when_the_stage_moves(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        set_priority(abc, student, {"priority": "high"})

        abc.client.post(f"{profile_url(student)}stage/", {"stage": "counselling"}, format="json")

        assert stored_priority(abc, student) == Priority.HIGH

    def test_the_log_names_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        me = abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        set_priority(abc, student, {"priority": "high"})

        [line] = json_logs.with_message("Priority changed")
        assert line["message"] == (
            f"Priority changed: student_id={student.pk}, by_staff_id={me.pk}"
        )
        assert "Sita" not in json_logs.text()


class TestRefused:
    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = set_priority(abc, student, {"priority": "high"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS
        assert stored_priority(abc, student) == Priority.NORMAL

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        response = set_priority(abc, student, {"priority": "high"})

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize(
        "value",
        ["urgent", "HIGH", "High", "", None, 1, True, ["high"], {"v": "high"}, *INJECTION_STRINGS],
    )
    def test_anything_but_the_three_is_refused(
        self, abc: ConsultancyContext, value: object
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = set_priority(abc, student, {"priority": value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_PRIORITY
        assert stored_priority(abc, student) == Priority.NORMAL

    def test_a_missing_priority_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = set_priority(abc, student, {})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_PRIORITY

    def test_an_unknown_field_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = set_priority(abc, student, {"priority": "high", "stage": "lost"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_priority(abc, student) == Priority.NORMAL

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = set_priority(abc, "00000000-0000-4000-8000-000000000000", {"priority": "high"})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_another_offices_student_is_not_found(
        self, abc: ConsultancyContext, make_consultancy: ConsultancyFactory
    ) -> None:
        other = make_consultancy("XYZ Education", "xyzedu")
        theirs = make_student(other)
        abc.log_in_as(Role.ADMIN)

        response = set_priority(abc, str(theirs.public_id), {"priority": "high"})

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored_priority(other, theirs) == Priority.NORMAL

    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_only_put_is_answered(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = getattr(abc.client, method)(priority_url(student))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestExistingStudents:
    def test_the_migration_makes_every_existing_student_normal(
        self, abc: ConsultancyContext
    ) -> None:
        before = ("students", "0008_public_id")
        after = ("students", "0009_student_priority")
        with tenant_context(abc.consultancy):
            executor = MigrationExecutor(connection)
            executor.migrate([before])
            old_student = executor.loader.project_state(before).apps.get_model(
                "students", "Student"
            )
            kept = old_student.objects.create(
                name="Old Student",
                phone="9841000009",
                country="australia",
                source="walk_in",
                highest_qualification="plus_two",
            )
            with connection.cursor() as cursor:  # check the rows now, so tables can be altered
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            executor = MigrationExecutor(connection)
            executor.migrate([after])
            executor.loader.build_graph()
            executor.migrate(executor.loader.graph.leaf_nodes())

            assert Student.objects.get(pk=kept.pk).priority == Priority.NORMAL


# The Students list (ticket 11; spec stories 87-89).
LIST = "/api/students/"


def listed(office: ConsultancyContext, **query: str) -> list[str]:
    """The names the Students list shows for `query`, in its order."""
    response = office.client.get(LIST, query)
    assert response.status_code == status.HTTP_200_OK, response.json()
    return [row["name"] for row in response.json()["results"]]


def with_priority(
    office: ConsultancyContext,
    name: str,
    priority: Priority,
    counsellor: Staff | None = None,
    stage: Stage = Stage.LEAD,
) -> Student:
    """A Student called `name` with `priority`."""
    return make_student(office, counsellor, stage, name=name, priority=priority)


class TestOnTheStudentsList:
    def test_each_row_has_its_priority(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        with_priority(abc, "Asha Rai", Priority.HIGH)

        [row] = abc.client.get(LIST).json()["results"]

        assert row["priority"] == "high"

    @pytest.mark.parametrize(
        ("value", "names"),
        [("high", ["Bina KC", "Asha Rai"]), ("normal", ["Chet Lama"]), ("low", ["Dev Shah"])],
    )
    def test_the_filter_shows_only_that_priority_in_the_same_order(
        self, abc: ConsultancyContext, value: str, names: list[str]
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        with_priority(abc, "Asha Rai", Priority.HIGH)
        with_priority(abc, "Chet Lama", Priority.NORMAL)
        with_priority(abc, "Bina KC", Priority.HIGH)
        with_priority(abc, "Dev Shah", Priority.LOW)

        assert listed(abc, priority=value) == names

    def test_no_filter_keeps_the_usual_order(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        with_priority(abc, "Asha Rai", Priority.LOW)
        with_priority(abc, "Bina KC", Priority.HIGH)

        assert listed(abc) == ["Bina KC", "Asha Rai"]

    def test_it_works_with_stage_counsellor_and_search(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        with_priority(abc, "Asha Rai", Priority.HIGH, counsellor=me, stage=Stage.COUNSELLING)
        with_priority(abc, "Asha Thapa", Priority.HIGH, counsellor=me)
        with_priority(abc, "Asha Lama", Priority.LOW, counsellor=me, stage=Stage.COUNSELLING)
        with_priority(abc, "Bina KC", Priority.HIGH, counsellor=me, stage=Stage.COUNSELLING)

        assert listed(abc, priority="high", stage="counselling", counsellor="me", q="Asha") == [
            "Asha Rai"
        ]

    @pytest.mark.parametrize(
        "value", ["urgent", "HIGH", "High", "1", "high,low", *INJECTION_STRINGS]
    )
    def test_a_bad_value_is_refused(self, abc: ConsultancyContext, value: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST, {"priority": value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == CHOOSE_PRIORITY

    def test_another_offices_students_never_appear(
        self, abc: ConsultancyContext, make_consultancy: ConsultancyFactory
    ) -> None:
        other = make_consultancy("XYZ Education", "xyzedu")
        with_priority(other, "Their Student", Priority.HIGH)
        with_priority(abc, "Our Student", Priority.HIGH)
        abc.log_in_as(Role.ADMIN)

        assert listed(abc, priority="high") == ["Our Student"]

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)

        assert abc.client.get(LIST, {"priority": "high"}).status_code == (status.HTTP_403_FORBIDDEN)


# Home's "High priority Students" (ticket 12; spec stories 90-94).
class TestForHome:
    def test_hide_lost_leaves_lost_students_out_and_counts_the_rest(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        for number in range(4):
            with_priority(abc, f"Active {number}", Priority.HIGH, stage=Stage.COUNSELLING)
        with_priority(abc, "Gone", Priority.HIGH, stage=Stage.LOST)
        with_priority(abc, "Calm", Priority.NORMAL)

        response = abc.client.get(LIST, {"priority": "high", "hide_lost": "true"})

        body = response.json()
        assert body["count"] == 4
        assert "Gone" not in [row["name"] for row in body["results"]]

    def test_without_hide_lost_lost_students_are_listed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        with_priority(abc, "Gone", Priority.HIGH, stage=Stage.LOST)

        assert listed(abc, priority="high") == ["Gone"]

    def test_a_counsellor_gets_only_their_own(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        with_priority(abc, "Mine", Priority.HIGH, counsellor=me)
        with_priority(abc, "Theirs", Priority.HIGH, counsellor=other)

        assert listed(abc, priority="high", hide_lost="true", counsellor="me") == ["Mine"]

    def test_an_admin_gets_everyones(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        with_priority(abc, "Theirs", Priority.HIGH, counsellor=other)
        with_priority(abc, "Nobody's", Priority.HIGH)

        assert listed(abc, priority="high", hide_lost="true") == ["Nobody's", "Theirs"]

    @pytest.mark.parametrize("value", ["maybe", "yes please", *INJECTION_STRINGS])
    def test_a_bad_hide_lost_is_refused(self, abc: ConsultancyContext, value: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST, {"hide_lost": value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "hide_lost" in response.json()

    def test_another_offices_students_never_count(
        self, abc: ConsultancyContext, make_consultancy: ConsultancyFactory
    ) -> None:
        other = make_consultancy("XYZ Education", "xyzedu")
        with_priority(other, "Their Student", Priority.HIGH)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST, {"priority": "high", "hide_lost": "true"})

        assert response.json()["count"] == 0
