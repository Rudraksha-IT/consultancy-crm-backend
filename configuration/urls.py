from __future__ import annotations

from django.urls import URLPattern, path

from configuration.views import DateDisplayView, OfficeDetailsView, OfficeLogoView

urlpatterns: list[URLPattern] = [
    path("configuration/logo/", OfficeLogoView.as_view(), name="configuration-logo"),
    path(
        "configuration/office-details/",
        OfficeDetailsView.as_view(),
        name="configuration-office-details",
    ),
    path("configuration/dates/", DateDisplayView.as_view(), name="configuration-dates"),
]
