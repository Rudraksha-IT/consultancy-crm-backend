"""Files sent with an email to a Student (spec 2026-11-18 stories 34, 47-52; ticket 12).

Attachments come only from files already in the app, named by kind and id: Course Files,
Document Examples and, for an email to one Student, that Student's own Document files. Every file
was checked when it was uploaded (at most 5 MB each); an email carries at most 10 MB in all. A
file in the Bin, or a Document file of another Student, is never attached. Logs carry counts only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from communication.exceptions import AttachmentRefusedError, AttachmentsUnreadableError
from course_files.models import CourseFile
from document_examples.models import DocumentExample
from document_examples.services import TypeGuide
from documents.exceptions import DocumentStorageError
from documents.files import PDF
from documents.models import DocumentFile
from documents.storage import document_storage
from emails.types import FileAttachment
from students.models import Student

# The most one email carries (spec story 49), and how many files may be named at once.
MAX_EMAIL_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 20
_MB = 1024 * 1024

GONE_MESSAGE = "One of these files can't be attached any more. Choose the files again."
NOT_TO_MANY_MESSAGE = "Only Course Files and Document Examples can go to many Students."
TOO_BIG_MESSAGE = (
    "The files add up to {total}, more than the 10 MB one email can carry. "
    "Remove {biggest} or another file."
)


class AttachmentKind(StrEnum):
    """Where an attached file comes from."""

    COURSE_FILE = "course_file"
    DOCUMENT_EXAMPLE = "document_example"
    DOCUMENT_FILE = "document_file"


@dataclass(frozen=True)
class Chosen:
    """One file as the composer names it: its kind and id.

    A Document file is named by its UUID (ADR 0027); Course Files and Document Examples still
    by number until ticket 18 switches them.
    """

    kind: AttachmentKind
    id: int | UUID


@dataclass(frozen=True)
class Attachable:
    """A file that can be attached, as the picker shows it.

    `name` is what Staff call it; `file_name` is the name the Student receives; `group` says
    where it belongs (the University and Course, or the Document Type).
    """

    kind: AttachmentKind
    id: int | UUID
    name: str
    file_name: str
    size: int
    group: str
    content_type: str
    storage_key: str


@dataclass(frozen=True)
class AttachableFiles:
    """Everything that can be attached: no Documents for an email to many Students."""

    course_files: list[Attachable]
    document_examples: list[Attachable]
    documents: list[Attachable]


def _course_file(file: CourseFile) -> Attachable:
    course = file.course
    return Attachable(
        AttachmentKind.COURSE_FILE,
        file.pk,
        file.name,
        file.file_name,
        file.size,
        f"{course.university.name}, {course.name}",
        PDF.content_type,
        file.storage_key,
    )


def example_attachable(example: DocumentExample, type_name: str) -> Attachable:
    """A Document Example as the picker shows it, under its Document Type's name."""
    return Attachable(
        AttachmentKind.DOCUMENT_EXAMPLE,
        example.pk,
        example.file_name,
        example.file_name,
        example.size,
        type_name,
        example.content_type,
        example.storage_key,
    )


def _document_file(file: DocumentFile) -> Attachable:
    return Attachable(
        AttachmentKind.DOCUMENT_FILE,
        file.public_id,
        file.file_name,
        file.file_name,
        file.size,
        file.document.document_type.name,
        file.content_type,
        file.storage_key,
    )


def _course_files() -> list[Attachable]:
    """Course Files in use, of Courses and Universities in use (as Staff pick a Course)."""
    rows = CourseFile.objects.filter(
        binned_at__isnull=True, course__archived=False, course__university__archived=False
    ).select_related("course__university")
    ordered = rows.order_by("course__university__name", "course__name", "uploaded_at", "pk")
    return [_course_file(file) for file in ordered]


def _examples() -> list[Attachable]:
    rows = DocumentExample.objects.filter(binned_at__isnull=True).select_related("document_type")
    ordered = rows.order_by("document_type__name", "uploaded_at", "pk")
    return [example_attachable(example, example.document_type.name) for example in ordered]


