"""My settings -> Email -> Change (spec 2026-11-22 stories 34-52; ticket 04).

Only a logged-in Staff member, for themselves; a Platform Admin in Support Access has no account
here to change. Ask with the new email and password (and the two-step number when it is on),
then confirm with the Code sent to the new email.
"""

from __future__ import annotations

from dataclasses import asdict

from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from codes.exceptions import CodeLimitReachedError, InvalidCodeError
from emails.exceptions import SendFailedError
from staff.context import get_request_staff
from staff.email_change import check_new_email, confirm_email_change, send_email_change_code_to
from staff.email_change_serializers import (
    EmailChangeConfirmSerializer,
    EmailChangeRequestSerializer,
)
from staff.exceptions import EmailTakenError, SameEmailError, WrongCurrentPasswordError
from staff.models import Staff
from staff.permissions import HasStaffAccount
from staff.reset_views import CODE_REFUSED_MESSAGE
from staff.serializers import EMAIL_TAKEN_MESSAGE
from tenancy.context import get_request_consultancy
from two_factor.serializers import NUMBER_MESSAGE
from two_factor.services import is_set_up
from two_factor.views import WRONG_PASSWORD_MESSAGE, check_second_step, device_of

CODE_SENT_MESSAGE = "We've sent a 6-digit code to your new email."
SAME_EMAIL_MESSAGE = "That's already your email."
LIMIT_REACHED_MESSAGE = "Too many codes were sent. Wait an hour, then ask for a new one."
SEND_FAILED_MESSAGE = "The email couldn't be sent. Try again in a moment."


def _refused(field: str, message: str) -> Response:
    return Response({field: [message]}, status=status.HTTP_400_BAD_REQUEST)


class _MyEmailView(APIView):
    permission_classes = (IsAuthenticated, HasStaffAccount)
    parser_classes = (JSONParser,)


class EmailChangeRequestView(_MyEmailView):
    """Email a Code to the new address, once the password (and number) are right."""

    def post(self, request: Request) -> Response:
        """Return 202 once the Code is sent, or what's wrong; nothing changes yet."""
        serializer = EmailChangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        staff = get_request_staff(request)
        needs_number = is_set_up(staff)
        if needs_number and "code" not in data and "recovery_code" not in data:
            return _refused("code", NUMBER_MESSAGE)
        checked = _checked_email(staff, data)
        if isinstance(checked, Response):
            return checked
        if needs_number:
            refused = check_second_step(staff, data)
            if refused is not None:
                return refused
        return _send(staff, checked, request)


def _checked_email(staff: Staff, data: dict[str, str]) -> str | Response:
    """The new email as stored, or the refusal of it or of the password."""
    try:
        return check_new_email(staff, data["email"], data["password"])
    except SameEmailError:
        # expected refusal, answered with its status; the request line logs it
        return _refused("email", SAME_EMAIL_MESSAGE)
    except WrongCurrentPasswordError:
        # expected refusal, answered with its status; the service logs it
        return _refused("password", WRONG_PASSWORD_MESSAGE)
    except EmailTakenError:
        # expected refusal, answered with its status; the request line logs it
        return _refused("email", EMAIL_TAKEN_MESSAGE)


def _send(staff: Staff, new_email: str, request: Request) -> Response:
    """Send the Code and answer how it went."""
    try:
        send_email_change_code_to(staff, new_email, get_request_consultancy(request))
    except CodeLimitReachedError:
        # expected refusal, answered with its status; the Code service logs it
        return Response({"detail": LIMIT_REACHED_MESSAGE}, status=status.HTTP_429_TOO_MANY_REQUESTS)
    except SendFailedError:
        # expected refusal, answered with its status; the outside call logs it
        return Response({"detail": SEND_FAILED_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response({"detail": CODE_SENT_MESSAGE}, status=status.HTTP_202_ACCEPTED)


class EmailChangeConfirmView(_MyEmailView):
    """Make the new email mine with its Code; this device stays logged in."""

    def post(self, request: Request) -> Response:
        """Return `{access, refresh, email}` (the Next.js server keeps the tokens)."""
        serializer = EmailChangeConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        staff = get_request_staff(request)
        try:
            tokens = confirm_email_change(
                staff,
                data["email"],
                data["code"],
                device_of(request),
                get_request_consultancy(request),
            )
        except InvalidCodeError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": CODE_REFUSED_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        except EmailTakenError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("email", EMAIL_TAKEN_MESSAGE)
        return Response({**asdict(tokens), "email": staff.email})
