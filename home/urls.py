from __future__ import annotations

from django.urls import URLPattern, path

from home.views import PipelineView, ProgressView, StudentProgressView, WaitingView

urlpatterns: list[URLPattern] = [
    path("home/progress/", ProgressView.as_view(), name="home-progress"),
    path("home/pipeline/", PipelineView.as_view(), name="home-pipeline"),
    path("home/waiting/", WaitingView.as_view(), name="home-waiting"),
    path(
        "students/<int:student_id>/progress/",
        StudentProgressView.as_view(),
        name="student-progress",
    ),
]
