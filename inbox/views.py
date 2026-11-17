"""REST endpoints for the Inbox (spec 128-130). Always on, for every Role.

Only GET and a JSON POST to send. Past due and Suspended offices are refused the POST by the
Consultancy Status middleware. Message text is never logged: the request line has no body.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import why_cannot_use_inbox
from inbox.answers import (
    check_answer,
    conversations_answer,
    message_answer,
    people_answer,
    thread_answer,
)
from inbox.exceptions import PersonHasLeftError
from inbox.serializers import SendSerializer
from inbox.services import (
    check,
    conversations,
    find_person,
    open_conversation,
    people_to_write_to,
    send,
)
from staff.context import get_request_staff
from staff.models import Staff

HAS_LEFT_MESSAGE = "This person has left the office. You can't write to them."


class _InboxView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (JSONParser,)

    def me(self, request: Request) -> Staff:
        """The Staff member using the Inbox; a Platform Admin is refused."""
        staff = get_request_staff(request)
        reason = why_cannot_use_inbox(staff)
        if reason is not None:
            raise PermissionDenied(reason)
        return staff


def _person(me: Staff, person_id: int) -> Staff:
    try:
        return find_person(me, person_id)
    except Staff.DoesNotExist as error:
        raise NotFound from error


class InboxView(_InboxView):
    """My conversations, newest first (GET)."""

    def get(self, request: Request) -> Response:
        """Return the list."""
        me = self.me(request)
        return Response(conversations_answer(conversations(me), me))


class InboxCheckView(_InboxView):
    """The 30-second check: how many conversations are unread, and the newest id (GET)."""

    def get(self, request: Request) -> Response:
        """Return the check."""
        return Response(check_answer(check(self.me(request))))


class InboxPeopleView(_InboxView):
    """Who I can start a conversation with (GET)."""

    def get(self, request: Request) -> Response:
        """Return everyone active in the office but me."""
        return Response(people_answer(list(people_to_write_to(self.me(request)))))


class ConversationView(_InboxView):
    """One conversation: read it (GET, marks it read) or write in it (POST)."""

    def get(self, request: Request, person_id: int) -> Response:
        """Return the conversation with the person."""
        me = self.me(request)
        person = _person(me, person_id)
        return Response(thread_answer(open_conversation(me, person), me))

    def post(self, request: Request, person_id: int) -> Response:
        """Send a message to the person and return it."""
        me = self.me(request)
        person = _person(me, person_id)
        serializer = SendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            message = send(me, person, serializer.text_value())
        except PersonHasLeftError as error:
            raise PermissionDenied(HAS_LEFT_MESSAGE) from error
        return Response(message_answer(message, me), status=status.HTTP_201_CREATED)
