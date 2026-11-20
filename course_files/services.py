"""Course Files: PDF brochures on each Course of the Catalogue (spec 53-61; ADR 0015).

Call inside the Consultancy's schema; who may do these is the views' job. Files go to private
storage under the Consultancy's own prefix, in their own folder, never beside a Student's
Documents. Deleting puts a Course File in the Bin; an Admin restores it or deletes it for good,
which removes the stored file too. Logs carry ids only, never names.
"""

from __future__ import annotations

import logging
import uuid

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from catalogue.models import Course
from course_files.exceptions import CourseNotLiveError, TooManyCourseFilesError
from course_files.models import CourseFile
from course_files.types import NewCourseFile
from documents.files import PDF, safe_file_name
from documents.storage import document_storage
from staff.actors import actor_name, stored_staff
from staff.models import Staff
from tenancy.context import get_active_schema_name

# More brochures than any Course needs; counts those in the Bin too.
MAX_FILES_PER_COURSE = 20
# Why an Admin's delete put it in the Bin (the Bin shows it).
BIN_REASON = "Deleted from the Catalogue"

logger = logging.getLogger(__name__)


def add_course_file(course: Course, new: NewCourseFile, by: Staff) -> CourseFile:
    """Store the PDF privately and keep it on the Course.

    Raises:
        CourseNotLiveError: The Course or its University is archived.
        TooManyCourseFilesError: The Course has MAX_FILES_PER_COURSE files already.
        DocumentStorageError: The file couldn't be stored; nothing is kept.
    """
    with transaction.atomic():
        locked = Course.objects.select_for_update().select_related("university").get(pk=course.pk)
        if locked.archived or locked.university.archived:
            raise CourseNotLiveError
        if CourseFile.objects.filter(course=locked).count() >= MAX_FILES_PER_COURSE:
            raise TooManyCourseFilesError
        folder = f"{get_active_schema_name()}/course-files/{locked.pk}/"
        key = f"{folder}{uuid.uuid4().hex}{PDF.extension}"
        file = CourseFile.objects.create(
            course=locked,
            name=new.name,
            file_name=new.file_name,
            size=new.size,
            storage_key=key,
            uploaded_by=stored_staff(by),
            uploaded_by_name=actor_name(by),
        )
        # Stored last, inside the transaction: if storage fails, no record is kept.
        document_storage().save(key, new.content, PDF.content_type)
    logger.info(
        "Course File added: course_file_id=%s, course_id=%s, size=%s, by_staff_id=%s",
        file.pk,
        locked.pk,
        file.size,
        by.pk,
    )
    return file


def rename_course_file(file: CourseFile, name: str, by: Staff) -> CourseFile:
    """Give the Course File a new name to show; the stored file keeps its own name."""
    file.name = name
    file.save(update_fields=["name"])
    logger.info("Course File renamed: course_file_id=%s, by_staff_id=%s", file.pk, by.pk)
    return file


def put_course_file_in_bin(file: CourseFile, by: Staff) -> None:
    """Put a Course File in the Bin; nobody can open or send it while it waits there."""
    file.binned_at = timezone.now()
    file.binned_by = stored_staff(by)
    file.binned_by_name = actor_name(by)
    file.bin_reason = BIN_REASON
    file.save(update_fields=["binned_at", "binned_by", "binned_by_name", "bin_reason"])
    logger.info("Course File put in the Bin: course_file_id=%s, by_staff_id=%s", file.pk, by.pk)


def restore_course_file(file: CourseFile, by: Staff) -> None:
    """Take a Course File out of the Bin, back on its Course."""
    file.binned_at = None
    file.binned_by = None
    file.binned_by_name = ""
    file.bin_reason = ""
    file.save(update_fields=["binned_at", "binned_by", "binned_by_name", "bin_reason"])
    logger.info("Course File restored: course_file_id=%s, by_staff_id=%s", file.pk, by.pk)


def delete_course_file_for_good(file: CourseFile, by: Staff) -> None:
    """Delete a Course File in the Bin with its stored file.

    The stored file goes first: if storage fails, the record stays and it can be tried again.

    Raises:
        DocumentStorageError: The stored file couldn't be removed; nothing is deleted.
    """
    file_id = file.pk
    document_storage().delete(file.storage_key)
    file.delete()
    logger.info("Course File deleted for good: course_file_id=%s, by_staff_id=%s", file_id, by.pk)


def course_file_link(file: CourseFile, *, as_attachment: bool, by: Staff) -> str:
    """A short-lived link to open or save the PDF, under its shown name."""
    link = document_storage().signed_link(
        file.storage_key,
        file_name=_download_name(file),
        content_type=PDF.content_type,
        as_attachment=as_attachment,
    )
    logger.info("Course File link issued: course_file_id=%s, by_staff_id=%s", file.pk, by.pk)
    return link


def live_course_file(file_id: int) -> CourseFile:
    """A Course File in use (not in the Bin).

    Raises:
        CourseFile.DoesNotExist: There is no such Course File, or it is in the Bin.
    """
    return CourseFile.objects.select_related("course").get(pk=file_id, binned_at__isnull=True)


def binned_course_file(file_id: int) -> CourseFile:
    """A Course File waiting in the Bin.

    Raises:
        CourseFile.DoesNotExist: There is no such Course File in the Bin.
    """
    return CourseFile.objects.get(pk=file_id, binned_at__isnull=False)


def binned_course_files() -> QuerySet[CourseFile]:
    """Every Course File in the Bin, with its Course and University."""
    return CourseFile.objects.filter(binned_at__isnull=False).select_related("course__university")


def _download_name(file: CourseFile) -> str:
    """The shown name as a file name ("Master of IT brochure.pdf"), kept safe for the header."""
    return safe_file_name(f"{file.name}{PDF.extension}", PDF)
