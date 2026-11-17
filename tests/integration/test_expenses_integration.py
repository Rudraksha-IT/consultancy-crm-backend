"""Expenses: what the office pays out, by month, for Admins (spec 76c, 76f; issue 36)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from expenses.models import Expense
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff, StaffStatus
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

LIST_URL = "/api/accounts/expenses/"
AMOUNT_MESSAGE = "Enter a whole amount from NPR 1 to NPR 10,00,00,000."
DATE_MESSAGE = "Enter a date from 1 Jan 2000 to today."
CATEGORY_MESSAGE = "Choose what kind of expense it is."
METHOD_MESSAGE = "Choose how it was paid."
PAID_TO_MESSAGE = "Say who it was paid to."
MONTH_MESSAGE = "Choose a month from Jan 2000 to this month, as YYYY-MM."
ONLY_ADMIN = {"detail": "Only an Admin can see or change Expenses."}
GOOD_BODY: dict[str, object] = {
    "spent_on": "2025-10-12",
    "category": "electricity",
    "paid_to": "Nepal Electricity Authority",
    "amount": 6500,
    "method": "khalti",
}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with Accounts on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.ACCOUNTS})
    return office


def expense_url(expense: Expense | int) -> str:
    """One Expense."""
    pk = expense if isinstance(expense, int) else expense.pk
    return f"{LIST_URL}{pk}/"


def make_expense(office: ConsultancyContext, by: Staff, **fields: object) -> Expense:
    """Store an Expense directly."""
    details: dict[str, object] = {
        "spent_on": date(2025, 10, 1),
        "category": "office_rent",
        "paid_to": "Office landlord",
        "amount": 85000,
        "method": "bank_transfer",
        **fields,
    }
    with tenant_context(office.consultancy):
        return Expense.objects.create(recorded_by=by, recorded_by_name=by.name, **details)


def add(office: ConsultancyContext, body: object) -> _MonkeyPatchedResponse:
    """POST a new Expense."""
    return office.client.post(LIST_URL, body, format="json")


def stored(office: ConsultancyContext) -> list[tuple[str, str, int]]:
    """Every Expense as stored: (category, paid to, amount), oldest first."""
    with tenant_context(office.consultancy):
        return list(Expense.objects.order_by("pk").values_list("category", "paid_to", "amount"))


def answer(expense: Expense) -> dict[str, object]:
    """One Expense as the API answers it."""
    return {
        "id": expense.pk,
        "spent_on": expense.spent_on.isoformat(),
        "category": expense.category,
        "paid_to": expense.paid_to,
        "amount": expense.amount,
        "method": expense.method,
        "recorded_by": {"name": expense.recorded_by_name},
    }


class TestMonthList:
    def test_an_admin_sees_one_months_expenses_newest_first_with_totals(
        self, abc: ConsultancyContext
    ) -> None:
        admin = named_staff(abc, "Anita Admin", Role.ADMIN)
        rent = make_expense(abc, admin, spent_on=date(2025, 10, 1), amount=85000)
        internet = make_expense(
            abc, admin, spent_on=date(2025, 10, 3), category="internet", amount=3500
        )
        second_rent = make_expense(abc, admin, spent_on=date(2025, 10, 3), amount=1000)
        make_expense(abc, admin, spent_on=date(2025, 9, 30), category="salaries", amount=320000)
        make_expense(abc, admin, spent_on=date(2025, 11, 1), amount=85000)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST_URL, {"month": "2025-10"})

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "month": "2025-10",
            "total": 89500,
            "count": 3,
            "page": 1,
            "page_size": 50,
            "by_category": [
                {"category": "office_rent", "amount": 86000},
                {"category": "internet", "amount": 3500},
            ],
            "results": [answer(second_rent), answer(internet), answer(rent)],
        }

    def test_the_same_amount_per_category_is_listed_by_name(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        make_expense(abc, admin, category="marketing", amount=500)
        make_expense(abc, admin, category="internet", amount=500)

        body = abc.client.get(LIST_URL, {"month": "2025-10"}).json()

        assert [row["category"] for row in body["by_category"]] == ["internet", "marketing"]

    def test_without_a_month_it_shows_this_month(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        today = timezone.localdate()
        make_expense(abc, admin, spent_on=today, amount=700)
        make_expense(abc, admin, spent_on=today.replace(day=1) - timedelta(days=1), amount=9)

        body = abc.client.get(LIST_URL).json()

        assert body["month"] == today.strftime("%Y-%m")
        assert (body["total"], body["count"]) == (700, 1)
        assert abc.client.get(LIST_URL, {"month": ""}).json()["month"] == body["month"]

    def test_a_month_with_nothing(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        body = abc.client.get(LIST_URL, {"month": "2025-01"}).json()

        assert body["total"] == 0
        assert body["count"] == 0
        assert body["by_category"] == []
        assert body["results"] == []

    def test_pages_of_50_with_totals_for_the_whole_month(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        for _ in range(51):
            make_expense(abc, admin, amount=10)

        first = abc.client.get(LIST_URL, {"month": "2025-10"}).json()
        second = abc.client.get(LIST_URL, {"month": "2025-10", "page": "2"}).json()

        assert (len(first["results"]), len(second["results"])) == (50, 1)
        assert (second["total"], second["count"], second["page"]) == (510, 51, 2)

    def test_a_page_past_the_end_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST_URL, {"month": "2025-10", "page": "2"})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        "month",
        ["1999-12", "2025-13", "2025-00", "2025-1", "25-10", "2025-10-01", "x", "<script>"],
    )
    def test_refused_months(self, abc: ConsultancyContext, month: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST_URL, {"month": month})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"month": [MONTH_MESSAGE]}

    def test_a_future_month_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        next_month = (timezone.localdate().replace(day=28) + timedelta(days=5)).strftime("%Y-%m")

        response = abc.client.get(LIST_URL, {"month": next_month})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize(
        "query", [{"page": "0"}, {"page": "x"}, {"page": "<script>"}, {"category": "rent"}]
    )
    def test_refused_queries(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "<script>" not in response.content.decode()

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins(self, abc: ConsultancyContext, role: Role) -> None:
        admin = named_staff(abc, "Anita Admin", Role.ADMIN)
        make_expense(abc, admin)
        abc.log_in_as(role)

        response = abc.client.get(LIST_URL, {"month": "2025-10"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN

    def test_a_deactivated_admin_is_refused(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        assert abc.client.get(LIST_URL).status_code in {401, 403}

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_only_get_and_post_on_the_list(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(LIST_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.client.get(LIST_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert add(abc, GOOD_BODY).status_code == status.HTTP_401_UNAUTHORIZED
        assert stored(abc) == []


class TestAddExpense:
    def test_an_admin_adds_an_expense(self, abc: ConsultancyContext) -> None:
        admin = named_staff(abc, "Anita Admin", Role.ADMIN)
        log_in(abc, admin)

        response = add(abc, GOOD_BODY)

        assert response.status_code == status.HTTP_201_CREATED
        with tenant_context(abc.consultancy):
            expense = Expense.objects.get()
        assert response.json() == {
            **GOOD_BODY,
            "id": expense.pk,
            "recorded_by": {"name": "Anita Admin"},
        }
        assert expense.recorded_by_id == admin.pk

    def test_paid_to_is_trimmed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        add(abc, {**GOOD_BODY, "paid_to": "  WorldLink  "})

        assert stored(abc) == [("electricity", "WorldLink", 6500)]

    @pytest.mark.parametrize(
        "category",
        [
            "office_rent",
            "salaries",
            "internet",
            "electricity",
            "marketing",
            "education_fair",
            "office_supplies",
            "tea_and_snacks",
            "other",
        ],
    )
    def test_every_category_on_the_starting_list(
        self, abc: ConsultancyContext, category: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        assert add(abc, {**GOOD_BODY, "category": category}).status_code == 201

    @pytest.mark.parametrize(
        "method", ["cash", "esewa", "khalti", "fonepay_qr", "bank_transfer", "cheque", "card"]
    )
    def test_every_method(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        assert add(abc, {**GOOD_BODY, "method": method}).status_code == 201

    def test_today_is_allowed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        today = timezone.localdate().isoformat()

        assert add(abc, {**GOOD_BODY, "spent_on": today}).status_code == 201

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_other_roles_are_refused_and_nothing_is_stored(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)

        response = add(abc, GOOD_BODY)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN
        assert stored(abc) == []

    def test_logs_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        json_logs.clear()

        add(abc, {**GOOD_BODY, "paid_to": "Secret landlord", "amount": 43210})

        with tenant_context(abc.consultancy):
            expense_id = Expense.objects.get().pk
        [line] = json_logs.with_message("Expense added")
        assert line["level"] == "INFO"
        assert line["message"] == f"Expense added: expense_id={expense_id}, by_staff_id={admin.pk}"
        assert "Secret landlord" not in json_logs.text()
        assert "43210" not in json_logs.text()


class TestRefusedExpenseInput:
    @pytest.fixture(autouse=True)
    def admin(self, abc: ConsultancyContext) -> Staff:
        return abc.log_in_as(Role.ADMIN)

    @pytest.mark.parametrize(
        "amount", [0, -5, 100_000_001, 12.5, "6500", True, None, [6500], {"n": 1}]
    )
    def test_bad_amounts(self, abc: ConsultancyContext, amount: object) -> None:
        response = add(abc, {**GOOD_BODY, "amount": amount})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"amount": [AMOUNT_MESSAGE]}
        assert stored(abc) == []

    @pytest.mark.parametrize(
        "spent_on",
        ["1999-12-31", "2025-02-30", "12/10/2025", "2025-10-1", "", None, 20251012, ["2025-10-12"]],
    )
    def test_bad_dates(self, abc: ConsultancyContext, spent_on: object) -> None:
        response = add(abc, {**GOOD_BODY, "spent_on": spent_on})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"spent_on": [DATE_MESSAGE]}

    def test_a_future_date_is_refused(self, abc: ConsultancyContext) -> None:
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()

        response = add(abc, {**GOOD_BODY, "spent_on": tomorrow})

        assert response.json() == {"spent_on": [DATE_MESSAGE]}

    @pytest.mark.parametrize(
        "category", ["Office rent", "rent", "", None, 1, ["other"], "<script>alert(1)</script>"]
    )
    def test_bad_categories(self, abc: ConsultancyContext, category: object) -> None:
        response = add(abc, {**GOOD_BODY, "category": category})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"category": [CATEGORY_MESSAGE]}

    @pytest.mark.parametrize("method", ["Cash", "bitcoin", "", None, 1, ["cash"]])
    def test_bad_methods(self, abc: ConsultancyContext, method: object) -> None:
        response = add(abc, {**GOOD_BODY, "method": method})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"method": [METHOD_MESSAGE]}

    @pytest.mark.parametrize("paid_to", ["", "   ", None, 12, True, ["WorldLink"], {"a": "b"}])
    def test_bad_paid_to(self, abc: ConsultancyContext, paid_to: object) -> None:
        response = add(abc, {**GOOD_BODY, "paid_to": paid_to})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"paid_to": [PAID_TO_MESSAGE]}

    def test_an_over_long_paid_to(self, abc: ConsultancyContext) -> None:
        response = add(abc, {**GOOD_BODY, "paid_to": "x" * 201})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"paid_to": ["Use at most 200 characters."]}

    def test_the_longest_paid_to_is_kept(self, abc: ConsultancyContext) -> None:
        assert add(abc, {**GOOD_BODY, "paid_to": "x" * 200}).status_code == 201

    def test_control_characters_are_refused(self, abc: ConsultancyContext) -> None:
        response = add(abc, {**GOOD_BODY, "paid_to": "Nepal\nElectricity"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc) == []

    def test_missing_fields(self, abc: ConsultancyContext) -> None:
        response = add(abc, {})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "spent_on": [DATE_MESSAGE],
            "category": [CATEGORY_MESSAGE],
            "paid_to": [PAID_TO_MESSAGE],
            "amount": [AMOUNT_MESSAGE],
            "method": [METHOD_MESSAGE],
        }

    @pytest.mark.parametrize("field", ["id", "recorded_by", "recorded_at", "student", "total"])
    def test_unknown_fields_are_refused_and_never_stored(
        self, abc: ConsultancyContext, field: str
    ) -> None:
        response = add(abc, {**GOOD_BODY, field: 1})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored(abc) == []

    @pytest.mark.parametrize("body", [[GOOD_BODY], "rent", 1])
    def test_a_body_that_is_not_an_object(self, abc: ConsultancyContext, body: object) -> None:
        response = add(abc, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc) == []

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        response = abc.client.post(LIST_URL, {**GOOD_BODY, "amount": "1"})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_text_is_kept_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        response = add(abc, {**GOOD_BODY, "paid_to": text})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["paid_to"] == text
        assert stored(abc) == [("electricity", text, 6500)]


class TestChangeExpense:
    def test_an_admin_changes_every_field(self, abc: ConsultancyContext) -> None:
        first = named_staff(abc, "Anita Admin", Role.ADMIN)
        expense = make_expense(abc, first)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(expense_url(expense), GOOD_BODY, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            **GOOD_BODY,
            "id": expense.pk,
            "recorded_by": {"name": "Anita Admin"},
        }
        assert stored(abc) == [("electricity", "Nepal Electricity Authority", 6500)]

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_other_roles_are_refused(self, abc: ConsultancyContext, role: Role) -> None:
        expense = make_expense(abc, named_staff(abc, "Anita Admin", Role.ADMIN))
        abc.log_in_as(role)

        response = abc.client.put(expense_url(expense), GOOD_BODY, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN
        assert stored(abc) == [("office_rent", "Office landlord", 85000)]

    def test_refused_input_changes_nothing(self, abc: ConsultancyContext) -> None:
        expense = make_expense(abc, abc.log_in_as(Role.ADMIN))

        response = abc.client.put(expense_url(expense), {**GOOD_BODY, "amount": -1}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc) == [("office_rent", "Office landlord", 85000)]

    def test_every_field_is_needed(self, abc: ConsultancyContext) -> None:
        expense = make_expense(abc, abc.log_in_as(Role.ADMIN))

        response = abc.client.put(expense_url(expense), {"amount": 5}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc) == [("office_rent", "Office landlord", 85000)]

    def test_an_unknown_expense_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(expense_url(999_999), GOOD_BODY, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "post", "patch"])
    def test_only_put_and_delete_on_one_expense(self, abc: ConsultancyContext, method: str) -> None:
        expense = make_expense(abc, abc.log_in_as(Role.ADMIN))

        response = getattr(abc.client, method)(expense_url(expense), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logs_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        expense = make_expense(abc, admin)
        json_logs.clear()

        abc.client.put(expense_url(expense), GOOD_BODY, format="json")

        [line] = json_logs.with_message("Expense changed")
        assert (
            line["message"] == f"Expense changed: expense_id={expense.pk}, by_staff_id={admin.pk}"
        )
        assert "Nepal Electricity" not in json_logs.text()


class TestDeleteExpense:
    def test_an_admin_deletes_an_expense(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        expense = make_expense(abc, admin)
        kept = make_expense(abc, admin, paid_to="WorldLink", amount=3500, category="internet")

        response = abc.client.delete(expense_url(expense))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert stored(abc) == [("internet", "WorldLink", 3500)]
        assert kept.pk != expense.pk

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_other_roles_are_refused(self, abc: ConsultancyContext, role: Role) -> None:
        expense = make_expense(abc, named_staff(abc, "Anita Admin", Role.ADMIN))
        abc.log_in_as(role)

        response = abc.client.delete(expense_url(expense))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert stored(abc) == [("office_rent", "Office landlord", 85000)]

    def test_an_unknown_expense_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.delete(expense_url(999_999)).status_code == 404

    def test_logs_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        expense = make_expense(abc, admin)
        json_logs.clear()

        abc.client.delete(expense_url(expense))

        [line] = json_logs.with_message("Expense deleted")
        assert (
            line["message"] == f"Expense deleted: expense_id={expense.pk}, by_staff_id={admin.pk}"
        )


class TestAccountsModuleOff:
    @pytest.fixture
    def office(self, make_consultancy: ConsultancyFactory) -> ConsultancyContext:
        return make_consultancy("XYZ Overseas", "xyzoverseas")

    @pytest.mark.parametrize("role", list(Role))
    def test_every_expense_endpoint_is_not_found_for_every_role(
        self, office: ConsultancyContext, role: Role
    ) -> None:
        expense = make_expense(office, named_staff(office, "Anita Admin", Role.ADMIN))
        office.log_in_as(role)

        answers = [
            office.client.get(LIST_URL),
            office.client.post(LIST_URL, GOOD_BODY, format="json"),
            office.client.put(expense_url(expense), GOOD_BODY, format="json"),
            office.client.delete(expense_url(expense)),
        ]

        assert [answer.status_code for answer in answers] == [404] * 4
        assert stored(office) == [("office_rent", "Office landlord", 85000)]

    def test_switching_back_on_brings_the_expenses_back(
        self, office: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        make_expense(office, office.log_in_as(Role.ADMIN), amount=85000)
        assert office.client.get(LIST_URL).status_code == status.HTTP_404_NOT_FOUND

        switch_modules(office.consultancy, {Module.ACCOUNTS})

        assert office.client.get(LIST_URL, {"month": "2025-10"}).json()["total"] == 85000
