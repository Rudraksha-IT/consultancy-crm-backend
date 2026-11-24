"""Public services for Document Types, a Student's Documents and their checklist (spec 63-70).

Call inside the Consultancy's schema. Who may do these is the views' job, through
`access.policy`. Files go to `documents.storage` under the Consultancy's own prefix; logs carry
ids only, never file names or contents.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.db.models.functions import Lower

from applications.models import Application
from applications.statuses import ApplicationStatus
from document_examples.services import TypeGuide, guides_for
from documents.checklist import (
    AppliedCourse,
    Checklist,
    CountryList,
    HeldDocument,
    ListedType,
    NamedType,
    RequiredType,
    StudentPlan,
    build_checklist,
    required_types,
)
from documents.exceptions import (
    AlreadyCheckedError,
    DocumentInBinError,
    DocumentStorageError,
    DuplicateDocumentTypeError,
    NotNewestCopyError,
    RejectedNeedsNewCopyError,
    TooManyDocumentsError,
    TooManyDocumentTypesError,
    TooManyFilesError,
)
from documents.files import DOWNLOAD_ONLY_TYPES
from documents.groups import DocumentGroup
from documents.models import Document, DocumentFile, DocumentRequirement, DocumentType
from documents.storage import document_storage
from documents.types import NewDocument, NewDocumentType, NewFile
from documents.verification import Verification
from history.models import HistoryKind
from history.services import Change, record
from staff.actors import actor_name, stored_staff
from staff.models import Staff
from students.models import Country, Stage, Student
from tenancy.context import get_active_schema_name

# How many Document Types one Consultancy can have.
MAX_DOCUMENT_TYPES = 100
# How many Documents (replaced ones included) one Student can have.
MAX_DOCUMENTS_PER_STUDENT = 200
# How many files one Document can hold (the pages of a long statement fit; issue 78).
MAX_FILES_PER_DOCUMENT = 20
# Leads haven't started yet and Lost Students have stopped: the Documents page leaves them out.
_NO_PAPERWORK_STAGES = (Stage.LEAD, Stage.LOST)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StudentChecklist:
    """A Student's checklist, the Documents it points at by id, and the types asked for them."""

    checklist: Checklist
    documents: Mapping[int, Document]
    # The Document Types asked for this Student alone (spec 63b), by UUID (ADR 0027).
    asked: tuple[uuid.UUID, ...] = ()
    # Each listed type's instruction and example files (Document Examples, spec 64), by type id.
    guides: Mapping[int, TypeGuide] = field(default_factory=dict)
    # Each listed type's UUID, its id outside the server, by type id.
    type_ids: Mapping[int, uuid.UUID] = field(default_factory=dict)


@dataclass(frozen=True)
class Paperwork:
    """One Student on the Documents page: how much their checklist needs looking at."""

    student: Student
    checklist: Checklist


def document_types() -> QuerySet[DocumentType]:
    """Every Document Type of the Consultancy, oldest first."""
    return DocumentType.objects.all()


def tidy_type_name(name: str) -> str:
    """The name with spaces trimmed and runs of spaces made one."""
    return " ".join(name.split())


def _same_name(name: str) -> str:
    return tidy_type_name(name).casefold()


def type_named(name: str) -> DocumentType | None:
    """The Document Type with this name, ignoring capitals and extra spaces, if there is one."""
    wanted = _same_name(name)
    return next((one for one in DocumentType.objects.all() if _same_name(one.name) == wanted), None)


def add_document_type(details: NewDocumentType, by: Staff) -> DocumentType:
    """Add a Document Type, its name tidied (see `tidy_type_name`).

    Raises:
        TooManyDocumentTypesError: The Consultancy already has the most allowed.
        DuplicateDocumentTypeError: Another type already has this name, ignoring capitals and
            extra spaces.
    """
    name = tidy_type_name(details.name)
    with transaction.atomic():
        if DocumentType.objects.count() >= MAX_DOCUMENT_TYPES:
            raise TooManyDocumentTypesError
        existing = type_named(name)
        if existing is not None:
            raise DuplicateDocumentTypeError(existing)
        try:
            with transaction.atomic():
                added = DocumentType.objects.create(
                    name=name,
                    needed_by_everyone=details.needed_by_everyone,
                    usual_group=details.usual_group,
                )
        except IntegrityError as error:  # pragma: no cover  # added by someone else meanwhile
            raise DuplicateDocumentTypeError from error
    logger.info("Document type added: document_type_id=%s, by_staff_id=%s", added.pk, by.pk)
    return added


