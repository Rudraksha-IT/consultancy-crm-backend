"""The Reports' counts (issue 28). Pure: no database, no request.

Every count comes from the Student's History (Stage changes) and their Visits:

- New visitors: Visits of kind New, in the month they came.
- Signed up: the first time a Student reached Counselling or further, once per Student ever.
- Applied: the first time a Student reached Applying or further, once per Student ever.
- Visa granted: each Visa, on the day it was granted (a granted Visa leaves the Student at Visa).
- Visa refused: each move from Visa back to Offer or Counselling (a Visa Refused moves them back).
- Lost: each move to Lost.
- Moved forward: a Stage change further along the journey (Lost is outside it).

The Classes report (issue 30) comes from Attendance: a class's share is the average of its
Students' shares of their marked days, Present and Late counting as attended (as on the class page).

The Money report (issue 29) adds up amounts by the month of their day: money in is Payments (on
the day paid) plus Commission (on the day Received); money out is Expenses (on the day spent).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from batches.attendance import attended_percent, average_percent
from expenses.models import ExpenseCategory
from students.models import Country, Source, Stage

MONTHS_IN_YEAR = 12

# The journey in order; Lost is outside it.
JOURNEY = (Stage.LEAD, Stage.COUNSELLING, Stage.APPLYING, Stage.OFFER, Stage.VISA)
_RANK = {stage: rank for rank, stage in enumerate(JOURNEY)}
# Stages that are gone (issue 74), as History written before then still names them.
_GONE_STAGES = {"onboarded": Stage.COUNSELLING, "completed": Stage.VISA}
_BACK_FROM_VISA = frozenset({Stage.OFFER, Stage.COUNSELLING})


@dataclass(frozen=True)
class StageMove:
    """One Stage change from a Student's History, on the office's local day."""

    student_id: int
    on: date
    before: Stage
    after: Stage


@dataclass
class MonthCounts:
    """What happened to Students in one month ("YYYY-MM")."""

    month: str
    new_visitors: int = 0
    signed_up: int = 0
    applied: int = 0
    visas_granted: int = 0
    visas_refused: int = 0
    lost: int = 0


def stage_from_history(value: str) -> Stage | None:
    """The Stage a History entry names; a Stage that is gone reads as where it now belongs."""
    if value in _GONE_STAGES:
        return _GONE_STAGES[value]
    return Stage(value) if value in Stage.values else None


def month_of(day: date) -> str:
    """The day's month as "YYYY-MM"."""
    return f"{day.year:04d}-{day.month:02d}"


def months_back(today: date, count: int) -> list[str]:
    """The last `count` months, oldest first, ending with today's."""
    first = today.year * MONTHS_IN_YEAR + today.month - 1 - (count - 1)
    return [
        f"{index // MONTHS_IN_YEAR:04d}-{index % MONTHS_IN_YEAR + 1:02d}"
        for index in range(first, first + count)
    ]


def is_forward(move: StageMove) -> bool:
    """Whether the move takes the Student further along the journey."""
    before, after = _RANK.get(move.before), _RANK.get(move.after)
    return before is not None and after is not None and after > before


def _reached(move: StageMove, stage: Stage) -> bool:
    rank = _RANK.get(move.after)
    return rank is not None and rank >= _RANK[stage]


@dataclass
class _FirstTimes:
    """When each Student first reached a Stage, kept while going through the moves in order."""

    stage: Stage
    seen: set[int] = field(default_factory=set)

    def is_first(self, move: StageMove) -> bool:
        if move.student_id in self.seen or not _reached(move, self.stage):
            return False
        self.seen.add(move.student_id)
        return True


def student_months(
    months: Sequence[str],
    new_visit_days: Iterable[date],
    moves: Iterable[StageMove],
    granted_days: Iterable[date] = (),
) -> list[MonthCounts]:
    """What happened in each of `months`.

    `moves` must hold every Stage change up to the last month, not only those in `months`, so a
    Student who signed up earlier is never counted again. `granted_days` are the days each Visa
    was granted.
    """
    counts = {month: MonthCounts(month) for month in months}
    for visited in _rows_of(counts, new_visit_days):
        visited.new_visitors += 1
    for granted in _rows_of(counts, granted_days):
        granted.visas_granted += 1
    signed_up, applied = _FirstTimes(Stage.COUNSELLING), _FirstTimes(Stage.APPLYING)
    for found in sorted(moves, key=lambda item: item.on):
        first_sign_up, first_application = signed_up.is_first(found), applied.is_first(found)
        row = counts.get(month_of(found.on))
        if row is None:
            continue
        row.signed_up += first_sign_up
        row.applied += first_application
        row.visas_refused += found.before == Stage.VISA and found.after in _BACK_FROM_VISA
        row.lost += found.after == Stage.LOST
    return [counts[month] for month in months]


