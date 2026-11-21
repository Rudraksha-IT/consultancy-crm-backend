"""REST endpoints for Course Files (spec 53-61).

Admins keep them; Counsellors and Front Desk open them; Teachers never see them. Files in the Bin
are reached only from the Bin tab, by an Admin.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from catalogue.models import Course
from catalogue.permissions import CanChangeCatalogue, CanReadCatalogue
from course_files.answers import course_file_answer
from course_files.exceptions import CourseNotLiveError, TooManyCourseFilesError
from course_files.models import CourseFile
from course_files.serializers import CourseFileRenameSerializer, NewCourseFileSerializer
from course_files.services import (
    MAX_FILES_PER_COURSE,
    add_course_file,
    binned_course_file,
    course_file_link,
    delete_course_file_for_good,
    live_course_file,
    put_course_file_in_bin,
    rename_course_file,
    restore_course_file,
)
from documents.exceptions import DocumentStorageError
from documents.serializers import DocumentLinkQuerySerializer, NothingSerializer
from documents.storage import SIGNED_LINK_SECONDS
from documents.views import IsBinAdmin
from staff.context import get_request_staff

COURSE_NOT_LIVE_MESSAGE = "This course is archived. Restore it before adding files."
TOO_MANY_MESSAGE = f"A course can have at most {MAX_FILES_PER_COURSE} Course Files."
NOT_STORED_MESSAGE = "We couldn't store the file. Try again."
NOT_DELETED_MESSAGE = "We couldn't delete the file. Try again."


def _course(course_id: int) -> Course:
    try:
        return Course.objects.get(pk=course_id)
    except Course.DoesNotExist as error:
        raise NotFound from error


def _live(file_id: int) -> CourseFile:
    try:
        return live_course_file(file_id)
    except CourseFile.DoesNotExist as error:
        raise NotFound from error


def _binned(file_id: int) -> CourseFile:
    try:
        return binned_course_file(file_id)
    except CourseFile.DoesNotExist as error:
        raise NotFound from error


def _refused(message: str) -> Response:
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


def _unavailable(message: str) -> Response:
    return Response({"detail": message}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


def _link(request: Request, file: CourseFile) -> Response:
    query = DocumentLinkQuerySerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    link = course_file_link(
        file, as_attachment=query.as_attachment(), by=get_request_staff(request)
    )
    return Response({"url": link, "expires_in": SIGNED_LINK_SECONDS})


class _CourseFileView(APIView):
    permission_classes = (IsAuthenticated, CanReadCatalogue, CanChangeCatalogue)


class CourseFileListView(_CourseFileView):
    """Upload a PDF to a Course (POST; Admins)."""

    parser_classes = (MultiPartParser,)
    http_method_names = ("post", "options")

    def post(self, request: Request, course_id: int) -> Response:
        """Store the PDF and return the Course File as kept."""
        course = _course(course_id)
        serializer = NewCourseFileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            file = add_course_file(
                course, serializer.to_new_course_file(), by=get_request_staff(request)
            )
        except CourseNotLiveError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(COURSE_NOT_LIVE_MESSAGE)
        except TooManyCourseFilesError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(TOO_MANY_MESSAGE)
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return _unavailable(NOT_STORED_MESSAGE)
        return Response(course_file_answer(file), status=status.HTTP_201_CREATED)


class CourseFileView(_CourseFileView):
    """Rename a Course File (PATCH) or put it in the Bin (DELETE); Admins."""

    parser_classes = (JSONParser,)
    http_method_names = ("patch", "delete", "options")

    def patch(self, request: Request, file_id: int) -> Response:
        """Give it a new name and return it as kept."""
        file = _live(file_id)
        serializer = CourseFileRenameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rename_course_file(file, serializer.name_value(), by=get_request_staff(request))
        return Response(course_file_answer(file))

    def delete(self, request: Request, file_id: int) -> Response:
        """Put it in the Bin."""
        file = _live(file_id)
        put_course_file_in_bin(file, by=get_request_staff(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class CourseFileLinkView(_CourseFileView):
    """A short-lived link to open or save a Course File in use (GET)."""

    http_method_names = ("get", "options")

    def get(self, request: Request, file_id: int) -> Response:
        """Return the link and how many seconds it works for."""
        return _link(request, _live(file_id))


class BinCourseFileView(APIView):
    """Delete a Course File in the Bin for good (DELETE; Admins)."""

    permission_classes = (IsAuthenticated, IsBinAdmin)
    http_method_names = ("delete", "options")

    def delete(self, request: Request, file_id: int) -> Response:
        """Remove the stored file and the record."""
        file = _binned(file_id)
        try:
            delete_course_file_for_good(file, by=get_request_staff(request))
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return _unavailable(NOT_DELETED_MESSAGE)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BinCourseFileRestoreView(APIView):
    """Restore a Course File from the Bin to its Course (POST; Admins)."""

    permission_classes = (IsAuthenticated, IsBinAdmin)
    parser_classes = (JSONParser,)
    http_method_names = ("post", "options")

    def post(self, request: Request, file_id: int) -> Response:
        """Restore it and return it as kept."""
        file = _binned(file_id)
        NothingSerializer(data=request.data).is_valid(raise_exception=True)
        restore_course_file(file, by=get_request_staff(request))
        return Response(course_file_answer(file))


class BinCourseFileLinkView(APIView):
    """A short-lived link to a Course File in the Bin, for an Admin on the Bin tab (GET)."""

    permission_classes = (IsAuthenticated, IsBinAdmin)
    http_method_names = ("get", "options")

    def get(self, request: Request, file_id: int) -> Response:
        """Return the link and how many seconds it works for."""
        return _link(request, _binned(file_id))
