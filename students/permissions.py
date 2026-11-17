"""DRF permission checks that ask the one policy in `access.policy`."""

from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from access.policy import TEACHERS_MESSAGE, StudentAction, can_act_on_student
from staff.models import Staff

NOT_FOR_TEACHERS_MESSAGE = TEACHERS_MESSAGE


class CanUseStudentList(BasePermission):
    """Seeing the list needs SEE; adding a Lead needs ADD."""

    message = NOT_FOR_TEACHERS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member do it."""
        user = request.user
        action = StudentAction.SEE if request.method in SAFE_METHODS else StudentAction.ADD
        return isinstance(user, Staff) and can_act_on_student(user, action)


class CanSeeStudents(BasePermission):
    """Every request about one Student needs SEE; changing them is checked per Student."""

    message = NOT_FOR_TEACHERS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member see Students."""
        user = request.user
        return isinstance(user, Staff) and can_act_on_student(user, StudentAction.SEE)


class CanRecordPayments(BasePermission):
    """Recording a Payment from any Student needs RECORD_PAYMENT (spec 71)."""

    message = NOT_FOR_TEACHERS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member take Payments."""
        user = request.user
        return isinstance(user, Staff) and can_act_on_student(user, StudentAction.RECORD_PAYMENT)
