"""REST endpoints about the Consultancy resolved from the request host."""

from __future__ import annotations

from dataclasses import asdict

from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from documents.exceptions import DocumentStorageError
from tenancy.context import get_request_consultancy
from tenancy.services import get_access, get_logo, get_theme, read_logo
from tenancy.theme import theme_to_stored

NOT_FOUND_MESSAGE = "Not found."
# The logo can be opened on its own: nothing in it may run, load anything or be framed.
LOGO_SECURITY_POLICY = "default-src 'none'; style-src 'unsafe-inline'; sandbox"


class CurrentConsultancyView(APIView):
    """Return the name, Theme, logo and Status of the Consultancy that owns the request's host.

    Public, so the login screen can show which Consultancy it belongs to, in its colours, and
    why it can't be used when its Status blocks it.
    """

    permission_classes = (AllowAny,)

    def get(self, request: Request) -> Response:
        """Return the current Consultancy's name, Theme, logo (or null) and Status."""
        consultancy = get_request_consultancy(request)
        logo = get_logo(consultancy)
        access = get_access(consultancy)
        return Response(
            {
                "name": consultancy.name,
                "theme": theme_to_stored(get_theme(consultancy)),
                "logo": None if logo is None else asdict(logo),
                "status": access.status.value,
                "status_message": access.message,
            }
        )


class OfficeLogoFileView(APIView):
    """The office logo's file (spec 99c). Public, as the login screens show it too.

    Served by the server itself from private storage; only the version shown now is found.
    """

    permission_classes = (AllowAny,)

    def get(self, request: Request, version: int) -> HttpResponse:
        """Return the logo's bytes with their own type, or 404."""
        consultancy = get_request_consultancy(request)
        try:
            logo = read_logo(consultancy, version)
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
