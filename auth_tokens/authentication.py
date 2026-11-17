"""DRF authentication by the access token the Next.js server sends as `Authorization: Bearer`."""

from __future__ import annotations

from rest_framework.authentication import BaseAuthentication
from rest_framework.request import Request

from auth_tokens.exceptions import InvalidTokenError
from auth_tokens.headers import CHALLENGE, read_bearer_token
from auth_tokens.refusals import TokenRefusedError
from auth_tokens.services import authenticate_access_token
from auth_tokens.types import Person, TokenClaims


class BearerTokenAuthentication(BaseAuthentication):
    """Find the person a Bearer access token is for, in the active schema.

    Staff on a Consultancy, Platform Admins on the Platform. Refuses inactive people and tokens
    older than their last sign-out.
    """

    def authenticate(self, request: Request) -> tuple[Person, TokenClaims] | None:
        """Return the person the Bearer token is for and its claims (`request.auth`).

        None when no token was sent.

        Raises:
            TokenRefusedError: The token was sent but can't be accepted here (401); the API's
                exception handler logs why.
        """
        try:
            raw = read_bearer_token(request.headers)
            return None if raw is None else authenticate_access_token(raw)
        except InvalidTokenError as error:
            raise TokenRefusedError(error.reason) from error

    def authenticate_header(self, request: Request) -> str:
        """Answer 401 (not 403) when nobody is logged in, with a Bearer challenge."""
        return CHALLENGE
