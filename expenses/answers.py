"""Expenses as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from expenses.models import Expense
from expenses.services import CategoryTotal


def expense_answer(expense: Expense) -> dict[str, object]:
    """One Expense: the day (AD), what kind, who to, how much, how, and who recorded it."""
    return {
        "id": expense.public_id,
        "spent_on": expense.spent_on.isoformat(),
        "category": expense.category,
        "paid_to": expense.paid_to,
        "amount": expense.amount,
        "method": expense.method,
        "recorded_by": {"name": expense.recorded_by_name},
    }


def category_total_answer(row: CategoryTotal) -> dict[str, object]:
    """What one kind of Expense came to."""
    return {"category": row.category.value, "amount": row.amount}
