"""REST endpoints for a Student's Stage and counsellor (spec 38-44)."""

from __future__ import annotations

from uuid import UUID

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import StudentAction, why_cannot
from staff.context import get_request_staff
from students.answers import student_profile
from students.exceptions import SameCounsellorError, StageChangeRefusedError, StudentChangedError
from students.journey import assign_counsellor, set_stage_by_hand, transfer_student
from students.journey_serializers import (
    SAME_COUNSELLOR_MESSAGE,
    CounsellorPickSerializer,
    StageChangeSerializer,
)
from students.models import Student
from students.permissions import CanSeeStudents
from students.profile import profile_of

STUDENT_CHANGED_MESSAGE = "Someone else just changed this Student. Refresh the page."


def _allowed_student(request: Request, student_id: UUID, action: StudentAction) -> Student:
    """The Student, if the Staff member may do `action`; 404 or 403 with the reason otherwise."""
    try:
        student = profile_of(student_id)
    except Student.DoesNotExist as error:
        raise NotFound from error
    reason = why_cannot(get_request_staff(request), action, student)
    if reason is not None:
        raise PermissionDenied(reason)
    return student


def _profile(request: Request, student: Student) -> Response:
    return Response(student_profile(student, viewer=get_request_staff(request)))


def _changed_meanwhile() -> Response:
    return Response({"detail": STUDENT_CHANGED_MESSAGE}, status=status.HTTP_409_CONFLICT)


class _StudentView(APIView):
    permission_classes = (IsAuthenticated, CanSeeStudents)


class StudentStageView(_StudentView):
    """Move the Student's Stage by hand, mark them Lost, or bring them back (spec 38-40)."""

    def post(self, request: Request, student_id: UUID) -> Response:
        """Make the move and return the profile as stored."""
        student = _allowed_student(request, student_id, StudentAction.SET_STAGE)
        serializer = StageChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            set_stage_by_hand(
                student,
                serializer.stage_value(),
                by=get_request_staff(request),
                lost_reason=serializer.reason(),
            )
        except StageChangeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response({"stage": [error.message]}, status=status.HTTP_400_BAD_REQUEST)
        return _profile(request, student)


class StudentAssignView(_StudentView):
    """Give a Student with no counsellor their first one (spec 28, 44)."""

    def post(self, request: Request, student_id: UUID) -> Response:
        """Assign the counsellor sent and return the profile as stored."""
        student = _allowed_student(request, student_id, StudentAction.ASSIGN)
        serializer = CounsellorPickSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            assign_counsellor(student, serializer.counsellor_value(), by=get_request_staff(request))
        except StudentChangedError:
            # expected refusal, answered with its status; the request line logs it
            return _changed_meanwhile()
        return _profile(request, student)


class StudentTransferView(_StudentView):
    """Transfer the Student to another counsellor (spec 42, 43)."""

    def post(self, request: Request, student_id: UUID) -> Response:
        """Transfer them and return the profile as stored."""
        student = _allowed_student(request, student_id, StudentAction.TRANSFER)
        serializer = CounsellorPickSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            transfer_student(student, serializer.counsellor_value(), by=get_request_staff(request))
        except SameCounsellorError:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"counsellor": [SAME_COUNSELLOR_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST
            )
        except StudentChangedError:
            # expected refusal, answered with its status; the request line logs it
            return _changed_meanwhile()
        return _profile(request, student)
