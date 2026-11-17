"""URL routes for the Platform subdomain; no Consultancy endpoint exists here."""

from __future__ import annotations

from django.urls import URLPattern, URLResolver, include, path

urlpatterns: list[URLPattern | URLResolver] = [
    path("api/platform/", include("platform_admin.urls")),
]

# An error outside any view answers with the reference to quote, never a debug page.
handler500 = "config.exceptions.server_error"
