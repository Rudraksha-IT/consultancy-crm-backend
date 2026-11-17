"""REST endpoints for the Consultancy's Configuration (office-wide settings), Admin only."""

from __future__ import annotations

import logging
from dataclasses import asdict

from rest_framework import status
from rest_framework.parsers import BaseParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from documents.exceptions import DocumentStorageError
from staff.permissions import IsAdmin
from tenancy.context import get_request_consultancy
from tenancy.exceptions import NoLogoError
from tenancy.serializers import (
    DateDisplaySerializer,
    LogoUploadSerializer,
    ShowNameSerializer,
)
from tenancy.services import (
    remove_logo,
    set_date_display,
    set_logo,
    set_show_name_beside_logo,
)
from tenancy.types import OfficeLogo

LOGO_NOT_SAVED_MESSAGE = "The logo couldn't be saved. Try again."
UPLOAD_A_LOGO_FIRST_MESSAGE = "Upload a logo first."

logger = logging.getLogger(__name__)


def _logo_answer(logo: OfficeLogo) -> Response:
    return Response({"logo": asdict(logo)})


class DateDisplayView(APIView):
    """Choose whether the Consultancy's dates are shown in AD or BS (Configuration → Dates)."""

    permission_classes = (IsAuthenticated, IsAdmin)

    def put(self, request: Request) -> Response:
        """Validate and store the choice, and return it."""
        serializer = DateDisplaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        date_display = serializer.to_date_display()
        consultancy = get_request_consultancy(request)
        set_date_display(consultancy, date_display)
        logger.info(
            "Date display changed: consultancy_id=%s, staff_id=%s, date_display=%s",
            consultancy.pk,
            request.user.pk,
            date_display.value,
        )
        return Response({"date_display": date_display.value})


class OfficeLogoView(APIView):
    """Configuration → Office logo (spec 99c): upload or change it, show the name, remove it."""

    permission_classes = (IsAuthenticated, IsAdmin)

    def get_parsers(self) -> list[BaseParser]:
        """A logo is uploaded as a form; the name switch is JSON."""
        if self.request.method == "POST":
            return [MultiPartParser()]
        return [JSONParser()]

    def post(self, request: Request) -> Response:
        """Check and store the logo, and return it as the screens will show it."""
        serializer = LogoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        consultancy = get_request_consultancy(request)
        try:
            logo = set_logo(consultancy, serializer.to_logo())
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return Response(
                {"detail": LOGO_NOT_SAVED_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        logger.info(
            "Office logo changed: consultancy_id=%s, staff_id=%s", consultancy.pk, request.user.pk
        )
        return _logo_answer(logo)

    def patch(self, request: Request) -> Response:
        """Choose whether the office name shows beside the logo."""
        serializer = ShowNameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        consultancy = get_request_consultancy(request)
        try:
            logo = set_show_name_beside_logo(consultancy, show_name=serializer.to_show_name())
        except NoLogoError:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"detail": UPLOAD_A_LOGO_FIRST_MESSAGE}, status=status.HTTP_400_BAD_REQUEST
            )
        return _logo_answer(logo)

    def delete(self, request: Request) -> Response:
        """Remove the logo; the initials show again."""
        consultancy = get_request_consultancy(request)
        remove_logo(consultancy)
        logger.info(
            "Office logo removed: consultancy_id=%s, staff_id=%s", consultancy.pk, request.user.pk
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
