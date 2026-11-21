"""Reports: Money, money in against money out each month (issue 29).

Spec 97, 99, 99a. Admin only; shown only while both the Reports and the Accounts Modules are on.
Money in is Payments (on the day paid) plus Commission Received (on the day received); money out
is Expenses (on the day spent).
"""

from __future__ import annotations

from datetime import date

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from catalogue.models import University
from fee_types.models import FeeType
from modules.services import switch_modules
from modules.types import Module
from reports.calculations import months_back
from staff.models import Role, Staff, StaffStatus
from students.models import Student
from tests.conftest import ConsultancyContext, ConsultancyFactory
from tests.integration.test_catalogue_integration import make_university
from tests.integration.test_commission_integration import make_commission
from tests.integration.test_expenses_integration import make_expense
from tests.integration.test_payments_integration import make_payment
from tests.integration.test_reports_integration import ADMINS_ONLY, months_ago, today
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

MONEY_URL = "/api/reports/money/"
BOTH_MODULES = {Module.REPORTS, Module.ACCOUNTS}
SIX = 6
TWELVE = 12


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """An office with Reports and Accounts on, and its Admin logged in."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, BOTH_MODULES)
    office.log_in_as(Role.ADMIN)
    return office


@pytest.fixture
def admin(abc: ConsultancyContext) -> Staff:
    """The Admin who recorded the money."""
    return named_staff(abc, "Maya Admin", Role.ADMIN)


@pytest.fixture
def student(abc: ConsultancyContext) -> Student:
    """A Student who pays."""
    return make_student(abc, name="Sita Gurung")


@pytest.fixture
def university(abc: ConsultancyContext) -> University:
    """A University that pays Commission."""
    return make_university(abc)


def money(office: ConsultancyContext, months: str | None = None) -> dict[str, object]:
    """The Money report's answer."""
    response = office.client.get(MONEY_URL, {} if months is None else {"months": months})
    assert response.status_code == status.HTTP_200_OK
    body: dict[str, object] = response.json()
    return body


def month_row(body: dict[str, object], on: date) -> dict[str, object]:
    """The report's row for the month of a day."""
    rows = body["months"]
    assert isinstance(rows, list)
    return next(row for row in rows if row["month"] == f"{on.year:04d}-{on.month:02d}")


def column_total(body: dict[str, object], column: str) -> int:
    """One column added up over every month."""
    rows = body["months"]
    assert isinstance(rows, list)
    return sum(int(row[column]) for row in rows)


