"""A Student's Balance: what they still owe, worked out from their Fees and Payments (spec 76b).

Balances are always calculated, never typed in or stored (GLOSSARY: Balance).
"""

from __future__ import annotations

from dataclasses import dataclass

NEGATIVE_TOTAL_MESSAGE = "Fee and Payment totals are never negative."


@dataclass(frozen=True)
class Balance:
    """Total Fees, total paid, what is still to pay, and anything paid beyond the Fees."""

    fees: int
    paid: int
    still_to_pay: int
    overpaid: int

    @property
    def is_paid_in_full(self) -> bool:
        """True when there are Fees and nothing of them is left to pay."""
        return self.fees > 0 and self.still_to_pay == 0

    @property
    def collected(self) -> int:
        """What has been paid towards the Fees: never more than the Fees themselves."""
        return min(self.paid, self.fees)


def balance_of(fees: int, paid: int) -> Balance:
    """The Balance for these totals in whole NPR.

    Paying more than the Fees owes nothing (never a negative Balance); the extra is `overpaid`.

    Raises:
        ValueError: A total is negative.
    """
    if fees < 0 or paid < 0:
        raise ValueError(NEGATIVE_TOTAL_MESSAGE)
    return Balance(
        fees=fees, paid=paid, still_to_pay=max(0, fees - paid), overpaid=max(0, paid - fees)
    )
