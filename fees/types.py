from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NewFee:
    """A Fee to add or a Fee's new details: what it's for and the amount in whole NPR."""

    purpose: str
    amount: int
