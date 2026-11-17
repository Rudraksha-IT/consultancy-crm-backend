"""DRF permission checks for Communication that ask the one policy in `access.policy`."""

from __future__ import annotations

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from access.policy import TEACHERS_MESSAGE, StudentAction, can_act_on_student
from modules.permissions import requires_module
from modules.types import Module
from staff.models import Staff

CommunicationModuleIsOn = requires_module(Module.COMMUNICATION)


class CanMessageStudents(BasePermission):
    """Seeing messages and sending one needs MESSAGE: Admins, counsellors and Front Desk."""

    message = TEACHERS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member message Students."""
        user = request.user
        return isinstance(user, Staff) and can_act_on_student(user, StudentAction.MESSAGE)
