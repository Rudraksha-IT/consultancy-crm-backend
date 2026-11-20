"""Fees by type and one Fee Type's Students (spec 79-93; ticket 08)."""

from __future__ import annotations

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from fee_types.models import FeeType
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff, StaffStatus
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, PlatformContext
from tests.integration.test_fees_integration import LIST_URL, ONLY_ADMIN_SEES, make_fee
from tests.integration.test_payments_integration import make_payment
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    make_student,
)
from tests.support_access import open_as_platform_admin

URL = "/api/accounts/fees-by-type/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with Accounts on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.ACCOUNTS})
    return office


def type_id(office: ConsultancyContext, name: str) -> int:
    """The id of the office's Fee Type with this name."""
    with tenant_context(office.consultancy):
        return FeeType.objects.get(name=name).pk


def type_url(office: ConsultancyContext, name: str) -> str:
    """One Fee Type's Students."""
    return f"{URL}{type_id(office, name)}/"


def lost(
    office: ConsultancyContext, name: str, reason: str = "Went with another office"
) -> Student:
    """A Student whose Stage is Lost."""
    return make_student(office, stage=Stage.LOST, name=name, lost_reason=reason)


def set_stage(office: ConsultancyContext, student: Student, stage: Stage) -> None:
    """Move the Student to another Stage directly."""
    with tenant_context(office.consultancy):
        Student.objects.filter(pk=student.pk).update(stage=stage)


def money(office: ConsultancyContext, admin: Staff) -> dict[str, Student]:
    """Aarav part paid, Sita paid ahead on Visa and short on Application, Hari Lost."""
    aarav = make_student(office, name="Aarav Sharma", phone="9841000001")
    sita = make_student(office, name="Sita Karki", phone="9841000002")
    hari = lost(office, "Hari Lama")
    make_fee(office, aarav, admin, fee_type="Visa fee", amount=40000)
    make_payment(office, aarav, admin, fee_type="Visa fee", amount=10000)
    make_fee(office, sita, admin, fee_type="Visa fee", amount=40000)
    make_payment(office, sita, admin, fee_type="Visa fee", amount=41000)
    make_fee(office, sita, admin, fee_type="Application fee", amount=25000)
    make_payment(office, sita, admin, fee_type="Application fee", amount=24000)
    make_payment(office, sita, admin, fee_type="IELTS class", amount=5000)
    make_fee(office, hari, admin, fee_type="Visa fee", amount=40000)
    make_payment(office, hari, admin, fee_type="Visa fee", amount=15000)
    return {"aarav": aarav, "sita": sita, "hari": hari}


def ref(office: ConsultancyContext, name: str, *, hidden: bool = False) -> dict[str, object]:
    """A Fee Type as Fees by type names it."""
    return {"id": type_id(office, name), "name": name, "hidden": hidden}


