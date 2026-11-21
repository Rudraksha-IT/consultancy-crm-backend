"""How a Payment and an edit to it are written in History (issue 19)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from fee_types.models import FeeType
from payments.models import Payment, PaymentMethod
from payments.services import describe_changes, describe_deleted_payment
from payments.types import NewPayment

VISA = FeeType(pk=3, name="Visa fee", position=3)
PTE = FeeType(pk=5, name="PTE class", position=5)


def payment(**fields: object) -> Payment:
    """An unsaved Payment; `fields` override the defaults."""
    details: dict[str, object] = {
        "amount": 25000,
        "paid_on": date(2026, 10, 30),
        "method": PaymentMethod.ESEWA,
        "fee_type": VISA,
        **fields,
    }
    return Payment(**details)


SAME = NewPayment(
    amount=25000, paid_on=date(2026, 10, 30), method=PaymentMethod.ESEWA, fee_type=VISA
)


def details(**fields: Any) -> NewPayment:  # noqa: ANN401  # any NewPayment field
    """New details equal to `payment()`'s; `fields` override them."""
    return replace(SAME, **fields)


def test_no_change_is_described_as_nothing() -> None:
    """Saving the same details is no change."""
    assert describe_changes(payment(), details()) is None


@pytest.mark.parametrize(
    ("change", "before", "after"),
    [
        ({"amount": 125000}, "Amount NPR 25,000", "Amount NPR 1,25,000"),
        ({"paid_on": date(2026, 9, 1)}, "Date 30 Oct 2026", "Date 1 Sep 2026"),
        ({"method": PaymentMethod.FONEPAY_QR}, "Method eSewa", "Method Fonepay QR"),
        ({"fee_type": PTE}, "For Visa fee", "For PTE class"),
        ({"note": "first half"}, "For Visa fee", "For Visa fee (first half)"),
    ],
)
def test_each_changed_field_is_named(change: dict[str, Any], before: str, after: str) -> None:
    """Only the changed field is named, before and after."""
    assert describe_changes(payment(), details(**change)) == (before, after)


def test_several_changes_are_listed_in_form_order() -> None:
    """Several changes read in the form's order."""
    change = describe_changes(
        payment(), details(fee_type=PTE, amount=1000, method=PaymentMethod.CASH)
    )

    assert change == (
        "Amount NPR 25,000; method eSewa; for Visa fee",
        "Amount NPR 1,000; method Cash; for PTE class",
    )


def test_a_deleted_payment_keeps_every_detail() -> None:
    """A deletion keeps the amount, Fee Type, method and date."""
    assert describe_deleted_payment(payment()) == "NPR 25,000, Visa fee, eSewa, 30 Oct 2026"
