from __future__ import annotations


class CourseNotLiveError(Exception):
    """The Course or its University is archived: restore it before adding files."""


class TooManyCourseFilesError(Exception):
    """The Course already has as many Course Files as one Course may have."""
