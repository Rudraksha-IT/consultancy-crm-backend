"""Reading a Catalogue CSV file (spec 48-49). Pure: no database.

The format (also in README.md, "Catalogue import"): a first row naming the columns
`university,country,course,level,intakes` (any order, any case), then one row per Course.
Intakes are months and years ("Mar 2027" or "2027-03") separated by semicolons (or commas).
Each row is checked on its own: good rows are returned, and every problem in a bad row is
reported with the row's number as a spreadsheet shows it (the column names are row 1).
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass

from django.db.models import TextChoices

from catalogue.models import (
    COURSE_NAME_MAX_LENGTH,
    MAX_INTAKES_PER_COURSE,
    UNIVERSITY_NAME_MAX_LENGTH,
    CourseLevel,
)
from catalogue.types import NewCourse
from students.models import Country

MAX_CSV_ROWS = 2000
COLUMNS = ("university", "country", "course", "level", "intakes")
# How much of a refused value a message repeats.
ECHO_MAX_LENGTH = 40

EMPTY_FILE_MESSAGE = "The file is empty."
HEADER_MESSAGE = f"The first row must name the columns: {','.join(COLUMNS)}."
TOO_MANY_ROWS_MESSAGE = (
    f"A file can have at most {MAX_CSV_ROWS:,} rows. Split it and import each part."
)
NOT_CSV_MESSAGE = "We couldn't read this file as CSV."
TOO_MANY_INTAKES_MESSAGE = f"A course can have at most {MAX_INTAKES_PER_COURSE} intakes."
NO_INTAKES_MESSAGE = "Add at least one intake."

_BYTE_ORDER_MARK = "﻿"
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_SPACES = re.compile(r"\s+")
_INTAKE_SEPARATORS = re.compile(r"[;,]")
_STORED_INTAKE = re.compile(r"^(20[0-9]{2})-(0[1-9]|1[0-2])\Z")
_WRITTEN_INTAKE = re.compile(r"^([a-z]+) +(20[0-9]{2})\Z")
_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_SHORT_MONTH_LENGTH = 3


class CatalogueFileError(Exception):
    """The file as a whole can't be imported; its message says why, safe to show."""


@dataclass(frozen=True)
class CatalogueRow:
    """One good row: a University (found or added by name) and one of its Courses."""

    row: int
    university: str
    country: Country
    course: NewCourse


@dataclass(frozen=True)
class RowProblem:
    """Why one row can't be imported."""

    row: int
    problem: str


@dataclass(frozen=True)
class ParsedCatalogue:
    """The good rows, and the problems of the bad ones, in file order."""

    rows: tuple[CatalogueRow, ...]
    problems: tuple[RowProblem, ...]


def parse_catalogue_csv(text: str) -> ParsedCatalogue:
    """Read a Catalogue CSV file, checking every row.

    Raises:
        CatalogueFileError: The file is empty, isn't CSV, lacks the column names, or has
            more than `MAX_CSV_ROWS` rows.
    """
    records = _records(text.removeprefix(_BYTE_ORDER_MARK))
    order = _column_order(records)
    rows: list[CatalogueRow] = []
    problems: list[RowProblem] = []
    count = 0
    for number, cells in records:
        if not any(cell.strip() for cell in cells):
            continue
        count += 1
        if count > MAX_CSV_ROWS:
            raise CatalogueFileError(TOO_MANY_ROWS_MESSAGE)
        row, found = _read_row(number, cells, order)
        if row is not None:
            rows.append(row)
        problems.extend(RowProblem(number, problem) for problem in found)
    return ParsedCatalogue(rows=tuple(rows), problems=tuple(problems))


def _records(text: str) -> _NumberedRecords:
    if "\x00" in text:
        raise CatalogueFileError(NOT_CSV_MESSAGE)
    if not text.strip():
        raise CatalogueFileError(EMPTY_FILE_MESSAGE)
    return _NumberedRecords(text)


class _NumberedRecords:
    """The file's records, each with its number (the first is 1); CSV errors become ours."""

    def __init__(self, text: str) -> None:
        self._reader = enumerate(csv.reader(io.StringIO(text, newline=""), strict=True), start=1)

    def __iter__(self) -> Iterator[tuple[int, list[str]]]:
        return self

    def __next__(self) -> tuple[int, list[str]]:
        try:
            return next(self._reader)
        except csv.Error as error:
            raise CatalogueFileError(NOT_CSV_MESSAGE) from error


