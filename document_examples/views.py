"""REST endpoints for Document Examples (spec 62-66).

Admins keep each Document Type's instruction and example files in Configuration → Documents;
Counsellors and Front Desk open the examples from a Student's checklist; Teachers never see them.
Examples in the Bin are reached only from the Bin tab, by an Admin.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from catalogue.permissions import NOT_FOR_TEACHERS_MESSAGE, READING_ROLES
from document_examples.answers import example_answer
from document_examples.exceptions import TooManyExamplesError
from document_examples.models import DocumentExample
from document_examples.serializers import InstructionSerializer, NewExampleSerializer
from document_examples.services import (
    MAX_EXAMPLES_PER_TYPE,
    add_example,
    binned_example,
    delete_example_for_good,
    example_link,
    live_example,
    put_example_in_bin,
    replace_example,
    restore_example,
    set_instruction,
)
from documents.answers import configured_type_answer
from documents.exceptions import DocumentStorageError
from documents.models import DocumentType
from documents.requirements import configured_types
from documents.serializers import DocumentLinkQuerySerializer, NothingSerializer
from documents.storage import SIGNED_LINK_SECONDS
from documents.views import IsBinAdmin
from staff.context import get_request_staff
from staff.models import Staff, StaffStatus
from staff.permissions import IsAdmin

TOO_MANY_MESSAGE = (
    f"A document can have at most {MAX_EXAMPLES_PER_TYPE} example files. Remove one to add another."
)
FULL_MESSAGE = f"This document already has {MAX_EXAMPLES_PER_TYPE} example files. Remove one first."
NOT_STORED_MESSAGE = "We couldn't store the file. Try again."
NOT_DELETED_MESSAGE = "We couldn't delete the file. Try again."


class CanSeeExamples(BasePermission):
    """Every Role that works with Students' paperwork opens examples; Teachers are refused."""

    message = NOT_FOR_TEACHERS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether this Staff member may open Document Examples."""
        user = request.user
        return (
            isinstance(user, Staff)
            and user.status == StaffStatus.ACTIVE
            and user.role in READING_ROLES
        )


def _type(type_id: int) -> DocumentType:
    try:
        return DocumentType.objects.get(pk=type_id)
    except DocumentType.DoesNotExist as error:
        raise NotFound from error


def _live(example_id: int) -> DocumentExample:
    try:
        return live_example(example_id)
    except DocumentExample.DoesNotExist as error:
        raise NotFound from error


def _binned(example_id: int) -> DocumentExample:
    try:
        return binned_example(example_id)
    except DocumentExample.DoesNotExist as error:
        raise NotFound from error


def _refused(message: str) -> Response:
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


def _unavailable(message: str) -> Response:
    return Response({"detail": message}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


def _link(request: Request, example: DocumentExample) -> Response:
    query = DocumentLinkQuerySerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    link = example_link(example, as_attachment=query.as_attachment(), by=get_request_staff(request))
    return Response({"url": link, "expires_in": SIGNED_LINK_SECONDS})


def _new_example(request: Request) -> NewExampleSerializer:
    serializer = NewExampleSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer


class InstructionView(APIView):
    """Set a Document Type's instruction (PUT; Admins, in Configuration)."""

    permission_classes = (IsAuthenticated, IsAdmin)
    parser_classes = (JSONParser,)
    http_method_names = ("put", "options")

    def put(self, request: Request, type_id: int) -> Response:
        """Set it and return the type as Configuration lists it."""
        serializer = InstructionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            set_instruction(type_id, serializer.instruction_value(), by=get_request_staff(request))
        except DocumentType.DoesNotExist as error:
            raise NotFound from error
        return Response(configured_type_answer(configured_types().get(pk=type_id)))


class ExampleListView(APIView):
    """Add an example file to a Document Type (POST; Admins, in Configuration)."""

    permission_classes = (IsAuthenticated, IsAdmin)
    parser_classes = (MultiPartParser,)
    http_method_names = ("post", "options")

    def post(self, request: Request, type_id: int) -> Response:
        """Store the file and return the example as kept."""
        document_type = _type(type_id)
        new = _new_example(request).to_new_example()
        try:
            example = add_example(document_type, new, by=get_request_staff(request))
        except TooManyExamplesError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(TOO_MANY_MESSAGE)
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return _unavailable(NOT_STORED_MESSAGE)
        return Response(example_answer(example), status=status.HTTP_201_CREATED)


class ExampleView(APIView):
    """Remove an example file to the Bin (DELETE; Admins, in Configuration)."""

    permission_classes = (IsAuthenticated, IsAdmin)
    http_method_names = ("delete", "options")

    def delete(self, request: Request, example_id: int) -> Response:
        """Put it in the Bin."""
        put_example_in_bin(_live(example_id), by=get_request_staff(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReplaceExampleView(APIView):
    """Keep a new file in place of an example; the old one goes to the Bin (POST; Admins)."""

    permission_classes = (IsAuthenticated, IsAdmin)
    parser_classes = (MultiPartParser,)
    http_method_names = ("post", "options")

    def post(self, request: Request, example_id: int) -> Response:
        """Store the new file and return it as kept."""
        old = _live(example_id)
        new = _new_example(request).to_new_example()
        try:
            example = replace_example(old, new, by=get_request_staff(request))
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return _unavailable(NOT_STORED_MESSAGE)
        return Response(example_answer(example), status=status.HTTP_201_CREATED)


class ExampleLinkView(APIView):
    """A short-lived link to open or save an example file in use (GET; not Teachers)."""

    permission_classes = (IsAuthenticated, CanSeeExamples)
    http_method_names = ("get", "options")

    def get(self, request: Request, example_id: int) -> Response:
        """Return the link and how many seconds it works for."""
        return _link(request, _live(example_id))


class BinExampleView(APIView):
    """Delete an example file in the Bin for good (DELETE; Admins)."""

    permission_classes = (IsAuthenticated, IsBinAdmin)
    http_method_names = ("delete", "options")

    def delete(self, request: Request, example_id: int) -> Response:
        """Remove the stored file and the record."""
        example = _binned(example_id)
        try:
            delete_example_for_good(example, by=get_request_staff(request))
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return _unavailable(NOT_DELETED_MESSAGE)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BinExampleRestoreView(APIView):
    """Restore an example file from the Bin to its Document Type (POST; Admins)."""

    permission_classes = (IsAuthenticated, IsBinAdmin)
    parser_classes = (JSONParser,)
    http_method_names = ("post", "options")

    def post(self, request: Request, example_id: int) -> Response:
        """Restore it and return it as kept."""
        example = _binned(example_id)
        NothingSerializer(data=request.data).is_valid(raise_exception=True)
        try:
            restore_example(example, by=get_request_staff(request))
        except TooManyExamplesError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(FULL_MESSAGE)
        return Response(example_answer(example))


class BinExampleLinkView(APIView):
    """A short-lived link to an example file in the Bin, for an Admin on the Bin tab (GET)."""

    permission_classes = (IsAuthenticated, IsBinAdmin)
    http_method_names = ("get", "options")

    def get(self, request: Request, example_id: int) -> Response:
        """Return the link and how many seconds it works for."""
        return _link(request, _binned(example_id))