def _rows_of(counts: Mapping[str, MonthCounts], days: Iterable[date]) -> Iterator[MonthCounts]:
    """The month row of each day, for the days in the months counted."""
    for day in days:
        row = counts.get(month_of(day))
        if row is not None:
            yield row


def sorted_sources(counts: Mapping[Source, int]) -> list[tuple[Source, int]]:
    """Every Source with its count, most first; ties keep the Sources' fixed order."""
    rows = [(source, counts.get(source, 0)) for source in Source]
    return sorted(rows, key=lambda row: -row[1])


@dataclass(frozen=True)
class ClassMark:
    """One Student's mark on one class day: attended (Present or Late) or not."""

    batch_id: int
    student_id: int
    attended: bool


def class_attendance(marks: Iterable[ClassMark]) -> dict[int, int | None]:
    """Per Batch id, the average of its Students' shares of class days attended, rounded half up.

    Each Student weighs the same, however many days they were marked. A Batch without marks is
    left out.
    """
    days: defaultdict[int, defaultdict[int, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0])
    )
    for found in marks:
        counts = days[found.batch_id][found.student_id]
        counts[0] += found.attended
        counts[1] += 1
    return {
        batch_id: average_percent(
            attended_percent(attended, marked) for attended, marked in students.values()
        )
        for batch_id, students in days.items()
    }


@dataclass(frozen=True)
class Amount:
    """An amount of money (whole NPR) on a day."""

    on: date
    amount: int


@dataclass
class MoneyMonth:
    """Money in (from Students and from Commission) and money out in one month ("YYYY-MM")."""

    month: str
    from_students: int = 0
    from_commission: int = 0
    money_out: int = 0

    @property
    def money_in(self) -> int:
        """Payments plus Received Commission."""
        return self.from_students + self.from_commission


def _add_up(counts: Mapping[str, MoneyMonth], amounts: Iterable[Amount], column: str) -> None:
    for found in amounts:
        row = counts.get(month_of(found.on))
        if row is not None:
            setattr(row, column, getattr(row, column) + found.amount)


def money_months(
    months: Sequence[str],
    payments: Iterable[Amount],
    commission: Iterable[Amount],
    expenses: Iterable[Amount],
) -> list[MoneyMonth]:
    """Money in and out in each of `months`; amounts on days outside them are left out."""
    counts = {month: MoneyMonth(month) for month in months}
    _add_up(counts, payments, "from_students")
    _add_up(counts, commission, "from_commission")
    _add_up(counts, expenses, "money_out")
    return [counts[month] for month in months]


def by_category(amounts: Iterable[tuple[str, int]]) -> list[tuple[ExpenseCategory, int]]:
    """Each kind of Expense that has any, with its total, largest first.

    Ties keep the categories' fixed order.
    """
    totals: defaultdict[ExpenseCategory, int] = defaultdict(int)
    for category, amount in amounts:
        totals[ExpenseCategory(category)] += amount
    order = list(ExpenseCategory)
    return sorted(totals.items(), key=lambda row: (-row[1], order.index(row[0])))


@dataclass(frozen=True)
class GrantedVisa:
    """A Visa granted on a day, for a University in a country."""

    on: date
    country: Country


@dataclass(frozen=True)
class NewVisitor:
    """A Student's first visit on a day, and the country they want to study in."""

    student_id: int
    on: date
    country: Country


def _by_country(countries: Iterable[Country]) -> list[tuple[Country, int]]:
    """Each country that has any, with its count, most first; ties keep the countries' order."""
    counts: defaultdict[Country, int] = defaultdict(int)
    for country in countries:
        counts[country] += 1
    order = list(Country)
    return sorted(counts.items(), key=lambda row: (-row[1], order.index(row[0])))


def countries_granted(
    visas: Iterable[GrantedVisa], start: date, end: date
) -> list[tuple[Country, int]]:
    """Visas granted between the days (both included), per country, most first."""
    return _by_country(visa.country for visa in visas if start <= visa.on <= end)


def countries_wanted(
    visitors: Iterable[NewVisitor], start: date, end: date
) -> list[tuple[Country, int]]:
    """New visitors between the days (both included), each Student once, per wanted country."""
    wanted = {
        visitor.student_id: visitor.country for visitor in visitors if start <= visitor.on <= end
    }
    return _by_country(wanted.values())
