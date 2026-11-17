from __future__ import annotations

from django.urls import URLPattern, path

from transfer_requests.views import (
    ApproveView,
    CancelView,
    DeclineView,
    StudentTransferRequestView,
    TransferRequestCountView,
    TransferRequestListView,
)

ONE = "transfer-requests/<int:request_id>/"

urlpatterns: list[URLPattern] = [
    path("transfer-requests/", TransferRequestListView.as_view(), name="transfer-requests"),
    path(
        "transfer-requests/count/",
        TransferRequestCountView.as_view(),
        name="transfer-requests-count",
    ),
    path(f"{ONE}approve/", ApproveView.as_view(), name="transfer-request-approve"),
    path(f"{ONE}decline/", DeclineView.as_view(), name="transfer-request-decline"),
    path(f"{ONE}cancel/", CancelView.as_view(), name="transfer-request-cancel"),
    path(
        "students/<int:student_id>/transfer-requests/",
        StudentTransferRequestView.as_view(),
        name="student-transfer-requests",
    ),
]