def set_needed_by_everyone(type_id: int, needed_by_everyone: bool, by: Staff) -> DocumentType:
    """Move a Document Type onto, or off, every Student's checklist.

    Raises:
        DocumentType.DoesNotExist: There is no such type here.
    """
    document_type = DocumentType.objects.get(pk=type_id)
    document_type.needed_by_everyone = needed_by_everyone
    document_type.save(update_fields=["needed_by_everyone"])
    logger.info(
        "Document type changed: document_type_id=%s, needed_by_everyone=%s, by_staff_id=%s",
        type_id,
        needed_by_everyone,
        by.pk,
    )
    return document_type


def checklist_of(student: Student) -> StudentChecklist:
    """The Student's checklist (spec 64), with the types asked for them alone.

    What everyone needs, the Country lists that apply, what their Courses ask for, what is asked
    for them alone, then their other Documents.
    """
    documents = list(
        Document.objects.filter(student=student)
        .select_related(
            "document_type",
            "application__course__university",
            "visa__application__course__university",
        )
        .prefetch_related("files")
    )
    required = _required_by_student([student.pk])[student.pk]
    checklist = build_checklist(required, [_held(d) for d in documents])
    asked = DocumentRequirement.objects.filter(student=student).values_list(
        "document_type__public_id", flat=True
    )
    type_ids = DocumentType.objects.filter(
        pk__in=[item.type_id for item in checklist.items]
    ).values_list("pk", "public_id")
    return StudentChecklist(
        checklist,
        {document.pk: document for document in documents},
        tuple(asked),
        guides_for(item.type_id for item in checklist.items),
        dict(type_ids),
    )


def upload_document(student: Student, details: NewDocument, by: Staff) -> Document:
    """Add a Pending Document holding the files, stored under the Consultancy's prefix.

    Raises:
        TooManyDocumentsError: The Student already has the most Documents allowed.
        DocumentStorageError: A file couldn't be stored; nothing is added.
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        if Document.objects.filter(student=locked).count() >= MAX_DOCUMENTS_PER_STUDENT:
            raise TooManyDocumentsError
        document = Document.objects.create(
            student=locked,
            document_type=details.document_type,
            application=details.application,
            visa=details.visa,
            uploaded_by=stored_staff(by),
            uploaded_by_name=actor_name(by),
        )
        _store_files(document, details.files, by)
    logger.info(
        "Document uploaded: document_id=%s, student_id=%s, document_type_id=%s, files=%s, "
        "by_staff_id=%s",
        document.pk,
        student.pk,
        details.document_type.pk,
        len(details.files),
        by.pk,
    )
    return document


def add_files(
    student: Student, document_id: int, files: tuple[NewFile, ...], by: Staff
) -> Document:
    """Add files to the newest copy of a Document; a Verified one needs checking again.

    Verification is one judgment for all the files, so a new page puts it back to Pending
    (spec 117). A Rejected Document is replaced by a new copy, never added to (spec 69).

    Raises:
        Document.DoesNotExist: The Student has no such Document.
        DocumentInBinError: It is in the Bin.
        RejectedNeedsNewCopyError: It was Rejected.
        NotNewestCopyError: A newer copy of its type has been uploaded.
        TooManyFilesError: It would hold more files than allowed.
        DocumentStorageError: A file couldn't be stored; nothing is added or changed.
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        document = Document.objects.get(student=locked, pk=document_id)
        _check_files_can_be_added(document, len(files))
        if document.verification == Verification.VERIFIED:
            document.verification = Verification.PENDING
            document.save(update_fields=["verification"])
        _store_files(document, files, by)
    logger.info(
        "Document files added: document_id=%s, student_id=%s, files=%s, by_staff_id=%s",
        document.pk,
        student.pk,
        len(files),
        by.pk,
    )
    return document


