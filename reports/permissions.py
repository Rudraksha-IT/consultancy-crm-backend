"""Who may see the Reports: only an active Admin (spec 93-99, issue 28)."""

from __future__ import annotations

from rest_framework.request import Request
from rest_framework.views import APIView

from staff.models import Staff, StaffStatus
from staff.permissions import IsAdmin

ONLY_ADMIN_REPORTS_MESSAGE = "Only an Admin can see reports."


class IsReportsAdmin(IsAdmin):
    """Allow only an active Admin to see the Reports."""

    message = ONLY_ADMIN_REPORTS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the request comes from an active Admin."""
        user = request.user
        return (
            super().has_permission(request, view)
            and isinstance(user, Staff)
            and user.status == StaffStatus.ACTIVE
        )
