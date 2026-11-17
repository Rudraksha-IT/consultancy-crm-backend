from __future__ import annotations

from django.urls import URLPattern, path

from exports.views import ExportsView

urlpatterns: list[URLPattern] = [
    path("exports/", ExportsView.as_view(), name="exports"),
]