def _check_files_can_be_added(document: Document, count: int) -> None:
    """Refuse adding `count` files to the Document, if they can't be (see `add_files`)."""
    if document.is_in_bin:
        raise DocumentInBinError
    if document.verification == Verification.REJECTED:
        raise RejectedNeedsNewCopyError
    if _has_newer_copy(document):
        raise NotNewestCopyError
    if document.files.count() + count > MAX_FILES_PER_DOCUMENT:
        raise TooManyFilesError


def _store_files(document: Document, files: tuple[NewFile, ...], by: Staff) -> None:
    """Add the Document's files and store each one; call inside a transaction.

    If one can't be stored, those already stored are removed too, and the error is raised so the
    transaction leaves no row pointing at nothing.
    """
    prefix = f"{get_active_schema_name()}/documents/{document.student.public_id}/"
    stored: list[str] = []
    try:
        for file in files:
            added = DocumentFile.objects.create(
                document=document,
                file_name=file.file_name,
                content_type=file.kind.content_type,
                size=file.size,
                storage_key=f"{prefix}{uuid.uuid4().hex}{file.kind.extension}",
                uploaded_by=stored_staff(by),
                uploaded_by_name=actor_name(by),
            )
            document_storage().save(added.storage_key, file.content, added.content_type)
            stored.append(added.storage_key)
    except DocumentStorageError:
        _forget(stored)
        raise


def _forget(keys: list[str]) -> None:
    """Remove stored files whose rows are being rolled back, as far as storage allows."""
    for key in keys:
        try:
            document_storage().delete(key)
        except DocumentStorageError:  # pragma: no cover  # storage went down meanwhile
            logger.warning("Stored file left behind after a failed upload")


def _has_newer_copy(document: Document) -> bool:
    """Whether a newer copy of its type is in use (copies in the Bin don't count)."""
    return Document.objects.filter(
        student_id=document.student_id,
        document_type_id=document.document_type_id,
        pk__gt=document.pk,
        binned_at__isnull=True,
    ).exists()


def check_document(
    student: Student,
    document_id: int,
    verification: Verification,
    by: Staff,
    reason: str = "",
) -> Document:
    """Mark the newest copy of a Pending Document correct, or Rejected with a reason (spec 68).

    A Rejected Document stays as it is; a new upload of its type replaces it (spec 69). Either
    judgment is added to the Student's History (issue 83).

    Raises:
        Document.DoesNotExist: The Student has no such Document.
        DocumentInBinError: It is in the Bin.
        AlreadyCheckedError: It has already been marked correct or rejected.
        NotNewestCopyError: A newer copy of its type has been uploaded.
    """
    if verification == Verification.PENDING:  # pragma: no cover  # refused by the serializer
        msg = "A Document can only be marked correct or rejected."
        raise ValueError(msg)
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        document = Document.objects.get(student=locked, pk=document_id)
        if document.is_in_bin:
            raise DocumentInBinError
        if document.verification != Verification.PENDING:
            raise AlreadyCheckedError
        if _has_newer_copy(document):
            raise NotNewestCopyError
        document.verification = verification
        document.rejection_reason = reason if verification == Verification.REJECTED else ""
        document.save(update_fields=["verification", "rejection_reason"])
        _record_check(locked, document, by)
    logger.info(
        "Document checked: document_id=%s, student_id=%s, verification=%s, by_staff_id=%s",
        document.pk,
        student.pk,
        verification.value,
        by.pk,
    )
    return document


