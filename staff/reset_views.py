"""Password reset by emailed Code: Your email -> Enter your code -> Choose a password (spec 16).

All three are open to anyone on the Consultancy's host; none says whether an email is known.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_tokens.browsers import login_browser
from auth_tokens.services import issue_tokens
from codes.exceptions import InvalidCodeError
from staff.context import build_staff_context
from staff.exceptions import PasswordRulesNotMetError
from staff.password_reset import check_reset_code, reset_password, send_reset_code
from staff.serializers import (
    ResetCodeSerializer,
    ResetCompleteSerializer,
    ResetRequestSerializer,
)
from staff.two_factor_views import staff_challenge_answer
from tenancy.context import get_request_consultancy

CODE_SENT_MESSAGE = "If that email belongs to someone here, we've sent them a 6-digit code."
CODE_ACCEPTED_MESSAGE = "That code is right."
CODE_REFUSED_MESSAGE = (
    "That code isn't right or has run out. Check the email, or send yourself a new one."
)
RULES_NOT_MET_MESSAGE = "The password doesn't meet every rule yet."

logger = logging.getLogger(__name__)


def _code_refused() -> Response:
    logger.info("Password reset code refused")
    return Response({"detail": CODE_REFUSED_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)


class _OpenView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)


class ResetRequestView(_OpenView):
    """Email a reset Code to an active Staff member of this Consultancy."""

    def post(self, request: Request) -> Response:
        """Answer the same whether or not anyone has that email."""
        serializer = ResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        office = str(get_request_consultancy(request).name)
        send_reset_code(serializer.validated_data["email"], office)
        return Response({"detail": CODE_SENT_MESSAGE}, status=status.HTTP_202_ACCEPTED)


class ResetCodeView(_OpenView):
    """Check a reset Code before the password is chosen, without using it up."""

    def post(self, request: Request) -> Response:
        """Return 200 for the right Code, 400 otherwise."""
        serializer = ResetCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            check_reset_code(serializer.validated_data["email"], serializer.validated_data["code"])
        except InvalidCodeError:
            # expected refusal, answered with its status; the request line logs it
            return _code_refused()
        return Response({"detail": CODE_ACCEPTED_MESSAGE})


class ResetCompleteView(_OpenView):
    """Set the new password, end every other login and log this device in."""

    def post(self, request: Request) -> Response:
        """Return `{access, refresh, context}` like a login, or what's wrong."""
        serializer = ResetCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            staff = reset_password(data["email"], data["code"], data["password"])
        except PasswordRulesNotMetError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"detail": RULES_NOT_MET_MESSAGE, "unmet_rules": [r.value for r in error.unmet]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except InvalidCodeError:
            # expected refusal, answered with its status; the request line logs it
            return _code_refused()
        if staff.two_factor_required:
            # A reset by Code never skips two-step login (issue 62).
            return staff_challenge_answer(staff, request)
        tokens = issue_tokens(staff, browser=login_browser(request))
        context = build_staff_context(staff, get_request_consultancy(request))
        return Response({**asdict(tokens), "context": asdict(context)})
