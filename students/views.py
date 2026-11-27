"""REST endpoints for Students (spec 23-31)."""

from __future__ import annotations

from django.core.paginator import EmptyPage
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from staff.context import get_request_staff
from students.answers import student_answer, student_rows
from students.permissions import CanUseStudentList
from students.serializers import NewStudentSerializer, StudentListQuerySerializer
from students.services import STUDENTS_PER_PAGE, add_student, find_students, page_of


class StudentListView(APIView):
    """List, search and filter Students (GET), or add a new visitor as a Lead (POST)."""

    permission_classes = (IsAuthenticated, CanUseStudentList)

    def get(self, request: Request) -> Response:
        """Return one page of matching Students, newest first."""
        query = StudentListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        students = find_students(query.to_filters(), viewer=get_request_staff(request))
        try:
            page = page_of(students, query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": STUDENTS_PER_PAGE,
                "results": student_rows(list(page), with_progress=query.wants_progress()),
            }
        )

    def post(self, request: Request) -> Response:
        """Add a Lead and return them as stored."""
        serializer = NewStudentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student = add_student(serializer.to_new_student(), by=get_request_staff(request))
        return Response(student_answer(student), status=status.HTTP_201_CREATED)
