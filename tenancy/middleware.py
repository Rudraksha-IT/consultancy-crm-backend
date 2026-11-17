"""Refuse calls not from the Next.js server, resolve the Consultancy, then apply its Status.

The host is the one the Next.js server forwards in `X-Forwarded-Host` (ADR 0002).
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.urls import set_urlconf
from django.utils import timezone
from django_tenants.middleware.main import TenantMainMiddleware

from auth_tokens.exceptions import InvalidTokenError
from auth_tokens.headers import read_bearer_token
from auth_tokens.models import TokenKind
from auth_tokens.services import authenticate_access_token, read_access_token
from tenancy.models import Consultancy, Domain, SetupState
from tenancy.services import get_status
from tenancy.status import (
    NOT_FOUND,
    AccessRefusal,
    ConsultancyStatus,
    decide_access,
    lets_support_access_in,
)
from tenancy.subdomains import get_central_login_host, get_platform_host

INTERNAL_TOKEN_HEADER = "X-Internal-Token"  # noqa: S105  # a header name, not a secret
MIN_INTERNAL_TOKEN_LENGTH = 32
REFUSED_MESSAGE = "Forbidden."

logger = logging.getLogger(__name__)


def has_internal_token(request: HttpRequest) -> bool:
    """Whether the request carries the Next.js server's internal token (constant-time compare)."""
    token: str = settings.INTERNAL_API_TOKEN
    sent = request.headers.get(INTERNAL_TOKEN_HEADER, "").encode()
    return len(token) >= MIN_INTERNAL_TOKEN_LENGTH and hmac.compare_digest(sent, token.encode())


class InternalTokenMiddleware:
    """Refuse (403) every request that lacks the Next.js server's internal token.

    Runs first, so a caller without the token learns nothing, not even whether a host exists.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Fail at startup when the token is missing or too short to be a real secret."""
        token: str = settings.INTERNAL_API_TOKEN
        if len(token) < MIN_INTERNAL_TOKEN_LENGTH:
            message = f"INTERNAL_API_TOKEN must be at least {MIN_INTERNAL_TOKEN_LENGTH} characters."
            raise ImproperlyConfigured(message)
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Pass the request on only when it carries the right token (constant-time compare)."""
        if not has_internal_token(request):
            logger.warning("internal token refused")
            return JsonResponse({"detail": REFUSED_MESSAGE}, status=403)
        return self.get_response(request)


def _shared_host_urlconfs() -> dict[str, str]:
    """The hosts that belong to no Consultancy, and the only URLs each one serves."""
    return {
        get_platform_host(): settings.PLATFORM_URLCONF,
        get_central_login_host(): settings.CENTRAL_LOGIN_URLCONF,
    }


class ConsultancyHostMiddleware(TenantMainMiddleware):  # type: ignore[misc]  # untyped base
    """Activate the Consultancy owning the host, or route a shared host to its own URLs.

    The shared hosts are the Platform and the Central login (public schema). Switched-off hosts,
    hosts of Terminated Consultancies, of Consultancies still being set up or whose setup
    failed (issue 60), and unknown hosts get 404 for every request.
    """

    def get_tenant(self, domain_model: type[Domain], hostname: str) -> Consultancy:
        """Return the Consultancy that owns the host (never for a shared host)."""
        if hostname in _shared_host_urlconfs():
            raise domain_model.DoesNotExist
        domain = (
            domain_model.objects.select_related("tenant")
            .exclude(tenant__status=ConsultancyStatus.TERMINATED.value)
            .get(domain=hostname, is_active=True, tenant__setup_state=SetupState.READY)
        )
        consultancy: Consultancy = domain.tenant
        return consultancy

    def no_tenant_found(self, request: HttpRequest, hostname: str) -> None:
        """Serve a shared host's own URLs (public schema); 404 on any other host."""
        urlconf = _shared_host_urlconfs().get(hostname)
        if urlconf is None:
            raise Http404
        request.urlconf = urlconf
        set_urlconf(request.urlconf)


def _is_support_access(request: HttpRequest) -> bool:
    """Whether the call carries a Support Access token of an open visit to this office (issue 27).

    A token of an ended visit doesn't count, even on endpoints that never check who is calling.
    """
    try:
        raw = read_bearer_token(request.headers)
        if raw is None or read_access_token(raw).kind != TokenKind.SUPPORT_ACCESS:
            return False
        authenticate_access_token(raw)
    except InvalidTokenError:  # silent: TokenConsultancyMiddleware or the view logs the refusal
        return False
    return True


def _refusal_for(consultancy: Consultancy, request: HttpRequest) -> AccessRefusal | None:
    status = get_status(consultancy)
    method, path = request.method or "", request.path
    is_visit = lets_support_access_in(
        status, method, path, is_support_access=_is_support_access(request)
    )
    if is_visit:
        return None
    return decide_access(status, consultancy.evaluation_ends_at, timezone.now(), method, path)


class ConsultancyAccessMiddleware:
    """Let a request through only as far as its Consultancy's Status allows (spec 13-13d).

    Runs right after the host picks the Consultancy, for every endpoint: Active and a running
    Evaluation let everything through; Past due and Suspended only reads plus logging in and
    out; an ended Evaluation nothing but what the login screen shows, and logging out. A
    Platform Admin on a Support Access visit is let through on any of them (issue 27). The
    refusal is 403 `{"detail", "status"}`. The Platform host has no Consultancy and is not
    affected.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Keep the next step of the chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Refuse the request if the Consultancy's Status doesn't allow it."""
        consultancy = getattr(request, "tenant", None)
        if isinstance(consultancy, Consultancy):
            refusal = _refusal_for(consultancy, request)
            if refusal is not None and refusal.http_status == NOT_FOUND:
                raise Http404
            if refusal is not None:
                return JsonResponse(refusal.body(), status=refusal.http_status)
        return self.get_response(request)