def _record_check(student: Student, document: Document, by: Staff) -> None:
    """Add the Document's Verified or Rejected (with its reason) to the Student's History."""
    name = document.document_type.name
    if document.verification == Verification.REJECTED:
        change = Change(after=name, detail=document.rejection_reason)
        record(student, HistoryKind.DOCUMENT_REJECTED, by, change)
    else:
        record(student, HistoryKind.DOCUMENT_VERIFIED, by, Change(after=name))


def document_link(
    student: Student, document_id: int, file_id: int, *, as_attachment: bool, by: Staff
) -> str:
    """A link to one file of one of the Student's Documents that works for a few minutes only.

    Nothing in the Bin is linked to here; an Admin opens it from the Bin tab (issue 79).

    Raises:
        DocumentFile.DoesNotExist: The Student has no such Document or file in use.
    """
    file = DocumentFile.objects.get(
        document__student=student,
        document_id=document_id,
        pk=file_id,
        binned_at__isnull=True,
        document__binned_at__isnull=True,
    )
    return signed_file_link(file, as_attachment=as_attachment, by=by)


def signed_file_link(file: DocumentFile, *, as_attachment: bool, by: Staff) -> str:
    """A link to the file that works for a few minutes only; call after every check.

    A zip is always saved, never opened in the browser (ticket 09).
    """
    link = document_storage().signed_link(
        file.storage_key,
        file_name=file.file_name,
        content_type=file.content_type,
        as_attachment=as_attachment or file.content_type in DOWNLOAD_ONLY_TYPES,
    )
    logger.info(
        "Document link issued: document_id=%s, file_id=%s, by_staff_id=%s",
        file.document_id,
        file.pk,
        by.pk,
    )
    return link


def waiting_count(students: QuerySet[Student]) -> int:
    """How many checklist Documents of these Students wait for a check (list tidy-ups 22).

    The same count the Documents page shows, Students' checklists only: a Document of a type not
    on the checklist ("Other documents") isn't counted. Leads, Lost and the Bin are left out, as on
    the page. A fixed number of queries.
    """
    return sum(one.checklist.counts.waiting for one in paperwork(students))


def only_waiting(found: list[Paperwork]) -> list[Paperwork]:
    """The Students with a checklist Document waiting for a check (issue 52, list tidy-ups 22)."""
    return [one for one in found if one.checklist.counts.waiting > 0]


def paperwork(students: QuerySet[Student]) -> list[Paperwork]:
    """The Students (not Leads or Lost) by how much their paperwork needs looking at (spec 64a).

    Rejected Documents weigh most, then ones waiting for a check, then missing ones. Ties by name.
    """
    with_paperwork = list(
        students.exclude(stage__in=_NO_PAPERWORK_STAGES)
        .select_related("assigned_counsellor")
        .order_by(Lower("name"), "pk")
    )
    checklists = checklists_of(student.pk for student in with_paperwork)
    found = [Paperwork(student, checklists[student.pk]) for student in with_paperwork]
    return sorted(found, key=lambda one: -one.checklist.counts.attention)


def checklists_of(student_ids: Iterable[int]) -> dict[int, Checklist]:
    """Each Student's checklist, by their id, in a fixed number of queries however many."""
    ids = list(student_ids)
    held = _held_by_student(ids)
    required = _required_by_student(ids)
    return {pk: build_checklist(required[pk], held.get(pk, [])) for pk in ids}


@dataclass(frozen=True)
class _Applied:
    """One of a Student's Applications that isn't Rejected: its Country and Course."""

    student_id: int
    country: str
    course_id: int
    course: str


