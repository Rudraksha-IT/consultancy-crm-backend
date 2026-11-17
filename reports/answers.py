"""How the Reports' endpoints answer (issues 28, 29, 30, 53, 85)."""

from __future__ import annotations

from dataclasses import asdict

from reports.calculations import MonthCounts
from reports.services import (
    ClassRow,
    CountryReport,
    MoneyReport,
    ProgressRow,
    StaffProgressReport,
)
from staff_progress.counting import COUNSELLOR_COUNTS, FRONT_DESK_COUNTS, TEACHER_COUNTS, Count
from students.models import Country, Source


def _countries(rows: list[tuple[Country, int]]) -> list[dict[str, object]]:
    return [{"country": country.value, "count": count} for country, count in rows]


def months_answer(months: list[MonthCounts], countries: CountryReport) -> dict[str, object]:
    """What happened to Students in each month, oldest first, and by country over all of them."""
    return {
        "months": [asdict(row) for row in months],
        "visas_granted_by_country": _countries(countries.visas_granted),
        "countries_wanted": _countries(countries.wanted),
    }


def sources_answer(rows: list[tuple[Source, int]]) -> dict[str, object]:
    """New visitors per Source, most first, and how many in all."""
    return {
        "total": sum(count for _, count in rows),
        "sources": [{"source": source.value, "count": count} for source, count in rows],
    }


def staff_progress_answer(report: StaffProgressReport) -> dict[str, object]:
    """The Counsellors, Front Desk and Teachers tables, and counts whose recording began later.

    The Counsellors' columns: Students now, the Counsellor cards, and Visas granted before Lost.
    """
    moved, made, applied, lost, tasks = COUNSELLOR_COUNTS
    return {
        "counsellors": [
            _person(row, {"students": row.students})
            | _counts(row, (moved, made, applied))
            | {"visas_granted": row.visas_granted}
            | _counts(row, (lost, tasks))
            for row in report.counsellors
        ],
        "front_desk": [_person(row) | _counts(row, FRONT_DESK_COUNTS) for row in report.front_desk],
        "teachers": [_person(row) | _counts(row, TEACHER_COUNTS) for row in report.teachers],
        "counted_from": {
            count.value: day.isoformat() for count, day in report.counted_from.items()
        },
    }


def _person(row: ProgressRow, more: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": row.staff.pk,
        "name": row.staff.name,
        "is_active": row.staff.is_active,
        **(more or {}),
    }


def _counts(row: ProgressRow, columns: tuple[Count, ...]) -> dict[str, object]:
    return {count.value: row.counts.values[count] for count in columns}


def classes_answer(rows: list[ClassRow]) -> dict[str, object]:
    """Each class's Students now and their share of class days attended between the dates."""
    return {
        "batches": [
            {
                "id": row.batch.pk,
                "name": row.batch.name,
                "test_type": row.batch.test_type,
                "teacher": row.batch.teacher.name,
                "students": row.students,
                "attended_percent": row.attended_percent,
            }
            for row in rows
        ]
    }


def money_answer(report: MoneyReport) -> dict[str, object]:
    """Money in (from Students, from Commission) and out each month, and out by category."""
    return {
        "months": [
            {
                "month": row.month,
                "from_students": row.from_students,
                "from_commission": row.from_commission,
                "money_out": row.money_out,
            }
            for row in report.months
        ],
        "money_out_by_category": [
            {"category": category.value, "amount": amount}
            for category, amount in report.money_out_by_category
        ],
    }
