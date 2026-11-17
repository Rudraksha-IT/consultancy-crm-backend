"""REST endpoints for Expenses, part of the Accounts Module and for Admins only (spec 76c).

Every endpoint answers 404 while Accounts is off, for every Role, before any other check.
"""

from __future__ import annotations

from django.core.paginator import EmptyPage, Paginator
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import ONLY_ADMIN_EXPENSES_MESSAGE, why_cannot_manage_expenses
from expenses.answers import category_total_answer, expense_answer
from expenses.models import Expense
from expenses.serializers import ExpenseListQuerySerializer, NewExpenseSerializer
from expenses.services import add_expense, change_expense, delete_expense, expenses_in
from modules.permissions import requires_module
from modules.types import Module
from staff.context import get_request_staff
from staff.models import Staff

# Expenses per page of one month's list.
EXPENSES_PER_PAGE = 50
AccountsModuleIsOn = requires_module(Module.ACCOUNTS)


class CanManageExpenses(BasePermission):
    """Expenses are for Admins (spec 76c)."""

    message = ONLY_ADMIN_EXPENSES_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member see and change Expenses."""
        user = request.user
        return isinstance(user, Staff) and why_cannot_manage_expenses(user) is None


class ExpensesView(APIView):
    """One month's Expenses with their totals (GET), or a new Expense (POST)."""

    permission_classes = (IsAuthenticated, AccountsModuleIsOn, CanManageExpenses)
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return one page of the month's Expenses, newest first, and the month's totals."""
        query = ExpenseListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        month = query.chosen_month()
        found = expenses_in(month)
        try:
            page = Paginator(found.expenses, EXPENSES_PER_PAGE).page(query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(
            {
                "month": str(month),
                "total": found.total,
                "count": page.paginator.count,
                "page": page.number,
                "page_size": EXPENSES_PER_PAGE,
                "by_category": [category_total_answer(row) for row in found.by_category],
                "results": [expense_answer(expense) for expense in page],
            }
        )

    def post(self, request: Request) -> Response:
        """Record an Expense and return it."""
        serializer = NewExpenseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        expense = add_expense(serializer.to_new_expense(), by=get_request_staff(request))
        return Response(expense_answer(expense), status=status.HTTP_201_CREATED)


class ExpenseView(APIView):
    """Change (PUT) or delete (DELETE) one Expense."""

    permission_classes = (IsAuthenticated, AccountsModuleIsOn, CanManageExpenses)
    parser_classes = (JSONParser,)

    def put(self, request: Request, expense_id: int) -> Response:
        """Replace every detail and return the Expense."""
        serializer = NewExpenseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            expense = change_expense(
                expense_id, serializer.to_new_expense(), by=get_request_staff(request)
            )
        except Expense.DoesNotExist as error:
            raise NotFound from error
        return Response(expense_answer(expense))

    def delete(self, request: Request, expense_id: int) -> Response:
        """Delete it."""
        try:
            delete_expense(expense_id, by=get_request_staff(request))
        except Expense.DoesNotExist as error:
            raise NotFound from error
        return Response(status=status.HTTP_204_NO_CONTENT)
