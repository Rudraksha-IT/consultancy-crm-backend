"""REST endpoints for CSV exports and their Export Records (spec 31b, 31c). Admins only.

An export is a POST, so the Next.js server's origin check applies to it (ADR 0003).
"""

from __future__ import annotations

from collections.abc import Mapping

from django.core.paginator import EmptyPage, Paginator
from django.http import HttpResponse
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import ONLY_ADMIN_EXPORTS_MESSAGE, why_cannot_export
from exports.answers import export_record_answer
from exports.exceptions import TooManyRowsError
from exports.models import ExportList
from exports.serializers import ExportRecordListQuerySerializer, ExportSerializer
from exports.services import CSV_CONTENT_TYPE, export_list, export_records
from modules.services import is_module_on
from modules.types import Module
from staff.context import get_request_staff
from staff.models import Staff
from tenancy.context import get_request_consultancy

EXPORT_RECORDS_PER_PAGE = 25
TOO_MANY_ROWS_MESSAGE = "That's more than 10,000 rows. Narrow the filters first."
# Lists that belong to a switchable Module: while it is off, they can't be exported (404).
LIST_MODULES: dict[str, Module] = {
    ExportList.CLOCK_IN.value: Module.CLOCK_IN,
    ExportList.PAYMENTS.value: Module.ACCOUNTS,
}


def list_module_is_off(request: Request) -> bool:
    """Whether the list asked for belongs to a Module that is off for this Consultancy."""
    data = request.data
    which = data.get("list") if isinstance(data, Mapping) else None
    module = LIST_MODULES.get(which) if isinstance(which, str) else None
    return module is not None and not is_module_on(get_request_consultancy(request), module)


class CanExport(BasePermission):
    """Exports are for Admins, and a Platform Admin in Support Access (spec 31b)."""

    message = ONLY_ADMIN_EXPORTS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member export and see Export Records."""
        user = request.user
        return isinstance(user, Staff) and why_cannot_export(user) is None


class ExportsView(APIView):
    """The Export Records (GET), or a new export of one list as a CSV file (POST)."""

    permission_classes = (IsAuthenticated, CanExport)
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return one page of Export Records, newest first."""
        query = ExportRecordListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            page = Paginator(export_records(), EXPORT_RECORDS_PER_PAGE).page(query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": EXPORT_RECORDS_PER_PAGE,
                "results": [export_record_answer(record) for record in page],
            }
        )

    def post(self, request: Request) -> HttpResponse:
        """Return the whole filtered list as a CSV file, and record the export."""
        if list_module_is_off(request):
            raise NotFound
        body = ExportSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            exported = export_list(
                body.export_list(), body.query(), body.sent_filters(), by=get_request_staff(request)
            )
        except TooManyRowsError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": TOO_MANY_ROWS_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        return HttpResponse(
            exported.content,
            content_type=CSV_CONTENT_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="{exported.name}"',
                "Cache-Control": "no-store",
            },
        )
