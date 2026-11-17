"""The Catalogue as the API returns it. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from catalogue.models import Course, University
from catalogue.types import ImportResult
from documents.models import DocumentRequirement


def _documents(requirements: list[DocumentRequirement]) -> list[dict[str, object]]:
    """The extra Document Types asked for (issue 40), oldest type first."""
    return [
        {"id": requirement.document_type_id, "name": requirement.document_type.name}
        for requirement in requirements
    ]


def course_answer(course: Course) -> dict[str, object]:
    """One Course with its Intakes ("YYYY-MM", AD), oldest first."""
    return {
        "id": course.pk,
        "university_id": course.university_id,
        "name": course.name,
        "level": course.level,
        "intakes": [intake.month for intake in course.intakes.all()],
        "archived": course.archived,
        "documents": _documents(list(course.document_requirements.all())),
        # How many Applications use it: such a Course can't be deleted (issue 92).
        "applications": _uses(course),
    }


def _uses(course: Course) -> int:
    """The count loaded with the Course, or counted now for one loaded without it."""
    count = getattr(course, "application_count", None)
    return count if isinstance(count, int) else course.applications.count()


def university_answer(university: University) -> dict[str, object]:
    """One University with the Courses that were loaded with it."""
    return {
        "id": university.pk,
        "name": university.name,
        "country": university.country,
        "archived": university.archived,
        "courses": [course_answer(course) for course in university.courses.all()],
    }


def import_answer(result: ImportResult) -> dict[str, object]:
    """What an import did; `failed` counts rows, and problems are in row order."""
    return {
        "imported": result.imported,
        "already_there": result.already_there,
        "failed": result.failed,
        "problems": [{"row": p.row, "problem": p.problem} for p in result.problems],
    }
