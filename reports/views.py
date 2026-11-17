"""REST endpoints for the Reports: Students, money, visitors' Sources, Staff Progress, classes.

Issues 28, 29, 30 and 85. GET only, Admin only; the Reports Module off answers 404 for everyone, and
so does the Money report while Accounts is off and the Classes report while the test-prep Module
is off.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.permissions import requires_module
from modules.services import is_module_on
from modules.types import Module
from reports.answers import (
    classes_answer,
    money_answer,
    months_answer,
    sources_answer,
    staff_progress_answer,
)
from reports.permissions import IsReportsAdmin
from reports.serializers import DateRangeQuerySerializer, MonthsQuerySerializer
from reports.services import (
    class_report,
    country_report,
    money_report,
    source_report,
    staff_progress_report,
    student_report,
)
from staff_progress.serializers import ProgressQuerySerializer
from tenancy.context import get_request_consultancy

REPORT_PERMISSIONS: tuple[type[BasePermission], ...] = (
    IsAuthenticated,
    requires_module(Module.REPORTS),
    IsReportsAdmin,
)
# The Module checks come before the Admin check, so nobody learns a switched-off report exists.
MONEY_REPORT_PERMISSIONS: tuple[type[BasePermission], ...] = (
    IsAuthenticated,
    requires_module(Module.REPORTS),
    requires_module(Module.ACCOUNTS),
    IsReportsAdmin,
)
CLASS_REPORT_PERMISSIONS: tuple[type[BasePermission], ...] = (
    IsAuthenticated,
    requires_module(Module.REPORTS),
    requires_module(Module.BATCHES),
    IsReportsAdmin,
)


class StudentReportView(APIView):
    """New visitors, sign-ups, applications, visas and Lost each month, and countries (GET).

    Spec 93, 93a, 93b.
    """

    permission_classes = REPORT_PERMISSIONS

    def get(self, request: Request) -> Response:
        """Return the last 6 or 12 months, this one last, and the countries over all of them."""
        query = MonthsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        month_count = query.month_count()
        return Response(months_answer(student_report(month_count), country_report(month_count)))


class MoneyReportView(APIView):
    """Money in and out each month, and where it came from and went (GET; spec 97)."""

    permission_classes = MONEY_REPORT_PERMISSIONS

    def get(self, request: Request) -> Response:
        """Return the last 6 or 12 months, this one last."""
        query = MonthsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(money_answer(money_report(query.month_count())))


class SourceReportView(APIView):
    """New visitors between two days, per Source (GET; spec 94)."""

    permission_classes = REPORT_PERMISSIONS

    def get(self, request: Request) -> Response:
        """Return the count per Source, most first."""
        query = DateRangeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(sources_answer(source_report(*query.days())))


class StaffProgressReportView(APIView):
    """Staff Progress for everyone, one table per Role, for a period of at most a year (GET).

    Spec 95, 127; the same counting as each person's Progress tab (issue 84).
    """

    permission_classes = REPORT_PERMISSIONS

    def get(self, request: Request) -> Response:
        """Return the Counsellors, Front Desk and (with classes on) Teachers tables, by name."""
        query = ProgressQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        batches_on = is_module_on(get_request_consultancy(request), Module.BATCHES)
        report = staff_progress_report(*query.days(), batches_on=batches_on)
        return Response(staff_progress_answer(report))


class ClassReportView(APIView):
    """Students per class and their share of class days attended between two days (GET; spec 98)."""

    permission_classes = CLASS_REPORT_PERMISSIONS

    def get(self, request: Request) -> Response:
        """Return one row per class running between the days, by name."""
        query = DateRangeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(classes_answer(class_report(*query.days())))
