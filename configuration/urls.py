from __future__ import annotations

from django.urls import URLPattern, path

from configuration.views import DateDisplayView, OfficeLogoView

urlpatterns: list[URLPattern] = [
    path("configuration/logo/", OfficeLogoView.as_view(), name="configuration-logo"),
    path("configuration/dates/", DateDisplayView.as_view(), name="configuration-dates"),
]
