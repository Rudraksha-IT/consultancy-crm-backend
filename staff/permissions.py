"""Permission checks on a Staff member's Role, for DRF views."""

from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from staff.models import PlatformVisitor, Role, Staff

ONLY_ADMIN_MESSAGE = "Only an Admin can change the Configuration."
ONLY_ADMIN_EMPLOYEES_MESSAGE = "Only an Admin can manage Employees."
NOT_FOR_TEACHERS_MESSAGE = "Teachers can't see this."


def _has_role(request: Request, roles: tuple[Role, ...]) -> bool:
    user = request.user
    return isinstance(user, Staff) and user.role in roles


class IsAdmin(BasePermission):
    """Allow only a logged-in Staff member whose Role is Admin."""

    message = ONLY_ADMIN_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the request comes from an Admin."""
        return _has_role(request, (Role.ADMIN,))


class IsEmployeesAdmin(IsAdmin):
    """Allow only an Admin to manage the office's Employees."""

    message = ONLY_ADMIN_EMPLOYEES_MESSAGE


class IsNotTeacher(BasePermission):
    """Allow every Role that works with Students: Admin, Counsellor and Front Desk."""

    message = NOT_FOR_TEACHERS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the request comes from Staff who work with Students."""
        return _has_role(request, (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK))


ONLY_STAFF_ACCOUNT_MESSAGE = "A Platform Admin has no account here to change."


class HasStaffAccount(BasePermission):
    """Refuse a Platform Admin on a Support Access visit: they have no Staff account (issue 27).

    Views with `visitors_may_read` let them read (e.g. My details) and refuse only changes.
    """

    message = ONLY_STAFF_ACCOUNT_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the caller is a Staff member, or only reading where that's allowed."""
        if not isinstance(request.user, PlatformVisitor):
            return True
        return bool(getattr(view, "visitors_may_read", False)) and request.method in SAFE_METHODS
