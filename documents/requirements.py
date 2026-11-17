"""Document Requirements: the extra Document Types a Country, a Course or one Student asks for.

Spec 63a-63c, 64 and 121 (issues 40, 80). Call inside the Consultancy's schema; who may do these
is the views' job, through `access.policy`, the Catalogue's permissions and `IsAdmin`. Logs carry
ids and counts only.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count, ProtectedError, Q, QuerySet

from catalogue.models import Course
from documents.exceptions import (
    AlreadyUploadedError,
    DocumentTypeInUseError,
    EveryoneNeedsTypeError,
    NotAskedError,
)
from documents.groups import DocumentGroup
from documents.models import Document, DocumentRequirement, DocumentType
from documents.services import add_document_type
from documents.types import NewDocumentType
from staff.models import Staff
from students.models import Country, Student

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TypeUses:
    """Where a Document Type is used: how many ask for it, and how many files it has."""

    countries: int
    courses: int
    students: int
    documents: int

    @property
    def is_used(self) -> bool:
        """Whether anything asks for it or holds a file of it."""
        return any((self.countries, self.courses, self.students, self.documents))


@dataclass(frozen=True)
class ListedType:
    """One row of a Country's list: a Document Type and the Document Group it goes in."""

    document_type: DocumentType
    group: DocumentGroup


@dataclass(frozen=True)
class CountryDocuments:
    """One Country's list, oldest type first."""

    country: Country
    listed: tuple[ListedType, ...]


def configured_types() -> QuerySet[DocumentType]:
    """Every Document Type, oldest first, each counted where it is used (see `uses_of`)."""
    return DocumentType.objects.annotate(
        used_by_countries=Count(
            "requirements__country",
            filter=~Q(requirements__country=""),
            distinct=True,
        ),
        used_by_courses=_requirements_with(course__isnull=False),
        used_by_students=_requirements_with(student__isnull=False),
        used_by_documents=Count("documents", distinct=True),
    )


def uses_of(document_type: DocumentType) -> TypeUses:
    """Where a Document Type from `configured_types` is used."""
    return TypeUses(
        countries=getattr(document_type, "used_by_countries", 0),
        courses=getattr(document_type, "used_by_courses", 0),
        students=getattr(document_type, "used_by_students", 0),
        documents=getattr(document_type, "used_by_documents", 0),
    )


def _requirements_with(**target: bool) -> Count:
    filters = {f"requirements__{field}": value for field, value in target.items()}
    return Count("requirements", filter=Q(**filters), distinct=True)


# The Countries with a document list: every one but Other, in the order they are offered.
LISTED_COUNTRIES = tuple(country for country in Country if country != Country.OTHER)


def country_lists() -> list[CountryDocuments]:
    """Every Country's document list (an empty one too), in one query."""
    rows = DocumentRequirement.objects.exclude(country="").select_related("document_type")
    found: dict[str, list[ListedType]] = {country.value: [] for country in LISTED_COUNTRIES}
    for row in rows.order_by("document_type_id", "pk"):
        if row.country in found:
            found[row.country].append(ListedType(row.document_type, DocumentGroup(row.group)))
    return [CountryDocuments(country, tuple(found[country.value])) for country in LISTED_COUNTRIES]


def set_country_requirements(
    country: Country, listed: Sequence[ListedType], by: Staff
) -> CountryDocuments:
    """Make the Country's list exactly `listed`, each type in its group (spec 121).

    Rows of types already on it are kept and moved to their new group. Files already uploaded
    are never touched: a Document no longer asked for shows under "Other documents".
    """
    with transaction.atomic():
        current = DocumentRequirement.objects.select_for_update().filter(country=country.value)
        wanted = {one.document_type.pk: one.group for one in listed}
        current.exclude(document_type_id__in=wanted).delete()
        kept = {row.document_type_id: row for row in current}
        for type_id, row in kept.items():
            if row.group != wanted[type_id]:
                row.group = wanted[type_id]
                row.save(update_fields=["group"])
        DocumentRequirement.objects.bulk_create(
            DocumentRequirement(
                document_type=one.document_type, country=country.value, group=one.group
            )
            for one in listed
            if one.document_type.pk not in kept
        )
    logger.info(
        "Country document list set: country=%s, document_types=%s, by_staff_id=%s",
        country.value,
        len(listed),
        by.pk,
    )
    return next(one for one in country_lists() if one.country == country)


