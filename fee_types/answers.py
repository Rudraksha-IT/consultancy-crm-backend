"""Fee Types as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from fee_types.models import FeeType


def fee_type_answer(fee_type: FeeType, *, with_uses: bool) -> dict[str, object]:
    """One Fee Type; how many Fees and Payments use it only for Admins (Configuration)."""
    uses = (
        {"fees": getattr(fee_type, "fee_uses", 0), "payments": getattr(fee_type, "payment_uses", 0)}
        if with_uses
        else None
    )
    return {
        "id": fee_type.pk,
        "name": fee_type.name,
        "hidden": fee_type.hidden,
        "other": fee_type.is_other,
        "uses": uses,
    }


def fee_type_ref(fee_type: FeeType) -> dict[str, object]:
    """The Fee Type a Fee or Payment is for, as its answer names it."""
    return {"id": fee_type.pk, "name": fee_type.name}
