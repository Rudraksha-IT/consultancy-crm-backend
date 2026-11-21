from __future__ import annotations

from django.urls import URLPattern, path

from payments.views import PaymentListView, PaymentView, StudentPaymentsView

urlpatterns: list[URLPattern] = [
    path(
        "students/<uuid:student_id>/payments/",
        StudentPaymentsView.as_view(),
        name="student-payments",
    ),
    path("payments/", PaymentListView.as_view(), name="payments"),
    path("payments/<uuid:payment_id>/", PaymentView.as_view(), name="payment"),
]
