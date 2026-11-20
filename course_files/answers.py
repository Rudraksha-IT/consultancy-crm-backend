"""Course Files as the API returns them. Built by hand so nothing extra (the storage key) leaks."""

from __future__ import annotations

from collections.abc import Iterable

from course_files.models import CourseFile
from documents.bin_answers import bin_answer


def course_file_answer(file: CourseFile) -> dict[str, object]:
    """One Course File in use: its name, the file's own name, size and when it was added."""
    return {
        "id": file.pk,
        "name": file.name,
        "file_name": file.file_name,
        "size": file.size,
        "uploaded_at": file.uploaded_at.isoformat(),
    }


def live_files_answer(files: Iterable[CourseFile]) -> list[dict[str, object]]:
    """The Course Files not in the Bin, oldest first (works on prefetched files)."""
    return [course_file_answer(file) for file in files if not file.is_in_bin]


def binned_course_file_answer(file: CourseFile) -> dict[str, object]:
    """A Course File on the Bin tab, with its University and Course, who and why."""
    course = file.course
    university = course.university
    return {
        "kind": "course_file",
        "course_file": {"id": file.pk, "name": file.name, "file_name": file.file_name},
        "university": {"id": university.pk, "name": university.name},
        "course": {"id": course.pk, "name": course.name},
        **bin_answer(file),
    }
