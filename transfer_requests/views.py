"""REST endpoints for Transfer Requests (spec 44a-44e). Always on, like Students.

Teachers are refused everything here (they don't see Students). Every change is a JSON POST.
"""

from __future__ import annotations

from uuid import UUID

from django.core.paginator import EmptyPage
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import (
    why_cannot_answer_transfer,
    why_cannot_ask_transfer,
    why_cannot_cancel_transfer,
    why_cannot_see_all_transfer_requests,
)
from staff.context import get_request_staff
from students.exceptions import SameCounsellorError, StudentChangedError
from students.journey_views import STUDENT_CHANGED_MESSAGE
from students.models import Student
from students.permissions import CanSeeStudents
from transfer_requests.answers import page_answer, transfer_request_answer
from transfer_requests.exceptions import (
    AlreadyAskedError,
    NotWaitingError,
    RequesterCantCounselError,
)
from transfer_requests.models import TransferRequest
from transfer_requests.serializers import (
    AskSerializer,
    DeclineSerializer,
    EmptySerializer,
    TransferRequestListQuerySerializer,
)
from transfer_requests.services import (
    Box,
    approve_transfer,
    ask_transfer,
    cancel_transfer,
    decline_transfer,
    requests_in,
    transfer_request,
    waiting_for,
)

ALREADY_ASKED_MESSAGE = "Someone has already asked to take this Student."
NOT_WAITING_MESSAGE = "This request is no longer waiting for an answer."
REQUESTER_CANT_COUNSEL_MESSAGE = "The person who asked can no longer counsel Students."


def _conflict(message: str) -> Response:
    return Response({"detail": message}, status=status.HTTP_409_CONFLICT)


class _TransferRequestView(APIView):
    permission_classes = (IsAuthenticated, CanSeeStudents)
    parser_classes = (JSONParser,)


class TransferRequestListView(_TransferRequestView):
    """The requests waiting for me, sent by me, or (Admins) everyone's (GET)."""

    def get(self, request: Request) -> Response:
        """Return one page, newest first, with how many wait for my answer."""
        staff = get_request_staff(request)
        query = TransferRequestListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        box = query.box_value()
        if box is Box.ALL:
            reason = why_cannot_see_all_transfer_requests(staff)
            if reason is not None:
                raise PermissionDenied(reason)
        try:
            found = requests_in(box, staff, query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(page_answer(found, staff, waiting_for(staff).count()))


class TransferRequestCountView(_TransferRequestView):
    """How many requests wait for my answer: the menu's count (GET)."""

    def get(self, request: Request) -> Response:
        """Return the count."""
        return Response({"count": waiting_for(get_request_staff(request)).count()})


class StudentTransferRequestView(_TransferRequestView):
    """Ask to take a Student who isn't mine (POST)."""

    def post(self, request: Request, student_id: UUID) -> Response:
        """Send the request with its reason and return it."""
        staff = get_request_staff(request)
        try:
            student = Student.objects.select_related("assigned_counsellor").get(
                public_id=student_id
            )
        except Student.DoesNotExist as error:
            raise NotFound from error
        reason = why_cannot_ask_transfer(staff, student)
        if reason is not None:
            raise PermissionDenied(reason)
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            sent = ask_transfer(student, staff, serializer.reason_value())
        except AlreadyAskedError:
            # expected refusal, answered with its status; the request line logs it
            return _conflict(ALREADY_ASKED_MESSAGE)
        answer = transfer_request_answer(transfer_request(sent.pk), staff)
        return Response(answer, status=status.HTTP_201_CREATED)


def _found(request_id: int) -> TransferRequest:
    try:
        return transfer_request(request_id)
    except TransferRequest.DoesNotExist as error:
        raise NotFound from error


def _may_answer(request: Request, found: TransferRequest) -> None:
    reason = why_cannot_answer_transfer(
        get_request_staff(request), found.student, found.asked_by_id
    )
    if reason is not None:
        raise PermissionDenied(reason)


def _answer(request: Request, request_id: int) -> Response:
    return Response(transfer_request_answer(_found(request_id), get_request_staff(request)))


class ApproveView(_TransferRequestView):
    """Approve: the Student is Transferred to whoever asked (POST, empty body)."""

    def post(self, request: Request, request_id: int) -> Response:
        """Approve and return the request as stored."""
        found = _found(request_id)
        _may_answer(request, found)
        EmptySerializer(data=request.data).is_valid(raise_exception=True)
        try:
            approve_transfer(found, by=get_request_staff(request))
        except NotWaitingError:
            # expected refusal, answered with its status; the request line logs it
            return _conflict(NOT_WAITING_MESSAGE)
        except RequesterCantCounselError:
            # expected refusal, answered with its status; the request line logs it
            return _conflict(REQUESTER_CANT_COUNSEL_MESSAGE)
        except (StudentChangedError, SameCounsellorError):
            # expected refusal, answered with its status; the request line logs it
            return _conflict(STUDENT_CHANGED_MESSAGE)
        return _answer(request, request_id)


class DeclineView(_TransferRequestView):
    """Decline, with an optional reason (POST)."""

    def post(self, request: Request, request_id: int) -> Response:
        """Decline and return the request as stored."""
        found = _found(request_id)
        _may_answer(request, found)
        serializer = DeclineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            decline_transfer(found, by=get_request_staff(request), reason=serializer.reason_value())
        except NotWaitingError:
            # expected refusal, answered with its status; the request line logs it
            return _conflict(NOT_WAITING_MESSAGE)
        return _answer(request, request_id)


class CancelView(_TransferRequestView):
    """Cancel my own request while it waits (POST, empty body)."""

    def post(self, request: Request, request_id: int) -> Response:
        """Cancel and return the request as stored."""
        found = _found(request_id)
        reason = why_cannot_cancel_transfer(get_request_staff(request), found.asked_by_id)
        if reason is not None:
            raise PermissionDenied(reason)
        EmptySerializer(data=request.data).is_valid(raise_exception=True)
        try:
            cancel_transfer(found, by=get_request_staff(request))
        except NotWaitingError:
            # expected refusal, answered with its status; the request line logs it
            return _conflict(NOT_WAITING_MESSAGE)
        return _answer(request, request_id)
