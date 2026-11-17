"""The product logo's read-only routes, served on the Platform and Central login hosts."""

from __future__ import annotations

from django.urls import URLPattern, path

from platform_admin.brand_views import ProductBrandView, ProductLogoFileView

urlpatterns: list[URLPattern] = [
    path("brand/", ProductBrandView.as_view(), name="product-brand"),
    path("brand/logo/<int:version>/", ProductLogoFileView.as_view(), name="product-logo-file"),
]
