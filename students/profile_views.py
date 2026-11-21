"""REST endpoints for one Student: profile, details, Note, History and records (spec 32-37, 45)."""

from __future__ import annotations

from uuid import UUID

from django.core.paginator import EmptyPage, Paginator
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import why_cannot_change
from history.answers import history_entry_answer
from history.serializers import HistoryQuerySerializer
from history.services import history_of
from staff.context import get_request_staff
from students.answers import (
    academic_record_answer,
    student_profile,
    test_score_answer,
    work_experience_answer,
)
from students.exceptions import TooManyEntriesError
from students.models import AcademicRecord, Student, TestScore, WorkExperience
from students.permissions import CanSeeStudents
from students.profile import (
    MAX_ENTRIES_PER_STUDENT,
    add_academic_record,
    add_test_score,
    add_work_experience,
    change_academic_record,
    change_details,
    change_test_score,
    change_work_experience,
    edit_note,
    profile_of,
    remove_entry,
)
from students.profile_serializers import (
    AcademicRecordSerializer,
    NoteSerializer,
    StudentDetailsSerializer,
    TestScoreSerializer,
    WorkExperienceSerializer,
)

HISTORY_PER_PAGE = 50
TOO_MANY_MESSAGE = f"A Student can have at most {MAX_ENTRIES_PER_STUDENT} of these."


def _student(student_id: UUID) -> Student:
    try:
        return profile_of(student_id)
    except Student.DoesNotExist as error:
        raise NotFound from error


def _changeable_student(request: Request, student_id: UUID) -> Student:
    """The Student, if the Staff member may change them; otherwise 403 with the reason."""
    student = _student(student_id)
    reason = why_cannot_change(get_request_staff(request), student)
    if reason is not None:
        raise PermissionDenied(reason)
    return student


def _profile_answer(request: Request, student_id: UUID) -> Response:
    student = _student(student_id)
    return Response(student_profile(student, viewer=get_request_staff(request)))


def _too_many() -> Response:
    return Response({"detail": TOO_MANY_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)


class _StudentView(APIView):
    permission_classes = (IsAuthenticated, CanSeeStudents)


class StudentProfileView(_StudentView):
    """Open one Student (GET) or change some of their details (PATCH)."""

    def get(self, request: Request, student_id: UUID) -> Response:
        """Return the Student's profile, and whether the viewer may change it."""
        return _profile_answer(request, student_id)

    def patch(self, request: Request, student_id: UUID) -> Response:
        """Change the details sent and return the profile as stored."""
        student = _changeable_student(request, student_id)
        serializer = StudentDetailsSerializer(student, data=request.data)
        serializer.is_valid(raise_exception=True)
        change_details(student, serializer.validated_data, by=get_request_staff(request))
        return _profile_answer(request, student_id)


class StudentNoteView(_StudentView):
    """Replace the Student's Note (PUT); History keeps the previous text."""

    def put(self, request: Request, student_id: UUID) -> Response:
        """Save the Note sent and return the profile as stored."""
        student = _changeable_student(request, student_id)
        serializer = NoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        edit_note(student, serializer.validated_data["note"], by=get_request_staff(request))
        return _profile_answer(request, student_id)


class StudentHistoryView(_StudentView):
    """The Student's History, newest first, a page at a time."""

    def get(self, request: Request, student_id: UUID) -> Response:
        """Return one page of the Student's History."""
        student = _student(student_id)
        query = HistoryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            page = Paginator(history_of(student), HISTORY_PER_PAGE).page(query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": HISTORY_PER_PAGE,
                "results": [history_entry_answer(entry) for entry in page],
            }
        )


class AcademicRecordListView(_StudentView):
    """Add a qualification to the Student's academic history."""

    def post(self, request: Request, student_id: UUID) -> Response:
        """Add it and return it as stored."""
        student = _changeable_student(request, student_id)
        serializer = AcademicRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            entry = add_academic_record(student, serializer.validated_data)
        except TooManyEntriesError:
            # expected refusal, answered with its status; the request line logs it
            return _too_many()
        return Response(academic_record_answer(entry), status=status.HTTP_201_CREATED)


class AcademicRecordView(_StudentView):
    """Change (PATCH) or remove (DELETE) one qualification."""

    def patch(self, request: Request, student_id: UUID, entry_id: UUID) -> Response:
        """Change the details sent and return it as stored."""
        student = _changeable_student(request, student_id)
        entry = get_object_or_404(AcademicRecord, public_id=entry_id, student=student)
        serializer = AcademicRecordSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        change_academic_record(entry, serializer.validated_data)
        return Response(academic_record_answer(entry))

    def delete(self, request: Request, student_id: UUID, entry_id: UUID) -> Response:
        """Remove it."""
        student = _changeable_student(request, student_id)
        remove_entry(get_object_or_404(AcademicRecord, public_id=entry_id, student=student))
        return Response(status=status.HTTP_204_NO_CONTENT)


class TestScoreListView(_StudentView):
    """Add an English test result to the Student."""

    def post(self, request: Request, student_id: UUID) -> Response:
        """Add it and return it as stored."""
        student = _changeable_student(request, student_id)
        serializer = TestScoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            score = add_test_score(student, serializer.validated_data)
        except TooManyEntriesError:
            # expected refusal, answered with its status; the request line logs it
            return _too_many()
        return Response(test_score_answer(score), status=status.HTTP_201_CREATED)


class TestScoreView(_StudentView):
    """Change (PATCH) or remove (DELETE) one test result."""

    def patch(self, request: Request, student_id: UUID, entry_id: UUID) -> Response:
        """Change the details sent and return it as stored."""
        student = _changeable_student(request, student_id)
        score = get_object_or_404(TestScore, public_id=entry_id, student=student)
        serializer = TestScoreSerializer(data=request.data, stored=score, partial=True)
        serializer.is_valid(raise_exception=True)
        change_test_score(score, serializer.validated_data)
        return Response(test_score_answer(score))

    def delete(self, request: Request, student_id: UUID, entry_id: UUID) -> Response:
        """Remove it."""
        student = _changeable_student(request, student_id)
        remove_entry(get_object_or_404(TestScore, public_id=entry_id, student=student))
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkExperienceListView(_StudentView):
    """Add a job to the Student's work experience (spec 35e)."""

    def post(self, request: Request, student_id: UUID) -> Response:
        """Add it and return it as stored."""
        student = _changeable_student(request, student_id)
        serializer = WorkExperienceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            job = add_work_experience(student, serializer.validated_data)
        except TooManyEntriesError:
            # expected refusal, answered with its status; the request line logs it
            return _too_many()
        return Response(work_experience_answer(job), status=status.HTTP_201_CREATED)


class WorkExperienceView(_StudentView):
    """Change (PATCH) or remove (DELETE) one job."""

    def patch(self, request: Request, student_id: UUID, entry_id: UUID) -> Response:
        """Change the details sent and return it as stored."""
        student = _changeable_student(request, student_id)
        job = get_object_or_404(WorkExperience, public_id=entry_id, student=student)
        serializer = WorkExperienceSerializer(data=request.data, stored=job, partial=True)
        serializer.is_valid(raise_exception=True)
        change_work_experience(job, serializer.validated_data)
        return Response(work_experience_answer(job))

    def delete(self, request: Request, student_id: UUID, entry_id: UUID) -> Response:
        """Remove it."""
        student = _changeable_student(request, student_id)
        remove_entry(get_object_or_404(WorkExperience, public_id=entry_id, student=student))
        return Response(status=status.HTTP_204_NO_CONTENT)
