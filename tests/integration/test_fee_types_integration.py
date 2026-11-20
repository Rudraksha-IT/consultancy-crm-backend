"""Fee Types: the office's own list of what a Fee or Payment is for (spec 67-72, 76; issue 06)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from fee_types.models import FeeType
from fee_types.services import MAX_FEE_TYPES
from fees.models import Fee
from history.models import HistoryEntry
from modules.services import switch_modules
from modules.types import Module
from payments.models import Payment
from staff.models import Role, Staff, StaffStatus
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

    from students.models import Student

FEE_TYPES_URL = "/api/fee-types/"
ORDER_URL = "/api/fee-types/order/"
DEFAULT_NAMES = [
    "Application fee",
    "College / tuition fee",
    "Visa fee",
    "IELTS class",
    "PTE class",
    "Documentation / other services",
    "Other",
]
ENTER_NAME = {"name": ["Enter the Fee Type's name."]}
DUPLICATE = {"name": ["There is already a Fee Type with this name."]}
ORDER = {"order": ["Send every Fee Type once, in the new order."]}
OTHER_FIXED = {"detail": "Other is always there. It can't be renamed, hidden or deleted."}
IN_USE = {"detail": "Fees or Payments use this Fee Type. Hide it instead."}
CHOOSE = {"fee_type": ["Choose a Fee Type."]}
HIDDEN = {"fee_type": ["This Fee Type is hidden. Choose another."]}
OTHER_NOTE = {"note": ["Add a short note for Other."]}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with Accounts on (for Fees)."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.ACCOUNTS})
    return office


def fee_type(office: ConsultancyContext, name: str) -> FeeType:
    """The office's Fee Type with this name."""
    with tenant_context(office.consultancy):
        return FeeType.objects.get(name=name)


def fee_type_id(office: ConsultancyContext, name: str) -> str:
    """The id of the office's Fee Type with this name: its UUID (ADR 0027)."""
    return str(fee_type(office, name).public_id)


def type_url(type_id: object) -> str:
    """One Fee Type."""
    return f"{FEE_TYPES_URL}{type_id}/"


def as_admin(office: ConsultancyContext) -> Staff:
    """Log in as an Admin and return them."""
    admin = named_staff(office, "Anita Admin", Role.ADMIN)
    log_in(office, admin)
    return admin


def listed(office: ConsultancyContext) -> list[dict[str, object]]:
    """The Fee Types as the list answers them."""
    results: list[dict[str, object]] = office.client.get(FEE_TYPES_URL).json()["results"]
    return results


def names(office: ConsultancyContext) -> list[str]:
    """The Fee Types' names, in the office's order."""
    return [str(row["name"]) for row in listed(office)]


def add(office: ConsultancyContext, body: object) -> _MonkeyPatchedResponse:
    """POST a new Fee Type."""
    return office.client.post(FEE_TYPES_URL, body, format="json")


def change(office: ConsultancyContext, type_id: object, body: object) -> _MonkeyPatchedResponse:
    """PATCH one Fee Type."""
    return office.client.patch(type_url(type_id), body, format="json")


def store_fee(office: ConsultancyContext, student: Student, by: Staff, name: str) -> Fee:
    """A Fee of this Fee Type, stored directly."""
    with tenant_context(office.consultancy):
        return Fee.objects.create(
            student=student,
            fee_type=FeeType.objects.get(name=name),
            amount=1000,
            added_by=by,
            added_by_name=by.name,
        )


def store_payment(office: ConsultancyContext, student: Student, by: Staff, name: str) -> Payment:
    """A Payment of this Fee Type, stored directly."""
    with tenant_context(office.consultancy):
        return Payment.objects.create(
            student=student,
            fee_type=FeeType.objects.get(name=name),
            amount=1000,
            paid_on=timezone.localdate(),
            method="cash",
            recorded_by=by,
            recorded_by_name=by.name,
        )


