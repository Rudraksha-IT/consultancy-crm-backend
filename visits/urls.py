from __future__ import annotations

from django.urls import URLPattern, path

from visits.views import StudentVisitsView, TodaysVisitsView

urlpatterns: list[URLPattern] = [
    path("visits/today/", TodaysVisitsView.as_view(), name="visits-today"),
    path("students/<uuid:student_id>/visits/", StudentVisitsView.as_view(), name="student-visits"),
]
