from __future__ import annotations

from django.urls import URLPattern, path

from commission.views import CommissionListView, CommissionStatusView, ReceivedCommissionView

urlpatterns: list[URLPattern] = [
    path("accounts/commission/", CommissionListView.as_view(), name="commission"),
    path(
        "accounts/commission/received/",
        ReceivedCommissionView.as_view(),
        name="commission-received",
    ),
    path(
        "accounts/commission/<commission:commission_id>/status/",
        CommissionStatusView.as_view(),
        name="commission-status",
    ),
]
