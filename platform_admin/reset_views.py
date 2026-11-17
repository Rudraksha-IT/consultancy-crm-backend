"""Forgot password on the Platform (issue 63): Your email -> Enter your code -> Choose a password.

Served only on the Platform host (config.platform_urls), open to anyone there; none says
whether an email belongs to a Platform Admin. The words are the same as for Staff (issue 08).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from codes.exceptions import InvalidCodeError
from config.responses import AnswerThenRun
from platform_admin.password_reset import (
    check_platform_reset_code,
    reset_platform_password,
    send_platform_reset_code,
)
from platform_admin.serializers import PlatformResetConfirmSerializer
from staff.exceptions import PasswordRulesNotMetError
from staff.reset_views import (
    CODE_ACCEPTED_MESSAGE,
    CODE_REFUSED_MESSAGE,
    CODE_SENT_MESSAGE,
    RULES_NOT_MET_MESSAGE,
)
from staff.serializers import ResetCodeSerializer, ResetRequestSerializer

PASSWORD_CHANGED_MESSAGE = "Your password was changed. Log in with your new password."  # noqa: S105


def _code_refused() -> Response:
    return Response({"detail": CODE_REFUSED_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)


class _OpenView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    parser_classes = (JSONParser,)


class PlatformResetRequestView(_OpenView):
    """Email a reset Code to an active Platform Admin."""

    def post(self, request: Request) -> Response:
        """Answer 202 with the same message for every valid email; the email goes after it."""
        serializer = ResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email: str = serializer.validated_data["email"]
        return AnswerThenRun(
            lambda: send_platform_reset_code(email),
            {"detail": CODE_SENT_MESSAGE},
            status=status.HTTP_202_ACCEPTED,
        )


class PlatformResetVerifyView(_OpenView):
    """Check a reset Code before the password is chosen, without using it up."""

    def post(self, request: Request) -> Response:
        """Return 200 for the right Code, 400 otherwise."""
        serializer = ResetCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            check_platform_reset_code(data["email"], data["code"])
        except InvalidCodeError:
            # expected refusal, answered with its status; the service logs it at WARNING
            return _code_refused()
        return Response({"detail": CODE_ACCEPTED_MESSAGE})


class PlatformResetConfirmView(_OpenView):
    """Set the new password; every Platform session ends and the person logs in again."""

    def post(self, request: Request) -> Response:
        """Return 200 with a short message (no tokens), or what's wrong."""
        serializer = PlatformResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            reset_platform_password(data["email"], data["code"], data["new_password"])
        except PasswordRulesNotMetError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"detail": RULES_NOT_MET_MESSAGE, "unmet_rules": [r.value for r in error.unmet]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except InvalidCodeError:
            # expected refusal, answered with its status; the service logs it at WARNING
            return _code_refused()
        return Response({"detail": PASSWORD_CHANGED_MESSAGE})
