"""Platform → Settings: is email working, and "Send a test email" (spec 2026-11-18 stories 3-4).

Platform Admins only, on the Platform host. The SMTP details are never shown: only the address
emails go out from and the status of the last test.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.conf import settings
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from config.serializers import StrictSerializer
from emails.looks import PRODUCT_NAME
from platform_admin.auth import get_request_platform_admin
from platform_admin.email_check import EmailState, EmailStatus, email_status, send_test_email
from platform_admin.exceptions import TestEmailLimitError
from platform_admin.permissions import IsPlatformAdmin

TEST_FAILED_MESSAGE = "The test email couldn't be sent. Check the email values in the deploy."
TEST_LIMIT_MESSAGE = "Too many test emails. Try again in an hour."
NOTHING_TO_SEND_MESSAGE = "Send nothing with this request."


class _NothingSerializer(StrictSerializer[dict[str, object]]):
    """The test email takes no input: an empty object, or no body at all."""


def _status_answer(shown: EmailStatus) -> dict[str, Any]:
    return {
        "state": shown.state.value,
        "tested_at": None
        if shown.tested_at is None
        else shown.tested_at.isoformat().replace("+00:00", "Z"),
        "reference": shown.reference,
    }


def _settings_answer(request: Request, shown: EmailStatus) -> dict[str, Any]:
    return {
        "email": _status_answer(shown),
        "from_address": settings.DEFAULT_FROM_EMAIL,
        "product_name": PRODUCT_NAME,
        "sent_to": str(get_request_platform_admin(request).email),
    }


class PlatformEmailView(APIView):
    """`{email: {state, tested_at, reference}, from_address, product_name, sent_to}`."""

    permission_classes = (IsPlatformAdmin,)

    def get(self, request: Request) -> Response:
        """Return the email status and where a test email would go."""
        return Response(_settings_answer(request, email_status()))


class PlatformTestEmailView(APIView):
    """Send the signed-in Platform Admin the test email; answer how it went."""

    permission_classes = (IsPlatformAdmin,)

    def post(self, request: Request) -> Response:
        """Send it; a failure answers its reference (never the server's words); 429 at the limit."""
        body = request.data
        if not isinstance(body, Mapping) or not _NothingSerializer(data=body).is_valid():
            return Response({"detail": NOTHING_TO_SEND_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        try:
            shown = send_test_email(get_request_platform_admin(request))
        except TestEmailLimitError:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"detail": TEST_LIMIT_MESSAGE}, status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        answer = _settings_answer(request, shown)
        if shown.state == EmailState.FAILED:
            answer["detail"] = TEST_FAILED_MESSAGE
        return Response(answer)
