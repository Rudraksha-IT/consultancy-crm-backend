"""REST endpoint for the Edit details page: one save for everything (issue 81)."""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import StudentAction, why_cannot
from staff.context import get_request_staff
from students.answers import student_profile
from students.edit_details import save_details
from students.edit_details_serializers import EditDetailsSerializer
from students.exceptions import StudentChangedError
from students.journey_views import STUDENT_CHANGED_MESSAGE
from students.models import Student
from students.permissions import CanSeeStudents
from students.profile import profile_of


def _student(student_id: int) -> Student:
    try:
        return profile_of(student_id)
    except Student.DoesNotExist as error:
        raise NotFound from error


class StudentEditDetailsView(APIView):
    """Save every field and the three lists together, or nothing (PUT only)."""

    permission_classes = (IsAuthenticated, CanSeeStudents)

    def put(self, request: Request, student_id: int) -> Response:
        """Save the page and return the profile as stored; 409 if it changed meanwhile."""
        staff = get_request_staff(request)
        student = _student(student_id)
        reason = why_cannot(staff, StudentAction.EDIT_DETAILS, student)
        if reason is not None:
            raise PermissionDenied(reason)
        serializer = EditDetailsSerializer(student, data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            save_details(student, serializer.edited(), by=staff)
        except StudentChangedError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": STUDENT_CHANGED_MESSAGE}, status=status.HTTP_409_CONFLICT)
        return Response(student_profile(_student(student_id), viewer=staff))
