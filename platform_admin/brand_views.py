"""The product's own logo (issue 65) and its saved logos (first live fixes, ticket 07).

The logo in use is read on the Platform and Central login hosts by anyone (the login screens
and the tab icon show it before anyone logs in); saved logos are listed, picked, removed and
uploaded on the Platform by Platform Admins only.
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
    MAX_SAVED_LOGOS,
    get_product_logo,
    get_product_name,
    list_saved_logos,
    read_product_logo,
    read_saved_logo,
    remove_saved_logo,
    set_product_logo,
    set_product_name,
    use_saved_logo,
)
from platform_admin.exceptions import SavedLogosFullError
from platform_admin.models import SavedProductLogo
from platform_admin.permissions import IsPlatformAdmin
from platform_admin.serializers import ProductNameSerializer, UseLogoSerializer
from tenancy.serializers import LogoUploadSerializer
from tenancy.types import StoredLogo
from tenancy.views import LOGO_SECURITY_POLICY, NOT_FOUND_MESSAGE

LOGO_NOT_SAVED_MESSAGE = "The logo couldn't be saved. Try again."
FULL_MESSAGE = f"You have {MAX_SAVED_LOGOS} saved logos. Remove one before uploading another."
_NO_STORE = {"Cache-Control": "no-store"}

logger = logging.getLogger(__name__)


class ProductBrandView(APIView):
    """`{logo: {url} | null, name}` for the login screens and every page; open to anyone."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    def get(self, request: Request) -> Response:
        """Return where the product's logo is (or null), and the product's name."""
        logo = get_product_logo()
        return Response(
            {"logo": None if logo is None else asdict(logo), "name": get_product_name()}
        )


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
        return _logo_file(logo)


def _logo_file(logo: StoredLogo | None) -> HttpResponse:
    """A logo's bytes with headers that stop it running anything, or 404."""
    if logo is None:
        return Response({"detail": NOT_FOUND_MESSAGE}, status=status.HTTP_404_NOT_FOUND)
    response = HttpResponse(logo.content, content_type=logo.content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = LOGO_SECURITY_POLICY
    response["Content-Disposition"] = "inline"
    return response


class ProductLogoView(APIView):
    """Upload a logo (a form with one file): it is saved and put in use."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (MultiPartParser,)

    def post(self, request: Request) -> Response:
        """Check and store the logo; answer `{logo: {url}}`."""
        serializer = LogoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            logo = set_product_logo(serializer.to_logo())
        except SavedLogosFullError:
            return Response({"file": [FULL_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST)
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return Response(
                {"detail": LOGO_NOT_SAVED_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        logger.info(
            "Product logo changed: platform_admin_id=%s", get_request_platform_admin(request).pk
        )
        return Response({"logo": asdict(logo)})


class SavedLogosView(APIView):
    """`{logos: [...], limit}`: every saved logo, newest first, the one in use marked."""

    permission_classes = (IsPlatformAdmin,)

    def get(self, request: Request) -> Response:
        """List the saved logos."""
        logos = [asdict(logo) for logo in list_saved_logos()]
        return Response({"logos": logos, "limit": MAX_SAVED_LOGOS}, headers=_NO_STORE)


class LogoInUseView(APIView):
    """`PUT {logo: <saved logo id> | null}`: use that saved logo, or the app's own logo."""

    permission_classes = (IsPlatformAdmin,)

    def put(self, request: Request) -> Response:
        """Change the logo in use; answer `{logo: {url} | null}`."""
        serializer = UseLogoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        logo = use_saved_logo(serializer.to_logo_id())
        logger.info(
            "Product logo in use changed: platform_admin_id=%s",
            get_request_platform_admin(request).pk,
        )
        return Response({"logo": None if logo is None else asdict(logo)})


class SavedLogoView(APIView):
    """Remove one saved logo and its file."""

    permission_classes = (IsPlatformAdmin,)

    def delete(self, request: Request, logo_id: int) -> Response:
        """Remove it; 404 for an unknown logo."""
        try:
            remove_saved_logo(logo_id)
        except SavedProductLogo.DoesNotExist:
            return Response({"detail": NOT_FOUND_MESSAGE}, status=status.HTTP_404_NOT_FOUND)
        logger.info(
            "Saved product logo removed: platform_admin_id=%s",
            get_request_platform_admin(request).pk,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SavedLogoFileView(APIView):
    """One saved logo's file, for its tile in Platform → Settings."""

    permission_classes = (IsPlatformAdmin,)

    def get(self, request: Request, logo_id: int) -> HttpResponse:
        """Return its bytes with their own type, or 404."""
        try:
            logo = read_saved_logo(logo_id)
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            logo = None
        response = _logo_file(logo)
        response["Cache-Control"] = "private, no-store"
        return response


class ProductNameView(APIView):
    """`PUT {name}`: the product's name in every tab, screen and Platform email (ticket 15)."""

    permission_classes = (IsPlatformAdmin,)

    def put(self, request: Request) -> Response:
        """Keep the checked name; answer `{name}` as kept."""
        serializer = ProductNameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        admin = get_request_platform_admin(request)
        name = set_product_name(serializer.validated_data["name"], admin)
        # The name itself stays out of the log; the change is recorded with it.
        logger.info("Product name changed: platform_admin_id=%s", admin.pk)
        return Response({"name": name})
