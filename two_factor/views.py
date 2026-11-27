"""The second step of a login, shared by the Platform (issue 61) and the offices (issue 62).

After the password, a login answers with a challenge instead of tokens; these endpoints take it
(the Next.js server keeps it in its sealed cookie) and log the person in once a number or a
recovery code passes. Each side says how a login is answered: the Platform names the Platform
Admin, an office gives the Staff context.
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_tokens.types import Person
from two_factor.exceptions import (
    ChallengeEndedError,
    TooManyWrongNumbersError,
    WrongNumberError,
)
from two_factor.serializers import ChallengeSerializer, ConfirmSerializer, VerifySerializer
from two_factor.services import (
    begin_setup,
    check_number_of,
    confirm_setup,
    start_challenge,
    use_recovery_code_of,
    verify_with_number,
    verify_with_recovery_code,
)

START_AGAIN_MESSAGE = "This login has timed out or was already used. Log in again."
WRONG_NUMBER_MESSAGE = "That number isn't right. Enter the newest number from your app."
WRONG_RECOVERY_CODE_MESSAGE = "That recovery code isn't right, or it was already used."
TOO_MANY_NUMBERS_MESSAGE = "Too many wrong numbers. Wait 15 minutes, then try again."
WRONG_PASSWORD_MESSAGE = "That isn't your current password."  # noqa: S105  # a message
ALREADY_ON_MESSAGE = "Two-step login is already on."
ALREADY_OFF_MESSAGE = "Two-step login is already off."
NOT_SET_UP_MESSAGE = "Set up two-step login first."
UNAVAILABLE_MESSAGE = "Two-step login can't be used on this server yet."

logger = logging.getLogger(__name__)


def request_host(request: Request) -> str:
    """The host the call came in on (forwarded by the Next.js server), lower case."""
    return request.get_host().lower()


def challenge_answer(person: Person, request: Request) -> Response:
    """A login's answer when two-step login is on: what to do next, and the challenge."""
    challenge = start_challenge(person, request_host(request))
    return Response({"two_factor": challenge.step.value, "challenge": challenge.token})


def start_again() -> Response:
    """The challenge is used up: the person logs in again."""
    return Response(
        {"detail": START_AGAIN_MESSAGE, "start_again": True},
        status=status.HTTP_400_BAD_REQUEST,
    )


def too_many() -> Response:
    """Too many wrong numbers lately."""
    return Response({"detail": TOO_MANY_NUMBERS_MESSAGE}, status=status.HTTP_429_TOO_MANY_REQUESTS)


def wrong(field: str) -> Response:
    """The number (`code`) or the recovery code was refused."""
    message = WRONG_RECOVERY_CODE_MESSAGE if field == "recovery_code" else WRONG_NUMBER_MESSAGE
    return Response({field: [message]}, status=status.HTTP_400_BAD_REQUEST)


def bad(detail: str) -> Response:
    """A short 400 refusal."""
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


def device_of(request: Request) -> str:
    """The device a logged-in request comes from, as named in its access token."""
    device = getattr(request.auth, "device", None)
    if not isinstance(device, str):  # pragma: no cover - every caller is logged in
        raise TypeError
    return device


def check_second_step(person: Person, data: dict[str, str]) -> Response | None:
    """The refusal for a wrong number or recovery code of the person's own, or None if right."""
    field = "code" if "code" in data else "recovery_code"
    try:
        if field == "code":
            check_number_of(person, data["code"])
        else:
            use_recovery_code_of(person, data["recovery_code"])
    except WrongNumberError:
        # expected refusal, answered with its status; the service logs it
        return wrong(field)
    except TooManyWrongNumbersError:
        # expected refusal, answered with its status; the service logs it
        return too_many()
    return None


def check_number(person: Person, code: str) -> Response | None:
    """The refusal for a wrong number of the person's own, or None when it is right."""
    return check_second_step(person, {"code": code})


class _ChallengeView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    parser_classes = (JSONParser,)


class _LoginStepView(_ChallengeView):
    def logged_in(self, request: Request, person: Person, **extra: object) -> Response:
        """Log the person in: the tokens and what this side shows after a login."""
        raise NotImplementedError  # pragma: no cover - each side answers its own way


class BaseTwoFactorSetupView(_ChallengeView):
    """Show the new app's secret once, as an `otpauth://` URI for the QR code."""

    def post(self, request: Request) -> Response:
        """Return `{uri}` for a setup challenge."""
        serializer = ChallengeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            _, uri = begin_setup(serializer.validated_data["challenge"], request_host(request))
        except ChallengeEndedError:
            # expected refusal, answered with its status; the service logs a wrong host
            return start_again()
        return Response({"uri": uri})


class BaseTwoFactorConfirmView(_LoginStepView):
    """Confirm the new app with one number: log in, and show the recovery codes once."""

    def post(self, request: Request) -> Response:
        """Return the login, with `recovery_codes`."""
        serializer = ConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            person, codes = confirm_setup(data["challenge"], data["code"], request_host(request))
        except ChallengeEndedError:
            # expected refusal, answered with its status; the service logs a wrong host
            return start_again()
        except WrongNumberError:
            # expected refusal, answered with its status; the service logs it
            return wrong("code")
        except TooManyWrongNumbersError:
            # expected refusal, answered with its status; the service logs it
            return too_many()
        return self.logged_in(request, person, recovery_codes=codes)


class BaseTwoFactorVerifyView(_LoginStepView):
    """The second step of a login: a number from the app, or a recovery code."""

    def post(self, request: Request) -> Response:
        """Return the login."""
        serializer = VerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        host = request_host(request)
        field = "code" if "code" in data else "recovery_code"
        try:
            if field == "code":
                person = verify_with_number(data["challenge"], data["code"], host)
            else:
                person = verify_with_recovery_code(data["challenge"], data["recovery_code"], host)
        except ChallengeEndedError:
            # expected refusal, answered with its status; the service logs a wrong host
            return start_again()
        except WrongNumberError:
            # expected refusal, answered with its status; the service logs it
            return wrong(field)
        except TooManyWrongNumbersError:
            # expected refusal, answered with its status; the service logs it
            return too_many()
        return self.logged_in(request, person)
