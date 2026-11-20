"""`GET /api/health/`: whether this Django answers and reaches its database (ticket 14).

A deploy switches to a new version only once this answers ok (Docker's healthcheck and the
Next.js server's `/healthz`). It runs before every other middleware, so it needs no office host
and no login, and isn't logged per request (it is called every few seconds). It still needs the
internal token: only the Next.js server and the container itself know it. It answers no data:
no version, no names, no error text.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse, JsonResponse

from tenancy.middleware import REFUSED_MESSAGE, has_internal_token

HEALTH_PATH = "/api/health/"
logger = logging.getLogger(__name__)
_NO_STORE = {"Cache-Control": "no-store"}


def ask_database() -> None:
    """One trivial query; raises when the database doesn't answer."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")


def health(request: HttpRequest) -> HttpResponse:
    """Answer the health check: ok, or unavailable without saying why."""
    if request.method != "GET":
        response = JsonResponse({"detail": "Method not allowed."}, status=405, headers=_NO_STORE)
        response["Allow"] = "GET"
        return response
    if not has_internal_token(request):
        logger.warning("internal token refused")
        return JsonResponse({"detail": REFUSED_MESSAGE}, status=403, headers=_NO_STORE)
    try:
        ask_database()
    except DatabaseError:
        # The reason stays out of the log: a database error can name its address.
        logger.warning("health check failed: database")
        return JsonResponse({"status": "unavailable"}, status=503, headers=_NO_STORE)
    return JsonResponse({"status": "ok"}, headers=_NO_STORE)


class HealthMiddleware:
    """Answer `/api/health/` before anything else; pass every other request on."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Wrap the rest of the middleware chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """The health answer for its exact path; everything else goes on unchanged."""
        if request.path == HEALTH_PATH:
            return health(request)
        return self.get_response(request)
