"""Who is logged in on the Platform subdomain."""

from __future__ import annotations

from django.http import HttpRequest
from rest_framework.request import Request

from platform_admin.exceptions import PlatformAdminNotAuthenticatedError
from platform_admin.models import PlatformAdmin
from staff.models import PlatformVisitor


def get_request_platform_admin(request: HttpRequest | Request) -> PlatformAdmin:
    """Return the Platform Admin logged in on the request.

    Raises:
        PlatformAdminNotAuthenticatedError: No Platform Admin is logged in.
    """
    user = request.user
    if not isinstance(user, PlatformAdmin):
        raise PlatformAdminNotAuthenticatedError
    return user


def get_request_platform_visitor(request: HttpRequest | Request) -> PlatformVisitor:
    """Return the Platform Admin inside the Consultancy on the request (Support Access).

    Raises:
        PlatformAdminNotAuthenticatedError: No Platform Admin is visiting.
    """
    user = request.user
    if not isinstance(user, PlatformVisitor):
        raise PlatformAdminNotAuthenticatedError
    return user
