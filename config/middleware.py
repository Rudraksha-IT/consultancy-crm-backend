"""One line per request, with the request's correlation ID (ai-backend's CustomLogMiddleware).

`"<METHOD> <path> <status> <n>ms"`: INFO, or WARNING when slower than `LOG_SLOW_REQUEST_MS`
(issue 58). Runs first, so even refused requests are logged and answered with their
correlation ID.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import monotonic

from django.conf import settings
from django.core.signals import request_finished
from django.http import HttpRequest, HttpResponse

from config.correlation import (
    CORRELATION_ID_HEADER,
    accept_correlation_id,
    clear_correlation_id,
    set_correlation_id,
)
from tenancy.context import activate_public_schema

logger = logging.getLogger(__name__)
_MS_PER_SECOND = 1000
# Django logs a failed response ("Bad Request: ...") after the middleware returns, so the ID
# stays current until the request has finished.
request_finished.connect(clear_correlation_id, dispatch_uid="clear_correlation_id")


def _printable(text: str) -> str:
    """Escape newlines and other control or non-ASCII characters, so a path can't forge a line."""
    return text.encode("unicode_escape").decode("ascii")


class RequestLogMiddleware:
    """Keep the caller's correlation ID if it is a UUID (else make one), log, and return it."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Wrap the rest of the middleware chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Handle the request with its correlation ID current, then log its line and duration.

        The connection starts on the public schema, so a request refused before its Consultancy
        is resolved is never logged under the previous request's Consultancy.
        """
        correlation_id = accept_correlation_id(request.headers.get(CORRELATION_ID_HEADER))
        set_correlation_id(correlation_id)
        activate_public_schema()
        started = monotonic()
        response = self.get_response(request)
        duration_ms = round((monotonic() - started) * _MS_PER_SECOND)
        is_slow = duration_ms > settings.LOG_SLOW_REQUEST_MS
        logger.log(
            logging.WARNING if is_slow else logging.INFO,
            "%s %s %s %sms",
            request.method,
            _printable(request.path),
            response.status_code,
            duration_ms,
        )
        response[CORRELATION_ID_HEADER] = correlation_id
        return response
