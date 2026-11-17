"""Who may see someone's Staff Progress: an Admin anyone in the office, others only themselves."""

from __future__ import annotations

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from staff.models import Role, Staff

ONLY_YOUR_OWN_MESSAGE = "You can only see your own progress."


class CanSeeStaffProgress(BasePermission):
    """Allow an Admin (or a Platform Admin in Support Access) for anyone; others for themselves."""

    message = ONLY_YOUR_OWN_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the logged-in person may see the Progress the address names."""
        user = request.user
        if not isinstance(user, Staff):
            return False
        return user.role == Role.ADMIN or view.kwargs.get("staff_id") == user.pk
