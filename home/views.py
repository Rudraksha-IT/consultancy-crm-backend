"""REST endpoints for the Homes: Students' progress and the pipeline (issue 33, spec 22a)."""

from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import PAPERWORK_ROLES_MESSAGE, StudentAction, can_act_on_student
from home.answers import pipeline_answer, progress_row, waiting_answer
from home.serializers import NoQuerySerializer, ProgressQuerySerializer
from home.services import progress_of_student, progress_of_students, stage_counts
from home.waiting import waiting_on
from modules.services import is_module_on
from modules.types import Module
from staff.context import get_request_staff
from staff.models import Role, Staff, StaffStatus
from students.models import Student
from students.permissions import CanSeeStudents
from tenancy.context import get_request_consultancy

ONLY_ADMIN_SEES_MESSAGE = "Only an Admin can see this."


class CanSeeProgress(BasePermission):
    """Students' progress is on the Counsellor's and the Admin's Homes."""

    message = PAPERWORK_ROLES_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member see Students' progress."""
        user = request.user
        return isinstance(user, Staff) and can_act_on_student(user, StudentAction.SEE_PROGRESS)


class IsActiveAdmin(BasePermission):
    """The pipeline is on the Admin's Home only."""

    message = ONLY_ADMIN_SEES_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the request comes from an active Admin."""
        user = request.user
        return (
            isinstance(user, Staff)
            and user.role == Role.ADMIN
            and user.status == StaffStatus.ACTIVE
        )


class ProgressView(APIView):
    """Students furthest along first, with their next step and documents (GET)."""

    permission_classes = (IsAuthenticated, CanSeeProgress)

    def get(self, request: Request) -> Response:
        """Return how many Students there are and the first `limit` of them."""
        query = ProgressQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        students = Student.objects.all()
        if query.only_mine():
            students = students.filter(assigned_counsellor=get_request_staff(request))
        found = progress_of_students(students)
        return Response(
            {
                "count": len(found),
                "results": [progress_row(row) for row in found[: query.limit_value()]],
            }
        )


class PipelineView(APIView):
    """How many Students are at each Stage, Lost included (GET)."""

    permission_classes = (IsAuthenticated, IsActiveAdmin)

    def get(self, request: Request) -> Response:
        """Return the count per Stage."""
        NoQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        return Response(pipeline_answer(stage_counts()))


class WaitingView(APIView):
    """What's waiting on a Counsellor or Admin (GET; spec 22a1).

    Documents to check, Transfer Requests to answer and, for a Counsellor, Visa interviews in
    the next 7 days.
    """

    permission_classes = (IsAuthenticated, CanSeeProgress)

    def get(self, request: Request) -> Response:
        """Return the counts and the first few interviews."""
        NoQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        clock_in_on = is_module_on(get_request_consultancy(request), Module.CLOCK_IN)
        waiting = waiting_on(get_request_staff(request), clock_in_on=clock_in_on)
        return Response(waiting_answer(waiting))


class StudentProgressView(APIView):
    """How far along one Student is and their next step, for the visitor desk (GET; spec 23a)."""

    permission_classes = (IsAuthenticated, CanSeeStudents)

    def get(self, request: Request, student_id: int) -> Response:
        """Return the Student's Stage, how far along they are, and their next step."""
        NoQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        try:
            student = Student.objects.get(pk=student_id)
        except Student.DoesNotExist as error:
            raise NotFound from error
        progress = progress_of_student(student)
        return Response(
            {
                "stage": student.stage,
                "percent": progress.percent,
                "next_step": progress.next_step,
                "is_done": progress.is_done,
            }
        )
