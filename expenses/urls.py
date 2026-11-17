from __future__ import annotations

from django.urls import URLPattern, path

from expenses.views import ExpensesView, ExpenseView

urlpatterns: list[URLPattern] = [
    path("accounts/expenses/", ExpensesView.as_view(), name="expenses"),
    path("accounts/expenses/<int:expense_id>/", ExpenseView.as_view(), name="expense"),
]
