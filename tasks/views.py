"""REST endpoints for Tasks (spec 77-82). Tasks are always on; every Role has My Tasks."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from django.core.paginator import EmptyPage, Paginator
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import why_cannot_link_student, why_cannot_mark_task_done
from staff.context import get_request_staff
from staff.models import Staff
from students.models import Student
from students.permissions import CanSeeStudents
from tasks.answers import task_answer
from tasks.models import Task
from tasks.serializers import (
    DoneSerializer,
    NewTaskSerializer,
    NoQuerySerializer,
    PageSerializer,
    TaskListQuerySerializer,
)
from tasks.services import (
    assignable_staff,
    create_task,
    due_now_count,
    my_tasks,
    open_tasks_of,
    set_task_done,
    task_with_people,
    visible_tasks,
)

# Tasks per page of a list.
TASKS_PER_PAGE = 25


def _page_answer(tasks: QuerySet[Task], number: int, viewer: Staff) -> Response:
    """One page of `tasks`, as every Task list answers."""
    try:
        page = Paginator(tasks, TASKS_PER_PAGE).page(number)
    except EmptyPage as error:
        raise NotFound from error
    today = timezone.localdate()
    return Response(
        {
            "count": page.paginator.count,
            "page": page.number,
            "page_size": TASKS_PER_PAGE,
            "results": [task_answer(task, viewer, today) for task in page],
        }
    )


class TaskListView(APIView):
    """My Tasks (GET; spec 79) and creating a Task (POST; spec 77-78)."""

    permission_classes = (IsAuthenticated,)
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return one page of a view of the logged-in Staff member's Tasks."""
        staff = get_request_staff(request)
        query = TaskListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        tasks = my_tasks(
            staff,
            query.task_view(),
            timezone.localdate(),
            priority=query.task_priority(),
            due_by=query.due_by_day(),
        )
        return _page_answer(tasks, query.page_number(), staff)

    def post(self, request: Request) -> Response:
        """Create the Task and return it."""
        staff = get_request_staff(request)
        # Checked before the Student's id is, so a Teacher can't learn which ids exist.
        if _names_a_student(request.data):
            reason = why_cannot_link_student(staff)
            if reason is not None:
                raise PermissionDenied(reason)
        serializer = NewTaskSerializer(data=request.data, context={"by": staff})
        serializer.is_valid(raise_exception=True)
        task = create_task(serializer.to_new_task(), by=staff)
        return Response(
            task_answer(task_with_people(task.pk, staff), staff, timezone.localdate()),
            status=status.HTTP_201_CREATED,
        )


class TaskView(APIView):
    """One Task: mark it done or open it again (PATCH only; spec 81)."""

    permission_classes = (IsAuthenticated,)
    parser_classes = (JSONParser,)

    def patch(self, request: Request, task_id: int) -> Response:
        """Set whether it's done and return it."""
        staff = get_request_staff(request)
        try:
            # Someone else's "Only me" Task is not found, as if it didn't exist (spec 77b).
            task = visible_tasks(staff).get(pk=task_id)
        except Task.DoesNotExist as error:
            raise NotFound from error
        reason = why_cannot_mark_task_done(staff, task.assignee_id, task.created_by_id)
        if reason is not None:
            raise PermissionDenied(reason)
        serializer = DoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = set_task_done(task.pk, done=serializer.is_done(), by=staff)
        return Response(task_answer(task, staff, timezone.localdate()))


class TaskCountView(APIView):
    """How many of my open Tasks are due today or overdue, for the menu (GET; spec 79a)."""

    permission_classes = (IsAuthenticated,)

    def get(self, request: Request) -> Response:
        """Return {"count": n}."""
        NoQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        staff = get_request_staff(request)
        return Response({"count": due_now_count(staff, timezone.localdate())})


class TaskAssigneesView(APIView):
    """Who a Task can be given to: every active Staff member (GET)."""

    permission_classes = (IsAuthenticated,)

    def get(self, request: Request) -> Response:
        """Return each one's id, name and Role, by name."""
        return Response([{"id": s.pk, "name": s.name, "role": s.role} for s in assignable_staff()])


class StudentTasksView(APIView):
    """One Student's open Tasks, for their profile (GET; spec 82)."""

    permission_classes = (IsAuthenticated, CanSeeStudents)

    def get(self, request: Request, student_id: UUID) -> Response:
        """Return one page of the Student's open Tasks, soonest first."""
        query = PageSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            student = Student.objects.get(public_id=student_id)
        except Student.DoesNotExist as error:
            raise NotFound from error
        return _page_answer(open_tasks_of(student), query.page_number(), get_request_staff(request))


def _names_a_student(data: object) -> bool:
    return isinstance(data, Mapping) and data.get("student") is not None
