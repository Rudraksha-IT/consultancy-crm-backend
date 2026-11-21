"""Support Access routes on a Consultancy's own address (issue 27), under /api/support-access/."""

from __future__ import annotations

from django.urls import URLPattern, path

from platform_admin.support_views import (
    SupportDataExportView,
    SupportEnterView,
    SupportModulesView,
    SupportResetCodeView,
    SupportSignOutView,
)

urlpatterns: list[URLPattern] = [
    path("enter/", SupportEnterView.as_view(), name="support-enter"),
    path("modules/", SupportModulesView.as_view(), name="support-modules"),
    path(
        "staff/<staff:staff_id>/reset-code/",
        SupportResetCodeView.as_view(),
        name="support-staff-reset-code",
    ),
    path(
        "staff/<staff:staff_id>/sign-out/",
        SupportSignOutView.as_view(),
        name="support-staff-sign-out",
    ),
    path("export/", SupportDataExportView.as_view(), name="support-export"),
]
