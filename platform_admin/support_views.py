"""Support Access endpoints on a Consultancy's own address (issue 27).

Entering a visit with its one-time code, and the Platform-only actions inside the office:
Features (Module switches), a password reset Code or sign-out for any Staff member, and the
download of all of the office's data. Leaving is the ordinary logout (auth_tokens.views).
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_tokens.services import issue_tokens
from codes.exceptions import CodeLimitReachedError
from modules.serializers import ModuleSwitchesSerializer
from modules.services import get_enabled_modules, switch_modules
from platform_admin.auth import get_request_platform_visitor
from platform_admin.office_tools import (
    office_data_zip,
    send_reset_code_to,
    sign_out_staff,
)
from platform_admin.permissions import IsPlatformVisitor
from platform_admin.serializers import EntryCodeSerializer
from platform_admin.support_access import SupportAccessRefusedError, enter_visit
from staff.context import build_staff_context
from staff.exceptions import EmployeeChangeRefusedError
from staff.models import Staff
from tenancy.context import get_request_consultancy

CODE_SENT_MESSAGE = "Reset code sent."
CODE_LIMIT_MESSAGE = "Too many codes were sent in the last hour. Try again later."
ZIP_CONTENT_TYPE = "application/zip"

logger = logging.getLogger(__name__)


class _PlatformVisitorView(APIView):
    permission_classes = (IsAuthenticated, IsPlatformVisitor)
    parser_classes = (JSONParser,)


class SupportEnterView(APIView):
    """Swap a visit's one-time entry code for login tokens on the office's own address."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    parser_classes = (JSONParser,)

    def post(self, request: Request) -> Response:
        """Return `{access, refresh, context}`, or 400 when the code can't be used here."""
        serializer = EntryCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        consultancy = get_request_consultancy(request)
        try:
            visitor = enter_visit(consultancy, serializer.validated_data["code"])
        except SupportAccessRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": error.message}, status=status.HTTP_400_BAD_REQUEST)
        tokens = issue_tokens(visitor)
        context = asdict(build_staff_context(visitor, consultancy))
        return Response({**asdict(tokens), "context": context})


class SupportModulesView(_PlatformVisitorView):
    """Switch the office's Modules from inside it ("Change features", spec 11a)."""

    def put(self, request: Request) -> Response:
        """Save every switchable Module's switch and return the Modules now on."""
        consultancy = get_request_consultancy(request)
        serializer = ModuleSwitchesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        switch_modules(consultancy, serializer.switched_on())
        logger.info(
            "Features saved inside the office: consultancy_id=%s, visit_id=%s",
            consultancy.pk,
            get_request_platform_visitor(request).support_visit_id,
        )
        return Response({"modules": get_enabled_modules(consultancy)})


class SupportResetCodeView(_PlatformVisitorView):
    """Email any Staff member, Admins included, a password reset Code (spec 11a)."""

    def post(self, request: Request, staff_id: int) -> Response:
        """Send the Code, or 400 when they haven't joined or too many were sent."""
        staff = get_object_or_404(Staff, pk=staff_id)
        office = get_request_consultancy(request)
        try:
            send_reset_code_to(staff, office, by=get_request_platform_visitor(request))
        except EmployeeChangeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": error.message}, status=status.HTTP_400_BAD_REQUEST)
        except CodeLimitReachedError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": CODE_LIMIT_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": CODE_SENT_MESSAGE})


class SupportSignOutView(_PlatformVisitorView):
    """Sign any Staff member, Admins included, out of every device (spec 11a)."""

    def post(self, request: Request, staff_id: int) -> Response:
        """End all their logins at once and return no content."""
        staff = get_object_or_404(Staff, pk=staff_id)
        sign_out_staff(staff, by=get_request_platform_visitor(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportDataExportView(_PlatformVisitorView):
    """Download all of the office's data (spec 11a). A POST, so the origin check applies."""

    def post(self, request: Request) -> HttpResponse:
        """Return a ZIP of CSV files, one per kind of record."""
        consultancy = get_request_consultancy(request)
        content = office_data_zip()
        logger.info(
            "Office data downloaded: consultancy_id=%s, visit_id=%s",
            consultancy.pk,
            get_request_platform_visitor(request).support_visit_id,
        )
        name = f"{consultancy.schema_name}-data-{timezone.localdate().isoformat()}.zip"
        return HttpResponse(
            content,
            content_type=ZIP_CONTENT_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
