"""Two-step login for Platform Admins, on the Platform subdomain (issue 61).

After the password, the login answers with a challenge instead of tokens; these endpoints take
it (the Next.js server keeps it in its sealed cookie) and give out the tokens once a number or a
recovery code passes. The Security page switches two-step login on and off.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_tokens.services import issue_tokens, sign_out_everywhere
from auth_tokens.types import Device, Person
from platform_admin.auth import get_request_platform_admin
from platform_admin.exceptions import (
    TooManyPasswordTriesError,
    WrongCurrentPlatformPasswordError,
)
from platform_admin.models import PlatformAdmin
from platform_admin.permissions import IsPlatformAdmin
from platform_admin.services import check_current_password
from two_factor.exceptions import ChallengeEndedError, TwoFactorUnavailableError
from two_factor.models import EventKind
from two_factor.serializers import MyTwoFactorSerializer
from two_factor.services import (
    check_available,
    forget,
    is_set_up,
    record,
    replace_recovery_codes,
    state_of,
)
from two_factor.views import (
    ALREADY_OFF_MESSAGE,
    ALREADY_ON_MESSAGE,
    NOT_SET_UP_MESSAGE,
    TOO_MANY_NUMBERS_MESSAGE,
    UNAVAILABLE_MESSAGE,
    WRONG_NUMBER_MESSAGE,
    WRONG_PASSWORD_MESSAGE,
    BaseTwoFactorConfirmView,
    BaseTwoFactorSetupView,
    BaseTwoFactorVerifyView,
    bad,
    challenge_answer,
    check_number,
    check_second_step,
    device_of,
)

__all__ = ["TOO_MANY_NUMBERS_MESSAGE", "WRONG_NUMBER_MESSAGE", "two_factor_login_answer"]

TOO_MANY_PASSWORDS_MESSAGE = "Too many wrong passwords. Wait 15 minutes, then try again."

logger = logging.getLogger(__name__)


def _summary(admin: PlatformAdmin) -> dict[str, object]:
    return {"id": admin.public_id, "name": admin.name, "email": admin.email}


def _as_platform_admin(person: Person) -> PlatformAdmin:
    """Only Platform Admins have challenges on the public schema."""
    if not isinstance(person, PlatformAdmin):  # pragma: no cover - public holds only these
        raise ChallengeEndedError
    return person


def two_factor_login_answer(admin: PlatformAdmin, request: Request) -> Response:
    """The login's answer when two-step login is on: what to do next, and the challenge."""
    answer = challenge_answer(admin, request)
    logger.info("platform admin password passed; two-step next: platform_admin_id=%s", admin.pk)
    return answer


class _PlatformLogin:
    def logged_in(self, request: Request, person: Person, **extra: object) -> Response:
        """Return `{access, refresh, platform_admin}` and anything extra."""
        admin = _as_platform_admin(person)
        tokens = issue_tokens(admin)
        return Response({**asdict(tokens), "platform_admin": _summary(admin), **extra})


class TwoFactorSetupView(BaseTwoFactorSetupView):
    """Show the new app's secret once, as an `otpauth://` URI for the QR code."""


class TwoFactorConfirmView(_PlatformLogin, BaseTwoFactorConfirmView):
    """Confirm the new app with one number: log in, and show the recovery codes once."""


class TwoFactorVerifyView(_PlatformLogin, BaseTwoFactorVerifyView):
    """The second step of a login: a number from the app, or a recovery code."""


def _state(admin: PlatformAdmin) -> dict[str, object]:
    state = state_of(admin)
    return {"required": admin.two_factor_required, **asdict(state)}


class MyTwoFactorView(APIView):
    """The Platform's Security page: two-step login on or off, and new recovery codes."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return `{required, set_up, recovery_codes_left}`; never the secret."""
        return Response(_state(get_request_platform_admin(request)))

    def post(self, request: Request) -> Response:
        """Switch on (setup starts at once), switch off, or make new recovery codes."""
        serializer = MyTwoFactorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        admin = get_request_platform_admin(request)
        if data["action"] == "on":
            return self._switch_on(request, admin)
        if data["action"] == "off":
            return self._switch_off(request, admin, data)
        return self._new_recovery_codes(admin, data["code"])

    def _switch_on(self, request: Request, admin: PlatformAdmin) -> Response:
        if admin.two_factor_required:
            return bad(ALREADY_ON_MESSAGE)
        try:
            check_available()
        except TwoFactorUnavailableError:
            logger.warning("two-step login switch-on refused: no secrets key set")
            return bad(UNAVAILABLE_MESSAGE)
        with transaction.atomic():
            admin.two_factor_required = True
            admin.save(update_fields=["two_factor_required"])
            forget(admin)
            record(admin, EventKind.SWITCHED_ON)
            sign_out_everywhere(admin)
            answer = two_factor_login_answer(admin, request)
        logger.info("two-step login switched on: platform_admin_id=%s", admin.pk)
        return answer

    def _switch_off(self, request: Request, admin: PlatformAdmin, data: dict[str, str]) -> Response:
        if not admin.two_factor_required:
            return bad(ALREADY_OFF_MESSAGE)
        refused = _check_password_and_second_step(admin, data)
        if refused is not None:
            return refused
        with transaction.atomic():
            admin.two_factor_required = False
            admin.save(update_fields=["two_factor_required"])
            forget(admin)
            record(admin, EventKind.SWITCHED_OFF)
            sign_out_everywhere(admin)
            tokens = issue_tokens(admin, Device(id=device_of(request), signed_in_at=timezone.now()))
        logger.info("two-step login switched off: platform_admin_id=%s", admin.pk)
        return Response({**asdict(tokens), "two_factor": _state(admin)})

    def _new_recovery_codes(self, admin: PlatformAdmin, code: str) -> Response:
        if not is_set_up(admin):
            return bad(NOT_SET_UP_MESSAGE)
        refused = check_number(admin, code)
        if refused is not None:
            return refused
        return Response({"recovery_codes": replace_recovery_codes(admin)})


def _check_password_and_second_step(admin: PlatformAdmin, data: dict[str, str]) -> Response | None:
    """The refusal for a wrong password, number or recovery code, or None when all pass."""
    try:
        check_current_password(admin, data["password"])
    except TooManyPasswordTriesError:
        # expected refusal, answered with its status; the service logs it
        return Response(
            {"detail": TOO_MANY_PASSWORDS_MESSAGE}, status=status.HTTP_429_TOO_MANY_REQUESTS
        )
    except WrongCurrentPlatformPasswordError:
        # expected refusal, answered with its status; the service logs it
        return Response({"password": [WRONG_PASSWORD_MESSAGE]}, status=400)
    return check_second_step(admin, data) if is_set_up(admin) else None
