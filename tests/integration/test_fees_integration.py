"""Student Fees and Balances, and the Accounts Student fees list (spec 76a, 76b, 76f; issue 35)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from fee_types.models import FeeType
from fees.models import Fee
from history.models import HistoryEntry, HistoryKind
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff, StaffStatus
from students.models import Stage, Student
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

LIST_URL = "/api/accounts/student-fees/"
AMOUNT_MESSAGE = "Enter a whole amount from NPR 1 to NPR 10,00,00,000."
CHOOSE_MESSAGE = "Choose a Fee Type."
NOT_YOUR_STUDENT = {
    "detail": "Not your Student. Only their counsellor or an Admin can change them."
}
COUNSELLOR_OR_ADMIN = {"detail": "Only the Student's counsellor or an Admin can do this."}
TEACHERS = {"detail": "Teachers can't see Students."}
ONLY_ADMIN_SEES = {"detail": "Only an Admin can see other Payments."}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with Accounts on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.ACCOUNTS})
    return office


def fees_url(student: Student | int) -> str:
    """One Student's Fees."""
    pk = student if isinstance(student, int) else student.pk
    return f"/api/students/{pk}/fees/"


def fee_url(fee: Fee | int) -> str:
    """One Fee."""
    pk = fee if isinstance(fee, int) else fee.pk
    return f"/api/fees/{pk}/"


def make_fee(
    office: ConsultancyContext,
    student: Student,
    by: Staff,
    fee_type: str = "Visa fee",
    **fields: object,
) -> Fee:
    """Store a Fee directly, of the Fee Type with this name."""
    details: dict[str, object] = {"amount": 40000, **fields}
    with tenant_context(office.consultancy):
        return Fee.objects.create(
            student=student,
            fee_type=FeeType.objects.get(name=fee_type),
            added_by=by,
            added_by_name=by.name,
            **details,
        )


def with_ids(office: ConsultancyContext, body: object) -> object:
    """The body with a `fee_type` given by name sent as that Fee Type's id."""
    if isinstance(body, dict) and isinstance(body.get("fee_type"), str):
        with tenant_context(office.consultancy):
            found = FeeType.objects.filter(name=body["fee_type"]).first()
        return {**body, "fee_type": found.pk if found else body["fee_type"]}
    return body


def add(office: ConsultancyContext, student: Student, body: object) -> _MonkeyPatchedResponse:
    """POST a new Fee for the Student (a Fee Type by name is sent as its id)."""
    return office.client.post(fees_url(student), with_ids(office, body), format="json")


def put_fee(office: ConsultancyContext, fee: Fee | int, body: object) -> _MonkeyPatchedResponse:
    """PUT a Fee's new details (a Fee Type by name is sent as its id)."""
    return office.client.put(fee_url(fee), with_ids(office, body), format="json")


def stored_fees(office: ConsultancyContext, student: Student) -> list[tuple[str, int]]:
    """The Student's Fees as stored, oldest first."""
    with tenant_context(office.consultancy):
        return list(
            Fee.objects.filter(student=student)
            .order_by("pk")
            .values_list("fee_type__name", "amount")
        )


def entries(office: ConsultancyContext, student: Student, kind: HistoryKind) -> list[HistoryEntry]:
    """The Student's History entries of one kind, oldest first."""
    with tenant_context(office.consultancy):
        return list(HistoryEntry.objects.filter(student=student, kind=kind).order_by("pk"))


def counsellor_and_student(office: ConsultancyContext) -> tuple[Staff, Student]:
    """A Counsellor and the Student assigned to them."""
    counsellor = named_staff(office, "Bikash Thapa")
    return counsellor, make_student(office, counsellor, Stage.COUNSELLING, name="Aarav Sharma")


