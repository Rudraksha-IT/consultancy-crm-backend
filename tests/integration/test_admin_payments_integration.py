"""Admin Payments: the list with filters, editing and deleting (spec 74-76; issue 19)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from fee_types.models import FeeType
from history.models import HistoryEntry, HistoryKind
from modules.services import switch_modules
from modules.types import Module
from payments.models import Payment
from payments.serializers import FilterDateField
from staff.models import Role, Staff, StaffStatus
from students.models import Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_payments_integration import make_payment
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

LIST_URL = "/api/payments/"
ONLY_ADMIN_SEES = {"detail": "Only an Admin can see other Payments."}
ONLY_ADMIN_CHANGES = {"detail": "Only an Admin can change or delete a Payment."}
NOT_ADMINS = (Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER)


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with Accounts on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.ACCOUNTS})
    return office


def payment_url(payment: Payment | int) -> str:
    """One Payment."""
    pk = payment if isinstance(payment, int) else payment.pk
    return f"/api/payments/{pk}/"


def days_ago(days: int) -> date:
    """A day in the past, in the office's time zone."""
    return timezone.localdate() - timedelta(days=days)


def listed(office: ConsultancyContext, **query: object) -> _MonkeyPatchedResponse:
    """GET the Payments list with this query string."""
    return office.client.get(LIST_URL, {key: str(value) for key, value in query.items()})


def ids(response: _MonkeyPatchedResponse) -> list[int]:
    """The listed Payments' ids, in order."""
    return [row["id"] for row in response.json()["results"]]


def type_id(office: ConsultancyContext, name: str) -> int:
    """The id of the office's Fee Type with this name."""
    with tenant_context(office.consultancy):
        return FeeType.objects.get(name=name).pk


def edit_body(office: ConsultancyContext, **changes: object) -> dict[str, object]:
    """A full Payment the server accepts as an edit; `changes` override it.

    A `fee_type` given by name is sent as its id.
    """
    body: dict[str, object] = {
        "amount": 30000,
        "paid_on": days_ago(1).isoformat(),
        "method": "khalti",
        "fee_type": "Visa fee",
        **changes,
    }
    if isinstance(body["fee_type"], str):
        body["fee_type"] = type_id(office, body["fee_type"])
    return body


def edit(office: ConsultancyContext, payment: Payment, **changes: object) -> _MonkeyPatchedResponse:
    """PUT the Payment's new details."""
    return office.client.put(payment_url(payment), edit_body(office, **changes), format="json")


def stored(office: ConsultancyContext, payment: Payment) -> Payment | None:
    """The Payment as stored now, or None once deleted."""
    with tenant_context(office.consultancy):
        return Payment.objects.filter(pk=payment.pk).first()


def entries(office: ConsultancyContext, student: Student, kind: HistoryKind) -> list[HistoryEntry]:
    """The Student's History entries of one kind, oldest first."""
    with tenant_context(office.consultancy):
        return list(HistoryEntry.objects.filter(student=student, kind=kind).order_by("pk"))


def desk_payment(office: ConsultancyContext, **fields: object) -> tuple[Staff, Student, Payment]:
    """A Payment recorded by Front Desk from a new Student."""
    desk = named_staff(office, "Sunita Shrestha", Role.FRONT_DESK)
    student = make_student(office, name="Aarav Sharma", phone="9841234567")
    return desk, student, make_payment(office, student, desk, **fields)


