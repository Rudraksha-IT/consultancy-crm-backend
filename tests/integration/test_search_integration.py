"""The top bar's global search: Students and classes from any screen (spec 29a, issue 49)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from batches.models import Batch, BatchMembership
from modules.services import switch_modules
from modules.types import Module
from search.models import SearchAllowance
from search.services import SEARCHES_PER_MINUTE
from staff.models import Role, Staff
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs
from tests.integration.test_consultancy_status_integration import set_status
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)
from tests.support_access import open_as_platform_admin

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

    from tests.conftest import PlatformContext

SEARCH_URL = "/api/search/"
TOO_SHORT = {"q": ["Type at least 2 characters."]}
TOO_MANY = {"detail": "Too many searches. Wait a minute and try again."}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """An office with test-prep classes on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.BATCHES})
    return office


def student(
    office: ConsultancyContext,
    name: str,
    counsellor: Staff | None = None,
    stage: Stage = Stage.LEAD,
    **fields: str,
) -> Student:
    """A Student with this name; `fields` override the defaults."""
    return make_student(office, counsellor, stage, name=name, **fields)


def batch(
    office: ConsultancyContext, name: str, teacher: Staff, students: tuple[Student, ...] = ()
) -> Batch:
    """A class running today, with `students` in it."""
    today = timezone.localdate()
    with tenant_context(office.consultancy):
        made = Batch.objects.create(
            name=name,
            test_type="ielts",
            teacher=teacher,
            time_slot="7:00-8:30 AM",
            starts_on=today,
            ends_on=today + timedelta(days=30),
        )
        for member in students:
            BatchMembership.objects.create(batch=made, student=member)
    return made


def searched(
    office: ConsultancyContext | APIClient, query: dict[str, str] | None = None
) -> _MonkeyPatchedResponse:
    """GET the search with `q` (or the given query)."""
    client = office.client if isinstance(office, ConsultancyContext) else office
    return client.get(SEARCH_URL, query if query is not None else {})


def found(response: _MonkeyPatchedResponse) -> tuple[list[str], list[str]]:
    """The names of the Students and of the classes found, in order."""
    assert response.status_code == status.HTTP_200_OK, response.json()
    body = response.json()
    return [row["name"] for row in body["students"]], [row["name"] for row in body["batches"]]


