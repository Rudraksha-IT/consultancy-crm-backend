from __future__ import annotations

from django.urls import URLPattern, path

from office_networks.views import CurrentNetworkView, OfficeNetworksView, OfficeNetworkView

urlpatterns: list[URLPattern] = [
    path("office-networks/", OfficeNetworksView.as_view(), name="office-networks"),
    path("office-networks/current/", CurrentNetworkView.as_view(), name="office-networks-current"),
    path(
        "office-networks/<office_network:office_network_id>/",
        OfficeNetworkView.as_view(),
        name="office-network",
    ),
]
