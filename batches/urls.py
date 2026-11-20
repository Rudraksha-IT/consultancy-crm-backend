from __future__ import annotations

from django.urls import URLPattern, path, register_converter

from batches.views import (
    AttendanceDayView,
    BatchListView,
    BatchMembersView,
    BatchMemberView,
    BatchTeachersView,
    BatchView,
    EnrolledStudentsView,
    StudentClassesView,
)


class DayConverter:
    """A day in an address, shaped "YYYY-MM-DD"; the view checks it's a real day of the class."""

    regex = "[0-9]{4}-[0-9]{2}-[0-9]{2}"

    def to_python(self, value: str) -> str:
        """Hand the text on as it is."""
        return value

    def to_url(self, value: str) -> str:
        """Write the day as it is."""
        return value


register_converter(DayConverter, "day")

urlpatterns: list[URLPattern] = [
    path("batches/", BatchListView.as_view(), name="batches"),
    path("batches/teachers/", BatchTeachersView.as_view(), name="batch-teachers"),
    path("batches/students/", EnrolledStudentsView.as_view(), name="batch-enrolled-students"),
    path("batches/<int:batch_id>/", BatchView.as_view(), name="batch"),
    path("batches/<int:batch_id>/students/", BatchMembersView.as_view(), name="batch-members"),
    path(
        "batches/<int:batch_id>/students/<uuid:student_id>/",
        BatchMemberView.as_view(),
        name="batch-member",
    ),
    path(
        "batches/<int:batch_id>/attendance/<day:day>/",
        AttendanceDayView.as_view(),
        name="batch-attendance-day",
    ),
    path(
        "students/<uuid:student_id>/batches/", StudentClassesView.as_view(), name="student-batches"
    ),
]
