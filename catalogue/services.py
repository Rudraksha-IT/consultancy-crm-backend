"""Public services for the Catalogue. Call them inside the Consultancy's schema.

Permission checks are the views' job: only Admins change the Catalogue (spec 46-50).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from uuid import uuid4

from django.core.paginator import Page, Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, ProtectedError, Q, QuerySet
from django.db.models.functions import Lower

from catalogue.csv_import import CatalogueRow, ParsedCatalogue, RowProblem
from catalogue.exceptions import DuplicateNameError, InUseError, NotArchivedError
from catalogue.intakes import tidy_intakes
from catalogue.models import Course, Intake, University
from catalogue.types import (
    CourseChange,
    CourseEdit,
    ImportResult,
    NewCourse,
    UniversityChange,
    UniversityEdit,
)
from commission.models import Commission
from documents.models import DocumentRequirement
from documents.requirements import set_course_requirements
from staff.models import Staff
from students.models import Country

UNIVERSITIES_PER_PAGE = 25
IMPORT_ROW_FAILED_MESSAGE = "This row clashes with a name added meanwhile. Import the file again."

logger = logging.getLogger(__name__)


def add_university(name: str, country: Country, by: Staff) -> University:
    """Add a University.

    Raises:
        DuplicateNameError: Another University already has this name (in any case).
    """
    university = _saved(lambda: University.objects.create(name=name, country=country))
    logger.info("University added: university_id=%s, by_staff_id=%s", university.pk, by.pk)
    return university


def change_university(university: University, change: UniversityChange, by: Staff) -> University:
    """Change the fields given in `change`; archiving hides it from pickers (spec 50).

    Raises:
        DuplicateNameError: Another University already has the new name.
    """
    _set_given(university, name=change.name, country=change.country, archived=change.archived)
    _saved(lambda: _save(university))
    logger.info("University changed: university_id=%s, by_staff_id=%s", university.pk, by.pk)
    return university


def add_course(university: University, details: NewCourse, by: Staff) -> Course:
    """Add a Course with its Intakes to a University.

    Raises:
        DuplicateNameError: The University already has a Course with this name.
    """

    def create() -> Course:
        course = Course.objects.create(
            university=university, name=details.name, level=details.level
        )
        _set_intakes(course, details.intakes)
        if details.documents:
            set_course_requirements(course, details.documents, by=by)
        return course

    course = _saved(create)
    logger.info(
        "Course added: course_id=%s, university_id=%s, by_staff_id=%s",
        course.pk,
        university.pk,
        by.pk,
    )
    return course


def change_course(course: Course, change: CourseChange, by: Staff) -> Course:
    """Change the fields given in `change`. Intakes no longer listed are removed.

    Raises:
        DuplicateNameError: The University already has another Course with the new name.
    """
    _set_given(course, name=change.name, level=change.level, archived=change.archived)
    intakes = change.intakes

    def save() -> Course:
        course.save()
        if intakes is not None:
            _set_intakes(course, intakes)
        return course

    _saved(save)
    logger.info("Course changed: course_id=%s, by_staff_id=%s", course.pk, by.pk)
    return course


def save_university_edit(university: University, edit: UniversityEdit, by: Staff) -> None:
    """Save a University's edit page: it, every Course on it, and the deletions, or nothing.

    Checks are the serializer's (issue 92). Courses left out of the page stay as they are.

    Raises:
        DuplicateNameError: A name was taken meanwhile; nothing is saved.
        InUseError: An Application started using a Course being deleted; nothing is saved.
    """

    def save() -> None:
        locked = University.objects.select_for_update().get(pk=university.pk)
        _set_given(locked, name=edit.name, country=edit.country, archived=edit.archived)
        locked.save()
        _delete_courses(edit.deleted_courses)
        stored = {course.pk: course for course in Course.objects.filter(university=locked)}
        _free_names(stored, edit.courses)
        for row in edit.courses:
            _save_course_row(locked, stored.get(row.id or 0), row, by)

    _saved(save)
    logger.info(
        "University edit saved: university_id=%s, courses=%s, deleted=%s, by_staff_id=%s",
        university.pk,
        len(edit.courses),
        len(edit.deleted_courses),
        by.pk,
    )


def delete_university(university: University, by: Staff) -> None:
    """Delete an archived University and all its Courses, unless anything uses it (issue 92).

    Raises:
        NotArchivedError: It isn't saved as archived.
        InUseError: An Application uses one of its Courses, or a Commission uses it.
    """
    if not university.archived:
        raise NotArchivedError
    courses = Course.objects.filter(university=university)
    applications = courses.aggregate(n=Count("applications"))["n"] or 0
    if applications:
        raise InUseError(applications, "application")
    commissions = Commission.objects.filter(university=university).count()
    if commissions:
        raise InUseError(commissions, "commission")
    university_id = university.pk
    course_count = courses.count()
    with transaction.atomic():
        courses.delete()
        university.delete()
    logger.info(
        "University deleted: university_id=%s, courses=%s, by_staff_id=%s",
        university_id,
        course_count,
        by.pk,
    )


def import_catalogue(parsed: ParsedCatalogue, by: Staff) -> ImportResult:
    """Add each good row's University (unless one has its name) and Course (unless it has one).

    Names match whatever their case, so importing a file again adds nothing twice (spec 48).
    A Course already there is left as it is, archived or not. A row naming a University
    already there in another country is a problem, not a guess. Each row is saved on its own.
    """
    universities = {university.name.lower(): university for university in University.objects.all()}
    course_names = {
        (university_id, name.lower())
        for university_id, name in Course.objects.values_list("university_id", "name")
    }
    imported = already_there = 0
    problems = list(parsed.problems)
    for row in parsed.rows:
        university = universities.get(row.university.lower())
        if university is not None and university.country != row.country:
            problems.append(RowProblem(row.row, _other_country(university, row)))
            continue
        if university is not None and (university.pk, row.course.name.lower()) in course_names:
            already_there += 1
            continue
        try:
            university = _saved(partial(_import_row, row, university))
        except DuplicateNameError:
            # silent: reported to the Admin as a row problem
            problems.append(RowProblem(row.row, IMPORT_ROW_FAILED_MESSAGE))
            continue
        universities[university.name.lower()] = university
        course_names.add((university.pk, row.course.name.lower()))
        imported += 1
    result = ImportResult(
        imported=imported,
        already_there=already_there,
        problems=tuple(sorted(problems, key=lambda problem: problem.row)),
    )
    logger.info(
        "Catalogue imported: imported=%s, already_there=%s, failed=%s, by_staff_id=%s",
        result.imported,
        result.already_there,
        result.failed,
        by.pk,
    )
    return result


def find_universities(query: str = "", *, include_archived: bool = False) -> QuerySet[University]:
    """Universities by their name or a Course's, each with its Courses and their Intakes.

    Without `include_archived`, archived Universities and Courses are left out, as pickers need.
    """
    courses = _courses_with_uses()
    universities = University.objects.all()
    if not include_archived:
        courses = courses.filter(archived=False)
        universities = universities.filter(archived=False)
    if query:
        # A University matches by its own name or any of its Courses' (spec 142).
        matching = Course.objects.filter(name__icontains=query).values("university_id")
        universities = universities.filter(Q(name__icontains=query) | Q(pk__in=matching))
    return universities.prefetch_related(Prefetch("courses", queryset=courses))


def page_of(universities: QuerySet[University], number: int) -> Page[University]:
    """One page of `universities`; the first page exists even when there are none.

    Raises:
        django.core.paginator.EmptyPage: There is no such page.
    """
    return Paginator(universities, UNIVERSITIES_PER_PAGE).page(number)


def university_by_id(university_id: int) -> University:
    """One University with its Courses and Intakes (archived ones included).

    Raises:
        University.DoesNotExist: There is no such University.
    """
    return find_universities(include_archived=True).get(pk=university_id)


def course_by_id(course_id: int) -> Course:
    """One Course with its Intakes.

    Raises:
        Course.DoesNotExist: There is no such Course.
    """
    return _courses_with_uses().get(pk=course_id)


def _courses_with_uses() -> QuerySet[Course]:
    """Courses with their Intakes, extra documents and how many Applications use each."""
    # A count drops the model's own ordering (GROUP BY), so name order is asked for again.
    return (
        Course.objects.prefetch_related("intakes", _asked_documents())
        .annotate(application_count=Count("applications"))
        .order_by(Lower("name"), "pk")
    )


def _asked_documents() -> Prefetch[str, QuerySet[DocumentRequirement], str]:
    """The extra Document Types a Course asks for, with their names (issues 40, 80)."""
    return Prefetch(
        "document_requirements",
        queryset=DocumentRequirement.objects.select_related("document_type"),
    )


def _set_given(record: University | Course, **values: object) -> None:
    """Set each field given a value; None leaves the field as it is."""
    for field, value in values.items():
        if value is not None:
            setattr(record, field, value)


def _save(university: University) -> University:
    university.save()
    return university


def _saved[T](save: Callable[[], T]) -> T:
    """Run `save` in one transaction; a name taken meanwhile is a duplicate, not a crash."""
    try:
        with transaction.atomic():
            return save()
    except IntegrityError as error:
        raise DuplicateNameError from error


def _set_intakes(course: Course, months: tuple[str, ...]) -> None:
    """Make the Course's Intakes exactly `months`, keeping the rows of those it already has."""
    wanted = tidy_intakes(months)
    course.intakes.exclude(month__in=wanted).delete()
    kept = set(course.intakes.values_list("month", flat=True))
    Intake.objects.bulk_create(Intake(course=course, month=m) for m in wanted if m not in kept)
    # Drop any prefetched Intakes so the answer shows what is stored now.
    getattr(course, "_prefetched_objects_cache", {}).pop("intakes", None)


