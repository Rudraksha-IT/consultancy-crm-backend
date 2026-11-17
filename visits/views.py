"""REST endpoints for Visits: today's timeline and one Student's Visits (spec 23a-23c)."""

from __future__ import annotations

from django.core.paginator import EmptyPage, Paginator
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import TEACHERS_MESSAGE, StudentAction, can_act_on_student
from home.serializers import NoQuerySerializer
from staff.context import get_request_staff
from staff.models import Staff
from students.models import Student
from visits.answers import timeline_answer, visit_answer
from visits.serializers import NewVisitSerializer, VisitPageQuerySerializer
from visits.services import MOST_VISITS_IN_A_DAY, log_follow_up, visits_of, visits_on

VISITS_PER_PAGE = 50


class CanLogVisits(BasePermission):
    """Seeing and logging Visits needs LOG_VISIT: Front Desk, Counsellors and Admins."""

    message = TEACHERS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member log Visits."""
        user = request.user
        return isinstance(user, Staff) and can_act_on_student(user, StudentAction.LOG_VISIT)


class TodaysVisitsView(APIView):
    """The office's Visits today, newest first, for the visitor desk's timeline (GET)."""

    permission_classes = (IsAuthenticated, CanLogVisits)

    def get(self, request: Request) -> Response:
        """Return how many came in today, and the newest `MOST_VISITS_IN_A_DAY` of them."""
        NoQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        visits = visits_on(timezone.localdate())
        return Response(
            {
                "count": visits.count(),
                "results": [timeline_answer(visit) for visit in visits[:MOST_VISITS_IN_A_DAY]],
            }
        )


class StudentVisitsView(APIView):
    """One Student's Visits (GET), and logging a returning Student's Visit (POST)."""

    permission_classes = (IsAuthenticated, CanLogVisits)
    parser_classes = (JSONParser,)

    def get(self, request: Request, student_id: int) -> Response:
        """Return one page of the Student's Visits, newest first."""
        student = _student(student_id)
        query = VisitPageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            page = Paginator(visits_of(student), VISITS_PER_PAGE).page(query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": VISITS_PER_PAGE,
                "results": [visit_answer(visit) for visit in page],
            }
        )

    def post(self, request: Request, student_id: int) -> Response:
        """Log the Visit, made now by the Staff member, and return it."""
        student = _student(student_id)
        serializer = NewVisitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        visit = log_follow_up(
            student,
            serializer.reason_value(),
            serializer.details_value(),
            by=get_request_staff(request),
        )
        return Response(visit_answer(visit), status=status.HTTP_201_CREATED)


def _student(student_id: int) -> Student:
    try:
        return Student.objects.get(pk=student_id)
    except Student.DoesNotExist as error:
        raise NotFound from error
