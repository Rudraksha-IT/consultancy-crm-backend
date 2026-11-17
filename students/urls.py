from __future__ import annotations

from django.urls import URLPattern, path

from students.edit_details_views import StudentEditDetailsView
from students.journey_views import StudentAssignView, StudentStageView, StudentTransferView
from students.profile_views import (
    AcademicRecordListView,
    AcademicRecordView,
    StudentHistoryView,
    StudentNoteView,
    StudentProfileView,
    TestScoreListView,
    TestScoreView,
    WorkExperienceListView,
    WorkExperienceView,
)
from students.views import StudentListView

ONE = "students/<int:student_id>/"

urlpatterns: list[URLPattern] = [
    path("students/", StudentListView.as_view(), name="students"),
    path(ONE, StudentProfileView.as_view(), name="student"),
    path(f"{ONE}details/", StudentEditDetailsView.as_view(), name="student-details"),
    path(f"{ONE}note/", StudentNoteView.as_view(), name="student-note"),
    path(f"{ONE}history/", StudentHistoryView.as_view(), name="student-history"),
    path(f"{ONE}stage/", StudentStageView.as_view(), name="student-stage"),
    path(f"{ONE}assign/", StudentAssignView.as_view(), name="student-assign"),
    path(f"{ONE}transfer/", StudentTransferView.as_view(), name="student-transfer"),
    path(
        f"{ONE}academic-records/",
        AcademicRecordListView.as_view(),
        name="student-academic-records",
    ),
    path(
        f"{ONE}academic-records/<int:entry_id>/",
        AcademicRecordView.as_view(),
        name="student-academic-record",
    ),
    path(f"{ONE}test-scores/", TestScoreListView.as_view(), name="student-test-scores"),
    path(f"{ONE}test-scores/<int:entry_id>/", TestScoreView.as_view(), name="student-test-score"),
    path(
        f"{ONE}work-experience/",
        WorkExperienceListView.as_view(),
        name="student-work-experience",
    ),
    path(
        f"{ONE}work-experience/<int:entry_id>/",
        WorkExperienceView.as_view(),
        name="student-job",
    ),
]
