from __future__ import annotations

from django.db import models

from catalogue.models import Course
from documents.models import FILE_NAME_MAX_LENGTH, STORAGE_KEY_MAX_LENGTH, InTheBin
from staff.models import STAFF_NAME_MAX_LENGTH, Staff

COURSE_FILE_NAME_MAX_LENGTH = 120


class CourseFile(InTheBin):
    """A PDF brochure kept on one Course of a University (spec 53-61; GLOSSARY: Course File).

    Never a Student's Document. The file is in private storage under the Consultancy's own prefix
    in its own folder (`storage_key`), reached only through a short-lived signed link (ADR 0015).
    Deleting puts it in the Bin, where an Admin restores it or deletes it for good. A Course that
    still has Course Files, even in the Bin, is never deleted (PROTECT).
    """

    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="files")
    # The name Staff see, which an Admin may change; the file keeps `file_name`.
    name = models.CharField(max_length=COURSE_FILE_NAME_MAX_LENGTH)
    # The uploaded file's own name, cleaned, always ending in ".pdf".
    file_name = models.CharField(max_length=FILE_NAME_MAX_LENGTH)
    size = models.PositiveIntegerField()
    storage_key = models.CharField(max_length=STORAGE_KEY_MAX_LENGTH, unique=True)
    # None when a Platform Admin uploaded it during Support Access (issue 27).
    uploaded_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    # Who uploaded it, as they were named then.
    uploaded_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta(InTheBin.Meta):
        ordering = ("uploaded_at", "pk")

    def __str__(self) -> str:
        return self.name
