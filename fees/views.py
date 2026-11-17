"""REST endpoints for Fees and the Student fees list, part of the Accounts Module (spec 76a-76f).

Every endpoint answers 404 while Accounts is off, for every Role, before any other check.
"""

from __future__ import annotations

from django.core.paginator import EmptyPage, Paginator
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import (
    ONLY_ADMIN_SEES_PAYMENTS_MESSAGE,
    StudentAction,
    can_act_on_student,
    why_cannot,
)
from fees.answers import owing_student_answer, student_fees_answer
from fees.models import Fee
from fees.serializers import (
    NewFeeSerializer,
    NoQuerySerializer,
    StudentFeesListQuerySerializer,
)
from fees.services import (
    add_fee,
    balance_of_student,
    change_fee,
    delete_fee,
    fee_with_student,
    fees_of,
    owing_rows,
    owing_students,
)
from modules.permissions import requires_module
from modules.types import Module
from staff.context import get_request_staff
from staff.models import Staff
from students.models import Student

# Students per page of the Student fees list.
OWING_PER_PAGE = 25
AccountsModuleIsOn = requires_module(Module.ACCOUNTS)


class CanSeeBalances(BasePermission):
    """What Students have paid and still owe is for Admins (spec 73, 76b)."""

    message = ONLY_ADMIN_SEES_PAYMENTS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member see Balances."""
        user = request.user
        return isinstance(user, Staff) and can_act_on_student(user, StudentAction.SEE_BALANCES)


def _check_may_change_fees(staff: Staff, student: Student) -> None:
    """Refuse (403) anyone but the Student's counsellor or an Admin."""
    reason = why_cannot(staff, StudentAction.CHANGE_FEES, student)
    if reason is not None:
        raise PermissionDenied(reason)


def _fees_answer(staff: Staff, student: Student) -> dict[str, object]:
    """The Student's Fees; with the Balance only for those who may see Balances."""
    sees_balance = can_act_on_student(staff, StudentAction.SEE_BALANCES)
    return student_fees_answer(
        fees_of(student), balance_of_student(student) if sees_balance else None
    )


class StudentFeesView(APIView):
    """One Student's Fees and Balance (GET), or a new Fee (POST; spec 76a-76b)."""

    permission_classes = (IsAuthenticated, AccountsModuleIsOn)
    parser_classes = (JSONParser,)

    def _student(self, request: Request, student_id: int) -> Student:
        try:
            student = Student.objects.get(pk=student_id)
        except Student.DoesNotExist as error:
            raise NotFound from error
        _check_may_change_fees(get_request_staff(request), student)
        return student

    def get(self, request: Request, student_id: int) -> Response:
        """Return the Student's Fees, their total and (for Admins) the Balance."""
        NoQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        student = self._student(request, student_id)
        return Response(_fees_answer(get_request_staff(request), student))

    def post(self, request: Request, student_id: int) -> Response:
        """Add a Fee and return the Student's Fees."""
        student = self._student(request, student_id)
        serializer = NewFeeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        staff = get_request_staff(request)
        add_fee(student, serializer.to_new_fee(), by=staff)
        return Response(_fees_answer(staff, student), status=status.HTTP_201_CREATED)


class FeeView(APIView):
    """Change (PUT) or delete (DELETE) one Fee: the Student's counsellor or an Admin (76a)."""

    permission_classes = (IsAuthenticated, AccountsModuleIsOn)
    parser_classes = (JSONParser,)

    def _fee(self, request: Request, fee_id: int) -> Fee:
        try:
            fee = fee_with_student(fee_id)
        except Fee.DoesNotExist as error:
            raise NotFound from error
        _check_may_change_fees(get_request_staff(request), fee.student)
        return fee

    def put(self, request: Request, fee_id: int) -> Response:
        """Replace the purpose and amount, and return the Student's Fees."""
        fee = self._fee(request, fee_id)
        serializer = NewFeeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        staff = get_request_staff(request)
        change_fee(fee.pk, serializer.to_new_fee(), by=staff)
        return Response(_fees_answer(staff, fee.student))

    def delete(self, request: Request, fee_id: int) -> Response:
        """Delete it; the Student's History keeps it."""
        fee = self._fee(request, fee_id)
        delete_fee(fee.pk, by=get_request_staff(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class StudentFeesListView(APIView):
    """Every Student who still owes, most first, with the office's totals (GET; spec 76b)."""

    permission_classes = (IsAuthenticated, AccountsModuleIsOn, CanSeeBalances)

    def get(self, request: Request) -> Response:
        """Return one page of Students who owe, and the totals across everyone with Fees."""
        query = StudentFeesListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        found = owing_students()
        try:
            page = Paginator(found.rows, OWING_PER_PAGE).page(query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        totals = found.totals
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": OWING_PER_PAGE,
                "totals": {
                    "agreed": totals.agreed,
                    "collected": totals.collected,
                    "still_to_collect": totals.still_to_collect,
                    "students": totals.students,
                    "owing": totals.owing,
                },
                "results": [owing_student_answer(row) for row in owing_rows(page)],
            }
        )
