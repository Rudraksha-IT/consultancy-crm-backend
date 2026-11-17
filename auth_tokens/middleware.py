"""Refuses a login token used anywhere but the schema it was issued on (ADR 0002 rule 5)."""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse, JsonResponse

from auth_tokens.exceptions import InvalidTokenError
from auth_tokens.headers import CHALLENGE, REFUSED_MESSAGE, read_bearer_token
from auth_tokens.refusals import log_token_refusal
from auth_tokens.services import read_access_token

MIN_SIGNING_KEY_BYTES = 32
UNAUTHORIZED = 401


class TokenConsultancyMiddleware:
    """Answer 401 when a Bearer token was issued for another schema or another kind of host.

    That is: its `tenant_schema` isn't the request's schema or is missing, or its `kind` doesn't
    belong on this host (a Platform Admin token on a Consultancy). Runs right after the host
    picks the Consultancy, before any view, for every endpoint.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Fail at startup when the signing key is missing or too short to be a real secret."""
        key: str = settings.JWT_SIGNING_KEY
        if len(key.encode()) < MIN_SIGNING_KEY_BYTES:
            message = f"JWT_SIGNING_KEY must be at least {MIN_SIGNING_KEY_BYTES} bytes."
            raise ImproperlyConfigured(message)
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Pass the request on unless it carries a token meant for somewhere else."""
        try:
            raw = read_bearer_token(request.headers)
            if raw is not None:
                read_access_token(raw)
        except InvalidTokenError as error:
            log_token_refusal(error.reason)
            response = JsonResponse({"detail": REFUSED_MESSAGE}, status=UNAUTHORIZED)
            response["WWW-Authenticate"] = CHALLENGE
            return response
        return self.get_response(request)
