"""Two-step login for Staff, on the Consultancy's own address (issue 62).

The second step of a login (the shared views from issue 61, answering with the Staff context),
an Admin's switch and reset on Employees, and the person's own section in My settings.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import why_cannot_manage_two_factor_of, why_cannot_switch_off_own_two_factor
from auth_tokens.browsers import login_browser
from auth_tokens.services import issue_tokens
from auth_tokens.types import Person
from staff.context import build_staff_context, get_request_staff
from staff.employee_views import employee_answer
from staff.exceptions import EmployeeChangeRefusedError
from staff.models import Staff
from staff.permissions import HasStaffAccount
from staff.serializers import NoBodySerializer, TwoFactorSwitchSerializer
from staff.two_factor import (
    reset_two_factor,
    set_two_factor_required,
    switch_off_myself,
    switch_on_myself,
)
from tenancy.context import get_request_consultancy
from two_factor.exceptions import ChallengeEndedError, TwoFactorUnavailableError
from two_factor.serializers import MyTwoFactorSerializer
from two_factor.services import is_set_up, replace_recovery_codes, state_of
from two_factor.views import (
    ALREADY_OFF_MESSAGE,
    ALREADY_ON_MESSAGE,
    NOT_SET_UP_MESSAGE,
    UNAVAILABLE_MESSAGE,
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

logger = logging.getLogger(__name__)


def staff_challenge_answer(staff: Staff, request: Request) -> Response:
    """A Staff login's answer when two-step login is on: what comes next, and the challenge.

    Used after the password, and after joining or a reset by Code, which never skip it.
    """
    answer = challenge_answer(staff, request)
    logger.info("Staff password passed; two-step next: staff_id=%s", staff.pk)
    return answer


def _as_staff(person: Person) -> Staff:
    """Only Staff have challenges in an office's schema."""
    if not isinstance(person, Staff):  # pragma: no cover - an office holds only Staff
        raise ChallengeEndedError
    return person


def _forbidden(detail: str) -> Response:
    return Response({"detail": detail}, status=status.HTTP_403_FORBIDDEN)


class _StaffLogin:
    def logged_in(self, request: Request, person: Person, **extra: object) -> Response:
        """Return `{access, refresh, context}` and anything extra, like a password login."""
        staff = _as_staff(person)
        tokens = issue_tokens(staff, browser=login_browser(request))
        context = build_staff_context(staff, get_request_consultancy(request))
        logger.info("Staff logged in: staff_id=%s", staff.pk)
        return Response({**asdict(tokens), "context": asdict(context), **extra})


class StaffTwoFactorSetupView(BaseTwoFactorSetupView):
    """Show the new app's secret once, as an `otpauth://` URI for the QR code."""


class StaffTwoFactorConfirmView(_StaffLogin, BaseTwoFactorConfirmView):
    """Confirm the new app with one number: log in, and show the recovery codes once."""


class StaffTwoFactorVerifyView(_StaffLogin, BaseTwoFactorVerifyView):
    """The second step of a Staff login: a number from the app, or a recovery code."""


class _EmployeeTwoFactorView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (JSONParser,)

    def _person(self, request: Request, staff_id: int) -> tuple[Staff, Staff]:
        """The person changed and who changes them."""
        return get_object_or_404(Staff, pk=staff_id), get_request_staff(request)


class EmployeeTwoFactorView(_EmployeeTwoFactorView):
    """An Admin switches someone's two-step login on or off."""

    def patch(self, request: Request, staff_id: int) -> Response:
        """Set `{required}` and return the employee as stored."""
        person, by = self._person(request, staff_id)
        refused = why_cannot_manage_two_factor_of(by, person)
        if refused is not None:
            return _forbidden(refused)
        serializer = TwoFactorSwitchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            set_two_factor_required(person, serializer.validated_data["required"], by)
        except EmployeeChangeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return bad(error.message)
        except TwoFactorUnavailableError:
            logger.warning("two-step login switch-on refused: no secrets key set")
            return bad(UNAVAILABLE_MESSAGE)
        return Response(employee_answer(person))


class EmployeeTwoFactorResetView(_EmployeeTwoFactorView):
    """An Admin resets the two-step login of someone who lost their phone."""

    def post(self, request: Request, staff_id: int) -> Response:
        """Remove their app, sign them out everywhere, and return the employee as stored."""
        person, by = self._person(request, staff_id)
        refused = why_cannot_manage_two_factor_of(by, person)
        if refused is not None:
            return _forbidden(refused)
        NoBodySerializer(data=request.data).is_valid(raise_exception=True)
        try:
            reset_two_factor(person, by)
        except EmployeeChangeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return bad(error.message)
        return Response(employee_answer(person))


def _state(staff: Staff) -> dict[str, object]:
    return {
        "required": staff.two_factor_required,
        **asdict(state_of(staff)),
        "can_switch_off": staff.two_factor_required
        and why_cannot_switch_off_own_two_factor(staff) is None,
    }


class MyTwoFactorView(APIView):
    """My settings: my own two-step login (switch on, switch off, new recovery codes)."""

    permission_classes = (IsAuthenticated, HasStaffAccount)
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return `{required, set_up, recovery_codes_left, can_switch_off}`; never the secret."""
        return Response(_state(get_request_staff(request)))

    def post(self, request: Request) -> Response:
        """Switch on (setup starts at once), switch off, or make new recovery codes."""
        serializer = MyTwoFactorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        staff = get_request_staff(request)
        if data["action"] == "on":
            return self._switch_on(request, staff)
        if data["action"] == "off":
            return self._switch_off(request, staff, data)
        return self._new_recovery_codes(staff, data["code"])

    def _switch_on(self, request: Request, staff: Staff) -> Response:
        if staff.two_factor_required:
            return bad(ALREADY_ON_MESSAGE)
        try:
            switch_on_myself(staff)
        except TwoFactorUnavailableError:
            logger.warning("two-step login switch-on refused: no secrets key set")
            return bad(UNAVAILABLE_MESSAGE)
        return staff_challenge_answer(staff, request)

    def _switch_off(self, request: Request, staff: Staff, data: dict[str, str]) -> Response:
        if not staff.two_factor_required:
            return bad(ALREADY_OFF_MESSAGE)
        refused_by_policy = why_cannot_switch_off_own_two_factor(staff)
        if refused_by_policy is not None:
            return _forbidden(refused_by_policy)
        if not staff.check_password(data["password"]):
            logger.info("two-step login switch-off refused, wrong password: staff_id=%s", staff.pk)
            return Response({"password": [WRONG_PASSWORD_MESSAGE]}, status=400)
        refused = check_second_step(staff, data) if is_set_up(staff) else None
        if refused is not None:
            return refused
        tokens = switch_off_myself(staff, device_of(request))
        return Response({**asdict(tokens), "two_factor": _state(staff)})

    def _new_recovery_codes(self, staff: Staff, code: str) -> Response:
        if not is_set_up(staff):
            return bad(NOT_SET_UP_MESSAGE)
        refused = check_number(staff, code)
        if refused is not None:
            return refused
        return Response({"recovery_codes": replace_recovery_codes(staff)})
