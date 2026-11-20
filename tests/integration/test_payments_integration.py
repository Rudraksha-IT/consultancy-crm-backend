"""Recording a Payment and its receipt (spec 71-73; issue 18)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from fee_types.models import FeeType
from history.models import HistoryEntry, HistoryKind
from modules.services import switch_modules
from modules.types import Module
from payments.models import Payment
from payments.serializers import AmountField, PaidOnField
from staff.models import Role, Staff, StaffStatus
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    NOT_FOR_TEACHERS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

AMOUNT_MESSAGE = {"amount": ["Enter a whole amount from NPR 1 to NPR 10,00,00,000."]}
PAID_ON_MESSAGE = {"paid_on": ["Enter a date from 1 Jan 2000 to today."]}
METHOD_MESSAGE = {"method": ["Choose how they paid."]}
ONLY_ADMIN_SEES = {"detail": "Only an Admin can see other Payments."}
STUDENT_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def payments_url(student: Student | int) -> str:
    """Where a Student's Payments are recorded."""
    pk = student if isinstance(student, int) else student.pk
    return f"/api/students/{pk}/payments/"


def payment_url(payment: Payment | int) -> str:
    """One Payment's receipt."""
    pk = payment if isinstance(payment, int) else payment.pk
    return f"/api/payments/{pk}/"


def today() -> str:
    """Today in the office's time zone, as the API writes dates."""
    return timezone.localdate().isoformat()


def fee_type_named(office: ConsultancyContext, name: str) -> FeeType:
    """The office's Fee Type with this name."""
    with tenant_context(office.consultancy):
        return FeeType.objects.get(name=name)


# What each line of a visit's Payments has; the date and method are the visit's own.
LINE_FIELDS = frozenset({"amount", "fee_type", "note"})


def good_line(office: ConsultancyContext, **changes: object) -> dict[str, object]:
    """One line the server accepts: NPR 25,000 for the Application fee; `changes` override it."""
    return {"amount": 25000, "fee_type": fee_type_named(office, "Application fee").pk, **changes}


def good_body(office: ConsultancyContext, **changes: object) -> dict[str, object]:
    """One visit's Payment with one line; `changes` override the line or the visit's fields."""
    line = good_line(office, **{k: v for k, v in changes.items() if k in LINE_FIELDS})
    visit = {k: v for k, v in changes.items() if k not in LINE_FIELDS}
    return {"paid_on": today(), "method": "esewa", **visit, "lines": [line]}


def line_error(error: dict[str, list[str]], at: int = 0, of: int = 1) -> dict[str, object]:
    """The answer when line `at` of `of` lines is refused: its errors in its place."""
    return {"lines": [error if index == at else {} for index in range(of)]}


def pay(
    office: ConsultancyContext, student: Student | int, **changes: object
) -> _MonkeyPatchedResponse:
    """POST a Payment for the Student."""
    return office.client.post(payments_url(student), good_body(office, **changes), format="json")


def stored_payments(office: ConsultancyContext) -> list[Payment]:
    """Every stored Payment, oldest first."""
    with tenant_context(office.consultancy):
        return list(Payment.objects.order_by("pk"))


def payment_entries(office: ConsultancyContext, student: Student) -> list[HistoryEntry]:
    """The Student's Payment History entries, oldest first."""
    with tenant_context(office.consultancy):
        return list(
            HistoryEntry.objects.filter(
                student=student, kind=HistoryKind.PAYMENT_RECORDED
            ).order_by("pk")
        )


def make_payment(
    office: ConsultancyContext, student: Student, by: Staff, **fields: object
) -> Payment:
    """Store a Payment directly; `fee_type` may be a Fee Type's name (PTE class if none)."""
    details: dict[str, object] = {
        "amount": 18000,
        "paid_on": timezone.localdate(),
        "method": "cash",
        **fields,
    }
    with tenant_context(office.consultancy):
        chosen = details.get("fee_type", "PTE class")
        if isinstance(chosen, str):
            details["fee_type"] = FeeType.objects.get(name=chosen)
        return Payment.objects.create(
            student=student, recorded_by=by, recorded_by_name=by.name, **details
        )


