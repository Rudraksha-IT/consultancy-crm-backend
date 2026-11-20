from __future__ import annotations

from django.urls import URLPattern, path

from staff_progress.views import StaffProgressView

urlpatterns: list[URLPattern] = [
    path("staff/<staff:staff_id>/progress/", StaffProgressView.as_view(), name="staff-progress"),
]
