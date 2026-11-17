"""Tasks: create, My Tasks with overdue ones marked, mark done, a Student's open Tasks.

Spec 77-79, 81-82; issue 21.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from staff.models import Role, Staff, StaffStatus
from students.models import Stage, Student
from tasks.models import Task
from tasks.serializers import AssigneeField, DoneField, DueOnField, StudentLinkField
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    NOT_FOR_TEACHERS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

TASKS_URL = "/api/tasks/"
ASSIGNEES_URL = "/api/tasks/assignees/"
ALL_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER)
TITLE_MESSAGE = {"title": ["Say what needs doing."]}
DUE_ON_MESSAGE = {"due_on": ["Enter a due date from today to 5 years ahead."]}
ASSIGNEE_MESSAGE = {"assignee": ["Choose who it's for from the list."]}
STUDENT_MESSAGE = {"student": ["Choose a Student from the list."]}
DONE_MESSAGE = {"done": ["Say whether it's done."]}
CANT_TICK = {"detail": "Only the person it's for, who gave it, or an Admin can tick it."}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def task_url(task: Task | int) -> str:
    """One Task."""
    pk = task if isinstance(task, int) else task.pk
    return f"/api/tasks/{pk}/"


def student_tasks_url(student: Student | int) -> str:
    """A Student's open Tasks."""
    pk = student if isinstance(student, int) else student.pk
    return f"/api/students/{pk}/tasks/"


def day(offset: int) -> date:
    """Today in the office's time zone, moved by `offset` days."""
    return timezone.localdate() + timedelta(days=offset)


def good_body(assignee: Staff, /, **changes: object) -> dict[str, object]:
    """A Task the server accepts; `changes` override it."""
    return {
        "title": "Call Sita about her IELTS score",
        "due_on": day(1).isoformat(),
        "assignee": assignee.pk,
        **changes,
    }


def create(
    office: ConsultancyContext, assignee: Staff, /, **changes: object
) -> _MonkeyPatchedResponse:
    """POST a new Task."""
    return office.client.post(TASKS_URL, good_body(assignee, **changes), format="json")


def make_task(
    office: ConsultancyContext,
    assignee: Staff,
    by: Staff | None = None,
    student: Student | None = None,
    **fields: object,
) -> Task:
    """Store a Task directly."""
    details: dict[str, object] = {"title": "Collect bank statement", "due_on": day(1), **fields}
    with tenant_context(office.consultancy):
        return Task.objects.create(
            assignee=assignee,
            created_by=by or assignee,
            created_by_name=(by or assignee).name,
            student=student,
            **details,
        )


def stored_tasks(office: ConsultancyContext) -> list[Task]:
    """Every stored Task, oldest first."""
    with tenant_context(office.consultancy):
        return list(Task.objects.order_by("pk"))


def listed(office: ConsultancyContext, view: str = "mine") -> list[str]:
    """The titles on one view of My Tasks, in order."""
    body = office.client.get(TASKS_URL, {"view": view}).json()
    return [task["title"] for task in body["results"]]


def tick(office: ConsultancyContext, task: Task, *, done: object = True) -> _MonkeyPatchedResponse:
    """Mark a Task done (or not)."""
    return office.client.patch(task_url(task), {"done": done}, format="json")


