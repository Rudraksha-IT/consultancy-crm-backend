from __future__ import annotations

from django.urls import URLPattern, path

from fees.views import FeeView, StudentFeesListView, StudentFeesView

urlpatterns: list[URLPattern] = [
    path("students/<int:student_id>/fees/", StudentFeesView.as_view(), name="student-fees"),
    path("fees/<int:fee_id>/", FeeView.as_view(), name="fee"),
    path("accounts/student-fees/", StudentFeesListView.as_view(), name="student-fees-list"),
]
