"""Public services for Fee Types (spec 67-70). Call inside the Consultancy's schema.

Who may change the list is the views' job (Admins only). Changes are logged by id only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Count, IntegerField, Max, OuterRef, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce

from fee_types.exceptions import (
    DuplicateFeeTypeError,
    FeeTypeInUseError,
    FeeTypeOrderError,
    OtherFeeTypeIsFixedError,
    TooManyFeeTypesError,
)
from fee_types.models import FeeType
from fees.models import Fee
from payments.models import Payment
from staff.models import Staff

logger = logging.getLogger(__name__)

# Far more than any office sells; keeps every picker short.
MAX_FEE_TYPES = 50


@dataclass(frozen=True)
class FeeTypeChange:
    """What to change on a Fee Type; None leaves that part as it is."""

    name: str | None = None
    hidden: bool | None = None


def fee_types_in_order() -> QuerySet[FeeType]:
    """Every Fee Type, hidden ones too, in the office's order."""
    return FeeType.objects.order_by("position", "pk")


def fee_types_with_uses() -> QuerySet[FeeType]:
    """Every Fee Type in order, with how many Fees (`fee_uses`) and Payments (`payment_uses`)."""
    return fee_types_in_order().annotate(
        fee_uses=_uses(Fee.objects.all()), payment_uses=_uses(Payment.objects.all())
    )


def tidy_name(name: str) -> str:
    """The name with spaces trimmed and runs of spaces made one."""
    return " ".join(name.split())


def describe_for(fee_type_name: str, note: str) -> str:
    """What a Fee or Payment is for, as History and receipts write it: "Visa fee (first half)"."""
    return f"{fee_type_name} ({note})" if note else fee_type_name


def add_fee_type(name: str, by: Staff) -> FeeType:
    """Add a Fee Type at the end of the list, offered at once.

    Raises:
        TooManyFeeTypesError: The office already has `MAX_FEE_TYPES`.
        DuplicateFeeTypeError: Another Fee Type has this name, ignoring capitals and spaces.
    """
    name = tidy_name(name)
    with transaction.atomic():
        # One add or reorder at a time, so positions never clash.
        existing = list(FeeType.objects.select_for_update().order_by("pk"))
        if len(existing) >= MAX_FEE_TYPES:
            raise TooManyFeeTypesError
        _check_name_is_free(name, except_id=None)
        last = FeeType.objects.aggregate(last=Max("position"))["last"] or 0
        added = _saved(FeeType(name=name, position=last + 1))
    logger.info("Fee type added: fee_type_id=%s, by_staff_id=%s", added.pk, by.pk)
    return added


def change_fee_type(type_id: int, change: FeeTypeChange, by: Staff) -> FeeType:
    """Rename a Fee Type, or hide or show it. Fees and Payments keep it either way.

    Raises:
        FeeType.DoesNotExist: There is no such Fee Type.
        OtherFeeTypeIsFixedError: It is "Other".
        DuplicateFeeTypeError: Another Fee Type has the new name.
    """
    with transaction.atomic():
        fee_type = FeeType.objects.select_for_update().get(pk=type_id)
        if fee_type.is_other:
            raise OtherFeeTypeIsFixedError
        if change.name is not None:
            fee_type.name = tidy_name(change.name)
            _check_name_is_free(fee_type.name, except_id=fee_type.pk)
        if change.hidden is not None:
            fee_type.hidden = change.hidden
        fee_type = _saved(fee_type)
    logger.info("Fee type changed: fee_type_id=%s, by_staff_id=%s", fee_type.pk, by.pk)
    return fee_type


def delete_fee_type(type_id: int, by: Staff) -> None:
    """Delete a Fee Type no Fee or Payment uses.

    Raises:
        FeeType.DoesNotExist: There is no such Fee Type.
        OtherFeeTypeIsFixedError: It is "Other".
        FeeTypeInUseError: A Fee or Payment uses it; hide it instead.
    """
    with transaction.atomic():
        fee_type = FeeType.objects.select_for_update().get(pk=type_id)
        if fee_type.is_other:
            raise OtherFeeTypeIsFixedError
        used_by_fees = Fee.objects.filter(fee_type=fee_type).exists()
        if used_by_fees or Payment.objects.filter(fee_type=fee_type).exists():
            raise FeeTypeInUseError
        fee_type.delete()
    logger.info("Fee type deleted: fee_type_id=%s, by_staff_id=%s", type_id, by.pk)


def reorder_fee_types(order: list[UUID], by: Staff) -> None:
    """Put the Fee Types in this order: every Fee Type's UUID, each once.

    Raises:
        FeeTypeOrderError: The ids aren't exactly every Fee Type's, each once.
    """
    with transaction.atomic():
        fee_types = {t.public_id: t for t in FeeType.objects.select_for_update()}
        if len(order) != len(set(order)) or set(order) != set(fee_types):
            raise FeeTypeOrderError
        for position, type_id in enumerate(order, start=1):
            fee_types[type_id].position = position
        FeeType.objects.bulk_update(fee_types.values(), ["position"])
    logger.info("Fee types reordered: by_staff_id=%s", by.pk)


def _check_name_is_free(name: str, *, except_id: int | None) -> None:
    others = FeeType.objects.filter(name__iexact=name)
    if except_id is not None:
        others = others.exclude(pk=except_id)
    if others.exists():
        raise DuplicateFeeTypeError


def _saved(fee_type: FeeType) -> FeeType:
    try:
        with transaction.atomic():
            fee_type.save()
    except IntegrityError as error:  # pragma: no cover  # added by someone else meanwhile
        raise DuplicateFeeTypeError from error
    return fee_type


def _uses(rows: QuerySet[Fee] | QuerySet[Payment]) -> Coalesce:
    """How many of `rows` (Fees or Payments) use the outer Fee Type."""
    counted = (
        rows.filter(fee_type=OuterRef("pk"))
        .order_by()
        .values("fee_type")
        .annotate(n=Count("pk"))
        .values("n")
    )
    return Coalesce(Subquery(counted), Value(0), output_field=IntegerField())
