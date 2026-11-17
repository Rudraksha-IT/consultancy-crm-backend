"""Joining with an Invite Code: Enter your code -> Choose a password (spec 18b; issue 07).

All three are open to anyone on the Consultancy's host; none says whether an email is invited.
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
from staff.employee_views import office_of
from staff.employees import find_invited
from staff.exceptions import PasswordRulesNotMetError
from staff.joining import check_join_code, join, send_new_join_code
from staff.reset_views import CODE_ACCEPTED_MESSAGE, CODE_REFUSED_MESSAGE, RULES_NOT_MET_MESSAGE
from staff.serializers import JoinCodeSerializer, JoinCompleteSerializer, JoinRequestSerializer
from staff.two_factor_views import staff_challenge_answer
from tenancy.context import get_request_consultancy

NEW_CODE_SENT_MESSAGE = "If that email has an invite here, we've sent it a new 6-digit code."
LOGIN_NEXT_PASSWORD = "password"  # noqa: S105  # the name of a step, not a secret
LOGIN_NEXT_JOIN = "join"

logger = logging.getLogger(__name__)


def _code_refused() -> Response:
    logger.info("Join code refused")
    return Response({"detail": CODE_REFUSED_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)


class _OpenView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)


class JoinRequestView(_OpenView):
    """Send me a new code: email a new join Code to an invited person."""

    def post(self, request: Request) -> Response:
        """Answer the same whether or not the email has an invite."""
        serializer = JoinRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        send_new_join_code(serializer.validated_data["email"], office_of(request))
        return Response({"detail": NEW_CODE_SENT_MESSAGE}, status=status.HTTP_202_ACCEPTED)


class JoinCodeView(_OpenView):
    """Check a join Code before the password is chosen, without using it up."""

    def post(self, request: Request) -> Response:
        """Return 200 for the right Code, 400 otherwise."""
        serializer = JoinCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            check_join_code(serializer.validated_data["email"], serializer.validated_data["code"])
        except InvalidCodeError:
            # expected refusal, answered with its status; the request line logs it
            return _code_refused()
        return Response({"detail": CODE_ACCEPTED_MESSAGE})


class JoinCompleteView(_OpenView):
    """Set the person's own password, make their account active and log them in."""

    def post(self, request: Request) -> Response:
        """Return `{access, refresh, context}` like a login, or what's wrong."""
        serializer = JoinCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            staff = join(data["email"], data["code"], data["password"])
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
            # Joining never skips two-step login (issue 62): setup comes next.
            return staff_challenge_answer(staff, request)
        tokens = issue_tokens(staff, browser=login_browser(request))
        context = build_staff_context(staff, get_request_consultancy(request))
        return Response({**asdict(tokens), "context": asdict(context)})


class LoginStepView(_OpenView):
    """Email-first login (issue 66): say what comes after the email.

    Someone invited here who hasn't joined goes to the Join page, with a fresh Code emailed.
    Everyone else, an unknown email included, gets the password step, so the answer never says
    who has an account.
    """

    def post(self, request: Request) -> Response:
        """Answer `{next: "join" | "password"}`."""
        serializer = JoinRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email: str = serializer.validated_data["email"]
        if find_invited(email) is None:
            return Response({"next": LOGIN_NEXT_PASSWORD})
        send_new_join_code(email, office_of(request))
        return Response({"next": LOGIN_NEXT_JOIN})
