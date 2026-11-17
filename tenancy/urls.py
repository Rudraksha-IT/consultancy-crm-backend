from __future__ import annotations

from django.urls import URLPattern, path

from tenancy.views import CurrentConsultancyView, OfficeLogoFileView

urlpatterns: list[URLPattern] = [
    path("consultancy/", CurrentConsultancyView.as_view(), name="current-consultancy"),
    path("consultancy/logo/<int:version>/", OfficeLogoFileView.as_view(), name="office-logo-file"),
]
