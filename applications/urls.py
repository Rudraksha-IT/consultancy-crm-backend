from __future__ import annotations

from django.urls import URLPattern, path

from applications.office_views import (
    ApplicationFilterChoicesView,
    OfficeApplicationListView,
    OfficeVisaListView,
    VisaFilterChoicesView,
)
from applications.views import (
    ApplicationListView,
    ApplicationView,
    ChosenOfferView,
    OfferLetterListView,
    VisaDatesView,
    VisaLetterView,
    VisaListView,
    VisaView,
)

ONE_STUDENT = "students/<uuid:student_id>/"

urlpatterns: list[URLPattern] = [
    path("applications/", OfficeApplicationListView.as_view(), name="applications"),
    path(
        "applications/filter-choices/",
        ApplicationFilterChoicesView.as_view(),
        name="application-filter-choices",
    ),
    path(
        f"{ONE_STUDENT}applications/",
        ApplicationListView.as_view(),
        name="student-applications",
    ),
    path(
        f"{ONE_STUDENT}applications/<uuid:application_id>/",
        ApplicationView.as_view(),
        name="student-application",
    ),
    path(
        f"{ONE_STUDENT}offer-letters/",
        OfferLetterListView.as_view(),
        name="student-offer-letters",
    ),
    path(f"{ONE_STUDENT}chosen-offer/", ChosenOfferView.as_view(), name="student-chosen-offer"),
    path(f"{ONE_STUDENT}visas/", VisaListView.as_view(), name="student-visas"),
    path(f"{ONE_STUDENT}visas/<uuid:visa_id>/", VisaView.as_view(), name="student-visa"),
    path(
        f"{ONE_STUDENT}visas/<uuid:visa_id>/dates/",
        VisaDatesView.as_view(),
        name="student-visa-dates",
    ),
    path(
        f"{ONE_STUDENT}visas/<uuid:visa_id>/letter/",
        VisaLetterView.as_view(),
        name="student-visa-letter",
    ),
    path("visas/", OfficeVisaListView.as_view(), name="visas"),
    path("visas/filter-choices/", VisaFilterChoicesView.as_view(), name="visa-filter-choices"),
]