class TestStudentFees:
    def test_an_admin_sees_the_fees_paid_and_balance(self, abc: ConsultancyContext) -> None:
        admin = named_staff(abc, "Anita Admin", Role.ADMIN)
        _, student = counsellor_and_student(abc)
        first = make_fee(abc, student, admin, fee_type="Application fee", amount=25000)
        second = make_fee(abc, student, admin, fee_type="Visa fee", amount=40000)
        make_payment(abc, student, admin, amount=30000)
        log_in(abc, admin)

        response = abc.client.get(fees_url(student))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "fees": [
                {
                    "id": first.pk,
                    "fee_type": {"id": first.fee_type_id, "name": "Application fee"},
                    "note": "",
                    "amount": 25000,
                    "added_by": {"name": "Anita Admin"},
                    "added_at": first.added_at.isoformat(),
                },
                {
                    "id": second.pk,
                    "fee_type": {"id": second.fee_type_id, "name": "Visa fee"},
                    "note": "",
                    "amount": 40000,
                    "added_by": {"name": "Anita Admin"},
                    "added_at": second.added_at.isoformat(),
                },
            ],
            "total": 65000,
            "balance": {"paid": 30000, "still_to_pay": 35000, "overpaid": 0},
        }

    def test_overpaying_owes_nothing_and_says_how_much_more(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        make_fee(abc, student, admin, amount=40000)
        make_payment(abc, student, admin, amount=45000)

        balance = abc.client.get(fees_url(student)).json()["balance"]

        assert balance == {"paid": 45000, "still_to_pay": 0, "overpaid": 5000}

    def test_no_fees_yet(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        assert abc.client.get(fees_url(student)).json() == {
            "fees": [],
            "total": 0,
            "balance": {"paid": 0, "still_to_pay": 0, "overpaid": 0},
        }

    def test_the_students_counsellor_sees_the_fees_but_no_payments_or_balance(
        self, abc: ConsultancyContext
    ) -> None:
        counsellor, student = counsellor_and_student(abc)
        make_fee(abc, student, counsellor, amount=40000)
        make_payment(abc, student, counsellor, amount=10000)
        log_in(abc, counsellor)

        body = abc.client.get(fees_url(student)).json()

        assert body["total"] == 40000
        assert body["balance"] is None

    def test_an_admin_who_counsels_sees_the_balance(self, abc: ConsultancyContext) -> None:
        admin = named_staff(abc, "Anita Admin", Role.ADMIN)
        student = make_student(abc, admin, Stage.COUNSELLING)
        log_in(abc, admin)

        assert abc.client.get(fees_url(student)).json()["balance"] is not None

    def test_another_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        _, student = counsellor_and_student(abc)
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.get(fees_url(student))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT

    @pytest.mark.parametrize(
        ("role", "reason"), [(Role.FRONT_DESK, COUNSELLOR_OR_ADMIN), (Role.TEACHER, TEACHERS)]
    )
    def test_front_desk_and_teachers_are_refused(
        self, abc: ConsultancyContext, role: Role, reason: dict[str, str]
    ) -> None:
        _, student = counsellor_and_student(abc)
        abc.log_in_as(role)

        response = abc.client.get(fees_url(student))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == reason

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(fees_url(999_999)).status_code == status.HTTP_404_NOT_FOUND

    def test_unknown_query_fields_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.get(fees_url(student), {"student": "2"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}


class TestAddFee:
    def test_the_counsellor_adds_a_fee_and_history_records_it(
        self, abc: ConsultancyContext
    ) -> None:
        counsellor, student = counsellor_and_student(abc)
        log_in(abc, counsellor)

        response = add(abc, student, {"fee_type": "Visa fee", "amount": 40000})

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert [(fee["fee_type"]["name"], fee["amount"]) for fee in body["fees"]] == [
            ("Visa fee", 40000)
        ]
        assert body["fees"][0]["added_by"] == {"name": "Bikash Thapa"}
        assert body["balance"] is None
        assert stored_fees(abc, student) == [("Visa fee", 40000)]
        with tenant_context(abc.consultancy):
            fee = Fee.objects.get(student=student)
        assert str(fee) == f"Fee {fee.pk}"
        [entry] = entries(abc, student, HistoryKind.FEE_ADDED)
        assert (entry.by_name, entry.before, entry.after) == (
            "Bikash Thapa",
            "",
            "Visa fee, NPR 40,000",
        )

    def test_an_admin_adds_a_fee_to_any_student(self, abc: ConsultancyContext) -> None:
        _, student = counsellor_and_student(abc)
        abc.log_in_as(Role.ADMIN)

        response = add(abc, student, {"fee_type": "IELTS class", "amount": 15000})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["balance"] == {"paid": 0, "still_to_pay": 15000, "overpaid": 0}

    def test_a_lost_student_can_still_be_given_a_fee(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.LOST, lost_reason="Moved abroad")

        assert (
            add(abc, student, {"fee_type": "Other", "note": "Courier", "amount": 100}).status_code
            == 201
        )

    def test_the_note_is_trimmed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = add(abc, student, {"fee_type": "Visa fee", "note": "  first  ", "amount": 1})

        assert response.json()["fees"][0]["note"] == "first"

    @pytest.mark.parametrize(
        ("role", "reason"),
        [
            (Role.COUNSELLOR, NOT_YOUR_STUDENT),
            (Role.FRONT_DESK, COUNSELLOR_OR_ADMIN),
            (Role.TEACHER, TEACHERS),
        ],
    )
    def test_other_roles_are_refused_and_nothing_is_stored(
        self, abc: ConsultancyContext, role: Role, reason: dict[str, str]
    ) -> None:
        _, student = counsellor_and_student(abc)
        abc.log_in_as(role)

        response = add(abc, student, {"fee_type": "Visa fee", "amount": 40000})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == reason
        assert stored_fees(abc, student) == []

    def test_a_counsellor_loses_the_right_after_a_transfer(self, abc: ConsultancyContext) -> None:
        counsellor, student = counsellor_and_student(abc)
        other = named_staff(abc, "Sita Rai")
        with tenant_context(abc.consultancy):
            student.assigned_counsellor = other
            student.save()
        log_in(abc, counsellor)

        response = add(abc, student, {"fee_type": "Visa fee", "amount": 40000})

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            fees_url(999_999), {"fee_type": "Visa fee", "amount": 1}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logs_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        add(abc, student, {"fee_type": "Other", "note": "Secret note", "amount": 40000})

        [line] = json_logs.with_message("Fee added")
        assert line["message"] == (
            f"Fee added: fee_id={stored_fee_id(abc, student)}, student_id={student.pk}, "
            f"by_staff_id={admin.pk}"
        )
        assert "Secret note" not in json_logs.text()
        assert "40000" not in json_logs.text()


def stored_fee_id(office: ConsultancyContext, student: Student) -> int:
    """The id of the Student's only Fee."""
    with tenant_context(office.consultancy):
        return Fee.objects.get(student=student).pk


class TestRefusedFeeInput:
    @pytest.fixture
    def student(self, abc: ConsultancyContext) -> Student:
        abc.log_in_as(Role.ADMIN)
        return make_student(abc)

    @pytest.mark.parametrize(
        "amount", [0, -5, 100_000_001, 12.5, "40000", True, None, [40000], {"n": 1}]
    )
    def test_bad_amounts(self, abc: ConsultancyContext, student: Student, amount: object) -> None:
        response = add(abc, student, {"fee_type": "Visa fee", "amount": amount})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"amount": [AMOUNT_MESSAGE]}
        assert stored_fees(abc, student) == []

    @pytest.mark.parametrize("fee_type", ["", None, 12_345, True, ["Visa fee"], {"a": "b"}])
    def test_bad_fee_types(
        self, abc: ConsultancyContext, student: Student, fee_type: object
    ) -> None:
        response = add(abc, student, {"fee_type": fee_type, "amount": 40000})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"fee_type": [CHOOSE_MESSAGE]}

    def test_an_over_long_note(self, abc: ConsultancyContext, student: Student) -> None:
        response = add(abc, student, {"fee_type": "Visa fee", "note": "x" * 201, "amount": 1})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"note": ["Use at most 200 characters."]}

    def test_other_needs_a_note(self, abc: ConsultancyContext, student: Student) -> None:
        response = add(abc, student, {"fee_type": "Other", "amount": 1})

        assert response.json() == {"note": ["Add a short note for Other."]}
        assert stored_fees(abc, student) == []

    def test_control_characters_are_refused(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        response = add(abc, student, {"fee_type": "Other", "note": "Visa\nfee", "amount": 1})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_fees(abc, student) == []

    def test_missing_fields(self, abc: ConsultancyContext, student: Student) -> None:
        response = add(abc, student, {})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"fee_type": [CHOOSE_MESSAGE], "amount": [AMOUNT_MESSAGE]}

    @pytest.mark.parametrize("field", ["student", "added_by", "added_at", "id", "paid"])
    def test_unknown_fields_are_refused_and_never_stored(
        self, abc: ConsultancyContext, student: Student, field: str
    ) -> None:
        response = add(abc, student, {"fee_type": "Visa fee", "amount": 1, field: 1})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_fees(abc, student) == []

    @pytest.mark.parametrize("body", [[{"fee_type": "Visa fee", "amount": 1}], "Visa", 1])
    def test_a_body_that_is_not_an_object(
        self, abc: ConsultancyContext, student: Student, body: object
    ) -> None:
        response = add(abc, student, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_fees(abc, student) == []

    def test_a_form_body_is_refused(self, abc: ConsultancyContext, student: Student) -> None:
        response = abc.client.post(fees_url(student), {"fee_type": "Visa fee", "amount": "1"})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_notes_are_kept_as_plain_text(
        self, abc: ConsultancyContext, student: Student, text: str
    ) -> None:
        response = add(abc, student, {"fee_type": "Other", "note": text, "amount": 1})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["fees"][0]["note"] == text.strip()

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_only_get_and_post_on_a_students_fees(
        self, abc: ConsultancyContext, student: Student, method: str
    ) -> None:
        response = getattr(abc.client, method)(fees_url(student), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestChangeFee:
    def test_the_counsellor_changes_a_fee_and_history_names_the_change(
        self, abc: ConsultancyContext
    ) -> None:
        counsellor, student = counsellor_and_student(abc)
        fee = make_fee(abc, student, counsellor, fee_type="Visa fee", amount=40000)
        log_in(abc, counsellor)

        body = {"fee_type": "College / tuition fee", "note": "first year", "amount": 45000}
        response = put_fee(abc, fee, body)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["total"] == 45000
        assert stored_fees(abc, student) == [("College / tuition fee", 45000)]
        [entry] = entries(abc, student, HistoryKind.FEE_EDITED)
        assert (entry.before, entry.after) == (
            "Visa fee, NPR 40,000",
            "College / tuition fee (first year), NPR 45,000",
        )

    def test_who_added_it_stays_the_same(self, abc: ConsultancyContext) -> None:
        counsellor, student = counsellor_and_student(abc)
        fee = make_fee(abc, student, counsellor)
        abc.log_in_as(Role.ADMIN)

        body = put_fee(abc, fee, {"fee_type": "Visa fee", "amount": 1}).json()

        assert body["fees"][0]["added_by"] == {"name": "Bikash Thapa"}

    def test_no_change_writes_no_history(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        fee = make_fee(abc, student, admin, fee_type="Visa fee", amount=40000)

        response = put_fee(abc, fee, {"fee_type": "Visa fee", "amount": 40000})

        assert response.status_code == status.HTTP_200_OK
        assert entries(abc, student, HistoryKind.FEE_EDITED) == []

    @pytest.mark.parametrize(
        ("role", "reason"),
        [
            (Role.COUNSELLOR, NOT_YOUR_STUDENT),
            (Role.FRONT_DESK, COUNSELLOR_OR_ADMIN),
            (Role.TEACHER, TEACHERS),
        ],
    )
    def test_other_roles_are_refused(
        self, abc: ConsultancyContext, role: Role, reason: dict[str, str]
    ) -> None:
        counsellor, student = counsellor_and_student(abc)
        fee = make_fee(abc, student, counsellor)
        abc.log_in_as(role)

        response = put_fee(abc, fee, {"fee_type": "Visa fee", "amount": 1})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == reason
        assert stored_fees(abc, student) == [("Visa fee", 40000)]

    def test_refused_input_changes_nothing(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc)
        fee = make_fee(abc, student, admin)

        response = put_fee(abc, fee, {"fee_type": "Visa fee", "amount": "1", "student": 3})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored_fees(abc, student) == [("Visa fee", 40000)]

    def test_both_fields_are_needed(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        fee = make_fee(abc, make_student(abc), admin)

        response = put_fee(abc, fee, {"amount": 5})

        assert response.json() == {"fee_type": [CHOOSE_MESSAGE]}

    def test_an_unknown_fee_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = put_fee(abc, 999_999, {"fee_type": "Visa fee", "amount": 1})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "post", "patch"])
    def test_only_put_and_delete_on_one_fee(self, abc: ConsultancyContext, method: str) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        fee = make_fee(abc, make_student(abc), admin)

        response = getattr(abc.client, method)(fee_url(fee), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestDeleteFee:
    def test_the_counsellor_deletes_a_fee_and_history_keeps_it(
        self, abc: ConsultancyContext
    ) -> None:
        counsellor, student = counsellor_and_student(abc)
        fee = make_fee(abc, student, counsellor, fee_type="Visa fee", amount=40000)
        log_in(abc, counsellor)

        response = abc.client.delete(fee_url(fee))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert stored_fees(abc, student) == []
        [entry] = entries(abc, student, HistoryKind.FEE_DELETED)
        assert (entry.by_name, entry.before, entry.after) == (
            "Bikash Thapa",
            "Visa fee, NPR 40,000",
            "",
        )

    @pytest.mark.parametrize(
        ("role", "reason"),
        [
            (Role.COUNSELLOR, NOT_YOUR_STUDENT),
            (Role.FRONT_DESK, COUNSELLOR_OR_ADMIN),
            (Role.TEACHER, TEACHERS),
        ],
    )
    def test_other_roles_are_refused(
        self, abc: ConsultancyContext, role: Role, reason: dict[str, str]
    ) -> None:
        counsellor, student = counsellor_and_student(abc)
        fee = make_fee(abc, student, counsellor)
        abc.log_in_as(role)

        response = abc.client.delete(fee_url(fee))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == reason
        assert len(stored_fees(abc, student)) == 1

    def test_an_unknown_fee_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.delete(fee_url(999_999)).status_code == status.HTTP_404_NOT_FOUND


class TestStudentFeesList:
    def test_an_admin_sees_who_still_owes_most_first_with_the_totals(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        aarav = make_student(abc, name="Aarav Sharma")
        sita = make_student(abc, name="Sita Karki")
        paid_up = make_student(abc, name="Bina Rai")
        overpaid = make_student(abc, name="Hari Lama")
        make_student(abc, name="No Fees", phone="9841000009")
        make_fee(abc, aarav, admin, amount=25000)
        make_fee(abc, aarav, admin, amount=40000)
        make_payment(abc, aarav, admin, amount=5000)
        make_payment(abc, aarav, admin, amount=10000)
        make_fee(abc, sita, admin, amount=80000)
        make_fee(abc, paid_up, admin, amount=15000)
        make_payment(abc, paid_up, admin, amount=15000)
        make_fee(abc, overpaid, admin, amount=10000)
        make_payment(abc, overpaid, admin, amount=12000)

        response = abc.client.get(LIST_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "count": 2,
            "page": 1,
            "page_size": 25,
            "totals": {
                "agreed": 170000,
                "collected": 40000,
                "still_to_collect": 130000,
                "students": 4,
                "owing": 2,
            },
            "results": [
                {
                    "student": {"id": sita.pk, "name": "Sita Karki"},
                    "fees": 80000,
                    "paid": 0,
                    "still_to_pay": 80000,
                },
                {
                    "student": {"id": aarav.pk, "name": "Aarav Sharma"},
                    "fees": 65000,
                    "paid": 15000,
                    "still_to_pay": 50000,
                },
            ],
        }

    def test_the_same_amount_owed_is_listed_by_name(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        for name in ("Sita Karki", "Aarav Sharma"):
            make_fee(abc, make_student(abc, name=name), admin, amount=100)

        names = [row["student"]["name"] for row in abc.client.get(LIST_URL).json()["results"]]

        assert names == ["Aarav Sharma", "Sita Karki"]

    def test_nobody_owes(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(LIST_URL).json() == {
            "count": 0,
            "page": 1,
            "page_size": 25,
            "totals": {
                "agreed": 0,
                "collected": 0,
                "still_to_collect": 0,
                "students": 0,
                "owing": 0,
            },
            "results": [],
        }

    def test_pages_of_25(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        for number in range(26):
            student = make_student(abc, name=f"Student {number:02d}", phone=f"98410{number:05d}")
            make_fee(abc, student, admin, amount=1000 + number)

        second = abc.client.get(LIST_URL, {"page": "2"}).json()

        assert (second["count"], second["page"]) == (26, 2)
        assert [row["fees"] for row in second["results"]] == [1000]

    def test_a_page_past_the_end_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(LIST_URL, {"page": "2"}).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        "query",
        [{"page": "0"}, {"page": "x"}, {"page": "1.5"}, {"page": "<script>"}, {"owing": "1"}],
    )
    def test_refused_queries(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "<script>" not in response.content.decode()

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = abc.client.get(LIST_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN_SEES

    def test_a_deactivated_admin_is_refused(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        assert abc.client.get(LIST_URL).status_code in {401, 403}

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_get_only(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(LIST_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.client.get(LIST_URL).status_code == status.HTTP_401_UNAUTHORIZED


class TestProfileFlag:
    def test_the_profile_says_who_may_change_fees(self, abc: ConsultancyContext) -> None:
        counsellor, student = counsellor_and_student(abc)
        log_in(abc, counsellor)
        assert abc.client.get(f"/api/students/{student.pk}/").json()["can_change_fees"] is True

        abc.log_in_as(Role.FRONT_DESK)
        assert abc.client.get(f"/api/students/{student.pk}/").json()["can_change_fees"] is False


class TestAccountsModuleOff:
    @pytest.fixture
    def office(self, make_consultancy: ConsultancyFactory) -> ConsultancyContext:
        return make_consultancy("XYZ Overseas", "xyzoverseas")

    @pytest.mark.parametrize("role", list(Role))
    def test_every_fee_endpoint_is_not_found_for_every_role(
        self, office: ConsultancyContext, role: Role
    ) -> None:
        admin = named_staff(office, "Anita Admin", Role.ADMIN)
        student = make_student(office)
        fee = make_fee(office, student, admin)
        office.log_in_as(role)
        body = with_ids(office, {"fee_type": "Visa fee", "amount": 1})

        answers = [
            office.client.get(fees_url(student)),
            office.client.post(fees_url(student), body, format="json"),
            office.client.put(fee_url(fee), body, format="json"),
            office.client.delete(fee_url(fee)),
            office.client.get(LIST_URL),
        ]

        assert [answer.status_code for answer in answers] == [404] * 5
        assert stored_fees(office, student) == [("Visa fee", 40000)]

    def test_switching_back_on_brings_the_fees_back(
        self, office: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        admin = office.log_in_as(Role.ADMIN)
        student = make_student(office)
        make_fee(office, student, admin, amount=40000)
        assert office.client.get(fees_url(student)).status_code == status.HTTP_404_NOT_FOUND

        switch_modules(office.consultancy, {Module.ACCOUNTS})

        assert office.client.get(fees_url(student)).json()["total"] == 40000
        assert office.client.get(LIST_URL).json()["totals"]["agreed"] == 40000
