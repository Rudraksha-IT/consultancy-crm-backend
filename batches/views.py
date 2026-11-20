"""REST endpoints for Batches, shown as "classes" (spec 22, 83-84, 86a, 87).

Every endpoint needs the test-prep Module: while it's off they answer 404 and the data is kept.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from uuid import UUID

from django.core.paginator import EmptyPage, Paginator
from django.db.models import Model, QuerySet
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import BatchAction, can_see_batch, why_cannot_take_attendance
from batches.answers import (
    attendance_day_answer,
    batch_answer,
    batch_with_students_answer,
    enrolled_student_answer,
    student_class_answer,
)
from batches.attendance import check_class_day, classes_of, percents_of, take_attendance
from batches.exceptions import (
    AlreadyInBatchError,
    DayOutsideBatchError,
    LostStudentError,
    NotInBatchError,
)
from batches.models import Batch, BatchMembership, Mark
from batches.permissions import BatchesModuleIsOn, batch_permission
from batches.serializers import (
    MARKED_TWICE_MESSAGE,
    AttendanceSerializer,
    BatchSerializer,
    NewMemberSerializer,
)
from batches.services import (
    active_teachers,
    add_student,
    batches_for,
    change_batch,
    create_batch,
    enrolled_students,
    members_of,
    remove_student,
    with_counts,
)
from config.public_ids import pk_for
from staff.context import get_request_staff
from staff.models import Staff
from students.models import Student
from students.permissions import CanSeeStudents
from tasks.serializers import PageSerializer

# Rows per page of a list.
ROWS_PER_PAGE = 25
ALREADY_IN_MESSAGE = "Already in this class."
LOST_MESSAGE = "A Lost Student can't join a class."


def _permissions(action: BatchAction | None = None) -> tuple[type[BasePermission], ...]:
    """Logged in first, so a logged-out caller can't learn whether the Module is on."""
    return (IsAuthenticated, BatchesModuleIsOn, batch_permission(action))


def _page_answer[T: Model](
    rows: QuerySet[T], request: Request, answer: Callable[[list[T]], list[dict[str, object]]]
) -> Response:
    """One page of `rows`, as every list answers; `answer` turns the page's rows into results."""
    query = PageSerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    try:
        page = Paginator(rows, ROWS_PER_PAGE).page(query.page_number())
    except EmptyPage as error:
        raise NotFound from error
    return Response(
        {
            "count": page.paginator.count,
            "page": page.number,
            "page_size": ROWS_PER_PAGE,
            "results": answer(list(page)),
        }
    )


def _batch(batch_id: int) -> Batch:
    try:
        return with_counts(Batch.objects.all(), timezone.localdate()).get(pk=batch_id)
    except Batch.DoesNotExist as error:
        raise NotFound from error


def _full_answer(batch_id: int, viewer: Staff, today: date) -> dict[str, object]:
    batch = _batch(batch_id)
    return batch_with_students_answer(batch, members_of(batch), viewer, today)


class BatchListView(APIView):
    """The classes I can see (GET; spec 22) and creating one (POST; spec 83)."""

    permission_classes = _permissions()
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return one page of classes: running and upcoming first, then finished."""
        staff = get_request_staff(request)
        today = timezone.localdate()
        return _page_answer(
            batches_for(staff, today),
            request,
            lambda batches: [batch_answer(batch, staff, today) for batch in batches],
        )

    def post(self, request: Request) -> Response:
        """Create the class and return it."""
        staff = get_request_staff(request)
        serializer = BatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch = create_batch(serializer.to_details(), by=staff)
        return Response(
            _full_answer(batch.pk, staff, timezone.localdate()), status=status.HTTP_201_CREATED
        )


class BatchView(APIView):
    """One class with its Students (GET), and replacing what it is (PUT; spec 83)."""

    permission_classes = _permissions()
    parser_classes = (JSONParser,)

    def get(self, request: Request, batch_id: int) -> Response:
        """Return the class; a Teacher finds only the ones they teach."""
        staff = get_request_staff(request)
        batch = _batch(batch_id)
        if not can_see_batch(staff, batch.teacher_id):
            raise NotFound
        return Response(_full_answer(batch.pk, staff, timezone.localdate()))

    def put(self, request: Request, batch_id: int) -> Response:
        """Save the class and return it."""
        staff = get_request_staff(request)
        batch = _batch(batch_id)
        serializer = BatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_batch(batch.pk, serializer.to_details(), by=staff)
        return Response(_full_answer(batch.pk, staff, timezone.localdate()))


class BatchMembersView(APIView):
    """Adding a Student to a class (POST only; spec 84)."""

    permission_classes = _permissions(BatchAction.CHANGE)
    parser_classes = (JSONParser,)

    def post(self, request: Request, batch_id: int) -> Response:
        """Add the Student and return the class."""
        staff = get_request_staff(request)
        batch = _batch(batch_id)
        serializer = NewMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            add_student(batch, serializer.student_to_add(), by=staff)
        except AlreadyInBatchError as error:
            raise ValidationError({"student": [ALREADY_IN_MESSAGE]}) from error
        except LostStudentError as error:
            raise ValidationError({"student": [LOST_MESSAGE]}) from error
        return Response(
            _full_answer(batch.pk, staff, timezone.localdate()), status=status.HTTP_201_CREATED
        )


class BatchMemberView(APIView):
    """Taking a Student out of a class (DELETE only; spec 84)."""

    permission_classes = _permissions(BatchAction.CHANGE)

    def delete(self, request: Request, batch_id: int, student_id: UUID) -> Response:
        """Remove the Student; answer with nothing."""
        batch = _batch(batch_id)
        try:
            remove_student(
                batch, pk_for(Student.objects.all(), student_id), by=get_request_staff(request)
            )
        except BatchMembership.DoesNotExist as error:
            raise NotFound from error
        return Response(status=status.HTTP_204_NO_CONTENT)


class EnrolledStudentsView(APIView):
    """The Students in classes, with the classes each is in (GET; spec 86a)."""

    permission_classes = _permissions(BatchAction.SEE_ENROLMENT)

    def get(self, request: Request) -> Response:
        """Return one page of enrolled Students, by name."""
        return _page_answer(enrolled_students(), request, _enrolled_answers)


class BatchTeachersView(APIView):
    """Who can teach a class: every active Teacher (GET), for the class form."""

    permission_classes = _permissions(BatchAction.CHANGE)

    def get(self, request: Request) -> Response:
        """Return each one's id and name, by name."""
        return Response([{"id": t.pk, "name": t.name} for t in active_teachers()])