def add_new_type_to_country_list(
    country: Country, name: str, group: DocumentGroup, by: Staff
) -> tuple[DocumentType, CountryDocuments]:
    """Create a Document Type and put it on the Country's list in `group`, together (spec 121a).

    The type is not needed by everyone and its usual group is `group`. Nothing is kept if
    either step is refused.

    Raises:
        TooManyDocumentTypesError: The Consultancy already has the most allowed.
        DuplicateDocumentTypeError: A type already has this name (ignoring capitals and spaces).
    """
    with transaction.atomic():
        added = add_document_type(
            NewDocumentType(name=name, needed_by_everyone=False, usual_group=group), by=by
        )
        DocumentRequirement.objects.create(
            document_type=added, country=country.value, group=group.value
        )
    logger.info(
        "New document added to a country's list: document_type_id=%s, country=%s, group=%s, "
        "by_staff_id=%s",
        added.pk,
        country.value,
        group.value,
        by.pk,
    )
    stored = next(one for one in country_lists() if one.country == country)
    return configured_types().get(pk=added.pk), stored


def is_on_country_list(country: Country, document_type: DocumentType) -> bool:
    """Whether the Country's list already has this type."""
    return DocumentRequirement.objects.filter(
        country=country.value, document_type=document_type
    ).exists()


def set_course_requirements(course: Course, types: Sequence[DocumentType], by: Staff) -> None:
    """Make the Course ask for exactly `types` on top of what everyone needs (spec 63a)."""
    with transaction.atomic():
        Course.objects.select_for_update().get(pk=course.pk)
        _replace(DocumentRequirement.objects.filter(course=course), types, course=course)
    _log_set("course_id", course.pk, types, by)


def set_student_requirements(student: Student, types: Sequence[DocumentType], by: Staff) -> None:
    """Ask this Student alone for exactly `types` (spec 63b).

    Raises:
        AlreadyUploadedError: A type taken off already has a file for this Student.
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        current = DocumentRequirement.objects.filter(student=locked)
        wanted = {document_type.pk for document_type in types}
        _refuse_if_uploaded(locked, current.exclude(document_type_id__in=wanted))
        _replace(current, types, student=locked)
    _log_set("student_id", student.pk, types, by)


def drop_student_requirement(student: Student, type_id: int, by: Staff) -> None:
    """Take one type asked for this Student alone off their checklist ("Not needed").

    Raises:
        NotAskedError: It isn't asked for this Student alone.
        AlreadyUploadedError: A file is already uploaded for it.
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        asked = DocumentRequirement.objects.filter(student=locked, document_type_id=type_id)
        if not asked.exists():
            raise NotAskedError
        _refuse_if_uploaded(locked, asked)
        asked.delete()
    logger.info(
        "Document requirement dropped: student_id=%s, document_type_id=%s, by_staff_id=%s",
        student.pk,
        type_id,
        by.pk,
    )


def remove_document_type(type_id: int, by: Staff) -> None:
    """Remove a Document Type nothing asks for and that has no files (Configuration).

    Raises:
        DocumentType.DoesNotExist: There is no such type here.
        EveryoneNeedsTypeError: Every Student needs it.
        DocumentTypeInUseError: Something asks for it, or a file of it is uploaded.
    """
    with transaction.atomic():
        document_type = DocumentType.objects.select_for_update().get(pk=type_id)
        if document_type.needed_by_everyone:
            raise EveryoneNeedsTypeError(document_type.name)
        if uses_of(configured_types().get(pk=type_id)).is_used:
            raise DocumentTypeInUseError
        try:
            with transaction.atomic():
                document_type.delete()
        except ProtectedError as error:  # pragma: no cover  # asked for or uploaded meanwhile
            raise DocumentTypeInUseError from error
    logger.info("Document type removed: document_type_id=%s, by_staff_id=%s", type_id, by.pk)


def _replace(
    current: QuerySet[DocumentRequirement],
    types: Sequence[DocumentType],
    **target: Course | Student,
) -> None:
    """Make `current` exactly `types`, keeping the rows of those already there."""
    wanted = {document_type.pk for document_type in types}
    current.exclude(document_type_id__in=wanted).delete()
    kept = set(current.values_list("document_type_id", flat=True))
    DocumentRequirement.objects.bulk_create(
        DocumentRequirement(document_type=document_type, **target)
        for document_type in types
        if document_type.pk not in kept
    )


def _refuse_if_uploaded(student: Student, dropped: QuerySet[DocumentRequirement]) -> None:
    uploaded = (
        Document.objects.filter(
            student=student, document_type_id__in=dropped.values("document_type_id")
        )
        .select_related("document_type")
        .order_by("document_type_id")
        .first()
    )
    if uploaded is not None:
        raise AlreadyUploadedError(uploaded.document_type.name)


def _log_set(target: str, target_id: int, types: Sequence[DocumentType], by: Staff) -> None:
    logger.info(
        "Document requirements set: %s=%s, document_types=%s, by_staff_id=%s",
        target,
        target_id,
        len(types),
        by.pk,
    )
