"""Task priority, "Only me" Tasks, the order of My Tasks and the Tasks count (issue 50).

Spec 77, 77a, 77b, 79, 79a: a Task is High, Normal (by default) or Low priority; a Task someone
gives themselves can be "Only me", which nobody else ever sees, not even an Admin or a Platform
Admin in Support Access, and which is never about a Student. "To do" is ordered overdue first,
then by priority, then by due date, and the menu counts my open Tasks due today or overdue.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from notifications.models import Notification
from staff.models import Role, Staff
from students.models import Student
from tasks.models import Task, TaskPriority
from tasks.serializers import DueByField
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    make_student,
    named_staff,
)
from tests.support_access import Visit, open_as_platform_admin

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

TASKS_URL = "/api/tasks/"
COUNT_URL = "/api/tasks/count/"
ALL_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER)
VIEWS = ("mine", "given", "done")
PRIORITY_MESSAGE = {"priority": ["Choose High, Normal or Low."]}
ONLY_ME_MESSAGE = {"only_me": ["Say whether only you should see it."]}
ONLY_ME_ASSIGNEE_MESSAGE = {"assignee": ["An “Only me” task can only be for you."]}
ONLY_ME_STUDENT_MESSAGE = {"student": ["An “Only me” task can't be about a Student."]}
PRIVATE_TITLE = "Renew my driving licence"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def day(offset: int) -> date:
    """Today in the office's time zone, moved by `offset` days."""
    return timezone.localdate() + timedelta(days=offset)


def create(
    office: ConsultancyContext, assignee: Staff, /, **changes: object
) -> _MonkeyPatchedResponse:
    """POST a new Task; `changes` override the body."""
    body = {
        "title": "Call Sita about her IELTS score",
        "due_on": day(1).isoformat(),
        "assignee": assignee.pk,
        **changes,
    }
    return office.client.post(TASKS_URL, body, format="json")


def make_task(
    office: ConsultancyContext,
    assignee: Staff,
    by: Staff | None = None,
    student: Student | None = None,
    **fields: object,
) -> Task:
    """Store a Task directly."""
    details: dict[str, object] = {"title": "Collect bank statement", "due_on": day(1), **fields}
    giver = by or assignee
    with tenant_context(office.consultancy):
        return Task.objects.create(
            assignee=assignee,
            created_by=giver,
            created_by_name=giver.name,
            student=student,
            **details,
        )


def private_task(
    office: ConsultancyContext, owner: Staff, student: Student | None = None, **fields: object
) -> Task:
    """An "Only me" Task `owner` gave themselves."""
    return make_task(office, owner, owner, student, title=PRIVATE_TITLE, only_me=True, **fields)


def titles(office: ConsultancyContext, **query: str) -> list[str]:
    """The titles on a view of My Tasks, in order."""
    response = office.client.get(TASKS_URL, query)
    assert response.status_code == status.HTTP_200_OK, response.content
    return [task["title"] for task in response.json()["results"]]


def due_count(office: ConsultancyContext) -> int:
    """The Tasks count beside Tasks in the menu."""
    response = office.client.get(COUNT_URL)
    assert response.status_code == status.HTTP_200_OK, response.content
    count: int = response.json()["count"]
    return count


