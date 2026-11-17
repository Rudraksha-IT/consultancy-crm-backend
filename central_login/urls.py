from __future__ import annotations

from django.urls import URLPattern, path

from central_login.views import CentralLoginView

urlpatterns: list[URLPattern] = [
    path("central-login/", CentralLoginView.as_view(), name="central-login"),
]
