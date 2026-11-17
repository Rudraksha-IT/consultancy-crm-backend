"""The product's own logo (issue 65).

Read on the Platform and Central login hosts by anyone (the login screens show it before anyone
logs in); changed on the Platform by Platform Admins only.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from django.http import HttpResponse
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from documents.exceptions import DocumentStorageError
from platform_admin.auth import get_request_platform_admin
from platform_admin.brand import (
    get_product_logo,
    read_product_logo,
    remove_product_logo,
    set_product_logo,
)
from platform_admin.permissions import IsPlatformAdmin
from tenancy.serializers import LogoUploadSerializer
from tenancy.views import LOGO_SECURITY_POLICY, NOT_FOUND_MESSAGE

LOGO_NOT_SAVED_MESSAGE = "The logo couldn't be saved. Try again."

logger = logging.getLogger(__name__)


class ProductBrandView(APIView):
    """`{logo: {url} | null}` for the login screens; open to anyone."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    def get(self, request: Request) -> Response:
        """Return where the product's logo is, or null."""
        logo = get_product_logo()
        return Response({"logo": None if logo is None else asdict(logo)})


class ProductLogoFileView(APIView):
    """The logo's file, served from private storage with headers that stop it running anything."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    def get(self, request: Request, version: int) -> HttpResponse:
        """Return the logo's bytes with their own type, or 404 for any other version."""
        try:
            logo = read_product_logo(version)
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            logo = None
        if logo is None:
            return Response({"detail": NOT_FOUND_MESSAGE}, status=status.HTTP_404_NOT_FOUND)
        response = HttpResponse(logo.content, content_type=logo.content_type)
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = LOGO_SECURITY_POLICY
        response["Content-Disposition"] = "inline"
        return response


class ProductLogoView(APIView):
    """Upload or replace the product's logo (a form with one file), or remove it."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (MultiPartParser,)

    def post(self, request: Request) -> Response:
        """Check and store the logo; answer `{logo: {url}}`."""
        serializer = LogoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            logo = set_product_logo(serializer.to_logo())
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return Response(
                {"detail": LOGO_NOT_SAVED_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        logger.info(
            "Product logo changed: platform_admin_id=%s", get_request_platform_admin(request).pk
        )
        return Response({"logo": asdict(logo)})

    def delete(self, request: Request) -> Response:
        """Remove the logo; the initials show again."""
        remove_product_logo()
        logger.info(
            "Product logo removed: platform_admin_id=%s", get_request_platform_admin(request).pk
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