class TestAccess:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_an_admin_sees_the_report(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = abc.client.get(MONEY_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ADMINS_ONLY

    @pytest.mark.parametrize(
        "modules",
        [set(), {Module.REPORTS}, {Module.ACCOUNTS}, {Module.BATCHES, Module.COMMUNICATION}],
    )
    @pytest.mark.parametrize("role", list(Role))
    def test_without_both_modules_every_role_gets_not_found(
        self, abc: ConsultancyContext, modules: set[Module], role: Role
    ) -> None:
        switch_modules(abc.consultancy, modules)
        if role != Role.ADMIN:  # The fixture's Admin is already logged in.
            abc.log_in_as(role)

        response = abc.client.get(MONEY_URL)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_switching_the_modules_back_on_brings_the_money_back(
        self, abc: ConsultancyContext, admin: Staff, student: Student
    ) -> None:
        make_payment(abc, student, admin, amount=18000, paid_on=today())
        switch_modules(abc.consultancy, {Module.REPORTS})
        hidden = abc.client.get(MONEY_URL)
        switch_modules(abc.consultancy, BOTH_MODULES)

        body = money(abc)

        assert hidden.status_code == status.HTTP_404_NOT_FOUND
        assert month_row(body, today())["from_students"] == 18000

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        response = abc.new_client().get(MONEY_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        response = getattr(abc.client, method)(MONEY_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_a_deactivated_admin_is_refused(self, abc: ConsultancyContext) -> None:
        old = named_staff(abc, "Old Admin", Role.ADMIN)
        log_in(abc, old)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=old.pk).update(status=StaffStatus.DEACTIVATED)

        response = abc.client.get(MONEY_URL)

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}


class TestMoneyReport:
    def test_an_empty_office_shows_six_months_of_zeros_ending_this_month(
        self, abc: ConsultancyContext
    ) -> None:
        body = money(abc)

        assert body == {
            "months": [
                {"month": month, "from_students": 0, "from_commission": 0, "money_out": 0}
                for month in months_back(today(), SIX)
            ],
            "money_out_by_category": [],
            "money_in_by_fee_type": [],
        }

    def test_twelve_months_can_be_asked_for(self, abc: ConsultancyContext) -> None:
        body = money(abc, "12")

        rows = body["months"]
        assert isinstance(rows, list)
        assert [row["month"] for row in rows] == months_back(today(), TWELVE)

    def test_money_in_and_out_each_month(
        self, abc: ConsultancyContext, admin: Staff, student: Student, university: University
    ) -> None:
        last_month = months_ago(1)
        make_payment(abc, student, admin, amount=18000, paid_on=today())
        make_payment(abc, student, admin, amount=40000, paid_on=last_month)
        make_commission(
            abc, student, university, admin, amount=290000, status="received", received_on=today()
        )
        make_expense(abc, admin, amount=85000, spent_on=last_month, category="office_rent")
        make_expense(abc, admin, amount=2500, spent_on=today(), category="internet")

        body = money(abc)

        assert month_row(body, today()) == {
            "month": months_back(today(), 1)[0],
            "from_students": 18000,
            "from_commission": 290000,
            "money_out": 2500,
        }
        assert month_row(body, last_month) == {
            "month": months_back(today(), 2)[0],
            "from_students": 40000,
            "from_commission": 0,
            "money_out": 85000,
        }

    def test_only_received_commission_is_money_in(
        self, abc: ConsultancyContext, admin: Staff, student: Student, university: University
    ) -> None:
        make_commission(abc, student, university, admin, amount=100, status="claim_sent")
        make_commission(abc, student, university, admin, amount=200)
        make_commission(
            abc, student, university, admin, amount=300, status="received", received_on=today()
        )

        body = money(abc)

        assert month_row(body, today())["from_commission"] == 300

    def test_money_before_the_months_is_left_out(
        self, abc: ConsultancyContext, admin: Staff, student: Student, university: University
    ) -> None:
        too_early = months_ago(SIX)
        make_payment(abc, student, admin, amount=5, paid_on=too_early)
        make_commission(
            abc, student, university, admin, amount=7, status="received", received_on=too_early
        )
        make_expense(abc, admin, amount=9, spent_on=too_early)
        make_expense(abc, admin, amount=11, spent_on=months_ago(SIX - 1))

        six = money(abc)
        twelve = money(abc, "12")

        assert column_total(six, "from_students") == 0
        assert six["money_out_by_category"] == [{"category": "office_rent", "amount": 11}]
        assert month_row(twelve, too_early) == {
            "month": f"{too_early.year:04d}-{too_early.month:02d}",
            "from_students": 5,
            "from_commission": 7,
            "money_out": 9,
        }
        assert twelve["money_out_by_category"] == [{"category": "office_rent", "amount": 20}]

    def test_money_in_by_fee_type_over_the_months_largest_first(
        self, abc: ConsultancyContext, admin: Staff, student: Student
    ) -> None:
        make_payment(abc, student, admin, amount=40000, paid_on=today(), fee_type="Visa fee")
        make_payment(abc, student, admin, amount=5000, paid_on=months_ago(2), fee_type="Visa fee")
        make_payment(abc, student, admin, amount=15000, paid_on=today(), fee_type="IELTS class")
        make_payment(abc, student, admin, amount=15000, paid_on=today(), fee_type="PTE class")
        make_payment(abc, student, admin, amount=99, paid_on=months_ago(SIX), fee_type="Other")
        with tenant_context(abc.consultancy):
            ids = {
                name: str(uuid) for name, uuid in FeeType.objects.values_list("name", "public_id")
            }

        body = money(abc)

        assert body["money_in_by_fee_type"] == [
            {"fee_type": {"id": ids["Visa fee"], "name": "Visa fee"}, "amount": 45000},
            {"fee_type": {"id": ids["IELTS class"], "name": "IELTS class"}, "amount": 15000},
            {"fee_type": {"id": ids["PTE class"], "name": "PTE class"}, "amount": 15000},
        ]

    def test_a_hidden_fee_type_still_counts(
        self, abc: ConsultancyContext, admin: Staff, student: Student
    ) -> None:
        make_payment(abc, student, admin, amount=700, paid_on=today(), fee_type="PTE class")
        with tenant_context(abc.consultancy):
            FeeType.objects.filter(name="PTE class").update(hidden=True)

            pte = str(FeeType.objects.get(name="PTE class").public_id)

        rows = money(abc)["money_in_by_fee_type"]

        assert rows == [{"fee_type": {"id": pte, "name": "PTE class"}, "amount": 700}]

    def test_money_out_by_category_over_the_months_largest_first(
        self, abc: ConsultancyContext, admin: Staff
    ) -> None:
        make_expense(abc, admin, amount=2500, spent_on=today(), category="internet")
        make_expense(abc, admin, amount=2500, spent_on=months_ago(2), category="internet")
        make_expense(abc, admin, amount=85000, spent_on=months_ago(1), category="office_rent")
        make_expense(abc, admin, amount=120000, spent_on=today(), category="salaries")

        body = money(abc)

        assert body["money_out_by_category"] == [
            {"category": "salaries", "amount": 120000},
            {"category": "office_rent", "amount": 85000},
            {"category": "internet", "amount": 5000},
        ]

    @pytest.mark.parametrize(
        "query",
        [
            {"months": "7"},
            {"months": "six"},
            {"months": "6.0"},
            {"months": "6", "extra": "1"},
            {"from": "2026-01-01"},
            *({"months": text} for text in INJECTION_STRINGS),
            *({"months": "6", text: "1"} for text in INJECTION_STRINGS),
        ],
    )
    def test_a_bad_query_is_refused_without_echoing_it(
        self, abc: ConsultancyContext, query: dict[str, str]
    ) -> None:
        response = abc.client.get(MONEY_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        for text in INJECTION_STRINGS:
            assert text not in response.content.decode()
