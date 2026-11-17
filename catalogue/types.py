from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from catalogue.models import CourseLevel
from students.models import Country

if TYPE_CHECKING:
    from catalogue.csv_import import RowProblem
    from documents.models import DocumentType


@dataclass(frozen=True)
class UniversityChange:
    """What an Admin changes on a University; None leaves a field as it is."""

    name: str | None = None
    country: Country | None = None
    archived: bool | None = None


@dataclass(frozen=True)
class CourseChange:
    """What an Admin changes on a Course; None leaves a field as it is."""

    name: str | None = None
    level: CourseLevel | None = None
    intakes: tuple[str, ...] | None = None
    archived: bool | None = None


@dataclass(frozen=True)
class NewCourse:
    """A Course to add to a University (spec 47)."""

    name: str
    level: CourseLevel
    intakes: tuple[str, ...]
    documents: tuple[DocumentType, ...] = ()


@dataclass(frozen=True)
class CourseEdit:
    """One Course on a University's edit page: a stored one (with its id) or a new one."""

    id: int | None
    name: str
    level: CourseLevel
    intakes: tuple[str, ...]
    archived: bool
    documents: tuple[DocumentType, ...]


@dataclass(frozen=True)
class UniversityEdit:
    """Everything a University's edit page saves at once (issue 92).

    Courses left out of `courses` and `deleted_courses` stay as they are.
    """

    name: str
    country: Country
    archived: bool
    courses: tuple[CourseEdit, ...]
    deleted_courses: tuple[int, ...]


@dataclass(frozen=True)
class ImportResult:
    """What a CSV import did: Courses added, Courses already there, and the problems found."""

    imported: int
    already_there: int
    problems: tuple[RowProblem, ...]

    @property
    def failed(self) -> int:
        """How many rows weren't imported."""
        return len({problem.row for problem in self.problems})