class TestTotals:
    def test_the_totals_and_one_row_per_fee_type_in_the_lists_order(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        money(abc, admin)

        response = abc.client.get(URL)

        assert response.status_code == status.HTTP_200_OK
        # Received: every Payment (Hari's too). Remaining: Aarav 30,000; Sita 0 (paid ahead
        # on Visa covers Application, and IELTS was paid with no Fee); Hari is Lost.
        assert response.json() == {
            "totals": {
                "to_collect": 125000,
                "received": 95000,
                "remaining": 30000,
                "lost_not_counted": 25000,
                "owing": 1,
            },
            "fee_types": [
                {"fee_type": ref(abc, "Application fee"), "fees": 25000, "received": 24000},
                {"fee_type": ref(abc, "Visa fee"), "fees": 80000, "received": 66000},
                {"fee_type": ref(abc, "IELTS class"), "fees": 0, "received": 5000},
            ],
        }

    def test_a_hidden_fee_type_still_shows_its_money(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        make_fee(abc, make_student(abc), admin, fee_type="PTE class", amount=9000)
        with tenant_context(abc.consultancy):
            FeeType.objects.filter(name="PTE class").update(hidden=True)

        rows = abc.client.get(URL).json()["fee_types"]

        assert rows == [
            {"fee_type": ref(abc, "PTE class", hidden=True), "fees": 9000, "received": 0}
        ]

    def test_nothing_yet(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(URL).json() == {
            "totals": {
                "to_collect": 0,
                "received": 0,
                "remaining": 0,
                "lost_not_counted": 0,
                "owing": 0,
            },
            "fee_types": [],
        }

    def test_a_student_who_comes_back_is_counted_at_once(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        hari = lost(abc, "Hari Lama")
        make_fee(abc, hari, admin, amount=40000)
        assert abc.client.get(URL).json()["totals"]["remaining"] == 0

        set_stage(abc, hari, Stage.COUNSELLING)

        assert abc.client.get(URL).json()["totals"]["remaining"] == 40000

    def test_the_home_figure_agrees(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        found = money(abc, admin)

        totals = abc.client.get(URL).json()["totals"]
        home = abc.client.get(LIST_URL, {"leave_out": "lost"}).json()

        assert home["totals"]["still_to_collect"] == totals["remaining"] == 30000
        assert home["totals"]["owing"] == totals["owing"] == 1
        assert [row["student"]["id"] for row in home["results"]] == [found["aarav"].pk]

    def test_the_student_fees_list_still_shows_lost_students_unless_asked(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        money(abc, admin)

        totals = abc.client.get(LIST_URL).json()["totals"]

        assert (totals["still_to_collect"], totals["owing"]) == (55000, 2)

    @pytest.mark.parametrize(
        "query", [{"page": "1"}, {"x": "1"}, {"leave_out": "lost"}, {"part": INJECTION_STRINGS[0]}]
    )
    def test_any_query_is_refused(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "<script>" not in response.content.decode()

    @pytest.mark.parametrize(
        "value", ["Lost", "students", "1", "true", *INJECTION_STRINGS, "x" * 500]
    )
    def test_the_student_fees_list_refuses_other_leave_outs(
        self, abc: ConsultancyContext, value: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST_URL, {"leave_out": value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "<script>" not in response.content.decode()


class TestFeeTypeStudents:
    def test_counted_students_most_still_owed_first(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        found = money(abc, admin)

        response = abc.client.get(type_url(abc, "Visa fee"))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "fee_type": ref(abc, "Visa fee"),
            "fees": 80000,
            "received": 66000,
            "part": "counted",
            "count": 2,
            "page": 1,
            "page_size": 25,
            "results": [
                {
                    "student": {"id": found["aarav"].pk, "name": "Aarav Sharma", "stage": "lead"},
                    "lost_reason": "",
                    "fee": 40000,
                    "paid": 10000,
                    "still_to_pay": 30000,
                },
                {
                    "student": {"id": found["sita"].pk, "name": "Sita Karki", "stage": "lead"},
                    "lost_reason": "",
                    "fee": 40000,
                    "paid": 41000,
                    "still_to_pay": 0,
                },
            ],
        }

    def test_the_lost_part(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        found = money(abc, admin)

        response = abc.client.get(type_url(abc, "Visa fee"), {"part": "lost"})

        assert response.json()["part"] == "lost"
        assert response.json()["count"] == 1
        assert response.json()["results"] == [
            {
                "student": {"id": found["hari"].pk, "name": "Hari Lama", "stage": "lost"},
                "lost_reason": "Went with another office",
                "fee": 40000,
                "paid": 15000,
                "still_to_pay": 25000,
            }
        ]

    def test_a_payment_with_no_fee_lists_the_student(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        found = money(abc, admin)

        rows = abc.client.get(type_url(abc, "IELTS class")).json()["results"]

        assert [(r["student"]["id"], r["fee"], r["paid"]) for r in rows] == [
            (found["sita"].pk, 0, 5000)
        ]

    def test_names_order_the_same_still_to_pay(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        for number, name in enumerate(("Sita Karki", "Aarav Sharma")):
            make_fee(abc, make_student(abc, name=name, phone=f"984100000{number}"), admin)

        rows = abc.client.get(type_url(abc, "Visa fee")).json()["results"]

        assert [row["student"]["name"] for row in rows] == ["Aarav Sharma", "Sita Karki"]

    def test_pages_of_25(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        for number in range(26):
            student = make_student(abc, name=f"Student {number:02d}", phone=f"98410{number:05d}")
            make_fee(abc, student, admin, amount=1000 + number)

        second = abc.client.get(type_url(abc, "Visa fee"), {"page": "2"}).json()

        assert (second["count"], second["page"]) == (26, 2)
        assert [row["fee"] for row in second["results"]] == [1000]

    def test_a_fee_type_nobody_used_has_no_students(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        answer = abc.client.get(type_url(abc, "PTE class")).json()

        assert (answer["count"], answer["results"], answer["fees"]) == (0, [], 0)

    def test_an_empty_part_means_the_counted_students(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(type_url(abc, "Visa fee"), {"part": ""}).json()["part"] == "counted"

    def test_a_page_past_the_end_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(type_url(abc, "Visa fee"), {"page": "2"})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_unknown_fee_type_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(f"{URL}999999/").status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        "query",
        [
            {"page": "0"},
            {"page": "x"},
            {"page": "1.5"},
            {"page": "<script>"},
            {"part": "Lost"},
            {"part": "counted,lost"},
            {"part": "x" * 500},
            *({"part": text} for text in INJECTION_STRINGS),
            {"sort": "name"},
        ],
    )
    def test_refused_queries(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(type_url(abc, "Visa fee"), query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "<script>" not in response.content.decode()

    @pytest.mark.parametrize("path", ["abc/", "-1/", "1.5/", "%3Cscript%3E/"])
    def test_a_fee_type_that_is_not_an_id_is_not_found(
        self, abc: ConsultancyContext, path: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(f"{URL}{path}").status_code == status.HTTP_404_NOT_FOUND

    def test_names_come_back_as_plain_text(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        make_fee(abc, make_student(abc, name=INJECTION_STRINGS[0]), admin)

        rows = abc.client.get(type_url(abc, "Visa fee")).json()["results"]

        assert rows[0]["student"]["name"] == INJECTION_STRINGS[0]


class TestWhoMaySee:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        answers = [abc.client.get(URL), abc.client.get(type_url(abc, "Visa fee"))]

        assert [answer.status_code for answer in answers] == [403, 403]
        assert all(answer.json() == ONLY_ADMIN_SEES for answer in answers)

    def test_a_deactivated_admin_is_refused(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        assert abc.client.get(URL).status_code in {401, 403}

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_get_only(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        answers = [
            getattr(abc.client, method)(url, {}, format="json")
            for url in (URL, type_url(abc, "Visa fee"))
        ]

        assert [answer.status_code for answer in answers] == [405, 405]

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        answers = [abc.client.get(URL), abc.client.get(type_url(abc, "Visa fee"))]

        assert [answer.status_code for answer in answers] == [401, 401]

    @pytest.mark.parametrize("role", list(Role))
    def test_accounts_off_is_not_found_for_every_role(
        self, make_consultancy: ConsultancyFactory, role: Role
    ) -> None:
        office = make_consultancy("XYZ Overseas", "xyzoverseas")
        office.log_in_as(role)

        answers = [office.client.get(URL), office.client.get(type_url(office, "Visa fee"))]

        assert [answer.status_code for answer in answers] == [404, 404]


class TestPlatformAdmins:
    def test_a_platform_admin_in_support_access_sees_it(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        admin = abc.add_staff(Role.ADMIN)
        make_fee(abc, make_student(abc), admin, amount=40000)
        visit = open_as_platform_admin(platform_site, abc)

        answers = [visit.client.get(URL), visit.client.get(type_url(abc, "Visa fee"))]

        assert [answer.status_code for answer in answers] == [200, 200]
        assert answers[0].json()["totals"]["remaining"] == 40000

    def test_not_on_the_platform_host(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        answers = [platform_site.client.get(URL), platform_site.client.get(f"{URL}1/")]

        assert [answer.status_code for answer in answers] == [404, 404]
