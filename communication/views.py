"""REST endpoints for Email: emails to Students and the list of them (ADR 0023).

Every endpoint needs the Communication Module (shown as "Email"): while it's off they answer 404
and the data is kept.
"""

from __future__ import annotations

from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db.models import QuerySet
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    ErrorDetail,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import BatchAction, why_cannot_on_batches
from batches.models import Batch
from batches.services import members_of
from communication.answers import bulk_answer, choosable_answer, message_answer
from communication.exceptions import NoContactEmailError
from communication.models import Message
from communication.permissions import CanMessageStudents, CommunicationModuleIsOn
from communication.serializers import (
    CHOOSE_SOMEONE_MESSAGE,
    BulkEmailSerializer,
    NewEmailSerializer,
    PreTickQuerySerializer,
)
from communication.services import send_to_student, send_to_students
from modules.services import is_module_on
from modules.types import Module
from staff.context import get_request_staff
from students.models import Stage, Student
from students.services import find_students
from students.types import StudentFilters
from tasks.serializers import PageSerializer
from tenancy.context import get_request_consultancy

# Rows per page of a list.
ROWS_PER_PAGE = 25
ONE_OF_MESSAGE = "Choose a class or a Student list."
TOO_MANY_MESSAGE = "Email at most {limit} Students at once."
NOT_FOUND_MESSAGE = "Some of these Students can't be found."
LOST_MESSAGE = "Lost Students can't be chosen."
NO_CONTACT_EMAIL_MESSAGE = (
    "Your Admin needs to add the office's contact email in Configuration first."
)
NO_CONTACT_EMAIL_CODE = "no_contact_email"

# Logged in first, so a logged-out caller can't learn whether the Module is on.
MESSAGING: tuple[type[BasePermission], ...] = (
    IsAuthenticated,
    CommunicationModuleIsOn,
    CanMessageStudents,
)


class NoContactEmail(APIException):
    """Nothing goes to Students until the office has a contact email (spec stories 13-14)."""

    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self) -> None:
        super().__init__()
        self.detail = {
            "detail": ErrorDetail(NO_CONTACT_EMAIL_MESSAGE),
            "code": ErrorDetail(NO_CONTACT_EMAIL_CODE),
        }


def _messages_page(rows: QuerySet[Message], request: Request) -> Response:
    """One page of emails, newest first, as every list answers."""
    query = PageSerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    try:
        page = Paginator(rows.select_related("student"), ROWS_PER_PAGE).page(query.page_number())
    except EmptyPage as error:
        raise NotFound from error
    return Response(
        {
            "count": page.paginator.count,
            "page": page.number,
            "page_size": ROWS_PER_PAGE,
            "results": [message_answer(message) for message in page],
        }
    )


class MessageListView(APIView):
    """Every email sent to Students, newest first (GET; the Email page)."""

    permission_classes = MESSAGING

    def get(self, request: Request) -> Response:
        """Return one page of emails."""
        return _messages_page(Message.objects.all(), request)


class StudentMessagesView(APIView):
    """One Student's emails (GET), and emailing them (POST)."""

    permission_classes = MESSAGING
    parser_classes = (JSONParser,)

    def get(self, request: Request, student_id: int) -> Response:
        """Return one page of the Student's emails, newest first."""
        student = _student(student_id)
        return _messages_page(Message.objects.filter(student=student), request)

    def post(self, request: Request, student_id: int) -> Response:
        """Send it, record it sent or failed, and return it."""
        student = _student(student_id)
        serializer = NewEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            message = send_to_student(
                student,
                serializer.email_text(),
                by=get_request_staff(request),
                consultancy=get_request_consultancy(request),
            )
        except NoContactEmailError as error:
            raise NoContactEmail from error
        return Response(message_answer(message), status=status.HTTP_201_CREATED)


def _student(student_id: int) -> Student:
    try:
        return Student.objects.get(pk=student_id)
    except Student.DoesNotExist as error:
        raise NotFound from error


class BulkMessagesView(APIView):
    """One email to many Students (spec story 43): the Students are chosen first.

    GET gives the Students a class or the Student list's filters pre-tick; POST sends to the
    Students chosen, each their own copy, one by one, and records each.
    """

    permission_classes = MESSAGING
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """The Students who can be chosen from a class or the Student list's filters."""
        query = PreTickQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        batch_id = query.batch_id()
        if batch_id is not None and query.has_student_filters():
            raise ValidationError({"detail": ONE_OF_MESSAGE})
        filters = None if batch_id is not None else query.to_filters()
        choosable = (
            _recipients(request, batch_id, filters)
            .exclude(stage=Stage.LOST)
            .exclude(email="")
            .order_by("name", "pk")
        )
        limit = settings.BULK_MAX_STUDENTS
        return Response(choosable_answer(list(choosable[:limit]), choosable.count(), limit))

    def post(self, request: Request) -> Response:
        """Send it to each Student chosen, record each sent or failed, and say how it went."""
        serializer = BulkEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        students = _chosen(serializer.student_ids())
        try:
            result = send_to_students(
                students,
                serializer.email_text(),
                by=get_request_staff(request),
                consultancy=get_request_consultancy(request),
            )
        except NoContactEmailError as error:
            raise NoContactEmail from error
        return Response(bulk_answer(result), status=status.HTTP_201_CREATED)


def _chosen(ids: list[int]) -> list[Student]:
    """The Students chosen, in the order given.

    Raises:
        ValidationError: nobody, too many, an unknown Student, or a Lost one was chosen.
    """
    limit = settings.BULK_MAX_STUDENTS
    if not ids:
        raise ValidationError({"students": [CHOOSE_SOMEONE_MESSAGE]})
    if len(ids) > limit:
        raise ValidationError({"students": [TOO_MANY_MESSAGE.format(limit=limit)]})
    found = Student.objects.in_bulk(ids)
    if len(found) != len(ids):
        raise ValidationError({"students": [NOT_FOUND_MESSAGE]})
    if any(student.stage == Stage.LOST for student in found.values()):
        raise ValidationError({"students": [LOST_MESSAGE]})
    return [found[pk] for pk in ids]


def _recipients(
    request: Request, batch_id: int | None, filters: StudentFilters | None
) -> QuerySet[Student]:
    """The Students in the class `batch_id`, or else those matching `filters`.

    A class needs the Batches Module (404 while off) and a Role that sees classes (403).
    """
    staff = get_request_staff(request)
    if batch_id is None:
        return find_students(filters or StudentFilters(), viewer=staff)
    if not is_module_on(get_request_consultancy(request), Module.BATCHES):
        raise NotFound
    reason = why_cannot_on_batches(staff, BatchAction.SEE)
    if reason is not None:
        raise PermissionDenied(reason)
    batch = Batch.objects.filter(pk=batch_id).first()
    if batch is None:
        raise NotFound
    return members_of(batch)
