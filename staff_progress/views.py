"""REST endpoint for Staff Progress (spec 125, issue 84). GET only.

Always on (part of Staff). An Admin, or a Platform Admin in Support Access, may see anyone in
the office; everyone else only themselves.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.services import is_module_on
from modules.types import Module
from staff.models import Staff
from staff_progress.answers import progress_answer
from staff_progress.permissions import CanSeeStaffProgress
from staff_progress.serializers import ProgressQuerySerializer
from staff_progress.services import progress_of
from tenancy.context import get_request_consultancy


class StaffProgressView(APIView):
    """One Staff member's cards and changes for a period of at most one year."""

    permission_classes = (IsAuthenticated, CanSeeStaffProgress)

    def get(self, request: Request, staff_id: int) -> Response:
        """Return the Progress for `?from=YYYY-MM-DD&to=YYYY-MM-DD`."""
        query = ProgressQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        staff = get_object_or_404(Staff, pk=staff_id)
        start, end = query.days()
        batches_on = is_module_on(get_request_consultancy(request), Module.BATCHES)
        return Response(progress_answer(progress_of(staff, start, end, batches_on=batches_on)))
