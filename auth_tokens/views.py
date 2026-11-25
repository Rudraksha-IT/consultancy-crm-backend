"""Refresh and logout, the same on Consultancy hosts (Staff) and the Platform host."""

from __future__ import annotations

import logging
from dataclasses import asdict

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_tokens.exceptions import InvalidTokenError
from auth_tokens.headers import CHALLENGE, REFUSED_MESSAGE
from auth_tokens.models import TokenKind
from auth_tokens.password_tries import PasswordTriesUsedUpError
from auth_tokens.refusals import log_token_refusal
from auth_tokens.serializers import RefreshTokenSerializer
from auth_tokens.services import revoke_refresh_token, rotate_refresh_token
from platform_admin.support_access import end_visit

LOGGED_OUT_MESSAGES = {
    TokenKind.STAFF: "Staff logged out: staff_id=%s",
    TokenKind.PLATFORM_ADMIN: "Platform Admin logged out: platform_admin_id=%s",
    TokenKind.SUPPORT_ACCESS: "Platform Admin left the office: platform_admin_id=%s",
}

logger = logging.getLogger(__name__)


def token_refused(error: InvalidTokenError) -> Response:
    """401 for a token that can't be used here, never saying why (only the log does)."""
    log_token_refusal(error.reason)
    return Response(
        {"detail": REFUSED_MESSAGE},
        status=status.HTTP_401_UNAUTHORIZED,
        headers={"WWW-Authenticate": CHALLENGE},
    )


TOO_MANY_WRONG_MESSAGE = (
    "Too many wrong passwords. Try again in 15 minutes, or reset your password."
)


def password_tries_used_up(error: PasswordTriesUsedUpError) -> Response:
    """429 for a login whose wrong passwords are used up, saying how long to wait."""
    return Response(
        {"detail": TOO_MANY_WRONG_MESSAGE},
        status=status.HTTP_429_TOO_MANY_REQUESTS,
        headers={"Retry-After": str(error.retry_after)},
    )


def _read_refresh_token(request: Request) -> str:
    serializer = RefreshTokenSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    refresh: str = serializer.validated_data["refresh"]
    return refresh


class RefreshView(APIView):
    """Swap a refresh token for a new access and refresh token (rotation)."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    def post(self, request: Request) -> Response:
        """Return `{access, refresh}`; the refresh token sent never works again."""
        try:
            pair = rotate_refresh_token(_read_refresh_token(request))
        except InvalidTokenError as error:
            return token_refused(error)
        return Response(asdict(pair))


class LogoutView(APIView):
    """Log one device out by revoking its refresh token; a Platform Admin leaves the office."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    def post(self, request: Request) -> Response:
        """Revoke the refresh token and return no content."""
        try:
            claims = revoke_refresh_token(_read_refresh_token(request))
        except InvalidTokenError as error:
            return token_refused(error)
        if claims.visit_id is not None:
            # Leaving the office ("Leave this office") ends the Support Access visit.
            end_visit(claims.visit_id)
        logger.info(LOGGED_OUT_MESSAGES[claims.kind], claims.user_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