class TestPriority:
    def test_a_new_task_is_normal_and_not_only_me(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["priority"] == "normal"
        assert response.json()["only_me"] is False
        with tenant_context(abc.consultancy):
            task = Task.objects.get()
        assert (task.priority, task.only_me) == (TaskPriority.NORMAL, False)

    @pytest.mark.parametrize("priority", ["high", "normal", "low"])
    def test_each_priority_is_kept(self, abc: ConsultancyContext, priority: str) -> None:
        me = abc.log_in_as(Role.FRONT_DESK)

        response = create(abc, me, priority=priority)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["priority"] == priority
        listed = abc.client.get(TASKS_URL).json()["results"]
        assert [task["priority"] for task in listed] == [priority]

    @pytest.mark.parametrize(
        "priority",
        [
            "urgent",
            "High",
            "HIGH",
            " high",
            "",
            None,
            1,
            0,
            True,
            ["high"],
            {"p": "high"},
            *INJECTION_STRINGS,
        ],
    )
    def test_any_other_priority_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, priority: object
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me, priority=priority)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == PRIORITY_MESSAGE
        with tenant_context(abc.consultancy):
            assert Task.objects.count() == 0

    def test_priority_cant_be_changed_after(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        task = make_task(abc, me)

        response = abc.client.patch(f"{TASKS_URL}{task.pk}/", {"priority": "high"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}

    def test_the_database_refuses_an_unknown_priority(self, abc: ConsultancyContext) -> None:
        me = abc.add_staff(Role.COUNSELLOR)
        with pytest.raises(IntegrityError), transaction.atomic():
            make_task(abc, me, priority="urgent")


class TestOnlyMe:
    def test_a_task_i_give_myself_can_be_only_me(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me, only_me=True, priority="low")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["only_me"] is True
        assert response.json()["student"] is None
        with tenant_context(abc.consultancy):
            assert Task.objects.get().only_me is True

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_every_role_can_keep_one_for_themselves(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        me = abc.log_in_as(role)

        response = create(abc, me, only_me=True)

        assert response.status_code == status.HTTP_201_CREATED
        assert titles(abc) == ["Call Sita about her IELTS score"]

    def test_it_cant_be_given_to_someone_else(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        other = named_staff(abc, "Bikash Thapa")

        response = create(abc, other, only_me=True)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ONLY_ME_ASSIGNEE_MESSAGE
        with tenant_context(abc.consultancy):
            assert Task.objects.count() == 0
            assert Notification.objects.count() == 0

    def test_it_cant_be_about_a_student(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me)

        response = create(abc, me, only_me=True, student=student.pk)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ONLY_ME_STUDENT_MESSAGE
        with tenant_context(abc.consultancy):
            assert Task.objects.count() == 0

    @pytest.mark.parametrize("only_me", ["true", "yes", 1, 0, None, [], {"a": 1}])
    def test_only_me_must_be_true_or_false(self, abc: ConsultancyContext, only_me: object) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me, only_me=only_me)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ONLY_ME_MESSAGE
        with tenant_context(abc.consultancy):
            assert Task.objects.count() == 0

    def test_false_is_an_ordinary_task(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        other = named_staff(abc, "Bikash Thapa")

        response = create(abc, other, only_me=False)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["only_me"] is False

    @pytest.mark.parametrize(
        "body", [{"only_me": False}, {"assignee": 1}, {"done": True, "assignee": 1}]
    )
    def test_it_cant_be_reassigned_or_made_public(
        self, abc: ConsultancyContext, body: dict[str, object]
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        task = private_task(abc, me)

        response = abc.client.patch(f"{TASKS_URL}{task.pk}/", body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        with tenant_context(abc.consultancy):
            stored = Task.objects.get()
        assert (stored.only_me, stored.assignee_id, stored.is_done) == (True, me.pk, False)

    def test_the_owner_ticks_it_and_finds_it_done(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        task = private_task(abc, me)

        response = abc.client.patch(f"{TASKS_URL}{task.pk}/", {"done": True}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["only_me"] is True
        assert titles(abc, view="done") == [PRIVATE_TITLE]

    def test_the_owner_is_not_notified_that_they_gave_it(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        create(abc, me, only_me=True, due_on=day(0).isoformat())

        bell = abc.client.get("/api/notifications/").json()
        assert [item["kind"] for item in bell["results"]] == ["due_today"]

    def test_the_database_refuses_one_for_someone_else(self, abc: ConsultancyContext) -> None:
        owner = abc.add_staff(Role.COUNSELLOR)
        other = named_staff(abc, "Bikash Thapa")
        with pytest.raises(IntegrityError), transaction.atomic():
            make_task(abc, other, by=owner, only_me=True)

    def test_the_database_refuses_one_about_a_student(self, abc: ConsultancyContext) -> None:
        owner = abc.add_staff(Role.COUNSELLOR)
        student = make_student(abc, owner)
        with pytest.raises(IntegrityError), transaction.atomic():
            private_task(abc, owner, student=student)


class TestOnlyMeIsInvisibleToEveryoneElse:
    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_no_other_role_lists_counts_or_ticks_it(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        owner = named_staff(abc, "Bikash Thapa")
        open_one = private_task(abc, owner, due_on=day(-1))
        private_task(abc, owner, is_done=True, done_at=timezone.now())
        other = abc.log_in_as(role)
        make_task(abc, other, by=other, title="Mine")

        listed = [titles(abc, view=view) for view in VIEWS]
        ticked = abc.client.patch(f"{TASKS_URL}{open_one.pk}/", {"done": True}, format="json")

        assert listed == [["Mine"], [], []]
        assert due_count(abc) == 0
        assert ticked.status_code == status.HTTP_404_NOT_FOUND
        assert PRIVATE_TITLE not in ticked.content.decode()
        bell = abc.client.get("/api/notifications/")
        assert PRIVATE_TITLE not in bell.content.decode()
        with tenant_context(abc.consultancy):
            assert Task.objects.get(pk=open_one.pk).is_done is False

    def test_another_admin_cant_reach_it_even_by_its_id(self, abc: ConsultancyContext) -> None:
        owner = abc.add_staff(Role.ADMIN, email="owner@abcedu.test")
        task = private_task(abc, owner)
        abc.log_in_as(Role.ADMIN)

        responses = [
            abc.client.patch(f"{TASKS_URL}{task.pk}/", {"done": True}, format="json"),
            abc.client.patch(f"{TASKS_URL}{task.pk}/", {"done": False}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2

    def test_the_owner_sees_it_with_their_other_tasks(self, abc: ConsultancyContext) -> None:
        owner = abc.log_in_as(Role.COUNSELLOR)
        private_task(abc, owner, due_on=day(0))
        make_task(abc, owner, title="Public", due_on=day(0))

        assert sorted(titles(abc)) == sorted([PRIVATE_TITLE, "Public"])
        assert due_count(abc) == 2

    def test_a_platform_admin_in_support_access_never_sees_it(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        owner = abc.add_staff(Role.COUNSELLOR)
        task = private_task(abc, owner, due_on=day(-1))
        visit: Visit = open_as_platform_admin(platform_site, abc)

        listed = [visit.client.get(TASKS_URL, {"view": view}) for view in VIEWS]
        ticked = visit.client.patch(f"{TASKS_URL}{task.pk}/", {"done": True}, format="json")
        count = visit.client.get(COUNT_URL)

        assert [r.json()["count"] for r in listed] == [0, 0, 0]
        assert ticked.status_code == status.HTTP_404_NOT_FOUND
        assert count.json() == {"count": 0}
        with tenant_context(abc.consultancy):
            assert Task.objects.get(pk=task.pk).is_done is False

    def test_a_platform_admin_cant_make_one(
        self, platform_site: PlatformContext, abc: ConsultancyContext
    ) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)
        visit: Visit = open_as_platform_admin(platform_site, abc)

        response = visit.client.post(
            TASKS_URL,
            {
                "title": "Check the fees",
                "due_on": day(1).isoformat(),
                "assignee": counsellor.pk,
                "only_me": True,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ONLY_ME_ASSIGNEE_MESSAGE

    def test_a_students_tasks_never_include_one(self, abc: ConsultancyContext) -> None:
        owner = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, owner)
        private_task(abc, owner)

        tasks = abc.client.get(f"/api/students/{student.pk}/tasks/").json()
        profile = abc.client.get(f"/api/students/{student.pk}/").json()

        assert (tasks["count"], profile["open_tasks"]) == (0, 0)


class TestOrder:
    def test_to_do_is_overdue_first_then_priority_then_due_date(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        for title, due, priority in [
            ("Low later", 1, "low"),
            ("Normal later", 1, "normal"),
            ("Overdue low", -2, "low"),
            ("High far", 5, "high"),
            ("Normal today", 0, "normal"),
            ("Overdue high", -1, "high"),
            ("Overdue normal old", -3, "normal"),
            ("Overdue normal new", -1, "normal"),
        ]:
            make_task(abc, me, title=title, due_on=day(due), priority=priority)

        assert titles(abc) == [
            "Overdue high",
            "Overdue normal old",
            "Overdue normal new",
            "Overdue low",
            "High far",
            "Normal today",
            "Normal later",
            "Low later",
        ]

    def test_given_to_others_keeps_its_order(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.FRONT_DESK)
        other = named_staff(abc, "Bikash Thapa")
        make_task(abc, other, by=me, title="Later high", due_on=day(3), priority="high")
        make_task(abc, other, by=me, title="Soon low", due_on=day(1), priority="low")

        assert titles(abc, view="given") == ["Soon low", "Later high"]


class TestPriorityFilter:
    @pytest.mark.parametrize(
        ("priority", "expected"),
        [("high", ["Urgent"]), ("normal", ["Usual"]), ("low", ["Someday"])],
    )
    def test_to_do_by_priority(
        self, abc: ConsultancyContext, priority: str, expected: list[str]
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        make_task(abc, me, title="Urgent", priority="high")
        make_task(abc, me, title="Usual")
        make_task(abc, me, title="Someday", priority="low")

        assert titles(abc, priority=priority) == expected

    def test_the_other_views_filter_too(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        other = named_staff(abc, "Bikash Thapa")
        make_task(abc, other, by=me, title="Given high", priority="high")
        make_task(abc, other, by=me, title="Given low", priority="low")
        make_task(abc, me, title="Done high", priority="high", is_done=True, done_at=timezone.now())
        make_task(abc, me, title="Done low", priority="low", is_done=True, done_at=timezone.now())

        assert titles(abc, view="given", priority="high") == ["Given high"]
        assert titles(abc, view="done", priority="low") == ["Done low"]

    def test_an_empty_priority_filter_means_any(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        make_task(abc, me, title="Urgent", priority="high")
        make_task(abc, me, title="Someday", priority="low")

        assert titles(abc, priority="") == ["Urgent", "Someday"]

    @pytest.mark.parametrize("priority", ["urgent", "High", " low", "1", "<b>high</b>"])
    def test_a_bad_priority_filter_is_refused(self, abc: ConsultancyContext, priority: str) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.get(TASKS_URL, {"priority": priority})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == PRIORITY_MESSAGE


class TestCount:
    def test_counts_my_open_tasks_due_today_or_overdue(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        other = named_staff(abc, "Bikash Thapa")
        make_task(abc, me, title="Overdue", due_on=day(-4))
        make_task(abc, me, title="Today", due_on=day(0), priority="low")
        private_task(abc, me, due_on=day(0))
        make_task(abc, me, title="Tomorrow", due_on=day(1))
        make_task(abc, me, title="Done", due_on=day(-1), is_done=True, done_at=timezone.now())
        make_task(abc, other, by=me, title="Given", due_on=day(-1))
        make_task(abc, other, title="Theirs", due_on=day(0))

        assert abc.client.get(COUNT_URL).json() == {"count": 3}

    def test_it_follows_changes(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.FRONT_DESK)
        assert due_count(abc) == 0

        created = create(abc, me, due_on=day(0).isoformat()).json()
        assert due_count(abc) == 1

        abc.client.patch(f"{TASKS_URL}{created['id']}/", {"done": True}, format="json")
        assert due_count(abc) == 0

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_every_role_has_one(self, abc: ConsultancyContext, role: Role) -> None:
        me = abc.log_in_as(role)
        make_task(abc, me, due_on=day(0))

        assert due_count(abc) == 1

    def test_only_get_is_allowed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        responses = [
            abc.client.post(COUNT_URL, {}, format="json"),
            abc.client.put(COUNT_URL, {}, format="json"),
            abc.client.patch(COUNT_URL, {}, format="json"),
            abc.client.delete(COUNT_URL),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_405_METHOD_NOT_ALLOWED] * 4

    def test_an_unknown_query_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(COUNT_URL, {"assignee": "1"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        response = abc.client.get(COUNT_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestLogging:
    def test_an_only_me_task_logs_ids_only(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        create(abc, me, only_me=True, title="Secret private title")

        [task_id] = [t.pk for t in _stored(abc)]
        json_logs.only(f"Task created: task_id={task_id}, assignee_id={me.pk}, by_staff_id={me.pk}")
        assert "Secret private" not in json_logs.text()


def _stored(office: ConsultancyContext) -> list[Task]:
    with tenant_context(office.consultancy):
        return list(Task.objects.order_by("pk"))


class TestDueByFilter:
    """The Homes' "Your tasks due soon" asks only for Tasks due by a day (spec 22a, 79a)."""

    def test_only_tasks_due_by_the_day_in_the_same_order(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        make_task(abc, me, title="High far", due_on=day(9), priority="high")
        make_task(abc, me, title="Normal soon", due_on=day(2))
        make_task(abc, me, title="Low today", due_on=day(0), priority="low")
        make_task(abc, me, title="Overdue", due_on=day(-1), priority="low")

        assert titles(abc, due_by=day(2).isoformat()) == ["Overdue", "Normal soon", "Low today"]

    @pytest.mark.parametrize(
        "due_by", ["tomorrow", "2026-13-01", "2026-02-30", "20260101", "1/2/2026", "<b>"]
    )
    def test_a_bad_day_is_refused(self, abc: ConsultancyContext, due_by: str) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.get(TASKS_URL, {"due_by": due_by})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"due_by": ["Enter a date as YYYY-MM-DD."]}

    def test_the_field_writes_a_day_as_text(self) -> None:
        assert DueByField().to_representation(date(2027, 2, 1)) == "2027-02-01"
