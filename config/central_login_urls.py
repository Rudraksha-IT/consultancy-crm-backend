"""URL routes for the Central login host; it belongs to no Consultancy (issue 44)."""

from __future__ import annotations

from django.urls import URLPattern, URLResolver, include, path

urlpatterns: list[URLPattern | URLResolver] = [
    path("api/", include("central_login.urls")),
    # The product's logo, for the Central login's screen (issue 65); read only.
    path("api/platform/", include("platform_admin.brand_urls")),
]

# An error outside any view answers with the reference to quote, never a debug page.
handler500 = "config.exceptions.server_error"
