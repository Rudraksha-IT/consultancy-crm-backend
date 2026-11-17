from __future__ import annotations

from django.urls import URLPattern, path

from catalogue.views import (
    CatalogueImportView,
    CourseListView,
    CourseView,
    IntakeListView,
    UniversityListView,
    UniversityView,
)

UNIVERSITIES = "catalogue/universities/"

urlpatterns: list[URLPattern] = [
    path(UNIVERSITIES, UniversityListView.as_view(), name="catalogue-universities"),
    path(
        f"{UNIVERSITIES}<int:university_id>/",
        UniversityView.as_view(),
        name="catalogue-university",
    ),
    path(
        f"{UNIVERSITIES}<int:university_id>/courses/",
        CourseListView.as_view(),
        name="catalogue-courses",
    ),
    path("catalogue/courses/<int:course_id>/", CourseView.as_view(), name="catalogue-course"),
    path("catalogue/intakes/", IntakeListView.as_view(), name="catalogue-intakes"),
    path("catalogue/import/", CatalogueImportView.as_view(), name="catalogue-import"),
]
