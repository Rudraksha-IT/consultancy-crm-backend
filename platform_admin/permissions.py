from __future__ import annotations

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from platform_admin.models import PlatformAdmin
from staff.models import PlatformVisitor


class IsPlatformAdmin(BasePermission):
    """Allows only a logged-in Platform Admin."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the request's user is a Platform Admin."""
        return isinstance(request.user, PlatformAdmin)


ONLY_PLATFORM_ADMIN_MESSAGE = "Only a Platform Admin can do this."


class IsPlatformVisitor(BasePermission):
    """Allows only a Platform Admin inside the Consultancy during Support Access (issue 27)."""

    message = ONLY_PLATFORM_ADMIN_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the request comes from a Platform Admin on a Support Access visit."""
        return isinstance(request.user, PlatformVisitor)
