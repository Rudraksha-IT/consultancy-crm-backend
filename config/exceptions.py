"""How the API logs and answers every failure (issue 58).

- An unexpected exception: one ERROR line with the traceback; the answer is a 500 with a
  `reference` (the correlation ID) the user can quote, and nothing about the code.
- A refused login token: one line with its reason code (see `auth_tokens.refusals`).
- A validation error (400): one DEBUG line with the field names, never their values.
- A request Django refuses to read (too many files or fields, or too big): a 400 with a short
  message and one WARNING line, never a 500.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import SuspiciousOperation
from django.http import HttpRequest, JsonResponse
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler, set_rollback

from auth_tokens.refusals import TokenRefusedError, log_token_refusal
from config.correlation import get_correlation_id

logger = logging.getLogger(__name__)
SOMETHING_WENT_WRONG = "Something went wrong."
NO_FIELDS = "-"
TOO_MUCH_SENT = "Too much was sent at once. Send less and try again."


def unexpected_error_body() -> dict[str, str]:
    """The answer to an unexpected error: a short message and the reference to quote."""
    return {"detail": SOMETHING_WENT_WRONG, "reference": get_correlation_id() or ""}


def handle_exception(exc: Exception, context: dict[str, Any]) -> Response:
    """DRF's own handling, plus the logging above; never lets an exception escape."""
    response = exception_handler(exc, context)
    if response is None and isinstance(exc, SuspiciousOperation):
        logger.warning("request refused unread: reason=%s", type(exc).__name__)
        return Response({"detail": TOO_MUCH_SENT}, status=status.HTTP_400_BAD_REQUEST)
    if response is None:
        request = context.get("request")
        logger.error(
            "unexpected error: method=%s path=%s",
            getattr(request, "method", NO_FIELDS),
            getattr(request, "path", NO_FIELDS),
            exc_info=exc,
        )
        set_rollback()
        return Response(unexpected_error_body(), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    if isinstance(exc, TokenRefusedError):
        log_token_refusal(exc.reason)
    elif isinstance(exc, ValidationError):
        fields = ",".join(exc.detail) if isinstance(exc.detail, dict) else NO_FIELDS
        logger.debug("validation failed: fields=%s", fields)
    return response


def server_error(request: HttpRequest) -> JsonResponse:
    """`handler500`: an error outside any view (Django has already logged it, with traceback)."""
    return JsonResponse(unexpected_error_body(), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
