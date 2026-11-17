"""REST endpoints for a Staff member's own Settings (spec 22c-22g; issue 38).

Everyone logged in uses them, whatever their Role, and only ever about themselves.
"""

from __future__ import annotations

from dataclasses import asdict

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_tokens.services import list_signed_in_devices
from auth_tokens.types import TokenClaims
from staff.context import get_request_staff
from staff.exceptions import PasswordRulesNotMetError, WrongCurrentPasswordError
from staff.models import Staff
from staff.my_settings import (
    change_my_password,
    notification_choices,
    sign_out_my_other_devices,
    update_my_details,
)
from staff.permissions import HasStaffAccount
from staff.reset_views import RULES_NOT_MET_MESSAGE
from staff.serializers import MyDetailsSerializer, NoBodySerializer, PasswordChangeSerializer

NOT_YOUR_CURRENT_ONE_MESSAGE = "That isn't your current password."


def my_details_answer(staff: Staff) -> dict[str, object]:
    """My details and choices. Never anything about the password or login."""
    return {
        "name": staff.name,
        "mobile": staff.mobile,
        "email": staff.email,
        "job_title": staff.job_title,
        "role": staff.role,
        "joined_on": staff.joined_on.isoformat(),
        "colour_mode": staff.colour_mode,
        "page_swipe": staff.page_swipe,
        "notifications": asdict(notification_choices(staff)),
    }


def _device_of(request: Request) -> str:
    """The device this request comes from, as named in its access token."""
    claims = request.auth
    if not isinstance(claims, TokenClaims):  # pragma: no cover - every caller is logged in
        raise TypeError
    return claims.device


class MyDetailsView(APIView):
    """See or change my own details, light/dark, Swipe between pages and email choices."""

    permission_classes = (IsAuthenticated, HasStaffAccount)
    visitors_may_read = True

    def get(self, request: Request) -> Response:
        """Return my details."""
        return Response(my_details_answer(get_request_staff(request)))

    def patch(self, request: Request) -> Response:
        """Save what was sent; anything an Admin sets is refused as unknown."""
        serializer = MyDetailsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        staff = update_my_details(get_request_staff(request), serializer.to_change())
        return Response(my_details_answer(staff))


class MyPasswordView(APIView):
    """Change my password; every other device is signed out at once."""

    permission_classes = (IsAuthenticated, HasStaffAccount)

    def post(self, request: Request) -> Response:
        """Return `{access, refresh}` for this device, or what's wrong."""
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            tokens = change_my_password(
                get_request_staff(request),
                data["current_password"],
                data["new_password"],
                _device_of(request),
            )
        except PasswordRulesNotMetError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"detail": RULES_NOT_MET_MESSAGE, "unmet_rules": [r.value for r in error.unmet]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except WrongCurrentPasswordError:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"current_password": [NOT_YOUR_CURRENT_ONE_MESSAGE]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(asdict(tokens))


class MyDevicesView(APIView):
    """My Signed-in devices: this one first, then others logged in, then a few logged out."""

    permission_classes = (IsAuthenticated, HasStaffAccount)

    def get(self, request: Request) -> Response:
        """Return each device's name and when it logged in, was last used and logged out.

        Never tokens or ids.
        """
        devices = list_signed_in_devices(get_request_staff(request), _device_of(request))
        return Response(
            [
                {
                    "name": device.name,
                    "signed_in_at": device.signed_in_at.isoformat(),
                    "last_used_at": device.last_used_at.isoformat(),
                    "logged_out_at": (
                        None if device.logged_out_at is None else device.logged_out_at.isoformat()
                    ),
                    "is_current": device.is_current,
                }
                for device in devices
            ]
        )


class SignOutOtherDevicesView(APIView):
    """Sign out of every other device at once."""

    permission_classes = (IsAuthenticated, HasStaffAccount)

    def post(self, request: Request) -> Response:
        """Return `{access, refresh}`: new tokens for this device."""
        NoBodySerializer(data=request.data).is_valid(raise_exception=True)
        tokens = sign_out_my_other_devices(get_request_staff(request), _device_of(request))
        return Response(asdict(tokens))
