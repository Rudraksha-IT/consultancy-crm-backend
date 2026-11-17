"""REST endpoints for Communication: the office's accounts and messages (spec 88-89, 91-92).

Every endpoint needs the Communication Module: while it's off they answer 404 and the data is kept.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db.models import QuerySet
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import BatchAction, why_cannot_on_batches
from batches.models import Batch
from batches.services import members_of
from communication.answers import (
    bulk_answer,
    message_answer,
    settings_answer,
    test_sms_answer,
)
from communication.exceptions import NoMobileError, NotSetUpError
from communication.models import Channel, Message
from communication.permissions import CanMessageStudents, CommunicationModuleIsOn
from communication.serializers import (
    EMAIL_NOT_SET_UP_MESSAGE,
    SMS_NOT_SET_UP_MESSAGE,
    BulkCountQuerySerializer,
    BulkMessageSerializer,
    NewMessageSerializer,
    NoFieldsSerializer,
    ProviderSettingsSerializer,
)
from communication.services import (
    email_is_ready,
    save_settings,
    saved_settings,
    send_test_sms,
    send_to_student,
    send_to_students,
    sms_is_ready,
)
from modules.services import is_module_on
from modules.types import Module
from staff.context import get_request_staff
from staff.permissions import IsAdmin
from students.models import Student
from students.services import find_students
from students.types import StudentFilters
from tasks.serializers import PageSerializer
from tenancy.context import get_request_consultancy

logger = logging.getLogger(__name__)

# Rows per page of a list.
ROWS_PER_PAGE = 25
NO_MOBILE_MESSAGE = "Add your Nepali mobile number in your Settings first."
ONE_OF_MESSAGE = "Choose a class or a Student list."
NOBODY_MESSAGE = "No Students to send to."
TOO_MANY_MESSAGE = "Message at most {limit} Students at once. Narrow the list first."
_NOT_SET_UP = {Channel.SMS: SMS_NOT_SET_UP_MESSAGE, Channel.EMAIL: EMAIL_NOT_SET_UP_MESSAGE}

# Logged in first, so a logged-out caller can't learn whether the Module is on.
ADMIN_ONLY: tuple[type[BasePermission], ...] = (IsAuthenticated, CommunicationModuleIsOn, IsAdmin)
MESSAGING: tuple[type[BasePermission], ...] = (
    IsAuthenticated,
    CommunicationModuleIsOn,
    CanMessageStudents,
)


def _messages_page(rows: QuerySet[Message], request: Request) -> Response:
    """One page of messages, newest first, as every list answers."""
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


class ProviderSettingsView(APIView):
    """The office's SMS and email accounts (GET), and saving them (PUT; spec 88). Admins only."""

    permission_classes = ADMIN_ONLY
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return the accounts, without their secrets."""
        return Response(settings_answer(saved_settings()))

    def put(self, request: Request) -> Response:
        """Validate and store the accounts, and return them as stored."""
        staff = get_request_staff(request)
        serializer = ProviderSettingsSerializer(
            data=request.data, context={"saved": saved_settings()}
        )
        serializer.is_valid(raise_exception=True)
        saved = save_settings(serializer.to_details(), by=staff)
        logger.info(
            "Provider settings saved: consultancy_id=%s, staff_id=%s, sms_ready=%s, email_ready=%s",
            get_request_consultancy(request).pk,
            staff.pk,
            sms_is_ready(saved),
            email_is_ready(saved),
        )
        return Response(settings_answer(saved))


class TestSmsView(APIView):
    """Send a test SMS to the Admin's own mobile, to check the SMS settings (POST)."""

    permission_classes = ADMIN_ONLY
    parser_classes = (JSONParser,)

    def post(self, request: Request) -> Response:
        """Send it and say how it went."""
        NoFieldsSerializer(data=request.data).is_valid(raise_exception=True)
        office_name = get_request_consultancy(request).name
        try:
            result = send_test_sms(get_request_staff(request), office_name)
        except NotSetUpError as error:
            raise ValidationError({"channel": [SMS_NOT_SET_UP_MESSAGE]}) from error
        except NoMobileError as error:
            raise ValidationError({"detail": NO_MOBILE_MESSAGE}) from error
        return Response(test_sms_answer(result))


class MessageListView(APIView):
    """Every message sent to Students, newest first (GET; the Messages page, spec 91)."""

    permission_classes = MESSAGING

    def get(self, request: Request) -> Response:
        """Return one page of messages."""
        return _messages_page(Message.objects.all(), request)


class StudentMessagesView(APIView):
    """One Student's messages (GET), and sending them an SMS or email (POST; spec 89, 91)."""

    permission_classes = MESSAGING
    parser_classes = (JSONParser,)

    def get(self, request: Request, student_id: int) -> Response:
        """Return one page of the Student's messages, newest first."""
        student = _student(student_id)
        return _messages_page(Message.objects.filter(student=student), request)

    def post(self, request: Request, student_id: int) -> Response:
        """Send it, record it sent or failed, and return it."""
        student = _student(student_id)
        serializer = NewMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        channel = serializer.channel_value()
        try:
            message = send_to_student(
                student,
                channel,
                serializer.body_value(),
                by=get_request_staff(request),
                office_name=get_request_consultancy(request).name,
            )
        except NotSetUpError as error:
            raise ValidationError({"channel": [_NOT_SET_UP[channel]]}) from error
        return Response(message_answer(message), status=status.HTTP_201_CREATED)


def _student(student_id: int) -> Student:
    try:
        return Student.objects.get(pk=student_id)
    except Student.DoesNotExist as error:
        raise NotFound from error


class BulkMessagesView(APIView):
    """One SMS or email to many Students (spec 90-91): a class, or a Student list's filters.

    GET says how many Students it would go to; POST sends it, one by one, and records each.
    """

    permission_classes = MESSAGING
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Count the Students a class or the Student list's filters name."""
        query = BulkCountQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        batch_id = query.batch_id()
        if batch_id is not None and query.has_student_filters():
            raise ValidationError({"detail": ONE_OF_MESSAGE})
        filters = None if batch_id is not None else query.to_filters()
        students = _recipients(request, batch_id, filters)
        return Response({"count": students.count(), "max": settings.BULK_MAX_STUDENTS})

    def post(self, request: Request) -> Response:
        """Send it to each Student, record each sent or failed, and say how it went."""
        serializer = BulkMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch_id, filters = serializer.batch_id(), serializer.student_filters()
        if (batch_id is None) == (filters is None):
            raise ValidationError({"detail": ONE_OF_MESSAGE})
        students = list(_recipients(request, batch_id, filters)[: settings.BULK_MAX_STUDENTS + 1])
        if not students:
            raise ValidationError({"detail": NOBODY_MESSAGE})
        if len(students) > settings.BULK_MAX_STUDENTS:
            limit = settings.BULK_MAX_STUDENTS
            raise ValidationError({"detail": TOO_MANY_MESSAGE.format(limit=limit)})
        channel = serializer.channel_value()
        try:
            result = send_to_students(
                students,
                channel,
                serializer.body_value(),
                by=get_request_staff(request),
                office_name=get_request_consultancy(request).name,
            )
        except NotSetUpError as error:
            raise ValidationError({"channel": [_NOT_SET_UP[channel]]}) from error
        return Response(bulk_answer(result), status=status.HTTP_201_CREATED)


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
