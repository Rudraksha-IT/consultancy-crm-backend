"""REST endpoints for Payments: recording, the receipt, and the Admin's list, edits and deletes.

Recording and receipts are always on (spec 71-73); the list, edits and deletes are part of the
Accounts Module and Admin only (spec 74-76).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from django.core.paginator import EmptyPage, Paginator
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import (
    ONLY_ADMIN_CHANGES_PAYMENTS_MESSAGE,
    ONLY_ADMIN_SEES_PAYMENTS_MESSAGE,
    StudentAction,
    can_act_on_student,
    why_cannot_see_payment,
)
from config.public_ids import pk_for
from modules.permissions import requires_module
from modules.types import Module
from payments.answers import payment_answer, receipt_answer
from payments.models import Payment
from payments.serializers import (
    NewPaymentSerializer,
    NewPaymentsSerializer,
    PaymentListQuerySerializer,
)
from payments.services import (
    change_payment,
    delete_payment,
    fee_type_of_payment,
    find_payments,
    payment_with_student,
    receipt_lines,
    record_payments,
)
from staff.context import get_request_staff
from staff.models import Staff
from students.models import Student
from students.permissions import CanRecordPayments, CanSeeStudents

# Payments per page of the Admin's list.
PAYMENTS_PER_PAGE = 25
AccountsModuleIsOn = requires_module(Module.ACCOUNTS)
# The methods that change one Payment; any other but GET is answered 405.
CHANGING_METHODS = frozenset({"PUT", "DELETE"})


class CanManagePayments(BasePermission):
    """Seeing every Payment, and changing or deleting one, is for Admins (spec 74-75)."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member manage Payments."""
        is_reading = request.method in SAFE_METHODS
        self.message = (
            ONLY_ADMIN_SEES_PAYMENTS_MESSAGE if is_reading else ONLY_ADMIN_CHANGES_PAYMENTS_MESSAGE
        )
        user = request.user
        return isinstance(user, Staff) and can_act_on_student(user, StudentAction.MANAGE_PAYMENTS)


class StudentPaymentsView(APIView):
    """Record one visit's Payments from a Student (POST only: there is no list here; spec 73)."""

    permission_classes = (IsAuthenticated, CanRecordPayments)
    parser_classes = (JSONParser,)

    def post(self, request: Request, student_id: UUID) -> Response:
        """Record a Payment per line, all on one receipt, and return that receipt."""
        staff = get_request_staff(request)
        try:
            student = Student.objects.get(public_id=student_id)
        except Student.DoesNotExist as error:
            raise NotFound from error
        serializer = NewPaymentsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        made = record_payments(student, serializer.to_new_payments(), by=staff)
        return Response(receipt_answer(made[0], made), status=status.HTTP_201_CREATED)


class PaymentListView(APIView):
    """Every Payment, filtered by date, Student and Fee Type, with their total (GET; spec 74)."""

    permission_classes = (IsAuthenticated, AccountsModuleIsOn, CanManagePayments)

    def get(self, request: Request) -> Response:
        """Return one page of matching Payments, newest first, and the total of all of them."""
        query = PaymentListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        found = find_payments(query.to_filters())
        try:
            page = Paginator(found.payments, PAYMENTS_PER_PAGE).page(query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": PAYMENTS_PER_PAGE,
                "total": found.total,
                "results": [payment_answer(payment) for payment in page],
            }
        )


class PaymentView(APIView):
    """One Payment: its receipt (GET), or an Admin's edit (PUT) or delete (DELETE)."""

    parser_classes = (JSONParser,)

    def get_permissions(self) -> Sequence[BasePermission]:
        """Receipts are always on; changes need the Accounts Module and an Admin."""
        if self.request.method in CHANGING_METHODS:
            return (IsAuthenticated(), AccountsModuleIsOn(), CanManagePayments())
        return (IsAuthenticated(), CanSeeStudents())

    def get(self, request: Request, payment_id: UUID) -> Response:
        """Return the receipt with every line on it.

        An Admin sees any; others only one they just recorded (spec 72-73).
        """
        try:
            payment = payment_with_student(_payment_pk(payment_id))
        except Payment.DoesNotExist as error:
            raise NotFound from error
        reason = why_cannot_see_payment(
            get_request_staff(request), payment.recorded_by_id, payment.recorded_at, timezone.now()
        )
        if reason is not None:
            raise PermissionDenied(reason)
        return Response(receipt_answer(payment, receipt_lines(payment)))

    def put(self, request: Request, payment_id: UUID) -> Response:
        """Replace the amount, date, method, Fee Type and note; return the receipt (spec 75-76).

        A hidden Fee Type may stay on the Payment that has it, never be newly chosen.
        """
        kept_type_id = fee_type_of_payment(_payment_pk(payment_id))
        if kept_type_id is None:
            raise NotFound
        serializer = NewPaymentSerializer(data=request.data, kept_type_id=kept_type_id)
        serializer.is_valid(raise_exception=True)
        try:
            payment = change_payment(
                _payment_pk(payment_id), serializer.to_new_payment(), by=get_request_staff(request)
            )
        except Payment.DoesNotExist as error:
            raise NotFound from error
        return Response(receipt_answer(payment, receipt_lines(payment)))

    def delete(self, request: Request, payment_id: UUID) -> Response:
        """Delete it; the Student's History keeps its details (spec 75-76)."""
        try:
            delete_payment(_payment_pk(payment_id), by=get_request_staff(request))
        except Payment.DoesNotExist as error:
            raise NotFound from error
        return Response(status=status.HTTP_204_NO_CONTENT)


def _payment_pk(payment_id: UUID) -> int:
    """The number of the Payment with this UUID in this office; 0 (none) if there is none."""
    return pk_for(Payment.objects.all(), payment_id)
