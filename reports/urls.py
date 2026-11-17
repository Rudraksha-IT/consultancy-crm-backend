from __future__ import annotations

from django.urls import URLPattern, path

from reports.views import (
    ClassReportView,
    MoneyReportView,
    SourceReportView,
    StaffProgressReportView,
    StudentReportView,
)

urlpatterns: list[URLPattern] = [
    path("reports/students/", StudentReportView.as_view(), name="reports-students"),
    path("reports/money/", MoneyReportView.as_view(), name="reports-money"),
    path("reports/sources/", SourceReportView.as_view(), name="reports-sources"),
    path(
        "reports/staff-progress/",
        StaffProgressReportView.as_view(),
        name="reports-staff-progress",
    ),
    path("reports/batches/", ClassReportView.as_view(), name="reports-batches"),
]
