"""The global search endpoint (spec 29a, issue 49)."""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import StudentAction, can_act_on_student
from modules.services import is_module_on
from modules.types import Module
from search.answers import batch_result, class_student_result, student_result
from search.serializers import SearchQuerySerializer
from search.services import find_batches, find_students, use_a_search
from staff.context import get_request_staff
from tenancy.context import get_request_consultancy

TOO_MANY_MESSAGE = "Too many searches. Wait a minute and try again."


class SearchView(APIView):
    """Students and classes the caller may see whose name (and more) holds `q` (GET only)."""

    permission_classes = (IsAuthenticated,)

    def get(self, request: Request) -> Response:
        """Return at most 5 Students, how many match in all, and at most 5 classes."""
        viewer = get_request_staff(request)
        if not use_a_search(viewer):
            return Response({"detail": TOO_MANY_MESSAGE}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        query = SearchQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        text = query.text()
        batches_on = is_module_on(get_request_consultancy(request), Module.BATCHES)
        students = find_students(text, viewer, batches_on)
        as_student = (
            student_result
            if can_act_on_student(viewer, StudentAction.SEE)
            else class_student_result
        )
        return Response(
            {
                "students": [as_student(student) for student in students.first],
                "student_count": students.count,
                "batches": [
                    batch_result(batch) for batch in find_batches(text, viewer, batches_on)
                ],
            }
        )
