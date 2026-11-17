from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from expenses.models import ExpenseCategory, ExpenseMethod


@dataclass(frozen=True)
class NewExpense:
    """An Expense to add, or an Expense's new details (spec 76c)."""

    spent_on: date
    category: ExpenseCategory
    paid_to: str
    amount: int
    method: ExpenseMethod


@dataclass(frozen=True)
class Month:
    """One calendar month (AD)."""

    year: int
    month: int

    @property
    def first_day(self) -> date:
        """The month's first day."""
        return date(self.year, self.month, 1)

    @property
    def next_first_day(self) -> date:
        """The first day of the month after it."""
        if self.month == MONTHS_IN_A_YEAR:
            return date(self.year + 1, 1, 1)
        return date(self.year, self.month + 1, 1)

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


MONTHS_IN_A_YEAR = 12