class TestList:
    def test_an_admin_sees_every_payment_newest_first_with_the_total(
        self, abc: ConsultancyContext
    ) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        aarav = make_student(abc, name="Aarav Sharma", phone="9841234567")
        sita = make_student(abc, name="Sita Karki", phone="9841000002")
        older = make_payment(abc, aarav, desk, amount=18000, paid_on=days_ago(5))
        newer = make_payment(abc, sita, desk, amount=40000, paid_on=days_ago(1), method="esewa")
        abc.log_in_as(Role.ADMIN)

        response = listed(abc)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert {key: body[key] for key in ("count", "page", "page_size", "total")} == {
            "count": 2,
            "page": 1,
            "page_size": 25,
            "total": 58000,
        }
        assert ids(response) == [newer.pk, older.pk]
        assert body["results"][0] == {
            "id": newer.pk,
            "receipt_number": f"{newer.pk:06d}",
            "student": {"id": sita.pk, "name": "Sita Karki", "phone": "9841000002"},
            "amount": 40000,
            "paid_on": days_ago(1).isoformat(),
            "method": "esewa",
            "fee_type": {"id": type_id(abc, "PTE class"), "name": "PTE class"},
            "note": "",
            "recorded_by": {"name": "Sunita Shrestha"},
            "recorded_at": newer.recorded_at.isoformat(),
        }

    def test_an_empty_list(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        body = listed(abc).json()

        assert (body["count"], body["total"], body["results"]) == (0, 0, [])

    def test_from_and_to_dates_include_both_days(self, abc: ConsultancyContext) -> None:
        _, student, _ = desk_payment(abc, paid_on=days_ago(10))
        desk = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        first = make_payment(abc, student, desk, paid_on=days_ago(7))
        last = make_payment(abc, student, desk, paid_on=days_ago(3))
        make_payment(abc, student, desk, paid_on=days_ago(1))
        abc.log_in_as(Role.ADMIN)

        response = listed(abc, **{"from": days_ago(7), "to": days_ago(3)})

        assert ids(response) == [last.pk, first.pk]
        assert response.json()["total"] == 36000

    def test_only_from_or_only_to(self, abc: ConsultancyContext) -> None:
        desk, student, old = desk_payment(abc, paid_on=days_ago(30))
        new = make_payment(abc, student, desk, paid_on=days_ago(1))
        abc.log_in_as(Role.ADMIN)

        assert ids(listed(abc, **{"from": days_ago(2)})) == [new.pk]
        assert ids(listed(abc, to=days_ago(2))) == [old.pk]

    def test_by_student(self, abc: ConsultancyContext) -> None:
        desk, aarav, theirs = desk_payment(abc)
        sita = make_student(abc, name="Sita Karki")
        make_payment(abc, sita, desk)
        abc.log_in_as(Role.ADMIN)

        assert ids(listed(abc, student=aarav.pk)) == [theirs.pk]
        assert ids(listed(abc, student=999_999)) == []

    @pytest.mark.parametrize("query", ["aarav", "SHARMA", "9841234567", "984-123"])
    def test_search_by_student_name_or_phone(self, abc: ConsultancyContext, query: str) -> None:
        desk, _, theirs = desk_payment(abc)
        make_payment(abc, make_student(abc, name="Sita Karki", phone="9800000000"), desk)
        abc.log_in_as(Role.ADMIN)

        assert ids(listed(abc, q=query)) == [theirs.pk]

    def test_a_search_of_only_dashes_looks_at_names(self, abc: ConsultancyContext) -> None:
        desk, _, _ = desk_payment(abc)
        hyphenated = make_student(abc, name="Ram-Bahadur Thapa", phone="9800000000")
        theirs = make_payment(abc, hyphenated, desk)
        abc.log_in_as(Role.ADMIN)

        assert ids(listed(abc, q="-")) == [theirs.pk]

    def test_search_by_receipt_number(self, abc: ConsultancyContext) -> None:
        desk, student, first = desk_payment(abc)
        make_payment(abc, student, desk)
        abc.log_in_as(Role.ADMIN)

        assert ids(listed(abc, q=first.receipt_number)) == [first.pk]

    def test_by_fee_type(self, abc: ConsultancyContext) -> None:
        desk, student, visa = desk_payment(abc, fee_type="Visa fee")
        make_payment(abc, student, desk, fee_type="PTE class")
        abc.log_in_as(Role.ADMIN)

        response = listed(abc, fee_type=type_id(abc, "Visa fee"))

        assert ids(response) == [visa.pk]
        assert response.json()["total"] == visa.amount

    def test_a_hidden_fee_type_still_filters(self, abc: ConsultancyContext) -> None:
        _, _, pte = desk_payment(abc, fee_type="PTE class")
        with tenant_context(abc.consultancy):
            FeeType.objects.filter(name="PTE class").update(hidden=True)
        abc.log_in_as(Role.ADMIN)

        assert ids(listed(abc, fee_type=type_id(abc, "PTE class"))) == [pte.pk]

    def test_an_unknown_fee_type_finds_nothing(self, abc: ConsultancyContext) -> None:
        desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        assert listed(abc, fee_type=999_999).json()["count"] == 0

    @pytest.mark.parametrize("value", ["visa", "1.5", "0", "-1", str(2**31)])
    def test_a_fee_type_that_is_not_an_id_is_refused(
        self, abc: ConsultancyContext, value: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = listed(abc, fee_type=value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["fee_type"]

    def test_filters_combine(self, abc: ConsultancyContext) -> None:
        desk, aarav, _ = desk_payment(abc, fee_type="Visa fee", paid_on=days_ago(20))
        wanted = make_payment(abc, aarav, desk, fee_type="Visa fee", paid_on=days_ago(2))
        make_payment(abc, aarav, desk, fee_type="PTE class", paid_on=days_ago(2))
        abc.log_in_as(Role.ADMIN)
        visa = type_id(abc, "Visa fee")

        response = listed(abc, student=aarav.pk, fee_type=visa, **{"from": days_ago(5)})

        assert ids(response) == [wanted.pk]

    def test_pages_of_25(self, abc: ConsultancyContext) -> None:
        desk, student, _ = desk_payment(abc)
        for _ in range(25):
            make_payment(abc, student, desk, amount=1000)
        abc.log_in_as(Role.ADMIN)

        second = listed(abc, page=2).json()

        assert (second["count"], second["page"], len(second["results"])) == (26, 2, 1)
        assert second["total"] == 18000 + 25 * 1000
        assert listed(abc, page=3).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("query", INJECTION_STRINGS)
    def test_injection_shaped_searches_are_plain_text(
        self, abc: ConsultancyContext, query: str
    ) -> None:
        desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = listed(abc, q=query)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 0
        with tenant_context(abc.consultancy):
            assert Payment.objects.count() == 1

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_sees_the_list(self, abc: ConsultancyContext, role: Role) -> None:
        desk_payment(abc, amount=987654)
        abc.log_in_as(role)

        response = listed(abc)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN_SEES
        assert "987654" not in response.content.decode()

    def test_a_deactivated_admin_is_refused(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        response = listed(abc)

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.new_client().get(LIST_URL).status_code == status.HTTP_401_UNAUTHORIZED


class TestRefusedListQueries:
    @pytest.mark.parametrize("value", ["2026-02-30", "02/11/2026", "today", "20261102", " "])
    @pytest.mark.parametrize("name", ["from", "to"])
    def test_a_bad_date_is_refused(self, abc: ConsultancyContext, name: str, value: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = listed(abc, **{name: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {name: ["Enter a date as YYYY-MM-DD."]}

    def test_to_before_from_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = listed(abc, **{"from": days_ago(1), "to": days_ago(2)})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"to": ["Choose an end date on or after the start date."]}

    @pytest.mark.parametrize("value", ["abc", "0", "-1", "1.5", "99999999999"])
    @pytest.mark.parametrize("name", ["student", "page"])
    def test_a_bad_number_is_refused(self, abc: ConsultancyContext, name: str, value: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = listed(abc, **{name: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == [name]

    @pytest.mark.parametrize(("name", "longest"), [("q", 100)])
    def test_over_long_text_is_refused(
        self, abc: ConsultancyContext, name: str, longest: int
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        assert listed(abc, **{name: "x" * longest}).status_code == status.HTTP_200_OK
        refused = listed(abc, **{name: "x" * (longest + 1)})

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert list(refused.json()) == [name]

    @pytest.mark.parametrize("name", ["from", "to", "student", "q", "fee_type", "page"])
    def test_an_empty_filter_means_any(self, abc: ConsultancyContext, name: str) -> None:
        desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = listed(abc, **{name: ""})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1

    def test_an_unknown_filter_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = listed(abc, recorded_by=1)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}


class TestEdit:
    def test_an_admin_changes_a_payment_and_gets_its_receipt(self, abc: ConsultancyContext) -> None:
        _, student, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = edit(abc, payment)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["id"] == payment.pk
        assert body["student"] == {"id": student.pk, "name": "Aarav Sharma", "phone": "9841234567"}
        assert (body["amount"], body["paid_on"], body["method"], body["fee_type"]["name"]) == (
            30000,
            days_ago(1).isoformat(),
            "khalti",
            "Visa fee",
        )
        # Who received it and when it was recorded stay as they were.
        assert body["recorded_by"] == {"name": "Sunita Shrestha"}
        assert body["recorded_at"] == payment.recorded_at.isoformat()
        changed = stored(abc, payment)
        assert changed is not None
        assert (changed.amount, changed.method, changed.recorded_by_id) == (
            30000,
            "khalti",
            payment.recorded_by_id,
        )

    def test_the_history_says_what_changed(self, abc: ConsultancyContext) -> None:
        _, student, payment = desk_payment(
            abc, amount=25000, paid_on=date(2026, 10, 30), method="esewa", fee_type="Visa fee"
        )
        admin = named_staff(abc, "Maya Admin", Role.ADMIN)
        log_in(abc, admin)

        edit(abc, payment, amount=125000, paid_on="2026-10-29", method="esewa", fee_type="Visa fee")

        [entry] = entries(abc, student, HistoryKind.PAYMENT_EDITED)
        assert entry.by_staff_id == admin.pk
        assert entry.by_name == "Maya Admin"
        assert entry.before == "Amount NPR 25,000; date 30 Oct 2026"
        assert entry.after == "Amount NPR 1,25,000; date 29 Oct 2026"
        assert entry.detail == f"Receipt {payment.receipt_number}"

    def test_method_and_fee_type_changes_are_named(self, abc: ConsultancyContext) -> None:
        _, student, payment = desk_payment(abc, method="cash", fee_type="PTE class")
        abc.log_in_as(Role.ADMIN)

        edit(
            abc,
            payment,
            amount=payment.amount,
            paid_on=payment.paid_on.isoformat(),
            method="bank_transfer",
            fee_type="IELTS class",
            note="evening batch",
        )

        [entry] = entries(abc, student, HistoryKind.PAYMENT_EDITED)
        assert entry.before == "Method Cash; for PTE class"
        assert entry.after == "Method Bank transfer; for IELTS class (evening batch)"

    def test_saving_without_a_change_writes_no_history(self, abc: ConsultancyContext) -> None:
        _, student, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = edit(
            abc,
            payment,
            amount=payment.amount,
            paid_on=payment.paid_on.isoformat(),
            method=payment.method,
            fee_type="PTE class",
        )

        assert response.status_code == status.HTTP_200_OK
        assert entries(abc, student, HistoryKind.PAYMENT_EDITED) == []

    def test_the_history_api_shows_the_edit(self, abc: ConsultancyContext) -> None:
        _, student, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)
        edit(abc, payment)

        [entry, *_] = abc.client.get(f"/api/students/{student.pk}/history/").json()["results"]

        assert entry["kind"] == "payment_edited"

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        _, student, payment = desk_payment(abc)
        admin = abc.log_in_as(Role.ADMIN)

        edit(abc, payment, note="Secret note text")

        entry = json_logs.only(
            f"Payment changed: payment_id={payment.pk}, student_id={student.pk}, "
            f"by_staff_id={admin.pk}"
        )
        assert entry["level"] == "INFO"
        assert "Secret note" not in json_logs.text()
        assert "Aarav" not in json_logs.text()

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_may_edit(self, abc: ConsultancyContext, role: Role) -> None:
        _, student, payment = desk_payment(abc)
        abc.log_in_as(role)

        response = edit(abc, payment)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN_CHANGES
        changed = stored(abc, payment)
        assert changed is not None
        assert changed.amount == payment.amount
        assert entries(abc, student, HistoryKind.PAYMENT_EDITED) == []

    def test_not_even_the_recorder_within_the_hour(self, abc: ConsultancyContext) -> None:
        desk, _, payment = desk_payment(abc)
        log_in(abc, desk)

        assert edit(abc, payment).status_code == status.HTTP_403_FORBIDDEN

    def test_an_unknown_payment_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(payment_url(999_999), edit_body(abc), format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        _, _, payment = desk_payment(abc)

        response = abc.new_client().put(payment_url(payment), edit_body(abc), format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestRefusedEdits:
    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("amount", 0, "Enter a whole amount from NPR 1 to NPR 10,00,00,000."),
            ("amount", "25000", "Enter a whole amount from NPR 1 to NPR 10,00,00,000."),
            ("amount", 25000.5, "Enter a whole amount from NPR 1 to NPR 10,00,00,000."),
            ("amount", True, "Enter a whole amount from NPR 1 to NPR 10,00,00,000."),
            ("paid_on", "2026-02-30", "Enter a date from 1 Jan 2000 to today."),
            ("paid_on", "1999-12-31", "Enter a date from 1 Jan 2000 to today."),
            ("method", "paypal", "Choose how they paid."),
            ("method", None, "Choose how they paid."),
            ("fee_type", None, "Choose a Fee Type."),
            ("fee_type", 3.5, "Choose a Fee Type."),
            ("note", "x" * 201, "Use at most 200 characters."),
        ],
    )
    def test_bad_values_are_refused_and_nothing_changes(
        self, abc: ConsultancyContext, field: str, value: object, message: str
    ) -> None:
        _, student, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = edit(abc, payment, **{field: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {field: [message]}
        unchanged = stored(abc, payment)
        assert unchanged is not None
        assert (unchanged.amount, unchanged.fee_type_id, unchanged.note) == (
            payment.amount,
            payment.fee_type_id,
            payment.note,
        )
        assert entries(abc, student, HistoryKind.PAYMENT_EDITED) == []

    def test_a_future_date_is_refused(self, abc: ConsultancyContext) -> None:
        _, _, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = edit(abc, payment, paid_on=(timezone.localdate() + timedelta(1)).isoformat())

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("missing", ["amount", "paid_on", "method", "fee_type"])
    def test_every_field_is_required(self, abc: ConsultancyContext, missing: str) -> None:
        _, _, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)
        body = edit_body(abc)
        del body[missing]

        response = abc.client.put(payment_url(payment), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == [missing]

    @pytest.mark.parametrize("field", ["student", "recorded_by", "recorded_at", "id"])
    def test_an_unknown_field_is_refused(self, abc: ConsultancyContext, field: str) -> None:
        _, student, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(payment_url(payment), {**edit_body(abc), field: 1}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        unchanged = stored(abc, payment)
        assert unchanged is not None
        assert unchanged.student_id == student.pk
        assert unchanged.amount == payment.amount

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        _, _, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(payment_url(payment), "amount=1", content_type="text/plain")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_a_body_that_isnt_an_object_is_refused(self, abc: ConsultancyContext) -> None:
        _, _, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(payment_url(payment), [edit_body(abc)], format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("note", ["fee\nnext", "fee\x00"])
    def test_control_characters_are_refused(self, abc: ConsultancyContext, note: str) -> None:
        _, _, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = edit(abc, payment, note=note)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["note"]

    @pytest.mark.parametrize("note", INJECTION_STRINGS)
    def test_injection_shaped_notes_are_kept_as_plain_text(
        self, abc: ConsultancyContext, note: str
    ) -> None:
        _, _, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = edit(abc, payment, note=note)

        assert response.json()["note"] == note.strip()
        changed = stored(abc, payment)
        assert changed is not None
        assert changed.note == note.strip()


class TestDelete:
    def test_an_admin_deletes_a_payment_and_history_keeps_its_details(
        self, abc: ConsultancyContext
    ) -> None:
        _, student, payment = desk_payment(
            abc, amount=25000, paid_on=date(2026, 10, 30), method="esewa", fee_type="Visa fee"
        )
        admin = named_staff(abc, "Maya Admin", Role.ADMIN)
        log_in(abc, admin)

        response = abc.client.delete(payment_url(payment))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert stored(abc, payment) is None
        [entry] = entries(abc, student, HistoryKind.PAYMENT_DELETED)
        assert entry.by_name == "Maya Admin"
        assert entry.before == "NPR 25,000, Visa fee, eSewa, 30 Oct 2026"
        assert entry.after == ""
        assert entry.detail == f"Receipt {payment.receipt_number}"
        # The Payment's earlier History stays.
        assert abc.client.get(payment_url(payment)).status_code == status.HTTP_404_NOT_FOUND

    def test_a_deleted_payment_leaves_the_list_and_total(self, abc: ConsultancyContext) -> None:
        desk, student, gone = desk_payment(abc, amount=5000)
        kept = make_payment(abc, student, desk, amount=7000)
        abc.log_in_as(Role.ADMIN)

        abc.client.delete(payment_url(gone))

        response = listed(abc)
        assert ids(response) == [kept.pk]
        assert response.json()["total"] == 7000

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        _, student, payment = desk_payment(abc)
        admin = abc.log_in_as(Role.ADMIN)

        abc.client.delete(payment_url(payment))

        json_logs.only(
            f"Payment deleted: payment_id={payment.pk}, student_id={student.pk}, "
            f"by_staff_id={admin.pk}"
        )
        assert "Aarav" not in json_logs.text()

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_only_an_admin_may_delete(self, abc: ConsultancyContext, role: Role) -> None:
        _, student, payment = desk_payment(abc)
        abc.log_in_as(role)

        response = abc.client.delete(payment_url(payment))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN_CHANGES
        assert stored(abc, payment) is not None
        assert entries(abc, student, HistoryKind.PAYMENT_DELETED) == []

    def test_an_unknown_payment_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.delete(payment_url(999_999)).status_code == 404

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        _, _, payment = desk_payment(abc)

        response = abc.new_client().delete(payment_url(payment))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert stored(abc, payment) is not None


class TestAccountsModule:
    def test_with_accounts_off_list_edit_and_delete_are_not_found(
        self, abc: ConsultancyContext
    ) -> None:
        _, _, payment = desk_payment(abc)
        switch_modules(abc.consultancy, set())
        abc.log_in_as(Role.ADMIN)

        responses = [
            listed(abc),
            edit(abc, payment),
            abc.client.delete(payment_url(payment)),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 3
        unchanged = stored(abc, payment)
        assert unchanged is not None
        assert unchanged.amount == payment.amount

    @pytest.mark.parametrize("role", NOT_ADMINS)
    def test_off_is_not_found_before_the_role_is_checked(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        _, _, payment = desk_payment(abc)
        switch_modules(abc.consultancy, set())
        abc.log_in_as(role)

        assert listed(abc).status_code == status.HTTP_404_NOT_FOUND
        assert edit(abc, payment).status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_gets_401_even_when_off(self, abc: ConsultancyContext) -> None:
        switch_modules(abc.consultancy, set())

        assert abc.new_client().get(LIST_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_recording_and_receipts_stay_when_off(self, abc: ConsultancyContext) -> None:
        switch_modules(abc.consultancy, set())
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        made = abc.client.post(
            f"/api/students/{student.pk}/payments/",
            {
                "paid_on": timezone.localdate().isoformat(),
                "method": "cash",
                "lines": [{"amount": 1000, "fee_type": type_id(abc, "PTE class")}],
            },
            format="json",
        )
        receipt = abc.client.get(payment_url(made.json()["id"]))

        assert (made.status_code, receipt.status_code) == (201, 200)

    def test_switched_on_again_the_payments_are_all_there(self, abc: ConsultancyContext) -> None:
        _, _, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)
        switch_modules(abc.consultancy, set())
        switch_modules(abc.consultancy, {Module.ACCOUNTS})

        assert ids(listed(abc)) == [payment.pk]


class TestFields:
    def test_a_filter_date_shows_as_text(self) -> None:
        assert FilterDateField().to_representation(date(2026, 11, 2)) == "2026-11-02"


class TestMethods:
    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_the_list_answers_only_get(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(LIST_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("method", ["post", "patch"])
    def test_one_payment_answers_get_put_and_delete_only(
        self, abc: ConsultancyContext, method: str
    ) -> None:
        _, _, payment = desk_payment(abc)
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(payment_url(payment), edit_body(abc), format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        unchanged = stored(abc, payment)
        assert unchanged is not None
        assert unchanged.amount == payment.amount

    def test_not_on_the_platform_host(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        _, _, payment = desk_payment(abc)
        platform_site.log_in()

        responses = [
            platform_site.client.get(LIST_URL),
            platform_site.client.put(payment_url(payment), edit_body(abc), format="json"),
            platform_site.client.delete(payment_url(payment)),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 3
        assert stored(abc, payment) is not None
