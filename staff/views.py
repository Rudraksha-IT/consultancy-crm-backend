"""REST endpoints for Staff: login (refresh and logout are in auth_tokens) and the context."""

from __future__ import annotations

import logging
from dataclasses import asdict

from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_tokens.browsers import login_browser
from auth_tokens.services import issue_tokens
from staff.context import build_staff_context, get_request_staff
from staff.models import Staff
from staff.serializers import LoginSerializer
from staff.services import normalize_email
from staff.two_factor_views import staff_challenge_answer
from tenancy.context import get_request_consultancy

INVALID_CREDENTIALS_MESSAGE = "Invalid email or password."

logger = logging.getLogger(__name__)


def _context(request: Request, staff: Staff) -> dict[str, object]:
    return asdict(build_staff_context(staff, get_request_consultancy(request)))


class LoginView(APIView):
    """Log a Staff member in to the Consultancy that owns the request's host."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    def post(self, request: Request) -> Response:
        """Return `{access, refresh, context}` for valid, active credentials."""
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        staff = authenticate(
            request._request,
            email=normalize_email(serializer.validated_data["email"]),
            password=serializer.validated_data["password"],
        )
        if not isinstance(staff, Staff):
            logger.info("Staff login failed")
            return Response(
                {"detail": INVALID_CREDENTIALS_MESSAGE}, status=status.HTTP_400_BAD_REQUEST
            )
        if staff.two_factor_required:
            return staff_challenge_answer(staff, request)
        tokens = issue_tokens(staff, browser=login_browser(request))
        logger.info("Staff logged in: staff_id=%s", staff.pk)
        return Response({**asdict(tokens), "context": _context(request, staff)})


class StaffContextView(APIView):
    """Return the current Staff and Consultancy context."""

    def get(self, request: Request) -> Response:
        """Return the logged-in Staff member, Role, Modules on and date display."""
        staff = get_request_staff(request)
        return Response(_context(request, staff))
