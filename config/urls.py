"""URL routes for Consultancy subdomains."""

from __future__ import annotations

from django.urls import URLPattern, URLResolver, include, path

urlpatterns: list[URLPattern | URLResolver] = [
    path("api/", include("tenancy.urls")),
    path("api/", include("staff.urls")),
    path("api/", include("configuration.urls")),
    path("api/", include("students.urls")),
    path("api/", include("catalogue.urls")),
    path("api/", include("applications.urls")),
    path("api/", include("documents.urls")),
    path("api/", include("course_files.urls")),
    path("api/", include("document_examples.urls")),
    path("api/", include("fee_types.urls")),
    path("api/", include("payments.urls")),
    path("api/", include("fees.urls")),
    path("api/", include("expenses.urls")),
    path("api/", include("commission.urls")),
    path("api/", include("tasks.urls")),
    path("api/", include("notifications.urls")),
    path("api/", include("batches.urls")),
    path("api/", include("communication.urls")),
    path("api/", include("home.urls")),
    path("api/", include("visits.urls")),
    path("api/", include("reports.urls")),
    path("api/", include("search.urls")),
    path("api/", include("exports.urls")),
    path("api/", include("office_networks.urls")),
    path("api/", include("clock_in.urls")),
    path("api/", include("transfer_requests.urls")),
    path("api/", include("staff_progress.urls")),
    path("api/", include("inbox.urls")),
    path("api/support-access/", include("platform_admin.support_urls")),
]

# An error outside any view answers with the reference to quote, never a debug page.
handler500 = "config.exceptions.server_error"