class TestEveryOfficeStartsWithTheDefaults:
    def test_seven_types_in_order_with_other_last(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        rows = listed(abc)

        assert [row["name"] for row in rows] == DEFAULT_NAMES
        assert [row["other"] for row in rows] == [False] * 6 + [True]
        assert {(row["hidden"], str(row["uses"])) for row in rows} == {
            (False, str({"fees": 0, "payments": 0}))
        }

    def test_another_office_has_its_own_list(
        self, abc: ConsultancyContext, make_consultancy: ConsultancyFactory
    ) -> None:
        xyz = make_consultancy("XYZ Overseas", "xyzoverseas")
        as_admin(abc)
        add(abc, {"name": "SOP writing"})
        log_in(xyz, named_staff(xyz, "Xavier Admin", Role.ADMIN))

        assert names(xyz) == DEFAULT_NAMES


class TestReadingTheList:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_staff_who_take_payments_see_it_without_the_uses(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        log_in(abc, named_staff(abc, "Sunita Shrestha", role))

        rows = listed(abc)

        assert [row["name"] for row in rows] == DEFAULT_NAMES
        assert {row["uses"] for row in rows} == {None}

    def test_hidden_ones_are_listed_and_marked(self, abc: ConsultancyContext) -> None:
        as_admin(abc)
        change(abc, fee_type_id(abc, "PTE class"), {"hidden": True})
        log_in(abc, named_staff(abc, "Sunita Shrestha", Role.FRONT_DESK))

        [pte] = [row for row in listed(abc) if row["name"] == "PTE class"]

        assert pte["hidden"] is True

    def test_teachers_are_refused(self, abc: ConsultancyContext) -> None:
        log_in(abc, named_staff(abc, "Tara Teacher", Role.TEACHER))

        assert abc.client.get(FEE_TYPES_URL).status_code == status.HTTP_403_FORBIDDEN

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        response = abc.client.get(FEE_TYPES_URL)

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}

    def test_the_uses_count_fees_and_payments(self, abc: ConsultancyContext) -> None:
        admin = as_admin(abc)
        student = make_student(abc)
        store_fee(abc, student, admin, "Visa fee")
        store_fee(abc, student, admin, "Visa fee")
        store_payment(abc, student, admin, "Visa fee")

        [visa] = [row for row in listed(abc) if row["name"] == "Visa fee"]

        assert visa["uses"] == {"fees": 2, "payments": 1}

    def test_it_is_there_with_accounts_off(self, make_consultancy: ConsultancyFactory) -> None:
        office = make_consultancy("ABC Education", "abcedu")
        log_in(office, named_staff(office, "Sunita Shrestha", Role.FRONT_DESK))

        assert names(office) == DEFAULT_NAMES


class TestAdding:
    def test_an_admin_adds_one_at_the_end(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        response = add(abc, {"name": "  SOP   writing "})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {
            "id": fee_type_id(abc, "SOP writing"),
            "name": "SOP writing",
            "hidden": False,
            "other": False,
            "uses": {"fees": 0, "payments": 0},
        }
        assert names(abc) == [*DEFAULT_NAMES, "SOP writing"]

    @pytest.mark.parametrize("name", ["visa fee", " VISA  FEE ", "Visa fee"])
    def test_a_name_already_used_is_refused(self, abc: ConsultancyContext, name: str) -> None:
        as_admin(abc)

        response = add(abc, {"name": name})

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, DUPLICATE)
        assert names(abc) == DEFAULT_NAMES

    @pytest.mark.parametrize("name", ["", "   ", None, 12, True, ["Visa"], {"a": "b"}])
    def test_a_missing_or_wrong_name_is_refused(
        self, abc: ConsultancyContext, name: object
    ) -> None:
        as_admin(abc)

        response = add(abc, {"name": name})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["name"]
        assert names(abc) == DEFAULT_NAMES

    def test_a_missing_name_says_what_to_do(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        assert add(abc, {}).json() == ENTER_NAME

    def test_sixty_characters_at_most(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        refused = add(abc, {"name": "x" * 61})
        accepted = add(abc, {"name": "y" * 60})

        assert refused.json() == {"name": ["Use at most 60 characters."]}
        assert accepted.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("name", ["SOP\nwriting", "SOP\x00", "SOP\x1b[31m"])
    def test_control_characters_are_refused(self, abc: ConsultancyContext, name: str) -> None:
        as_admin(abc)

        response = add(abc, {"name": name})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["name"]

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_names_are_kept_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        as_admin(abc)

        response = add(abc, {"name": text[:60]})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["name"] == " ".join(text[:60].split())

    def test_unknown_fields_are_refused(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        response = add(abc, {"name": "SOP writing", "is_other": True})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert names(abc) == DEFAULT_NAMES

    @pytest.mark.parametrize("body", [["SOP writing"], "SOP writing", 1])
    def test_a_body_that_is_not_an_object(self, abc: ConsultancyContext, body: object) -> None:
        as_admin(abc)

        assert add(abc, body).status_code == status.HTTP_400_BAD_REQUEST

    def test_at_most_fifty(self, abc: ConsultancyContext) -> None:
        as_admin(abc)
        with tenant_context(abc.consultancy):
            FeeType.objects.bulk_create(
                FeeType(name=f"Type {n}", position=100 + n)
                for n in range(MAX_FEE_TYPES - len(DEFAULT_NAMES))
            )

        response = add(abc, {"name": "One too many"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "An office can have at most 50 Fee Types."}

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_add(self, abc: ConsultancyContext, role: Role) -> None:
        log_in(abc, named_staff(abc, "Sunita Shrestha", role))

        response = add(abc, {"name": "SOP writing"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        with tenant_context(abc.consultancy):
            assert not FeeType.objects.filter(name="SOP writing").exists()

    def test_a_deactivated_admin_is_refused(self, abc: ConsultancyContext) -> None:
        admin = as_admin(abc)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        response = add(abc, {"name": "SOP writing"})

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}

    def test_logs_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        admin = as_admin(abc)

        add(abc, {"name": "Secret type name"})

        added = fee_type(abc, "Secret type name").pk  # logs keep the number
        assert json_logs.only(f"Fee type added: fee_type_id={added}, by_staff_id={admin.pk}")
        assert "Secret type name" not in json_logs.text()


class TestChanging:
    def test_rename(self, abc: ConsultancyContext) -> None:
        as_admin(abc)
        visa = fee_type_id(abc, "Visa fee")

        response = change(abc, visa, {"name": "Visa processing"})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == "Visa processing"
        assert names(abc)[2] == "Visa processing"

    def test_a_rename_shows_on_old_fees_and_payments(self, abc: ConsultancyContext) -> None:
        admin = as_admin(abc)
        student = make_student(abc)
        payment = store_payment(abc, student, admin, "Visa fee")

        change(abc, fee_type_id(abc, "Visa fee"), {"name": "Visa processing"})

        receipt = abc.client.get(f"/api/payments/{payment.public_id}/").json()
        assert receipt["fee_type"]["name"] == "Visa processing"

    def test_changing_only_the_capitals_of_its_own_name(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        response = change(abc, fee_type_id(abc, "Visa fee"), {"name": "Visa Fee"})

        assert response.json()["name"] == "Visa Fee"

    def test_another_types_name_is_refused(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        response = change(abc, fee_type_id(abc, "Visa fee"), {"name": "pte  class"})

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, DUPLICATE)
        assert names(abc) == DEFAULT_NAMES

    def test_hide_and_show(self, abc: ConsultancyContext) -> None:
        as_admin(abc)
        pte = fee_type_id(abc, "PTE class")

        hidden = change(abc, pte, {"hidden": True}).json()
        shown = change(abc, pte, {"hidden": False}).json()

        assert (hidden["hidden"], shown["hidden"]) == (True, False)

    def test_rename_and_hide_at_once(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        body = change(abc, fee_type_id(abc, "PTE class"), {"name": "PTE", "hidden": True}).json()

        assert (body["name"], body["hidden"]) == ("PTE", True)

    @pytest.mark.parametrize(
        "body", [{}, {"hidden": "true"}, {"hidden": None}, {"name": ""}, {"position": 1}]
    )
    def test_bad_changes_are_refused(self, abc: ConsultancyContext, body: object) -> None:
        as_admin(abc)

        response = change(abc, fee_type_id(abc, "PTE class"), body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert names(abc) == DEFAULT_NAMES

    @pytest.mark.parametrize("body", [{"name": "Misc"}, {"hidden": True}])
    def test_other_never_changes(self, abc: ConsultancyContext, body: object) -> None:
        as_admin(abc)

        response = change(abc, fee_type_id(abc, "Other"), body)

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            OTHER_FIXED,
        )
        assert fee_type(abc, "Other").hidden is False

    def test_an_unknown_type_is_not_found(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        assert change(abc, 999_999, {"hidden": True}).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_change(self, abc: ConsultancyContext, role: Role) -> None:
        log_in(abc, named_staff(abc, "Sunita Shrestha", role))

        response = change(abc, fee_type_id(abc, "Visa fee"), {"hidden": True})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert fee_type(abc, "Visa fee").hidden is False

    @pytest.mark.parametrize("method", ["get", "put", "post"])
    def test_only_patch_and_delete_on_one(self, abc: ConsultancyContext, method: str) -> None:
        as_admin(abc)

        response = getattr(abc.client, method)(type_url(fee_type_id(abc, "Visa fee")))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestDeleting:
    def test_an_unused_type_is_deleted(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        response = abc.client.delete(type_url(fee_type_id(abc, "PTE class")))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert "PTE class" not in names(abc)

    @pytest.mark.parametrize("store", [store_fee, store_payment])
    def test_one_in_use_is_refused(
        self, abc: ConsultancyContext, store: Callable[..., object]
    ) -> None:
        admin = as_admin(abc)
        store(abc, make_student(abc), admin, "Visa fee")

        response = abc.client.delete(type_url(fee_type_id(abc, "Visa fee")))

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, IN_USE)
        assert "Visa fee" in names(abc)

    def test_other_is_never_deleted(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        response = abc.client.delete(type_url(fee_type_id(abc, "Other")))

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            OTHER_FIXED,
        )

    def test_an_unknown_type_is_not_found(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        assert abc.client.delete(type_url(999_999)).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_delete(self, abc: ConsultancyContext, role: Role) -> None:
        log_in(abc, named_staff(abc, "Sunita Shrestha", role))

        response = abc.client.delete(type_url(fee_type_id(abc, "PTE class")))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        with tenant_context(abc.consultancy):
            assert FeeType.objects.filter(name="PTE class").exists()


class TestOrder:
    def ids(self, office: ConsultancyContext) -> list[str]:
        """Every Fee Type's UUID, in the order they show."""
        with tenant_context(office.consultancy):
            found = FeeType.objects.order_by("position", "pk").values_list("public_id", flat=True)
            return [str(public_id) for public_id in found]

    def test_a_new_order_is_kept_and_answered(self, abc: ConsultancyContext) -> None:
        as_admin(abc)
        ids = self.ids(abc)
        wanted = [ids[-1], *ids[:-1]]

        response = abc.client.put(ORDER_URL, {"order": wanted}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert [row["id"] for row in response.json()["results"]] == wanted
        assert names(abc) == ["Other", *DEFAULT_NAMES[:-1]]

    @pytest.mark.parametrize("wrong", ["missing", "twice", "unknown"])
    def test_anything_but_every_id_once_is_refused(
        self, abc: ConsultancyContext, wrong: str
    ) -> None:
        as_admin(abc)
        ids = self.ids(abc)
        sent = {
            "missing": ids[1:],
            "twice": [ids[0], *ids[:-1]],
            "unknown": [*ids[:-1], str(uuid.uuid4())],
        }[wrong]

        response = abc.client.put(ORDER_URL, {"order": sent}, format="json")

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, ORDER)
        assert names(abc) == DEFAULT_NAMES

    @pytest.mark.parametrize("sent", [[], None, "1,2,3", [True], ["1"], [1.5], [0], [2**31], [1]])
    def test_a_wrong_shape_is_refused(self, abc: ConsultancyContext, sent: object) -> None:
        as_admin(abc)

        response = abc.client.put(ORDER_URL, {"order": sent}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert names(abc) == DEFAULT_NAMES

    def test_too_long_a_list_is_refused(self, abc: ConsultancyContext) -> None:
        as_admin(abc)

        response = abc.client.put(
            ORDER_URL, {"order": [str(uuid.uuid4()) for _ in range(60)]}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins_reorder(self, abc: ConsultancyContext, role: Role) -> None:
        log_in(abc, named_staff(abc, "Sunita Shrestha", role))

        response = abc.client.put(ORDER_URL, {"order": self.ids(abc)[::-1]}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestOldPurposesBecomeOther:
    """The migrations that move each typed purpose into the note under Other (spec 76)."""

    def test_fees_and_payments_keep_their_text_as_the_note(self, abc: ConsultancyContext) -> None:
        admin = named_staff(abc, "Anita Admin", Role.ADMIN)
        student = make_student(abc)
        before = [("fees", "0002_platform_admin_actor"), ("payments", "0002_platform_admin_actor")]
        after = [("fees", "0005_fee_type_required"), ("payments", "0005_fee_type_required")]
        with tenant_context(abc.consultancy):
            executor = MigrationExecutor(connection)
            executor.migrate(before)
            old_apps = executor.loader.project_state(before).apps
            old_fee = old_apps.get_model("fees", "Fee").objects.create(
                student_id=student.pk,
                purpose="Visa processing (first half)",
                amount=40000,
                added_by_id=admin.pk,
                added_by_name=admin.name,
            )
            old_payment = old_apps.get_model("payments", "Payment").objects.create(
                student_id=student.pk,
                purpose="x" * 200,
                amount=25000,
                paid_on=timezone.localdate(),
                method="cash",
                recorded_by_id=admin.pk,
                recorded_by_name=admin.name,
            )
            with connection.cursor() as cursor:  # check the rows now, so tables can be altered
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            executor.loader.build_graph()
            executor.migrate(executor.loader.graph.leaf_nodes())

            fee = Fee.objects.select_related("fee_type").get(pk=old_fee.pk)
            payment = Payment.objects.select_related("fee_type").get(pk=old_payment.pk)

        assert (fee.fee_type.name, fee.note, fee.amount) == (
            "Other",
            "Visa processing (first half)",
            40000,
        )
        assert (payment.fee_type.name, payment.note) == ("Other", "x" * 200)
        assert payment.receipt_number == f"{old_payment.pk:06d}"


def payment_body(office: ConsultancyContext, name: str, **changes: object) -> dict[str, object]:
    """A Payment the server accepts, of this Fee Type; `changes` override it."""
    return {
        "amount": 25000,
        "paid_on": timezone.localdate().isoformat(),
        "method": "esewa",
        "fee_type": fee_type_id(office, name),
        **changes,
    }


def fee_body(office: ConsultancyContext, name: str, **changes: object) -> dict[str, object]:
    """A Fee the server accepts, of this Fee Type; `changes` override it."""
    return {"fee_type": fee_type_id(office, name), "amount": 40000, **changes}


def line_refusal(response: _MonkeyPatchedResponse) -> object:
    """The refusal of a visit's only line: the answer must be `{"lines": [refusal]}`."""
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert list(body) == ["lines"]
    [refusal] = body["lines"]
    return refusal


class TestPickingAFeeType:
    """Every Fee and Payment picks an offered Fee Type; Other needs a note (spec 69-72)."""

    @pytest.fixture
    def student(self, abc: ConsultancyContext) -> Student:
        as_admin(abc)
        return make_student(abc)

    def record(
        self, office: ConsultancyContext, student: Student, body: dict[str, object]
    ) -> _MonkeyPatchedResponse:
        """Record `body` (as one Payment's fields) as a visit with that one line."""
        visit = {"paid_on": body.pop("paid_on"), "method": body.pop("method"), "lines": [body]}
        return office.client.post(
            f"/api/students/{student.public_id}/payments/", visit, format="json"
        )

    def add_fee(
        self, office: ConsultancyContext, student: Student, body: object
    ) -> _MonkeyPatchedResponse:
        return office.client.post(f"/api/students/{student.public_id}/fees/", body, format="json")

    def test_a_payment_names_its_fee_type_and_note(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        response = self.record(abc, student, payment_body(abc, "Visa fee", note=" first half "))

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert (body["fee_type"], body["note"]) == (
            {"id": fee_type_id(abc, "Visa fee"), "name": "Visa fee"},
            "first half",
        )

    def test_the_note_is_optional_except_for_other(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        without = self.record(abc, student, payment_body(abc, "Visa fee"))
        other = self.record(abc, student, payment_body(abc, "Other"))
        other_blank = self.record(abc, student, payment_body(abc, "Other", note="   "))
        other_noted = self.record(abc, student, payment_body(abc, "Other", note="Courier"))

        assert (without.status_code, without.json()["note"]) == (status.HTTP_201_CREATED, "")
        assert line_refusal(other) == OTHER_NOTE
        assert line_refusal(other_blank) == OTHER_NOTE
        assert other_noted.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("sent", [None, "3", True, 1.5, 0, -1, 2**31, 999_999, [3]])
    def test_a_missing_or_unknown_fee_type_is_refused(
        self, abc: ConsultancyContext, student: Student, sent: object
    ) -> None:
        payment = self.record(abc, student, payment_body(abc, "Visa fee", fee_type=sent))
        fee = self.add_fee(abc, student, fee_body(abc, "Visa fee", fee_type=sent))

        assert line_refusal(payment) == CHOOSE
        assert (fee.status_code, fee.json()) == (status.HTTP_400_BAD_REQUEST, CHOOSE)

    def test_a_fee_type_is_needed(self, abc: ConsultancyContext, student: Student) -> None:
        body = payment_body(abc, "Visa fee")
        del body["fee_type"]

        assert line_refusal(self.record(abc, student, body)) == CHOOSE

    def test_a_hidden_fee_type_is_not_offered(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        change(abc, fee_type_id(abc, "PTE class"), {"hidden": True})

        payment = self.record(abc, student, payment_body(abc, "PTE class"))
        fee = self.add_fee(abc, student, fee_body(abc, "PTE class"))

        assert line_refusal(payment) == HIDDEN
        assert (fee.status_code, fee.json()) == (status.HTTP_400_BAD_REQUEST, HIDDEN)

    def test_an_old_payment_keeps_its_hidden_fee_type_when_edited(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        switch_modules(abc.consultancy, {Module.ACCOUNTS})
        payment = self.record(abc, student, payment_body(abc, "PTE class")).json()
        change(abc, fee_type_id(abc, "PTE class"), {"hidden": True})
        url = f"/api/payments/{payment['id']}/"

        kept = abc.client.put(url, payment_body(abc, "PTE class", amount=1000), format="json")
        moved = abc.client.put(url, payment_body(abc, "Visa fee"), format="json")
        back = abc.client.put(url, payment_body(abc, "PTE class"), format="json")

        assert (kept.status_code, kept.json()["fee_type"]["name"]) == (
            status.HTTP_200_OK,
            "PTE class",
        )
        assert moved.status_code == status.HTTP_200_OK
        assert (back.status_code, back.json()) == (status.HTTP_400_BAD_REQUEST, HIDDEN)

    def test_an_old_fee_keeps_its_hidden_fee_type_when_edited(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        added = self.add_fee(abc, student, fee_body(abc, "PTE class")).json()
        change(abc, fee_type_id(abc, "PTE class"), {"hidden": True})
        url = f"/api/fees/{added['fees'][0]['id']}/"

        kept = abc.client.put(url, fee_body(abc, "PTE class", amount=1000), format="json")
        moved = abc.client.put(url, fee_body(abc, "Visa fee"), format="json")
        back = abc.client.put(url, fee_body(abc, "PTE class"), format="json")

        assert kept.status_code == status.HTTP_200_OK
        assert moved.status_code == status.HTTP_200_OK
        assert (back.status_code, back.json()) == (status.HTTP_400_BAD_REQUEST, HIDDEN)

    @pytest.mark.parametrize("note", [12, True, ["x"], None])
    def test_a_note_that_is_not_text_is_refused(
        self, abc: ConsultancyContext, student: Student, note: object
    ) -> None:
        response = self.record(abc, student, payment_body(abc, "Visa fee", note=note))

        assert list(line_refusal(response)) == ["note"]  # type: ignore[call-overload]

    def test_a_note_of_two_hundred_characters_at_most(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        refused = self.record(abc, student, payment_body(abc, "Visa fee", note="x" * 201))
        accepted = self.record(abc, student, payment_body(abc, "Visa fee", note="x" * 200))

        assert line_refusal(refused) == {"note": ["Use at most 200 characters."]}
        assert accepted.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("note", ["first\nhalf", "x\x00", "x\x1b[31m"])
    def test_control_characters_in_the_note_are_refused(
        self, abc: ConsultancyContext, student: Student, note: str
    ) -> None:
        response = self.record(abc, student, payment_body(abc, "Visa fee", note=note))

        assert list(line_refusal(response)) == ["note"]  # type: ignore[call-overload]

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_notes_are_kept_as_plain_text(
        self, abc: ConsultancyContext, student: Student, text: str
    ) -> None:
        response = self.record(abc, student, payment_body(abc, "Other", note=text))

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["note"] == text.strip()

    def test_history_names_the_fee_type_and_note(
        self, abc: ConsultancyContext, student: Student
    ) -> None:
        self.record(abc, student, payment_body(abc, "Visa fee", note="first half"))
        self.add_fee(abc, student, fee_body(abc, "IELTS class"))

        with tenant_context(abc.consultancy):
            entries = list(
                HistoryEntry.objects.filter(student=student)
                .order_by("pk")
                .values_list("kind", "after")
            )

        assert entries == [
            ("payment_recorded", "NPR 25,000, Visa fee (first half), eSewa"),
            ("fee_added", "IELTS class, NPR 40,000"),
        ]
