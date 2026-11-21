from __future__ import annotations

from dataclasses import dataclass

from fee_types.models import FeeType


@dataclass(frozen=True)
class NewFee:
    """A Fee to add or a Fee's new details: its Fee Type, a note, and the amount in whole NPR."""

    fee_type: FeeType
    amount: int
    note: str = ""
