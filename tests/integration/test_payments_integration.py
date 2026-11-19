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


def good_body(office: ConsultancyContext, **changes: object) -> dict[str, object]:
    """A Payment the server accepts, for the Application fee; `changes` override it."""
    return {
        "amount": 25000,
        "paid_on": today(),
        "method": "esewa",
        "fee_type": fee_type_named(office, "Application fee").pk,
        **changes,
    }


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
        assert response.json() == {
            "id": payment.pk,
            "receipt_number": f"{payment.pk:06d}",
            "student": {"id": student.pk, "name": "Aarav Sharma", "phone": "9841234567"},
            "amount": 25000,
            "paid_on": today(),
            "method": "esewa",
            "fee_type": {
                "id": fee_type_named(abc, "Application fee").pk,
                "name": "Application fee",
            },
            "note": "",
            "recorded_by": {"name": "Sunita Shrestha"},
            "recorded_at": payment.recorded_at.isoformat(),
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

        [payment] = stored_payments(abc)
        [entry] = payment_entries(abc, student)
        assert entry.by_staff_id == desk.pk
        assert entry.by_name == "Sunita Shrestha"
        assert entry.before == ""
        assert entry.after == "NPR 1,25,000, Visa fee, Fonepay QR"
        assert entry.detail == f"Receipt {payment.pk:06d}"

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
        assert response.json() == AMOUNT_MESSAGE
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

    @pytest.mark.parametrize("missing", ["amount", "paid_on", "method", "fee_type"])
    def test_every_field_is_required(self, abc: ConsultancyContext, missing: str) -> None:
        abc.log_in_as(Role.FRONT_DESK)
        body = good_body(abc)
        del body[missing]

        response = abc.client.post(payments_url(make_student(abc)), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == [missing]

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