def _delete_courses(course_ids: tuple[int, ...]) -> None:
    """Delete these Courses with their Intakes and extra documents.

    Raises:
        InUseError: An Application uses one of them (checked before; this is a race).
    """
    try:
        Course.objects.filter(pk__in=course_ids).delete()
    except ProtectedError as error:
        raise InUseError(len(error.protected_objects), "application") from error


def _free_names(stored: dict[int, Course], rows: tuple[CourseEdit, ...]) -> None:
    """Give each Course being renamed a passing name first, so two Courses can swap names."""
    for row in rows:
        course = stored.get(row.id or 0)
        if course is not None and course.name != row.name:
            Course.objects.filter(pk=course.pk).update(name=f"{course.pk} {uuid4().hex}")


def _save_course_row(
    university: University, course: Course | None, row: CourseEdit, by: Staff
) -> None:
    """Store one Course of the edit page, new or changed, with its Intakes and documents."""
    if course is None:
        course = Course(university=university)
    course.name = row.name
    course.level = row.level
    course.archived = row.archived
    course.save()
    _set_intakes(course, row.intakes)
    set_course_requirements(course, row.documents, by=by)


def _import_row(row: CatalogueRow, university: University | None) -> University:
    """Add the row's Course, and its University first if there isn't one yet."""
    if university is None:
        university = University.objects.create(name=row.university, country=row.country)
    course = Course.objects.create(
        university=university, name=row.course.name, level=row.course.level
    )
    _set_intakes(course, row.course.intakes)
    return university


def _other_country(university: University, row: CatalogueRow) -> str:
    return (
        f"{university.name} is already in the Catalogue in "
        f"{Country(university.country).label}, not {row.country.label}."
    )
