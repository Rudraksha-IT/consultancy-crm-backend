"""The Platform Admins tab and the Platform's Join page (issue 65). Platform host only."""

from __future__ import annotations

from dataclasses import asdict

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from codes.exceptions import CodeLimitReachedError, InvalidCodeError
from config.responses import AnswerThenRun
from platform_admin.auth import get_request_platform_admin
from platform_admin.exceptions import PlatformAdminDetailsError
from platform_admin.models import PlatformAdmin
from platform_admin.permissions import IsPlatformAdmin
from platform_admin.serializers import AddAdminSerializer, PlatformResetConfirmSerializer
from platform_admin.team import (
    TeamChangeRefusedError,
    check_join_code,
    complete_join,
    deactivate_platform_admin,
    invite_platform_admin,
    is_invited,
    list_platform_admins,
    reactivate_platform_admin,
    resend_join_code,
    row_of,
    send_join_code_again,
)
from platform_admin.types import PlatformAdminDetails
from staff.exceptions import PasswordRulesNotMetError
from staff.reset_views import (
    CODE_ACCEPTED_MESSAGE,
    CODE_REFUSED_MESSAGE,
    CODE_SENT_MESSAGE,
    RULES_NOT_MET_MESSAGE,
)
from staff.serializers import ResetCodeSerializer, ResetRequestSerializer

JOIN_PATH = "/platform/join"
JOINED_MESSAGE = "Your password is set. Log in with it."  # a message
TOO_MANY_CODES_MESSAGE = "Too many codes were sent to them this hour. Try again later."
CODE_SENT_AGAIN_MESSAGE = "A new code is on its way."


def _join_link(request: Request) -> str:
    """The Platform's Join page, on the address and scheme the Platform was opened on."""
    return f"{request.scheme or 'https'}://{request.get_host()}{JOIN_PATH}"


def _refused(message: str) -> Response:
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


class PlatformAdminsView(APIView):
    """List every Platform Admin, or invite one by Code."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return every Platform Admin with their status; never a password or secret."""
        me = get_request_platform_admin(request)
        return Response([asdict(row) for row in list_platform_admins(me)])

    def post(self, request: Request) -> Response:
        """Invite `{name, email}`; answer 201 with their row."""
        serializer = AddAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        me = get_request_platform_admin(request)
        try:
            admin = invite_platform_admin(
                PlatformAdminDetails(name=data["name"], email=data["email"]),
                join_link=_join_link(request),
                by=me,
            )
        except PlatformAdminDetailsError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {k: [m] for k, m in error.problems.items()}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(asdict(row_of(admin, me)), status=status.HTTP_201_CREATED)


class PlatformAdminResendView(APIView):
    """Send the join Code again to someone still invited."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (JSONParser,)

    def post(self, request: Request, admin_id: int) -> Response:
        """Answer 200, or 400 when they aren't invited any more."""
        admin = get_object_or_404(PlatformAdmin, pk=admin_id)
        try:
            resend_join_code(
                admin, join_link=_join_link(request), by=get_request_platform_admin(request)
            )
        except TeamChangeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error.message)
        except CodeLimitReachedError:
            # expected refusal; issue_code logged it at WARNING
            return Response(
                {"detail": TOO_MANY_CODES_MESSAGE}, status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        return Response({"detail": CODE_SENT_AGAIN_MESSAGE})


class PlatformAdminDeactivateView(APIView):
    """Deactivate another Platform Admin (or cancel an invite)."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (JSONParser,)

    def post(self, request: Request, admin_id: int) -> Response:
        """Answer 200 with their row, or 400 with why not."""
        admin = get_object_or_404(PlatformAdmin, pk=admin_id)
        me = get_request_platform_admin(request)
        try:
            saved = deactivate_platform_admin(admin, by=me)
        except TeamChangeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error.message)
        return Response(asdict(row_of(saved, me)))


class PlatformAdminReactivateView(APIView):
    """Bring a deactivated Platform Admin back (a cancelled invite gets a new Code)."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (JSONParser,)

    def post(self, request: Request, admin_id: int) -> Response:
        """Answer 200 with their row, or 400 when they aren't deactivated."""
        admin = get_object_or_404(PlatformAdmin, pk=admin_id)
        me = get_request_platform_admin(request)
        try:
            saved = reactivate_platform_admin(admin, join_link=_join_link(request), by=me)
        except TeamChangeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error.message)
        return Response(asdict(row_of(saved, me)))


class _OpenView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    parser_classes = (JSONParser,)


class PlatformJoinRequestView(_OpenView):
    """The Join page's "Send me a new code": always the same answer; the email goes after it."""

    def post(self, request: Request) -> Response:
        """Answer 202 for every valid email."""
        serializer = ResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email: str = serializer.validated_data["email"]
        link = _join_link(request)
        return AnswerThenRun(
            lambda: send_join_code_again(email, link),
            {"detail": CODE_SENT_MESSAGE},
            status=status.HTTP_202_ACCEPTED,
        )


class PlatformJoinVerifyView(_OpenView):
    """Check a join Code without using it up."""

    def post(self, request: Request) -> Response:
        """Return 200 for the right Code, 400 otherwise."""
        serializer = ResetCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            check_join_code(data["email"], data["code"])
        except InvalidCodeError:
            # expected refusal; the service logs it at WARNING
            return _refused(CODE_REFUSED_MESSAGE)
        return Response({"detail": CODE_ACCEPTED_MESSAGE})


class PlatformJoinCompleteView(_OpenView):
    """Choose the password with the Code; the person then logs in (two-step login as usual)."""

    def post(self, request: Request) -> Response:
        """Return 200 with a short message (no tokens), or what's wrong."""
        serializer = PlatformResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            complete_join(data["email"], data["code"], data["new_password"])
        except PasswordRulesNotMetError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"detail": RULES_NOT_MET_MESSAGE, "unmet_rules": [r.value for r in error.unmet]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except InvalidCodeError:
            # expected refusal; the service logs it at WARNING
            return _refused(CODE_REFUSED_MESSAGE)
        return Response({"detail": JOINED_MESSAGE})


class PlatformLoginStepView(_OpenView):
    """Email-first login (issue 66): say what comes after the email.

    An invited Platform Admin goes to Join (a fresh Code is emailed); everyone else, an unknown
    email included, gets the password step.
    """

    def post(self, request: Request) -> Response:
        """Answer `{next: "join" | "password"}`."""
        serializer = ResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email: str = serializer.validated_data["email"]
        if not is_invited(email):
            return Response({"next": "password"})
        send_join_code_again(email, _join_link(request))
        return Response({"next": "join"})