class TestFindingStudents:
    def test_finds_students_by_name_email_or_phone_digits(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        student(abc, "Sita Karki", phone="9841234567", email="sita@gmail.test")
        student(abc, "Manisha Basnet", phone="9841022333", email="m.basnet@yahoo.test")
        student(abc, "Anish Neupane", phone="9803334444")

        assert found(searched(abc, {"q": "sita"}))[0] == ["Sita Karki"]
        assert found(searched(abc, {"q": "YAHOO"}))[0] == ["Manisha Basnet"]
        assert found(searched(abc, {"q": "98410-22 3"}))[0] == ["Manisha Basnet"]
        assert found(searched(abc, {"q": "(980) 333"}))[0] == ["Anish Neupane"]
        assert found(searched(abc, {"q": "nobody here"}))[0] == []

    def test_a_name_starting_with_the_search_comes_first_then_by_name(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        for name in ("Lisa Rai", "Sanjay Thapa", "Asha Sapkota", "Sabin Lama"):
            student(abc, name)

        assert found(searched(abc, {"q": "sa"}))[0] == [
            "Sabin Lama",
            "Sanjay Thapa",
            "Asha Sapkota",
            "Lisa Rai",
        ]

    def test_at_most_five_with_the_count_of_every_match(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for number in range(7):
            student(abc, f"Sita {number}")

        body = searched(abc, {"q": "sita"}).json()

        assert [row["name"] for row in body["students"]] == [f"Sita {n}" for n in range(5)]
        assert body["student_count"] == 7

    def test_a_row_has_the_stage_counsellor_and_phone_and_nothing_else(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        counsellor = named_staff(abc, "Ram Shrestha")
        sita = student(
            abc, "Sita Karki", counsellor=counsellor, stage=Stage.COUNSELLING, passport_number="X1"
        )
        student(abc, "Sita Unassigned")

        rows = searched(abc, {"q": "sita"}).json()["students"]

        assert rows == [
            {
                "kind": "student",
                "id": str(sita.public_id),
                "name": "Sita Karki",
                "phone": "9841000001",
                "stage": "counselling",
                "assigned_counsellor": {"id": counsellor.pk, "name": "Ram Shrestha"},
            },
            {
                "kind": "student",
                "id": rows[1]["id"],
                "name": "Sita Unassigned",
                "phone": "9841000001",
                "stage": "lead",
                "assigned_counsellor": None,
            },
        ]

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_every_student_role_finds_every_student(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        other = named_staff(abc, "Hari Other")
        student(abc, "Sita Karki", counsellor=other)
        student(abc, "Sita Lost", stage=Stage.LOST, lost_reason="Moved away")
        abc.log_in_as(role)

        assert found(searched(abc, {"q": "sita"}))[0] == ["Sita Karki", "Sita Lost"]

    @pytest.mark.parametrize("query", [*INJECTION_STRINGS, "%%", "__", "\\\\", "' OR 1=1 --"])
    def test_injection_shaped_searches_find_nothing_and_break_nothing(
        self, abc: ConsultancyContext, query: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student(abc, "Sita Karki")

        assert found(searched(abc, {"q": query})) == ([], [])


class TestFindingClasses:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR])
    def test_admins_and_counsellors_find_every_class_by_name(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        teacher = abc.add_staff(Role.TEACHER)
        morning = batch(abc, "IELTS Morning", teacher)
        batch(abc, "PTE Evening", teacher)
        abc.log_in_as(role)

        body = searched(abc, {"q": "ielts"}).json()

        assert body["batches"] == [
            {"id": morning.pk, "name": "IELTS Morning", "time_slot": "7:00-8:30 AM"}
        ]

    def test_at_most_five_classes(self, abc: ConsultancyContext) -> None:
        teacher = abc.add_staff(Role.TEACHER)
        for number in range(6):
            batch(abc, f"IELTS {number}", teacher)
        abc.log_in_as(Role.ADMIN)

        assert found(searched(abc, {"q": "ielts"}))[1] == [f"IELTS {n}" for n in range(5)]

    def test_front_desk_finds_students_but_no_classes(self, abc: ConsultancyContext) -> None:
        teacher = abc.add_staff(Role.TEACHER)
        batch(abc, "IELTS Morning", teacher)
        student(abc, "Ielts Hopeful")
        abc.log_in_as(Role.FRONT_DESK)

        assert found(searched(abc, {"q": "ielts"})) == (["Ielts Hopeful"], [])

    def test_no_classes_when_the_module_is_off_and_back_when_on(
        self, abc: ConsultancyContext
    ) -> None:
        teacher = abc.add_staff(Role.TEACHER)
        batch(abc, "IELTS Morning", teacher)
        abc.log_in_as(Role.ADMIN)

        switch_modules(abc.consultancy, set())
        assert found(searched(abc, {"q": "ielts"})) == ([], [])

        switch_modules(abc.consultancy, {Module.BATCHES})
        assert found(searched(abc, {"q": "ielts"})) == ([], ["IELTS Morning"])


class TestTeachers:
    def _classes(self, abc: ConsultancyContext) -> tuple[Staff, Student, Student]:
        """A logged-in Teacher with one Student in their class, and one in another Teacher's."""
        me = named_staff(abc, "Gita Teacher", Role.TEACHER)
        other = named_staff(abc, "Hari Teacher", Role.TEACHER)
        mine = student(abc, "Sita Mine", stage=Stage.APPLYING)
        theirs = student(abc, "Sita Theirs")
        batch(abc, "IELTS Morning", me, (mine,))
        batch(abc, "IELTS Evening", other, (theirs,))
        log_in(abc, me)
        return me, mine, theirs

    def test_a_teacher_finds_only_their_own_classes_and_students(
        self, abc: ConsultancyContext
    ) -> None:
        self._classes(abc)

        assert found(searched(abc, {"q": "sita"})) == (["Sita Mine"], [])
        assert found(searched(abc, {"q": "ielts"})) == ([], ["IELTS Morning"])

    def test_a_teachers_row_names_the_class_and_nothing_about_the_student(
        self, abc: ConsultancyContext
    ) -> None:
        me, mine, _ = self._classes(abc)
        with tenant_context(abc.consultancy):
            first = Batch.objects.get(name="IELTS Morning")
        batch(abc, "Zz Later class", me, (mine,))

        rows = searched(abc, {"q": "sita"}).json()["students"]

        assert rows == [
            {
                "kind": "class_student",
                "id": str(mine.public_id),
                "name": "Sita Mine",
                "batch": {"id": first.pk, "name": "IELTS Morning"},
            }
        ]

    def test_a_teacher_with_the_module_off_finds_nothing(self, abc: ConsultancyContext) -> None:
        self._classes(abc)
        switch_modules(abc.consultancy, set())

        assert found(searched(abc, {"q": "sita"})) == ([], [])


class TestRefusedInput:
    @pytest.mark.parametrize("query", ["a", " a ", ""])
    def test_fewer_than_two_characters_is_refused(
        self, abc: ConsultancyContext, query: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = searched(abc, {"q": query})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TOO_SHORT

    def test_no_search_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = searched(abc, {})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TOO_SHORT

    def test_more_than_a_hundred_characters_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = searched(abc, {"q": "a" * 101})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["q"]

    def test_a_hundred_characters_is_fine(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert searched(abc, {"q": "a" * 100}).status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("query", ["sita\nkarki", "sita\x00", "\tsita\x07"])
    def test_control_characters_are_refused(self, abc: ConsultancyContext, query: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = searched(abc, {"q": query})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["q"]

    def test_unknown_fields_are_refused_without_naming_them(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = searched(abc, {"q": "sita", "<b>role</b>": "admin"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert "role" not in response.content.decode()

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(SEARCH_URL, {"q": "sita"}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        response = searched(abc.new_client(), {"q": "sita"})

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestRateLimit:
    def test_too_many_searches_in_a_minute_are_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)
        student(abc, "Sita Karki")
        with tenant_context(abc.consultancy):
            SearchAllowance.objects.create(
                searcher=f"staff:{me.pk}",
                window_started_at=timezone.now(),
                used=SEARCHES_PER_MINUTE - 1,
            )

        assert searched(abc, {"q": "sita"}).status_code == status.HTTP_200_OK
        response = searched(abc, {"q": "sita"})

        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert response.json() == TOO_MANY

    def test_each_search_counts_even_a_refused_one(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)

        for _ in range(SEARCHES_PER_MINUTE):
            searched(abc, {"q": "a"})
        response = searched(abc, {"q": "sita"})

        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        with tenant_context(abc.consultancy):
            assert SearchAllowance.objects.get(searcher=f"staff:{me.pk}").used == (
                SEARCHES_PER_MINUTE
            )

    def test_the_limit_is_per_staff_member(self, abc: ConsultancyContext) -> None:
        busy = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            SearchAllowance.objects.create(
                searcher=f"staff:{busy.pk}",
                window_started_at=timezone.now(),
                used=SEARCHES_PER_MINUTE,
            )
        assert searched(abc, {"q": "sita"}).status_code == status.HTTP_429_TOO_MANY_REQUESTS

        abc.log_in_as(Role.COUNSELLOR)

        assert searched(abc, {"q": "sita"}).status_code == status.HTTP_200_OK

    def test_a_new_minute_starts_again(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            SearchAllowance.objects.create(
                searcher=f"staff:{me.pk}",
                window_started_at=timezone.now() - timedelta(seconds=61),
                used=SEARCHES_PER_MINUTE,
            )

        assert searched(abc, {"q": "sita"}).status_code == status.HTTP_200_OK
        with tenant_context(abc.consultancy):
            assert SearchAllowance.objects.get(searcher=f"staff:{me.pk}").used == 1

    def test_the_refusal_is_logged_as_a_warning_without_the_search(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        me = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            SearchAllowance.objects.create(
                searcher=f"staff:{me.pk}", window_started_at=timezone.now(), used=99
            )

        searched(abc, {"q": "9841234567"})

        warning = json_logs.with_message("Search limit reached")
        assert [line["level"] for line in warning] == ["WARNING"]
        assert "9841234567" not in json_logs.text()


class TestSupportAccess:
    def test_a_platform_admin_inside_finds_like_an_admin(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        student(abc, "Sita Karki")
        teacher = abc.add_staff(Role.TEACHER)
        batch(abc, "Sita's class", teacher)
        visitor = open_as_platform_admin(platform_site, abc).client

        assert found(searched(visitor, {"q": "sita"})) == (["Sita Karki"], ["Sita's class"])


class TestRestrictedConsultancy:
    @pytest.mark.parametrize("value", ["past_due", "suspended"])
    def test_a_read_only_office_can_still_search(self, abc: ConsultancyContext, value: str) -> None:
        abc.log_in_as(Role.ADMIN)
        student(abc, "Sita Karki")
        set_status(abc, value)

        assert found(searched(abc, {"q": "sita"}))[0] == ["Sita Karki"]


class TestLogging:
    def test_the_search_text_is_never_logged(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        searched(abc, {"q": "sita@gmail.test"})

        assert "sita@gmail.test" not in json_logs.text()