def _enrolled_answers(students: list[Student]) -> list[dict[str, object]]:
    shares = percents_of(students)
    return [enrolled_student_answer(student, shares[student.pk]) for student in students]


def _class_day(batch: Batch, text: str) -> date:
    """The day named in the address, if it's one of the class's days; else 400."""
    try:
        day = date.fromisoformat(text)
        check_class_day(batch, day)
    except (ValueError, DayOutsideBatchError) as error:
        raise ValidationError({"day": [_outside_dates_message(batch)]}) from error
    return day


def _outside_dates_message(batch: Batch) -> str:
    return f"Choose a day from {batch.starts_on.isoformat()} to {batch.ends_on.isoformat()}."


def _visible_batch(staff: Staff, batch_id: int) -> Batch:
    """The class, if `staff` may open it; a Teacher finds only the ones they teach."""
    batch = _batch(batch_id)
    if not can_see_batch(staff, batch.teacher_id):
        raise NotFound
    return batch


class AttendanceDayView(APIView):
    """One class day's Attendance: reading it (GET) and taking it (PUT; spec 85).

    Anyone who may open the class reads it; only its Teacher or an Admin takes it.
    """

    permission_classes = _permissions(BatchAction.SEE)
    parser_classes = (JSONParser,)

    def get(self, request: Request, batch_id: int, day: str) -> Response:
        """Return each Student in the class with their mark that day."""
        staff = get_request_staff(request)
        batch = _visible_batch(staff, batch_id)
        on = _class_day(batch, day)
        return Response(attendance_day_answer(batch, on, members_of(batch), staff))

    def put(self, request: Request, batch_id: int, day: str) -> Response:
        """Replace the day's marks and return the day."""
        staff = get_request_staff(request)
        batch = _visible_batch(staff, batch_id)
        reason = why_cannot_take_attendance(staff, batch.teacher_id)
        if reason is not None:
            raise PermissionDenied(reason)
        on = _class_day(batch, day)
        serializer = AttendanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            take_attendance(batch, on, _by_number(serializer.to_marks()), by=staff)
        except NotInBatchError as error:
            raise ValidationError({"marks": [MARKED_TWICE_MESSAGE]}) from error
        return Response(attendance_day_answer(batch, on, members_of(batch), staff))


class StudentClassesView(APIView):
    """The classes one Student is in, with their Attendance, for their profile (GET; spec 86)."""

    permission_classes = (IsAuthenticated, BatchesModuleIsOn, CanSeeStudents)

    def get(self, request: Request, student_id: UUID) -> Response:
        """Return each class by name with the share attended and the latest class days."""
        try:
            student = Student.objects.get(public_id=student_id)
        except Student.DoesNotExist as error:
            raise NotFound from error
        return Response([student_class_answer(entry) for entry in classes_of(student)])


def _by_number(marks: dict[UUID, Mark]) -> dict[int, Mark]:
    """The marks by Student number; a UUID of no Student here becomes 0, which no class holds."""
    numbers = dict(
        Student.objects.filter(public_id__in=marks).values_list("public_id", "pk").iterator()
    )
    return {numbers.get(public_id, 0): mark for public_id, mark in marks.items()}