def _documents_of(student: Student) -> list[Attachable]:
    """The Student's own Document files in use, of Documents in use."""
    rows = DocumentFile.objects.filter(
        document__student=student, binned_at__isnull=True, document__binned_at__isnull=True
    ).select_related("document__document_type")
    return [_document_file(file) for file in rows.order_by("document__document_type__name", "pk")]


def attachable_files(student: Student | None) -> AttachableFiles:
    """What can be attached to an email to `student`, or (None) to many Students."""
    documents = [] if student is None else _documents_of(student)
    return AttachableFiles(_course_files(), _examples(), documents)


def suggested_examples(guides: Iterable[TypeGuide], type_names: Sequence[str]) -> list[Attachable]:
    """The examples of the Documents still needed, in checklist order (Documents needed)."""
    return [
        example_attachable(example, name)
        for guide, name in zip(guides, type_names, strict=True)
        for example in guide.examples
    ]


def chosen_files(chosen: Sequence[Chosen], student: Student | None) -> list[Attachable]:
    """The files named, each once, in the order named, checked for this email.

    `student` is the one Student emailed, or None for an email to many.

    Raises:
        AttachmentRefusedError: a Document file in an email to many, a file that is in the Bin,
            unknown or another Student's, or more than 10 MB in all.
    """
    wanted = list(dict.fromkeys(chosen))
    if student is None and any(c.kind == AttachmentKind.DOCUMENT_FILE for c in wanted):
        raise AttachmentRefusedError(NOT_TO_MANY_MESSAGE)
    found = _found(wanted, student)
    if len(found) != len(wanted):
        raise AttachmentRefusedError(GONE_MESSAGE)
    files = [found[c] for c in wanted]
    total = sum(file.size for file in files)
    if total > MAX_EMAIL_BYTES:
        biggest = max(files, key=lambda file: file.size)
        raise AttachmentRefusedError(
            TOO_BIG_MESSAGE.format(total=f"{total / _MB:.1f} MB", biggest=biggest.file_name)
        )
    return files


def _ids(wanted: Sequence[Chosen], kind: AttachmentKind) -> list[int]:
    return [c.id for c in wanted if c.kind == kind and isinstance(c.id, int)]


def _uuids(wanted: Sequence[Chosen], kind: AttachmentKind) -> list[UUID]:
    return [c.id for c in wanted if c.kind == kind and isinstance(c.id, UUID)]


def _found(wanted: Sequence[Chosen], student: Student | None) -> dict[Chosen, Attachable]:
    """Each file named that is in use (and the Student's own, for a Document file)."""
    course_files = CourseFile.objects.filter(
        pk__in=_ids(wanted, AttachmentKind.COURSE_FILE), binned_at__isnull=True
    ).select_related("course__university")
    examples = DocumentExample.objects.filter(
        pk__in=_ids(wanted, AttachmentKind.DOCUMENT_EXAMPLE), binned_at__isnull=True
    ).select_related("document_type")
    found = [_course_file(file) for file in course_files]
    found += [example_attachable(example, example.document_type.name) for example in examples]
    if student is not None:
        documents = DocumentFile.objects.filter(
            public_id__in=_uuids(wanted, AttachmentKind.DOCUMENT_FILE),
            document__student=student,
            binned_at__isnull=True,
            document__binned_at__isnull=True,
        ).select_related("document__document_type")
        found += [_document_file(file) for file in documents]
    return {Chosen(file.kind, file.id): file for file in found}


def read_files(files: Sequence[Attachable]) -> list[FileAttachment]:
    """Each file's bytes from private storage, ready to send.

    Raises:
        AttachmentsUnreadableError: storage couldn't give a file back; nothing is sent.
    """
    storage = document_storage()
    try:
        return [
            FileAttachment(file.file_name, file.content_type, storage.read(file.storage_key))
            for file in files
        ]
    except DocumentStorageError as error:
        raise AttachmentsUnreadableError from error
