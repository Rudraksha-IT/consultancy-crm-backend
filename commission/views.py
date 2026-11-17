"""REST endpoints for Commission, part of the Accounts Module and for Admins only (spec 76d-e).

Every endpoint answers 404 while Accounts is off, for every Role, before any other check.
"""

from __future__ import annotations

from django.core.paginator import EmptyPage, Paginator
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import ONLY_ADMIN_COMMISSION_MESSAGE, why_cannot_manage_commission
from commission.answers import commission_answer, totals_answer
from commission.models import Commission
from commission.serializers import (
    CommissionListQuerySerializer,
    NewCommissionSerializer,
    ReceivedQuerySerializer,
    StatusSerializer,
)
from commission.services import (
    add_commission,
    change_status,
    commission_totals,
    every_commission,
    received_between,
)
from modules.permissions import requires_module
from modules.types import Module
from staff.context import get_request_staff
from staff.models import Staff

# Commission per page of the list.
COMMISSION_PER_PAGE = 25
AccountsModuleIsOn = requires_module(Module.ACCOUNTS)


class CanManageCommission(BasePermission):
    """Commission is for Admins (spec 76d)."""

    message = ONLY_ADMIN_COMMISSION_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member see and change Commission."""
        user = request.user
        return isinstance(user, Staff) and why_cannot_manage_commission(user) is None


PERMISSIONS = (IsAuthenticated, AccountsModuleIsOn, CanManageCommission)


class CommissionListView(APIView):
    """Every Commission, newest first, with totals by status (GET); or new Commission (POST)."""

    permission_classes = PERMISSIONS
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return one page of Commission and the totals across all of it."""
        query = CommissionListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            page = Paginator(every_commission(), COMMISSION_PER_PAGE).page(query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": COMMISSION_PER_PAGE,
                "totals": totals_answer(commission_totals()),
                "results": [commission_answer(commission) for commission in page],
            }
        )

    def post(self, request: Request) -> Response:
        """Record Commission, Waiting for enrolment, and return it."""
        serializer = NewCommissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        commission = add_commission(serializer.to_new_commission(), by=get_request_staff(request))
        return Response(commission_answer(commission), status=status.HTTP_201_CREATED)


class CommissionStatusView(APIView):
    """Change one Commission's status (PUT); Received records the day (spec 76d)."""

    permission_classes = PERMISSIONS
    parser_classes = (JSONParser,)

    def put(self, request: Request, commission_id: int) -> Response:
        """Set the status and return the Commission."""
        serializer = StatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            commission = change_status(
                commission_id, serializer.new_status(), by=get_request_staff(request)
            )
        except Commission.DoesNotExist as error:
            raise NotFound from error
        return Response(commission_answer(commission))


class ReceivedCommissionView(APIView):
    """Commission Received between two days: money in from Universities (GET; spec 76e)."""

    permission_classes = PERMISSIONS

    def get(self, request: Request) -> Response:
        """Return how much was Received, and how many."""
        query = ReceivedQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        received = received_between(query.to_period())
        return Response({"total": received.total, "count": received.count})
