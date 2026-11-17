"""REST endpoints for the office-wide Applications and Visas lists (spec 57a, 62b). Read-only."""

from __future__ import annotations

from django.core.paginator import EmptyPage
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from applications.answers import (
    filter_choices_answer,
    office_application_row,
    office_visa_row,
    visa_filter_choices_answer,
)
from applications.office_list import (
    APPLICATIONS_PER_PAGE,
    filter_choices,
    find_applications,
    page_of_applications,
)
from applications.serializers import ApplicationListQuerySerializer, VisaListQuerySerializer
from applications.visa_list import (
    VISAS_PER_PAGE,
    checklists_for,
    find_visas,
    page_of_visas,
    visa_filter_choices,
)
from config.serializers import StrictSerializer
from staff.context import get_request_staff
from students.permissions import CanUseStudentList


class OfficeApplicationListView(APIView):
    """Every Application in the office, searched, filtered and sorted (GET only)."""

    permission_classes = (IsAuthenticated, CanUseStudentList)

    def get(self, request: Request) -> Response:
        """Return one page of matching Applications."""
        query = ApplicationListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        applications = find_applications(query.to_filters(), viewer=get_request_staff(request))
        try:
            page = page_of_applications(applications, query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": APPLICATIONS_PER_PAGE,
                "results": [office_application_row(application) for application in page],
            }
        )


class ApplicationFilterChoicesView(APIView):
    """The Intakes, years and countries the list's filters offer (GET only)."""

    permission_classes = (IsAuthenticated, CanUseStudentList)

    def get(self, request: Request) -> Response:
        """Return the choices; the request takes no query fields."""
        StrictSerializer[None](data=request.query_params).is_valid(raise_exception=True)
        return Response(filter_choices_answer(filter_choices()))


class OfficeVisaListView(APIView):
    """Every Visa in the office, filtered and ordered by interview (GET only)."""

    permission_classes = (IsAuthenticated, CanUseStudentList)

    def get(self, request: Request) -> Response:
        """Return one page of matching Visas, each with its Student's document count."""
        query = VisaListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        visas = find_visas(query.to_filters(), viewer=get_request_staff(request))
        try:
            page = page_of_visas(visas, query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        listed = list(page)
        checklists = checklists_for(listed)
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": VISAS_PER_PAGE,
                "results": [office_visa_row(visa, checklists[visa.student_id]) for visa in listed],
            }
        )


class VisaFilterChoicesView(APIView):
    """The Intakes and countries the Visas list's filters offer (GET only)."""

    permission_classes = (IsAuthenticated, CanUseStudentList)

    def get(self, request: Request) -> Response:
        """Return the choices; the request takes no query fields."""
        StrictSerializer[None](data=request.query_params).is_valid(raise_exception=True)
        return Response(visa_filter_choices_answer(visa_filter_choices()))