class TestRecording:
    def test_front_desk_records_a_payment_and_gets_its_receipt(
        self, abc: ConsultancyContext
    ) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        log_in(abc, desk)
        student = make_student(abc, name="Aarav Sharma", phone="9841234567")

        response = pay(abc, student)

        assert response.status_code == status.HTTP_201_CREATED
        [payment] = stored_payments(abc)
        application_fee = {
            "id": fee_type_named(abc, "Application fee").pk,
            "name": "Application fee",
        }
        assert response.json() == {
            "id": payment.pk,
            "receipt_number": f"{payment.pk:06d}",
            "student": {"id": student.pk, "name": "Aarav Sharma", "phone": "9841234567"},
            "amount": 25000,
            "paid_on": today(),
            "method": "esewa",
            "fee_type": application_fee,
            "note": "",
            "recorded_by": {"name": "Sunita Shrestha"},
            "recorded_at": payment.recorded_at.isoformat(),
            "lines": [
                {
                    "id": payment.pk,
                    "fee_type": application_fee,
                    "note": "",
                    "amount": 25000,
                    "paid_on": today(),
                    "method": "esewa",
                }
            ],
            "total": 25000,
        }
        assert payment.recorded_by_id == desk.pk

    @pytest.mark.parametrize("role", STUDENT_ROLES)
    def test_every_student_role_records_for_any_student(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        other = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, other, Stage.COUNSELLING)
        abc.log_in_as(role)

        assert pay(abc, student).status_code == status.HTTP_201_CREATED

    def test_a_lost_student_can_still_pay(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, stage=Stage.LOST, lost_reason="Went elsewhere")
        abc.log_in_as(Role.FRONT_DESK)

        assert pay(abc, student).status_code == status.HTTP_201_CREATED

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = pay(abc, student)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS
        assert stored_payments(abc) == []

    def test_a_deactivated_staff_member_is_refused(self, abc: ConsultancyContext) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        log_in(abc, desk)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=desk.pk).update(status=StaffStatus.DEACTIVATED)

        response = pay(abc, make_student(abc))

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
        assert stored_payments(abc) == []

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        response = abc.new_client().post(payments_url(student), good_body(abc), format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        assert pay(abc, 999_999).status_code == status.HTTP_404_NOT_FOUND
        assert stored_payments(abc) == []

    def test_a_payment_made_on_an_earlier_day_keeps_that_day(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        earlier = (timezone.localdate() - timedelta(days=3)).isoformat()

        response = pay(abc, make_student(abc), paid_on=earlier)

        assert response.json()["paid_on"] == earlier

    @pytest.mark.parametrize(
        "method", ["cash", "esewa", "khalti", "fonepay_qr", "bank_transfer", "cheque"]
    )
    def test_every_method_is_accepted(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        assert pay(abc, make_student(abc), method=method).json()["method"] == method

    def test_the_note_is_trimmed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = pay(abc, make_student(abc), note="  first half  ")

        assert response.json()["note"] == "first half"

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        desk = abc.log_in_as(Role.FRONT_DESK)
        student = make_student(abc, name="Aarav Sharma")

        pay(abc, student, note="Secret note text")

        [payment] = stored_payments(abc)
        entry = json_logs.only(
            f"Payment recorded: payment_id={payment.pk}, student_id={student.pk}, "
            f"by_staff_id={desk.pk}"
        )
        assert entry["level"] == "INFO"
        assert "Secret note" not in json_logs.text()
        assert "Aarav" not in json_logs.text()


class TestHistory:
    def test_recording_writes_one_history_entry(self, abc: ConsultancyContext) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        log_in(abc, desk)
        student = make_student(abc)

        visa = fee_type_named(abc, "Visa fee").pk
        pay(abc, student, amount=125000, method="fonepay_qr", fee_type=visa)

        assert len(stored_payments(abc)) == 1
        [entry] = payment_entries(abc, student)
        assert entry.by_staff_id == desk.pk
        assert entry.by_name == "Sunita Shrestha"
        assert entry.before == ""
        assert entry.after == "NPR 1,25,000, Visa fee, Fonepay QR"
        assert entry.detail == ""  # no receipt numbers shown for now (2026-11-20)

    def test_the_history_api_shows_it(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        pay(abc, student)

        [entry] = abc.client.get(f"/api/students/{student.pk}/history/").json()["results"]

        assert entry["kind"] == "payment_recorded"
        assert entry["after"] == "NPR 25,000, Application fee, eSewa"

    def test_a_refused_payment_writes_no_history(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        student = make_student(abc)

        pay(abc, student, amount=0)

        assert payment_entries(abc, student) == []


class TestRefusedInput:
    @pytest.mark.parametrize(
        "amount", [0, -5, 100_000_001, 25000.5, "25000", True, None, [25000], {"n": 1}]
    )
    def test_a_bad_amount_is_refused(self, abc: ConsultancyContext, amount: object) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = pay(abc, make_student(abc), amount=amount)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == line_error(AMOUNT_MESSAGE)
        assert stored_payments(abc) == []

    def test_the_largest_amount_is_accepted(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        assert pay(abc, make_student(abc), amount=100_000_000).status_code == 201

    @pytest.mark.parametrize(
        "paid_on",
        ["1999-12-31", "2026-02-30", "02/11/2026", "", "tomorrow", 20261102, None, True, ["x"]],
    )
    def test_a_bad_date_is_refused(self, abc: ConsultancyContext, paid_on: object) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = pay(abc, make_student(abc), paid_on=paid_on)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == PAID_ON_MESSAGE

    def test_a_date_in_the_future_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()

        response = pay(abc, make_student(abc), paid_on=tomorrow)

        assert response.json() == PAID_ON_MESSAGE

    @pytest.mark.parametrize("method", ["Cash", "paypal", "", 1, None, True, ["cash"]])
    def test_an_unknown_method_is_refused(self, abc: ConsultancyContext, method: object) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = pay(abc, make_student(abc), method=method)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == METHOD_MESSAGE

    @pytest.mark.parametrize("missing", ["paid_on", "method", "lines"])
    def test_every_field_is_required(self, abc: ConsultancyContext, missing: str) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        body = good_body(abc)
        del body[missing]

        response = abc.client.post(payments_url(make_student(abc)), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == [missing]

    @pytest.mark.parametrize("missing", ["amount", "fee_type"])
    def test_every_line_needs_its_fee_type_and_amount(
        self, abc: ConsultancyContext, missing: str
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        line = good_line(abc)
        del line[missing]
        body = {**good_body(abc), "lines": [line]}

        response = abc.client.post(payments_url(make_student(abc)), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        [errors] = response.json()["lines"]
        assert list(errors) == [missing]
        assert stored_payments(abc) == []

    @pytest.mark.parametrize("field", ["recorded_by", "student", "receipt_number", "id"])
    def test_an_unknown_field_is_refused_and_nothing_stored(
        self, abc: ConsultancyContext, field: str
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        body = {**good_body(abc), field: 1}

        response = abc.client.post(payments_url(make_student(abc)), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_payments(abc) == []

    def test_a_body_that_isnt_an_object_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.post(payments_url(make_student(abc)), [good_body(abc)], format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_payments(abc) == []

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.post(payments_url(make_student(abc)), good_body(abc))

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("note", INJECTION_STRINGS)
    def test_injection_shaped_notes_are_kept_as_plain_text(
        self, abc: ConsultancyContext, note: str
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        student = make_student(abc)

        response = pay(abc, student, note=note)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["note"] == note.strip()
        [payment] = stored_payments(abc)
        assert payment.note == note.strip()


LINES_MESSAGE = {"lines": ["Add 1 to 10 lines, each with a Fee Type and an amount."]}
HIDDEN = {"fee_type": ["This Fee Type is hidden. Choose another."]}
OTHER_NOTE = {"note": ["Add a short note for Other."]}


def visit_body(office: ConsultancyContext, *lines: dict[str, object]) -> dict[str, object]:
    """One visit's money split across these lines, paid today by Fonepay QR."""
    return {"paid_on": today(), "method": "fonepay_qr", "lines": list(lines)}


def line_for(
    office: ConsultancyContext, name: str, amount: int, note: str = ""
) -> dict[str, object]:
    """One line: `amount` for the Fee Type with this name."""
    return {"fee_type": fee_type_named(office, name).pk, "amount": amount, "note": note}


def split_visit(office: ConsultancyContext, student: Student) -> _MonkeyPatchedResponse:
    """Visa fee 40,000, IELTS class 10,000 and courier (Other) 500, on one receipt."""
    body = visit_body(
        office,
        line_for(office, "Visa fee", 40000),
        line_for(office, "IELTS class", 10000, "first half"),
        line_for(office, "Other", 500, "Courier"),
    )
    return office.client.post(payments_url(student), body, format="json")


class TestSeveralLines:
    """One visit's money split across Fee Types: a Payment per line, one receipt (spec 73)."""

    def test_each_line_is_a_payment_and_they_share_one_receipt(
        self, abc: ConsultancyContext
    ) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        log_in(abc, desk)
        student = make_student(abc, name="Aarav Sharma")

        response = split_visit(abc, student)

        assert response.status_code == status.HTTP_201_CREATED
        visa, ielts, courier = stored_payments(abc)
        number = f"{visa.pk:06d}"
        assert [p.receipt_number for p in (visa, ielts, courier)] == [number] * 3
        assert [(p.amount, p.note) for p in (visa, ielts, courier)] == [
            (40000, ""),
            (10000, "first half"),
            (500, "Courier"),
        ]
        assert {(p.paid_on.isoformat(), p.method, p.recorded_by_id) for p in (visa, ielts)} == {
            (today(), "fonepay_qr", desk.pk)
        }
        body = response.json()
        assert (body["id"], body["receipt_number"], body["total"]) == (visa.pk, number, 50500)
        assert [
            (line["id"], line["fee_type"]["name"], line["note"], line["amount"])
            for line in body["lines"]
        ] == [
            (visa.pk, "Visa fee", "", 40000),
            (ielts.pk, "IELTS class", "first half", 10000),
            (courier.pk, "Other", "Courier", 500),
        ]

    @pytest.mark.parametrize("role", STUDENT_ROLES)
    def test_every_student_role_records_several_lines(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        abc.log_in_as(role)

        assert split_visit(abc, make_student(abc)).status_code == status.HTTP_201_CREATED
        assert len(stored_payments(abc)) == 3

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)

        response = split_visit(abc, make_student(abc))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert stored_payments(abc) == []

    def test_each_line_writes_its_own_history_entry_naming_no_receipt(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        student = make_student(abc)

        split_visit(abc, student)

        assert [(e.after, e.detail) for e in payment_entries(abc, student)] == [
            ("NPR 40,000, Visa fee, Fonepay QR", ""),
            ("NPR 10,000, IELTS class (first half), Fonepay QR", ""),
            ("NPR 500, Other (Courier), Fonepay QR", ""),
        ]

    def test_the_receipt_of_any_line_shows_every_line_and_the_total(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        made = split_visit(abc, make_student(abc)).json()

        receipts = [abc.client.get(payment_url(line["id"])).json() for line in made["lines"]]

        assert [r["lines"] for r in receipts] == [made["lines"]] * 3
        assert {(r["receipt_number"], r["total"]) for r in receipts} == {
            (made["receipt_number"], 50500)
        }

    def test_the_same_fee_type_twice_stays_two_payments(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        body = visit_body(
            abc, line_for(abc, "Visa fee", 20000), line_for(abc, "Visa fee", 20000, "second half")
        )

        response = abc.client.post(payments_url(make_student(abc)), body, format="json")

        assert response.json()["total"] == 40000
        assert [p.amount for p in stored_payments(abc)] == [20000, 20000]

    def test_each_visit_gets_its_own_receipt_number(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        student = make_student(abc)

        first = split_visit(abc, student).json()
        later = pay(abc, student).json()
        third = split_visit(abc, student).json()

        numbers = [first["receipt_number"], later["receipt_number"], third["receipt_number"]]
        assert len(set(numbers)) == 3
        assert [line["amount"] for line in later["lines"]] == [25000]

    def test_up_to_ten_lines_are_taken(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        body = visit_body(abc, *[line_for(abc, "PTE class", 1000)] * 10)

        response = abc.client.post(payments_url(make_student(abc)), body, format="json")

        assert (response.status_code, response.json()["total"]) == (201, 10000)


class TestOneBadLineSavesNothing:
    """Saving several lines is all or nothing (ticket 07)."""

    def post(self, office: ConsultancyContext, body: object) -> _MonkeyPatchedResponse:
        office.log_in_as(Role.FRONT_DESK)
        self.student = make_student(office)
        return office.client.post(payments_url(self.student), body, format="json")

    def assert_nothing_saved(self, office: ConsultancyContext) -> None:
        assert stored_payments(office) == []
        assert payment_entries(office, self.student) == []

    @pytest.mark.parametrize(
        ("bad", "error"),
        [
            ({"amount": 0}, AMOUNT_MESSAGE),
            ({"amount": "500"}, AMOUNT_MESSAGE),
            ({"fee_type": 999_999}, {"fee_type": ["Choose a Fee Type."]}),
            ({"note": "x" * 201}, {"note": ["Use at most 200 characters."]}),
            ({"note": 5}, {"note": ["Not a valid string."]}),
            ({"receipt": 1}, {"detail": ["Unknown field."]}),
        ],
    )
    def test_a_bad_second_line_is_named_and_nothing_is_saved(
        self, abc: ConsultancyContext, bad: dict[str, object], error: dict[str, list[str]]
    ) -> None:
        body = visit_body(
            abc, line_for(abc, "Visa fee", 40000), {**line_for(abc, "IELTS class", 10000), **bad}
        )

        response = self.post(abc, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == line_error(error, at=1, of=2)
        self.assert_nothing_saved(abc)

    def test_other_without_a_note_saves_nothing(self, abc: ConsultancyContext) -> None:
        body = visit_body(abc, line_for(abc, "Visa fee", 40000), line_for(abc, "Other", 500))

        response = self.post(abc, body)

        assert response.json() == line_error(OTHER_NOTE, at=1, of=2)
        self.assert_nothing_saved(abc)

    def test_a_hidden_fee_type_on_one_line_saves_nothing(self, abc: ConsultancyContext) -> None:
        with tenant_context(abc.consultancy):
            FeeType.objects.filter(name="PTE class").update(hidden=True)
        body = visit_body(abc, line_for(abc, "PTE class", 1000), line_for(abc, "Visa fee", 1))

        response = self.post(abc, body)

        assert response.json() == line_error(HIDDEN, at=0, of=2)
        self.assert_nothing_saved(abc)

    @pytest.mark.parametrize(
        "lines",
        [[], None, "lines", 5, {"amount": 1}, ["line"], [[1]], [None], "too many"],
    )
    def test_the_lines_must_be_one_to_ten_objects(
        self, abc: ConsultancyContext, lines: object
    ) -> None:
        if lines == "too many":
            lines = [line_for(abc, "PTE class", 1000)] * 11

        response = self.post(abc, {**visit_body(abc), "lines": lines})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == LINES_MESSAGE
        self.assert_nothing_saved(abc)

    def test_a_bad_visit_field_saves_no_line(self, abc: ConsultancyContext) -> None:
        body = {**visit_body(abc, line_for(abc, "Visa fee", 1)), "method": "paypal"}

        response = self.post(abc, body)

        assert response.json() == METHOD_MESSAGE
        self.assert_nothing_saved(abc)

    @pytest.mark.parametrize("note", INJECTION_STRINGS)
    def test_injection_shaped_notes_on_several_lines_are_plain_text(
        self, abc: ConsultancyContext, note: str
    ) -> None:
        body = visit_body(
            abc, line_for(abc, "Other", 500, note), line_for(abc, "Visa fee", 1000, note)
        )

        response = self.post(abc, body)

        assert response.status_code == status.HTTP_201_CREATED
        assert [line["note"] for line in response.json()["lines"]] == [note.strip()] * 2


class TestChangesAfterwards:
    """A Payment edited or deleted later keeps its receipt number; the receipt shows it as now."""

    @pytest.fixture
    def made(self, abc: ConsultancyContext) -> dict[str, object]:
        switch_modules(abc.consultancy, {Module.ACCOUNTS})
        abc.log_in_as(Role.ADMIN)
        self.student = make_student(abc)
        body: dict[str, object] = split_visit(abc, self.student).json()
        return body

    def line_ids(self, made: dict[str, object]) -> list[int]:
        lines = made["lines"]
        assert isinstance(lines, list)
        return [int(line["id"]) for line in lines]

    def test_an_edited_line_keeps_the_number_and_the_receipt_shows_it_now(
        self, abc: ConsultancyContext, made: dict[str, object]
    ) -> None:
        visa, ielts, _ = self.line_ids(made)
        edit = {**line_for(abc, "IELTS class", 15000, "whole course")}
        edit.update(paid_on=today(), method="cash")

        edited = abc.client.put(payment_url(ielts), edit, format="json")
        receipt = abc.client.get(payment_url(visa)).json()

        assert edited.status_code == status.HTTP_200_OK
        assert edited.json()["receipt_number"] == made["receipt_number"]
        assert receipt["receipt_number"] == made["receipt_number"]
        assert [(line["amount"], line["note"], line["method"]) for line in receipt["lines"]] == [
            (40000, "", "fonepay_qr"),
            (15000, "whole course", "cash"),
            (500, "Courier", "fonepay_qr"),
        ]
        assert receipt["total"] == 55500
        edits = [e for e in all_entries(abc, self.student) if e.kind == HistoryKind.PAYMENT_EDITED]
        assert [e.detail for e in edits] == [""]

    def test_a_deleted_line_leaves_the_rest_on_the_same_receipt(
        self, abc: ConsultancyContext, made: dict[str, object]
    ) -> None:
        visa, ielts, courier = self.line_ids(made)

        deleted = abc.client.delete(payment_url(visa))
        gone = abc.client.get(payment_url(visa))
        receipt = abc.client.get(payment_url(courier)).json()

        assert (deleted.status_code, gone.status_code) == (204, 404)
        assert receipt["receipt_number"] == made["receipt_number"]
        assert [line["id"] for line in receipt["lines"]] == [ielts, courier]
        assert receipt["total"] == 10500
        deletions = [
            e for e in all_entries(abc, self.student) if e.kind == HistoryKind.PAYMENT_DELETED
        ]
        assert [e.detail for e in deletions] == [""]

    def test_the_admin_list_lists_every_line_on_its_own(
        self, abc: ConsultancyContext, made: dict[str, object]
    ) -> None:
        rows = abc.client.get("/api/payments/").json()["results"]

        assert sorted(row["id"] for row in rows) == sorted(self.line_ids(made))
        assert all("lines" not in row for row in rows)

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_an_admin_edits_or_deletes_a_line(
        self, abc: ConsultancyContext, made: dict[str, object], role: Role
    ) -> None:
        _, ielts, _ = self.line_ids(made)
        abc.log_in_as(role)
        edit = {**line_for(abc, "IELTS class", 1), "paid_on": today(), "method": "cash"}

        edited = abc.client.put(payment_url(ielts), edit, format="json")
        deleted = abc.client.delete(payment_url(ielts))

        assert (edited.status_code, deleted.status_code) == (403, 403)
        assert [p.amount for p in stored_payments(abc)] == [40000, 10000, 500]


def all_entries(office: ConsultancyContext, student: Student) -> list[HistoryEntry]:
    """Every History entry of the Student, oldest first."""
    with tenant_context(office.consultancy):
        return list(HistoryEntry.objects.filter(student=student).order_by("pk"))


class TestReceipt:
    def test_the_recorder_reopens_the_receipt_they_just_made(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        student = make_student(abc)
        made = pay(abc, student).json()

        response = abc.client.get(payment_url(made["id"]))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == made

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_others_dont_see_someone_elses_payment(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        payment = make_payment(abc, make_student(abc), desk)
        abc.log_in_as(role)

        response = abc.client.get(payment_url(payment))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN_SEES

    def test_their_own_older_payment_is_no_longer_shown(self, abc: ConsultancyContext) -> None:
        desk = abc.log_in_as(Role.FRONT_DESK)
        payment = make_payment(abc, make_student(abc), desk)
        with tenant_context(abc.consultancy):
            Payment.objects.filter(pk=payment.pk).update(
                recorded_at=timezone.now() - timedelta(hours=1, seconds=1)
            )

        response = abc.client.get(payment_url(payment))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN_SEES

    def test_an_admin_sees_any_receipt(self, abc: ConsultancyContext) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        payment = make_payment(abc, make_student(abc), desk)
        with tenant_context(abc.consultancy):
            Payment.objects.filter(pk=payment.pk).update(
                recorded_at=timezone.now() - timedelta(days=90)
            )
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(payment_url(payment))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["recorded_by"] == {"name": "Sunita Shrestha"}
        assert response.json()["method"] == "cash"

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        teacher = abc.log_in_as(Role.TEACHER)
        payment = make_payment(abc, make_student(abc), teacher)

        response = abc.client.get(payment_url(payment))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    def test_an_unknown_payment_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(payment_url(999_999)).status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        desk = abc.add_staff(Role.FRONT_DESK)
        payment = make_payment(abc, make_student(abc), desk)

        response = abc.new_client().get(payment_url(payment))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_the_receipt_keeps_the_name_of_the_recorder_after_they_leave(
        self, abc: ConsultancyContext
    ) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        payment = make_payment(abc, make_student(abc), desk)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=desk.pk).update(
                status=StaffStatus.DEACTIVATED, name="Sunita Karki"
            )
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(payment_url(payment))

        assert response.json()["recorded_by"] == {"name": "Sunita Shrestha"}


class TestNoListsOrTotals:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_there_is_no_list_of_a_students_payments(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        student = make_student(abc)
        make_payment(abc, student, desk)
        abc.log_in_as(role)

        response = abc.client.get(payments_url(student))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert "amount" not in response.content.decode()

    @pytest.mark.parametrize("role", STUDENT_ROLES)
    def test_the_profile_shows_no_payments_or_totals(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        desk = named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK)
        student = make_student(abc)
        make_payment(abc, student, desk, amount=987654)
        abc.log_in_as(role)

        body = abc.client.get(f"/api/students/{student.pk}/")

        assert "987654" not in body.content.decode()
        assert body.json()["can_record_payment"] is True


class TestMethods:
    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_recording_answers_only_post(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(payments_url(make_student(abc)))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("method", ["post", "patch"])
    def test_the_receipt_answers_only_get(self, abc: ConsultancyContext, method: str) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        payment = make_payment(abc, make_student(abc), admin)

        response = getattr(abc.client, method)(payment_url(payment), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert len(stored_payments(abc)) == 1

    @pytest.mark.parametrize("method", ["put", "delete"])
    def test_without_accounts_a_payment_cant_be_changed(
        self, abc: ConsultancyContext, method: str
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        payment = make_payment(abc, make_student(abc), admin)

        response = getattr(abc.client, method)(payment_url(payment), {}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert len(stored_payments(abc)) == 1

    def test_not_on_the_platform_host(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        student = make_student(abc)
        platform_site.log_in()

        recorded = platform_site.client.post(payments_url(student), good_body(abc), format="json")
        receipt = platform_site.client.get(payment_url(1))

        assert recorded.status_code == status.HTTP_404_NOT_FOUND
        assert receipt.status_code == status.HTTP_404_NOT_FOUND
        assert stored_payments(abc) == []


class TestModels:
    def test_the_database_refuses_a_payment_of_nothing(self, abc: ConsultancyContext) -> None:
        desk = abc.add_staff(Role.FRONT_DESK)
        student = make_student(abc)
        with tenant_context(abc.consultancy), pytest.raises(IntegrityError), transaction.atomic():
            Payment.objects.create(
                student=student,
                recorded_by=desk,
                amount=0,
                paid_on=timezone.localdate(),
                method="cash",
                fee_type=FeeType.objects.get(name="Other"),
                note="x",
                recorded_by_name=desk.name,
            )

    def test_the_fields_show_their_values(self) -> None:
        assert AmountField().to_representation(25000) == 25000
        assert PaidOnField().to_representation(date(2026, 11, 2)) == "2026-11-02"

    def test_a_payment_reads_as_its_receipt_number(self, abc: ConsultancyContext) -> None:
        desk = abc.add_staff(Role.FRONT_DESK)
        payment = make_payment(abc, make_student(abc), desk)

        assert str(payment) == f"{payment.pk:06d}"
        assert payment.receipt == payment.pk

    def test_payments_saved_one_by_one_each_get_their_own_number(
        self, abc: ConsultancyContext
    ) -> None:
        desk = abc.add_staff(Role.FRONT_DESK)
        student = make_student(abc)
        first, second = make_payment(abc, student, desk), make_payment(abc, student, desk)

        assert (first.receipt, second.receipt) == (first.pk, second.pk)