class TestCreating:
    def test_front_desk_gives_a_counsellor_a_task_about_a_student(
        self, abc: ConsultancyContext
    ) -> None:
        desk = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        counsellor = named_staff(abc, "Bikash Thapa")
        log_in(abc, desk)
        student = make_student(abc, counsellor, name="Manisha Rai")

        response = create(abc, counsellor, student=student.pk)

        assert response.status_code == status.HTTP_201_CREATED
        [task] = stored_tasks(abc)
        assert response.json() == {
            "id": task.pk,
            "title": "Call Sita about her IELTS score",
            "due_on": day(1).isoformat(),
            "priority": "normal",
            "only_me": False,
            "timing": "upcoming",
            "is_done": False,
            "done_at": None,
            "assignee": {"id": counsellor.pk, "name": "Bikash Thapa"},
            "created_by": {"id": desk.pk, "name": "Ramesh KC"},
            "student": {"id": student.pk, "name": "Manisha Rai"},
            "can_mark_done": True,
        }
        assert task.created_by_id == desk.pk

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_every_role_creates_a_task_for_themselves(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        me = abc.log_in_as(role)

        response = create(abc, me)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["student"] is None

    def test_a_task_can_be_given_to_any_active_role(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)
        teacher = named_staff(abc, "Gita Lama", Role.TEACHER)

        assert create(abc, teacher).status_code == status.HTTP_201_CREATED

    def test_due_today_is_accepted_and_shown_as_today(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me, due_on=day(0).isoformat())

        assert response.json()["timing"] == "today"

    def test_a_student_of_any_stage_can_be_linked(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, stage=Stage.LOST, lost_reason="Went elsewhere")

        assert create(abc, me, student=student.pk).status_code == status.HTTP_201_CREATED

    def test_the_student_can_be_null(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        assert create(abc, me, student=None).json()["student"] is None

    def test_the_title_is_trimmed(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        assert create(abc, me, title="  Call Sita  ").json()["title"] == "Call Sita"

    def test_a_teacher_cant_link_a_student(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.TEACHER)
        student = make_student(abc)

        response = create(abc, me, student=student.pk)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS
        assert stored_tasks(abc) == []

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        other = named_staff(abc, "Bikash Thapa")

        create(abc, other, title="Secret task title")

        [task] = stored_tasks(abc)
        entry = json_logs.only(
            f"Task created: task_id={task.pk}, assignee_id={other.pk}, by_staff_id={me.pk}"
        )
        assert entry["level"] == "INFO"
        assert "Secret task" not in json_logs.text()
        assert "Bikash" not in json_logs.text()


class TestRefusedInput:
    @pytest.mark.parametrize("title", ["", "   ", None, 12, True, ["Call"], {"t": 1}])
    def test_a_missing_or_wrong_title_is_refused(
        self, abc: ConsultancyContext, title: object
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me, title=title)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TITLE_MESSAGE
        assert stored_tasks(abc) == []

    def test_an_over_long_title_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        refused = create(abc, me, title="x" * 201)
        accepted = create(abc, me, title="x" * 200)

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert refused.json() == {"title": ["Use at most 200 characters."]}
        assert accepted.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("title", ["Call\nSita", "Call\x00", "Call\x1b[31m"])
    def test_control_characters_in_the_title_are_refused(
        self, abc: ConsultancyContext, title: str
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me, title=title)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["title"]

    @pytest.mark.parametrize(
        "due_on", ["2026-02-30", "02/11/2026", "", "tomorrow", 20261102, None, True, ["x"]]
    )
    def test_a_bad_due_date_is_refused(self, abc: ConsultancyContext, due_on: object) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me, due_on=due_on)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == DUE_ON_MESSAGE

    def test_a_due_date_in_the_past_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me, due_on=day(-1).isoformat())

        assert response.json() == DUE_ON_MESSAGE

    def test_a_due_date_more_than_five_years_ahead_is_refused(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        limit = DueOnField.latest(timezone.localdate())

        refused = create(abc, me, due_on=(limit + timedelta(days=1)).isoformat())
        accepted = create(abc, me, due_on=limit.isoformat())

        assert refused.json() == DUE_ON_MESSAGE
        assert accepted.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("assignee", [0, -1, 999_999, "1", 1.5, True, None, [1], {"id": 1}])
    def test_a_bad_assignee_is_refused(self, abc: ConsultancyContext, assignee: object) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me, assignee=assignee)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ASSIGNEE_MESSAGE

    @pytest.mark.parametrize("left", [StaffStatus.INVITED, StaffStatus.DEACTIVATED])
    def test_someone_not_active_cant_be_given_a_task(
        self, abc: ConsultancyContext, left: StaffStatus
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        other = named_staff(abc, "Bikash Thapa")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=other.pk).update(status=left)

        response = create(abc, me, assignee=other.pk)

        assert response.json() == ASSIGNEE_MESSAGE
        assert stored_tasks(abc) == []

    @pytest.mark.parametrize("student", [0, 999_999, "1", 1.5, True, [1], {"id": 1}])
    def test_a_bad_student_is_refused(self, abc: ConsultancyContext, student: object) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me, student=student)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == STUDENT_MESSAGE

    @pytest.mark.parametrize("missing", ["title", "due_on", "assignee"])
    def test_the_title_due_date_and_assignee_are_required(
        self, abc: ConsultancyContext, missing: str
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        body = good_body(me)
        del body[missing]

        response = abc.client.post(TASKS_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == [missing]

    @pytest.mark.parametrize("field", ["created_by", "is_done", "done_at", "id", "timing"])
    def test_an_unknown_field_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, field: str
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.post(TASKS_URL, {**good_body(me), field: 1}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_tasks(abc) == []

    def test_a_body_that_isnt_an_object_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.post(TASKS_URL, [good_body(me)], format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_tasks(abc) == []

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.post(TASKS_URL, good_body(me))

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("title", INJECTION_STRINGS)
    def test_injection_shaped_titles_are_kept_as_plain_text(
        self, abc: ConsultancyContext, title: str
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        response = create(abc, me, title=title)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["title"] == title
        [task] = stored_tasks(abc)
        assert task.title == title


class TestMyTasks:
    def test_to_do_lists_my_open_tasks_soonest_first(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        other = named_staff(abc, "Anita Rai")
        make_task(abc, me, title="Later", due_on=day(5))
        make_task(abc, me, title="Overdue", due_on=day(-2))
        make_task(abc, me, title="Today", due_on=day(0))
        make_task(abc, me, title="Done already", is_done=True, done_at=timezone.now())
        make_task(abc, other, title="Someone else's")
        log_in(abc, me)

        assert listed(abc) == ["Overdue", "Today", "Later"]

    def test_mine_is_the_default_view(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        make_task(abc, me, title="Mine")
        log_in(abc, me)

        body = abc.client.get(TASKS_URL).json()

        assert [task["title"] for task in body["results"]] == ["Mine"]

    def test_overdue_and_today_are_marked(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        make_task(abc, me, title="Overdue", due_on=day(-1))
        make_task(abc, me, title="Today", due_on=day(0))
        make_task(abc, me, title="Later", due_on=day(1))
        log_in(abc, me)

        results = abc.client.get(TASKS_URL).json()["results"]

        assert [task["timing"] for task in results] == ["overdue", "today", "upcoming"]

    def test_given_to_others_lists_what_i_gave_open_first(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        other = named_staff(abc, "Bikash Thapa")
        make_task(abc, other, me, title="Done one", is_done=True, done_at=timezone.now())
        make_task(abc, other, me, title="Later one", due_on=day(4))
        make_task(abc, other, me, title="Soon one", due_on=day(1))
        make_task(abc, me, me, title="For myself")
        make_task(abc, other, other, title="Not from me")
        log_in(abc, me)

        assert listed(abc, "given") == ["Soon one", "Later one", "Done one"]

    def test_done_lists_my_finished_tasks_latest_first(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        now = timezone.now()
        make_task(abc, me, title="Earlier", is_done=True, done_at=now - timedelta(hours=3))
        make_task(abc, me, title="Latest", is_done=True, done_at=now)
        make_task(abc, me, title="Open")
        log_in(abc, me)

        assert listed(abc, "done") == ["Latest", "Earlier"]
        done = abc.client.get(TASKS_URL, {"view": "done"}).json()["results"][0]
        assert done["timing"] == "done"
        assert done["done_at"] is not None

    def test_the_list_is_paged(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        for number in range(26):
            make_task(abc, me, title=f"Task {number}", due_on=day(number))
        log_in(abc, me)

        first = abc.client.get(TASKS_URL).json()
        second = abc.client.get(TASKS_URL, {"page": 2}).json()

        assert (first["count"], first["page"], first["page_size"]) == (26, 1, 25)
        assert len(first["results"]) == 25
        assert [task["title"] for task in second["results"]] == ["Task 25"]
        assert abc.client.get(TASKS_URL, {"page": 3}).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        "query",
        [{"view": "all"}, {"view": "MINE"}, {"page": "0"}, {"page": "x"}, {"assignee": "1"}],
    )
    def test_bad_filters_are_refused(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.get(TASKS_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_teacher_sees_their_tasks_without_the_student(self, abc: ConsultancyContext) -> None:
        teacher = named_staff(abc, "Gita Lama", Role.TEACHER)
        counsellor = named_staff(abc, "Bikash Thapa")
        make_task(abc, teacher, counsellor, make_student(abc, name="Kritika Shah"))
        log_in(abc, teacher)

        response = abc.client.get(TASKS_URL)

        [task] = response.json()["results"]
        assert task["student"] is None
        assert "Kritika" not in response.content.decode()

    def test_deactivated_staff_keep_their_name_on_past_tasks(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        leaver = named_staff(abc, "Bikash Thapa")
        make_task(abc, leaver, me, title="Given to the leaver")
        make_task(abc, me, leaver, title="From the leaver")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=leaver.pk).update(status=StaffStatus.DEACTIVATED)
        log_in(abc, me)

        [given] = abc.client.get(TASKS_URL, {"view": "given"}).json()["results"]
        [mine] = abc.client.get(TASKS_URL).json()["results"]

        assert given["assignee"] == {"id": leaver.pk, "name": "Bikash Thapa"}
        assert mine["created_by"] == {"id": leaver.pk, "name": "Bikash Thapa"}


class TestMarkingDone:
    def test_the_assignee_marks_it_done_and_reopens_it(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        task = make_task(abc, me)

        done = tick(abc, task)
        reopened = tick(abc, task, done=False)

        assert done.status_code == status.HTTP_200_OK
        assert done.json()["is_done"] is True
        assert done.json()["timing"] == "done"
        assert done.json()["done_at"] is not None
        assert reopened.json()["is_done"] is False
        assert reopened.json()["done_at"] is None
        assert listed(abc) == ["Collect bank statement"]

    def test_a_done_task_leaves_to_do_and_appears_in_done(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        task = make_task(abc, me)

        tick(abc, task)

        assert listed(abc) == []
        assert listed(abc, "done") == ["Collect bank statement"]

    def test_marking_done_twice_keeps_the_first_time(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        task = make_task(abc, me)

        first = tick(abc, task).json()["done_at"]
        second = tick(abc, task).json()["done_at"]

        assert first == second

    def test_whoever_gave_it_can_tick_it(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.FRONT_DESK)
        task = make_task(abc, named_staff(abc, "Bikash Thapa"), me)

        assert tick(abc, task).status_code == status.HTTP_200_OK

    def test_an_admin_can_tick_anyones_task(self, abc: ConsultancyContext) -> None:
        counsellor = named_staff(abc, "Bikash Thapa")
        task = make_task(abc, counsellor)
        abc.log_in_as(Role.ADMIN)

        assert tick(abc, task).status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_anyone_else_is_refused(self, abc: ConsultancyContext, role: Role) -> None:
        counsellor = named_staff(abc, "Bikash Thapa")
        task = make_task(abc, counsellor)
        abc.log_in_as(role)

        response = tick(abc, task)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == CANT_TICK
        assert stored_tasks(abc)[0].is_done is False

    @pytest.mark.parametrize("done", ["true", 1, 0, None, [True], {"v": True}])
    def test_done_must_be_true_or_false(self, abc: ConsultancyContext, done: object) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        task = make_task(abc, me)

        response = tick(abc, task, done=done)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == DONE_MESSAGE

    def test_done_is_required(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        task = make_task(abc, me)

        response = abc.client.patch(task_url(task), {}, format="json")

        assert response.json() == DONE_MESSAGE

    @pytest.mark.parametrize("field", ["title", "assignee", "due_on", "created_by"])
    def test_nothing_but_done_can_be_changed(self, abc: ConsultancyContext, field: str) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        task = make_task(abc, me)

        response = abc.client.patch(task_url(task), {"done": True, field: 1}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_tasks(abc)[0].is_done is False

    def test_an_unknown_task_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.patch(task_url(999_999), {"done": True}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        task = make_task(abc, me, title="Secret task title")

        tick(abc, task)
        tick(abc, task, done=False)

        json_logs.only(f"Task marked done: task_id={task.pk}, by_staff_id={me.pk}")
        json_logs.only(f"Task reopened: task_id={task.pk}, by_staff_id={me.pk}")
        assert "Secret task" not in json_logs.text()


class TestCanMarkDone:
    def test_the_answer_says_who_may_tick_it(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        counsellor = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, counsellor)
        make_task(abc, counsellor, counsellor, student, title="Not mine")
        make_task(abc, counsellor, me, student, title="Given by me")
        log_in(abc, me)

        results = abc.client.get(student_tasks_url(student)).json()["results"]

        assert {task["title"]: task["can_mark_done"] for task in results} == {
            "Not mine": False,
            "Given by me": True,
        }


class TestStudentTasks:
    def test_the_profile_lists_the_students_open_tasks(self, abc: ConsultancyContext) -> None:
        counsellor = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, counsellor)
        other = make_student(abc, counsellor, name="Aarav Sharma")
        make_task(abc, counsellor, student=student, title="Later", due_on=day(3))
        make_task(abc, counsellor, student=student, title="Overdue", due_on=day(-1))
        make_task(
            abc, counsellor, student=student, title="Done", is_done=True, done_at=timezone.now()
        )
        make_task(abc, counsellor, student=other, title="Someone else's")
        make_task(abc, counsellor, title="No student")
        abc.log_in_as(Role.FRONT_DESK)

        body = abc.client.get(student_tasks_url(student)).json()

        assert [task["title"] for task in body["results"]] == ["Overdue", "Later"]
        assert body["count"] == 2

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_everyone_who_sees_students_sees_them(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        counsellor = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, counsellor)
        make_task(abc, counsellor, student=student)
        abc.log_in_as(role)

        response = abc.client.get(student_tasks_url(student))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(student_tasks_url(student))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(student_tasks_url(999_999))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("query", [{"page": "0"}, {"view": "mine"}])
    def test_bad_query_is_refused(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(student_tasks_url(student), query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_task_about_a_student_shows_on_their_profile_list(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me)

        create(abc, me, student=student.pk)

        assert abc.client.get(student_tasks_url(student)).json()["count"] == 1


class TestProfileCount:
    def test_the_profile_counts_the_students_open_tasks(self, abc: ConsultancyContext) -> None:
        counsellor = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, counsellor)
        make_task(abc, counsellor, student=student)
        make_task(abc, counsellor, student=student, due_on=day(-3))
        make_task(abc, counsellor, student=student, is_done=True, done_at=timezone.now())
        make_task(abc, counsellor, student=make_student(abc, name="Aarav Sharma"))
        abc.log_in_as(Role.FRONT_DESK)

        body = abc.client.get(f"/api/students/{student.pk}/").json()

        assert body["open_tasks"] == 2


class TestAssignees:
    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_every_role_sees_who_can_be_given_a_task(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        named_staff(abc, "Bikash Thapa")
        named_staff(abc, "Anita Rai", Role.FRONT_DESK)
        leaver = named_staff(abc, "Old Timer")
        invited = named_staff(abc, "New Person")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=leaver.pk).update(status=StaffStatus.DEACTIVATED)
            Staff.objects.filter(pk=invited.pk).update(status=StaffStatus.INVITED)
        me = abc.log_in_as(role)

        response = abc.client.get(ASSIGNEES_URL)

        assert response.status_code == status.HTTP_200_OK
        names = [person["name"] for person in response.json()]
        assert names == sorted(["Anita Rai", "Bikash Thapa", me.name])
        assert set(response.json()[0]) == {"id", "name", "role"}

    def test_only_get(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(ASSIGNEES_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestAccess:
    def test_logged_out_is_refused_everywhere(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        task = make_task(abc, staff)
        student = make_student(abc)
        client = abc.new_client()

        responses = [
            client.get(TASKS_URL),
            client.post(TASKS_URL, good_body(staff), format="json"),
            client.patch(task_url(task), {"done": True}, format="json"),
            client.get(ASSIGNEES_URL),
            client.get(student_tasks_url(student)),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 5
        assert stored_tasks(abc)[0].is_done is False

    def test_a_deactivated_staff_member_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=me.pk).update(status=StaffStatus.DEACTIVATED)

        response = create(abc, me)

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
        assert stored_tasks(abc) == []

    @pytest.mark.parametrize("method", ["put", "delete"])
    def test_the_list_answers_only_get_and_post(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(TASKS_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("method", ["get", "put", "post", "delete"])
    def test_one_task_answers_only_patch(self, abc: ConsultancyContext, method: str) -> None:
        me = abc.log_in_as(Role.ADMIN)
        task = make_task(abc, me)

        response = getattr(abc.client, method)(task_url(task), {"done": True}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert len(stored_tasks(abc)) == 1

    @pytest.mark.parametrize("method", ["post", "patch", "delete"])
    def test_a_students_tasks_answer_only_get(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = getattr(abc.client, method)(student_tasks_url(student), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_not_on_the_platform_host(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        platform_site.log_in()

        listed_there = platform_site.client.get(TASKS_URL)
        created = platform_site.client.post(TASKS_URL, good_body(staff), format="json")

        assert listed_there.status_code == status.HTTP_404_NOT_FOUND
        assert created.status_code == status.HTTP_404_NOT_FOUND
        assert stored_tasks(abc) == []


class TestModel:
    def test_a_task_reads_as_its_title(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)

        assert str(make_task(abc, staff, title="Call Sita")) == "Call Sita"

    def test_the_due_date_field_shows_its_value(self) -> None:
        assert DueOnField().to_representation(day(0)) == day(0).isoformat()

    def test_five_years_after_a_leap_day_is_the_28th(self) -> None:
        assert DueOnField.latest(date(2028, 2, 29)) == date(2033, 2, 28)

    def test_the_other_fields_show_their_values(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        student = make_student(abc)

        assert AssigneeField().to_representation(staff) == staff.pk
        assert StudentLinkField().to_representation(student) == student.pk
        assert StudentLinkField().to_representation(None) is None
        assert DoneField().to_representation(value=True) is True