def _required_by_student(ids: list[int]) -> dict[int, list[RequiredType]]:
    """What each Student must bring, in checklist order (spec 64, 121), in four queries."""
    everyone = [
        _named(document_type.pk, document_type.name, document_type.usual_group)
        for document_type in DocumentType.objects.filter(needed_by_everyone=True)
    ]
    students = list(
        Student.objects.filter(pk__in=ids).values_list(
            "pk", "country", "chosen_offer__status", "chosen_offer__course__university__country"
        )
    )
    applied = [
        _Applied(*row)
        for row in Application.objects.filter(student_id__in=ids)
        .exclude(status=ApplicationStatus.REJECTED)
        .order_by("pk")
        .values_list("student_id", "course__university__country", "course_id", "course__name")
    ]
    asked_by = _requirements_by_target(
        countries={row[1] for row in students} | {one.country for one in applied},
        course_ids={one.course_id for one in applied},
        student_ids=ids,
    )
    by_student: dict[int, list[_Applied]] = defaultdict(list)
    for one in applied:
        by_student[one.student_id].append(one)
    plans = {
        pk: _plan(
            country,
            chosen_country if chosen_status == ApplicationStatus.OFFER else None,
            by_student[pk],
            asked_by,
        )
        for pk, country, chosen_status, chosen_country in students
    }
    return {
        pk: required_types(everyone, plans[pk], _types_of(asked_by, ("student", pk)))
        for pk in ids
        if pk in plans
    }


_Target = tuple[str, int | str]


def _plan(
    country: str,
    chosen_country: str | None,
    applied: list[_Applied],
    asked_by: dict[_Target, list[ListedType]],
) -> StudentPlan:
    """Where one Student is going: preferred country, Countries applied to, the Chosen Offer's."""
    return StudentPlan(
        preferred=_country_list(country, asked_by),
        applying=[_country_list(c, asked_by) for c in dict.fromkeys(a.country for a in applied)],
        chosen=None if chosen_country is None else _country_list(chosen_country, asked_by),
        courses=[
            AppliedCourse(a.course, _types_of(asked_by, ("course", a.course_id))) for a in applied
        ],
    )


def _types_of(asked_by: dict[_Target, list[ListedType]], target: _Target) -> list[NamedType]:
    return [listed.type for listed in asked_by.get(target, [])]


def _country_list(country: str, asked_by: dict[_Target, list[ListedType]]) -> CountryList:
    return CountryList(Country(country).label, asked_by.get(("country", country), []))


def _named(pk: int, name: str, usual_group: str) -> NamedType:
    return NamedType(pk, name, DocumentGroup(usual_group))


def _requirements_by_target(
    countries: set[str], course_ids: set[int], student_ids: list[int]
) -> dict[_Target, list[ListedType]]:
    """The types each of these Countries, Courses and Students asks for, oldest type first.

    A Country's row keeps the group its list puts the type in; the others use the usual group.
    """
    rows = (
        DocumentRequirement.objects.filter(
            Q(country__in=countries - {""})
            | Q(course_id__in=course_ids)
            | Q(student_id__in=student_ids)
        )
        .order_by("document_type_id", "pk")
        .values_list(
            "country",
            "group",
            "course_id",
            "student_id",
            "document_type_id",
            "document_type__name",
            "document_type__usual_group",
        )
    )
    found: dict[_Target, list[ListedType]] = defaultdict(list)
    for country, group, course_id, student_id, type_id, name, usual_group in rows:
        named = _named(type_id, name, usual_group)
        if country:
            key: _Target = ("country", country)
        elif course_id is not None:
            key = ("course", course_id)
        else:
            key = ("student", student_id)
        found[key].append(ListedType(named, DocumentGroup(group or usual_group)))
    return found


def _held(document: Document) -> HeldDocument:
    return HeldDocument(
        id=document.pk,
        type_id=document.document_type_id,
        type_name=document.document_type.name,
        verification=Verification(document.verification),
        in_bin=document.is_in_bin,
    )


def _held_by_student(student_ids: Iterable[int]) -> dict[int, list[HeldDocument]]:
    rows = (
        Document.objects.filter(student_id__in=list(student_ids))
        .order_by("pk")
        .values_list(
            "student_id",
            "pk",
            "document_type_id",
            "document_type__name",
            "verification",
            "binned_at",
        )
    )
    held: dict[int, list[HeldDocument]] = defaultdict(list)
    for student_id, pk, type_id, type_name, verification, binned_at in rows:
        held[student_id].append(
            HeldDocument(
                pk, type_id, type_name, Verification(verification), in_bin=binned_at is not None
            )
        )
    return held
