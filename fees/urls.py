from __future__ import annotations

from django.urls import URLPattern, path

from fees.views import (
    FeesByTypeView,
    FeeTypeStudentsView,
    FeeView,
    StudentFeesListView,
    StudentFeesView,
)

urlpatterns: list[URLPattern] = [
    path("students/<uuid:student_id>/fees/", StudentFeesView.as_view(), name="student-fees"),
    path("fees/<uuid:fee_id>/", FeeView.as_view(), name="fee"),
    path("accounts/student-fees/", StudentFeesListView.as_view(), name="student-fees-list"),
    path("accounts/fees-by-type/", FeesByTypeView.as_view(), name="fees-by-type"),
    path(
        "accounts/fees-by-type/<int:fee_type_id>/",
        FeeTypeStudentsView.as_view(),
        name="fee-type-students",
    ),
]