def _column_order(records: _NumberedRecords) -> dict[str, int]:
    """Where each column is, from the first non-blank row."""
    for _, cells in records:
        if not any(cell.strip() for cell in cells):
            continue
        names = [cell.strip().lower() for cell in cells]
        if sorted(names) != sorted(COLUMNS):
            raise CatalogueFileError(HEADER_MESSAGE)
        return {name: names.index(name) for name in COLUMNS}
    raise CatalogueFileError(EMPTY_FILE_MESSAGE)  # pragma: no cover - blank text is caught first


def _read_row(
    number: int, cells: list[str], order: dict[str, int]
) -> tuple[CatalogueRow | None, list[str]]:
    if len(cells) != len(COLUMNS):
        return None, [f"This row has {len(cells)} columns; it needs {len(COLUMNS)}."]
    value = {name: cells[index].strip() for name, index in order.items()}
    problems: list[str] = []
    university = _name(value["university"], "University name", UNIVERSITY_NAME_MAX_LENGTH, problems)
    country = _choice(value["country"], "Country", Country, problems)
    course = _name(value["course"], "Course name", COURSE_NAME_MAX_LENGTH, problems)
    level = _choice(value["level"], "Level", CourseLevel, problems)
    intakes = _intakes(value["intakes"], problems)
    if problems:
        return None, problems
    assert country is not None and level is not None  # noqa: S101 - no problems means both found
    return (
        CatalogueRow(
            row=number,
            university=university,
            country=Country(country),
            course=NewCourse(name=course, level=CourseLevel(level), intakes=intakes),
        ),
        [],
    )


def _name(value: str, label: str, max_length: int, problems: list[str]) -> str:
    if not value:
        problems.append(f"{label} is empty.")
    elif len(value) > max_length:
        problems.append(f"{label} is longer than {max_length} characters.")
    elif _CONTROL_CHARACTERS.search(value):
        problems.append(f"{label} has characters that aren't allowed.")
    return value


def _choice[C: TextChoices](
    value: str, label: str, choices: type[C], problems: list[str]
) -> C | None:
    """The choice whose name (or API value) this is, ignoring case and extra spaces."""
    wanted = _SPACES.sub(" ", value).lower()
    for choice in choices:
        if wanted in (choice.value, str(choice.label).lower()):
            return choice
    if not value:
        problems.append(f"{label} is empty.")
    else:
        names = ", ".join(str(choice.label) for choice in choices)
        problems.append(f"{label} “{_echo(value)}” isn't one of {names}.")
    return None


def _intakes(value: str, problems: list[str]) -> tuple[str, ...]:
    pieces = [piece.strip() for piece in _INTAKE_SEPARATORS.split(value) if piece.strip()]
    if not pieces:
        problems.append(NO_INTAKES_MESSAGE)
        return ()
    months: set[str] = set()
    for piece in pieces:
        month = _intake(piece)
        if month is None:
            problems.append(f"Intake “{_echo(piece)}” must look like “Sep 2027”.")
        else:
            months.add(month)
    if len(months) > MAX_INTAKES_PER_COURSE:
        problems.append(TOO_MANY_INTAKES_MESSAGE)
    return tuple(sorted(months))


def _intake(piece: str) -> str | None:
    """Read "Mar 2027", "March 2027" or "2027-03" (any case) as "2027-03"; None otherwise."""
    text = piece.lower()
    if _STORED_INTAKE.match(text):
        return text
    written = _WRITTEN_INTAKE.match(text)
    if written is None:
        return None
    word, year = written.groups()
    for index, name in enumerate(_MONTHS, start=1):
        if word == name or (len(word) == _SHORT_MONTH_LENGTH and name.startswith(word)):
            return f"{year}-{index:02d}"
    return None


def _echo(value: str) -> str:
    """A refused value as a message repeats it: no control characters, and not too long."""
    clean = _CONTROL_CHARACTERS.sub("", value)
    return clean if len(clean) <= ECHO_MAX_LENGTH else f"{clean[:ECHO_MAX_LENGTH]}…"
