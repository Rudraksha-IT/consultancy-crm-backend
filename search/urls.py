from __future__ import annotations

from django.urls import URLPattern, path

from search.views import SearchView

urlpatterns: list[URLPattern] = [
    path("search/", SearchView.as_view(), name="search"),
]
