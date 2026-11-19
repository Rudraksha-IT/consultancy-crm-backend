from __future__ import annotations

from django.urls import URLPattern, path

from fee_types.views import FeeTypeListView, FeeTypeOrderView, FeeTypeView

urlpatterns: list[URLPattern] = [
    path("fee-types/", FeeTypeListView.as_view(), name="fee-types"),
    path("fee-types/order/", FeeTypeOrderView.as_view(), name="fee-types-order"),
    path("fee-types/<int:type_id>/", FeeTypeView.as_view(), name="fee-type"),
]
