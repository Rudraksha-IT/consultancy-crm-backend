from __future__ import annotations

from django.urls import URLPattern, path

from course_files.views import (
    BinCourseFileLinkView,
    BinCourseFileRestoreView,
    BinCourseFileView,
    CourseFileLinkView,
    CourseFileListView,
    CourseFileView,
)

ONE_FILE = "catalogue/course-files/<course_file:file_id>/"
ONE_BINNED = "documents/bin/course-files/<course_file:file_id>/"

urlpatterns: list[URLPattern] = [
    path(
        "catalogue/courses/<course:course_id>/files/",
        CourseFileListView.as_view(),
        name="catalogue-course-files",
    ),
    path(ONE_FILE, CourseFileView.as_view(), name="catalogue-course-file"),
    path(f"{ONE_FILE}link/", CourseFileLinkView.as_view(), name="catalogue-course-file-link"),
    path(ONE_BINNED, BinCourseFileView.as_view(), name="bin-course-file"),
    path(
        f"{ONE_BINNED}restore/",
        BinCourseFileRestoreView.as_view(),
        name="bin-course-file-restore",
    ),
    path(f"{ONE_BINNED}link/", BinCourseFileLinkView.as_view(), name="bin-course-file-link"),
]
