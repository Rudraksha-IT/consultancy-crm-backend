"""Public services for Expenses (spec 76c). Call inside the Consultancy's schema.

Who may see or change Expenses is the views' job, through `access.policy`. Expenses belong to
no Student, so no History is written (History is a Student's trail).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import QuerySet, Sum

from expenses.models import Expense, ExpenseCategory
from expenses.types import Month, NewExpense
from staff.actors import actor_name, stored_staff
from staff.models import Staff

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CategoryTotal:
    """What one kind of Expense came to in a month."""

    category: ExpenseCategory
    amount: int


@dataclass(frozen=True)
class MonthOfExpenses:
    """One month's Expenses (a lazy query, newest first), their total and totals by kind."""

    expenses: QuerySet[Expense]
    total: int
    by_category: list[CategoryTotal]


def expenses_in(month: Month) -> MonthOfExpenses:
    """The month's Expenses, newest first; its total; and each kind's total, largest first."""
    found = Expense.objects.filter(
        spent_on__gte=month.first_day, spent_on__lt=month.next_first_day
    ).order_by("-spent_on", "-pk")
    per_category = found.order_by().values("category").annotate(amount=Sum("amount"))
    by_category = sorted(
        (CategoryTotal(ExpenseCategory(row["category"]), row["amount"]) for row in per_category),
        key=lambda row: (-row.amount, row.category.value),
    )
    return MonthOfExpenses(
        expenses=found,
        total=sum(row.amount for row in by_category),
        by_category=by_category,
    )


def add_expense(details: NewExpense, by: Staff) -> Expense:
    """Record an Expense."""
    expense = Expense.objects.create(
        spent_on=details.spent_on,
        category=details.category,
        paid_to=details.paid_to,
        amount=details.amount,
        method=details.method,
        recorded_by=stored_staff(by),
        recorded_by_name=actor_name(by),
    )
    _log("Expense added", expense.pk, by)
    return expense


def change_expense(expense_id: int, details: NewExpense, by: Staff) -> Expense:
    """Replace every detail of an Expense. Who recorded it, and when, stay as they were.

    Raises:
        Expense.DoesNotExist: There is no such Expense.
    """
    with transaction.atomic():
        expense = Expense.objects.select_for_update().get(pk=expense_id)
        expense.spent_on = details.spent_on
        expense.category = details.category
        expense.paid_to = details.paid_to
        expense.amount = details.amount
        expense.method = details.method
        expense.save(update_fields=["spent_on", "category", "paid_to", "amount", "method"])
    _log("Expense changed", expense.pk, by)
    return expense


def delete_expense(expense_id: int, by: Staff) -> None:
    """Delete an Expense.

    Raises:
        Expense.DoesNotExist: There is no such Expense.
    """
    deleted, _ = Expense.objects.filter(pk=expense_id).delete()
    if deleted == 0:
        raise Expense.DoesNotExist
    _log("Expense deleted", expense_id, by)


def _log(what: str, expense_id: int, by: Staff) -> None:
    """Log ids only: never who it was paid to or the amount."""
    logger.info("%s: expense_id=%s, by_staff_id=%s", what, expense_id, by.pk)
