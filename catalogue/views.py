"""REST endpoints for the Catalogue: Universities, Courses and Intakes (spec 46-47, 50-51)."""

from __future__ import annotations

from django.core.paginator import EmptyPage
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from catalogue.answers import course_answer, import_answer, university_answer
from catalogue.csv_import import CatalogueFileError, parse_catalogue_csv
from catalogue.edit_serializers import (
    UniversityEditSerializer,
    kept_files_message,
    used_by_message,
)
from catalogue.exceptions import (
    DuplicateNameError,
    InUseError,
    KeepsFilesError,
    NotArchivedError,
)
from catalogue.models import Course, University
from catalogue.permissions import CanChangeCatalogue, CanReadCatalogue
from catalogue.serializers import (
    CatalogueImportSerializer,
    CatalogueIntakesQuerySerializer,
    CatalogueQuerySerializer,
    CourseChangeSerializer,
    NewCourseSerializer,
    NewUniversitySerializer,
    UniversityChangeSerializer,
)
from catalogue.services import (
    UNIVERSITIES_PER_PAGE,
    add_course,
    add_university,
    change_course,
    change_university,
    course_by_id,
    delete_university,
    find_universities,
    import_catalogue,
    page_of,
    save_university_edit,
    university_by_id,
)
from catalogue.upcoming import upcoming_intakes
from staff.context import get_request_staff

UNIVERSITY_NAME_TAKEN_MESSAGE = "There's already a university with this name."
COURSE_NAME_TAKEN_MESSAGE = "This university already has a course with this name."
NAME_TAKEN_MEANWHILE_MESSAGE = "A name was taken meanwhile. Refresh the page and save again."
ARCHIVE_UNIVERSITY_FIRST_MESSAGE = "Archive the university and save before deleting it."


def _university(university_id: int) -> University:
    try:
        return university_by_id(university_id)
    except University.DoesNotExist as error:
        raise NotFound from error


def _course(course_id: int) -> Course:
    try:
        return course_by_id(course_id)
    except Course.DoesNotExist as error:
        raise NotFound from error


def _name_taken(message: str) -> ValidationError:
    return ValidationError({"name": [message]})


class _CatalogueView(APIView):
    permission_classes = (IsAuthenticated, CanReadCatalogue, CanChangeCatalogue)


class UniversityListView(_CatalogueView):
    """List Universities for picking or for the Catalogue page (GET), or add one (POST)."""

    def get(self, request: Request) -> Response:
        """Return one page of Universities by name, with their Courses and Intakes."""
        query = CatalogueQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        universities = find_universities(query.query(), include_archived=query.includes_archived())
        try:
            page = page_of(universities, query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": UNIVERSITIES_PER_PAGE,
                "results": [university_answer(university) for university in page],
            }
        )

    def post(self, request: Request) -> Response:
        """Add a University and return it as stored."""
        serializer = NewUniversitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name, country = serializer.to_values()
        try:
            university = add_university(name, country, by=get_request_staff(request))
        except DuplicateNameError as error:
            raise _name_taken(UNIVERSITY_NAME_TAKEN_MESSAGE) from error
        return Response(university_answer(university), status=status.HTTP_201_CREATED)


class UniversityView(_CatalogueView):
    """One University: read, save its edit page, change some fields, or delete it (issue 92).

    GET reads it, PUT saves the edit page, PATCH changes some fields, DELETE removes it once
    archived.
    """

    def get(self, request: Request, university_id: int) -> Response:
        """Return the University with every Course, archived ones included."""
        return Response(university_answer(_university(university_id)))

    def put(self, request: Request, university_id: int) -> Response:
        """Save the University and every Course on its edit page at once, or nothing."""
        university = _university(university_id)
        serializer = UniversityEditSerializer(university, request.data)
        serializer.is_valid(raise_exception=True)
        try:
            save_university_edit(university, serializer.edit(), by=get_request_staff(request))
        except DuplicateNameError as error:
            raise ValidationError({"detail": [NAME_TAKEN_MEANWHILE_MESSAGE]}) from error
        except InUseError as error:
            message = used_by_message(error.count, error.what)
            raise ValidationError({"deleted_courses": [message]}) from error
        return Response(university_answer(_university(university_id)))

    def delete(self, request: Request, university_id: int) -> Response:
        """Delete an archived University and its Courses, unless anything uses it."""
        university = _university(university_id)
        try:
            delete_university(university, by=get_request_staff(request))
        except NotArchivedError as error:
            raise ValidationError({"detail": ARCHIVE_UNIVERSITY_FIRST_MESSAGE}) from error
        except KeepsFilesError as error:
            message = kept_files_message(error.count, of_courses=True)
            raise ValidationError({"detail": message}) from error
        except InUseError as error:
            raise ValidationError({"detail": used_by_message(error.count, error.what)}) from error
        return Response(status=status.HTTP_204_NO_CONTENT)

    def patch(self, request: Request, university_id: int) -> Response:
        """Change the fields sent and return the University as stored."""
        university = _university(university_id)
        serializer = UniversityChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            change_university(university, serializer.to_change(), by=get_request_staff(request))
        except DuplicateNameError as error:
            raise _name_taken(UNIVERSITY_NAME_TAKEN_MESSAGE) from error
        return Response(university_answer(_university(university_id)))


class CourseListView(_CatalogueView):
    """Add a Course to one University (POST)."""

    def post(self, request: Request, university_id: int) -> Response:
        """Add the Course with its Intakes and return it as stored."""
        university = _university(university_id)
        serializer = NewCourseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        details = serializer.to_new_course()
        try:
            course = add_course(university, details, by=get_request_staff(request))
        except DuplicateNameError as error:
            raise _name_taken(COURSE_NAME_TAKEN_MESSAGE) from error
        return Response(course_answer(course), status=status.HTTP_201_CREATED)


class CourseView(_CatalogueView):
    """Change, archive or restore one Course (PATCH)."""

    def patch(self, request: Request, course_id: int) -> Response:
        """Change the fields sent and return the Course as stored."""
        course = _course(course_id)
        serializer = CourseChangeSerializer(course, request.data)
        serializer.is_valid(raise_exception=True)
        try:
            change_course(course, serializer.to_change(), by=get_request_staff(request))
        except DuplicateNameError as error:
            raise _name_taken(COURSE_NAME_TAKEN_MESSAGE) from error
        return Response(course_answer(_course(course_id)))


class CatalogueImportView(_CatalogueView):
    """Import Universities and Courses from a CSV file's text (POST; spec 48-49)."""

    def post(self, request: Request) -> Response:
        """Import the good rows and answer with the counts and every bad row's problems."""
        serializer = CatalogueImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            parsed = parse_catalogue_csv(serializer.text())
        except CatalogueFileError as error:
            raise ValidationError({"csv": [str(error)]}) from error
        result = import_catalogue(parsed, by=get_request_staff(request))
        return Response(import_answer(result))


class IntakeListView(_CatalogueView):
    """The Catalogue's upcoming Intakes, for a Student's preferred Intake (GET; spec 145)."""

    def get(self, request: Request) -> Response:
        """Return each upcoming month once, oldest first."""
        CatalogueIntakesQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        return Response({"intakes": upcoming_intakes(timezone.localdate())})
