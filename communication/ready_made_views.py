"""REST endpoints for ready-made emails: the menu and the filled-in draft (ticket 09).

Read only (GET): Staff edit the draft in the composer and send it through the Email endpoints.
Every endpoint needs the Communication Module; Fee reminder also Accounts and Class details also
Batches (404 while off).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from communication.answers import draft_answer, menu_answer
from communication.exceptions import (
    ReadyMadeOffError,
    ReadyMadeRefusedError,
    ReadyMadeUnavailableError,
    RecordNotChosenError,
)
from communication.ready_made import Asker, many_draft, many_menu, student_draft, student_menu
from communication.serializers import DraftQuerySerializer
from communication.views import MESSAGING, student_or_404
from staff.context import get_request_staff
from tasks.serializers import NoQuerySerializer
from tenancy.context import get_request_consultancy


def _asker(request: Request) -> Asker:
    return Asker(get_request_staff(request), get_request_consultancy(request))


def _draft_query(request: Request) -> DraftQuerySerializer:
    query = DraftQuerySerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    return query


@contextmanager
def _refusals() -> Iterator[None]:
    """Answer the service's refusals: 404 for a switched-off Module, 403, or 400 with the field."""
    try:
        yield
    except ReadyMadeOffError as error:
        raise NotFound from error
    except ReadyMadeRefusedError as error:
        raise PermissionDenied(f"{error.reason}.") from error
    except ReadyMadeUnavailableError as error:
        raise ValidationError({"kind": [error.reason]}) from error
    except RecordNotChosenError as error:
        raise ValidationError({"record": [error.reason]}) from error


class StudentReadyMadeView(APIView):
    """The ready-made emails for one Student, with the records to pick (GET)."""

    permission_classes = MESSAGING

    def get(self, request: Request, student_id: int) -> Response:
        """Return every ready-made email offered, and why any can't be used now."""
        NoQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        student = student_or_404(student_id)
        return Response(menu_answer(student_menu(student, _asker(request))))


class StudentDraftView(APIView):
    """One ready-made email filled in for one Student (GET ?kind=…&record=…)."""

    permission_classes = MESSAGING

    def get(self, request: Request, student_id: int) -> Response:
        """Return the subject line and message to start from."""
        query = _draft_query(request)
        student = student_or_404(student_id)
        with _refusals():
            draft = student_draft(
                student, query.kind_chosen(), query.record_typed(), _asker(request)
            )
        return Response(draft_answer(draft))


class ManyReadyMadeView(APIView):
    """The ready-made emails that can go to many Students (GET)."""

    permission_classes = MESSAGING

    def get(self, request: Request) -> Response:
        """Return Class details (with the classes to pick) and Plain email."""
        NoQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        return Response(menu_answer(many_menu(_asker(request))))


class ManyDraftView(APIView):
    """A ready-made email filled in for many Students, "Dear Student," (GET ?kind=…&record=…)."""

    permission_classes = MESSAGING

    def get(self, request: Request) -> Response:
        """Return the subject line and message to start from."""
        query = _draft_query(request)
        with _refusals():
            draft = many_draft(query.kind_chosen(), query.record_typed(), _asker(request))
        return Response(draft_answer(draft))
