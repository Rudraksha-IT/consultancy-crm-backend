"""Who may use the Catalogue: every Role but Teacher reads it; only Admins change it."""

from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from staff.models import Role, Staff, StaffStatus

ONLY_ADMIN_CATALOGUE_MESSAGE = "Only an Admin can change the Catalogue."
NOT_FOR_TEACHERS_MESSAGE = "Teachers can't see this."
# Roles that pick Universities and Courses, e.g. for Applications (spec 51).
READING_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)


def _active_staff(request: Request) -> Staff | None:
    user = request.user
    return user if isinstance(user, Staff) and user.status == StaffStatus.ACTIVE else None


class CanReadCatalogue(BasePermission):
    """Every request needs a Role that works with Students; Teachers are refused."""

    message = NOT_FOR_TEACHERS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether this Staff member may see the Catalogue."""
        staff = _active_staff(request)
        return staff is not None and staff.role in READING_ROLES


class CanChangeCatalogue(BasePermission):
    """Reading is checked by `CanReadCatalogue`; every change needs an Admin."""

    message = ONLY_ADMIN_CATALOGUE_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether this request reads, or comes from an Admin."""
        if request.method in SAFE_METHODS:
            return True
        staff = _active_staff(request)
        return staff is not None and staff.role == Role.ADMIN
