"""Balance = total Fees minus total Payments, never stored (spec 76b; GLOSSARY: Balance)."""

from __future__ import annotations

import pytest

from fees.balance import Balance, balance_of


class TestBalanceOf:
    def test_what_is_still_to_pay_is_fees_minus_payments(self) -> None:
        assert balance_of(fees=80000, paid=25000) == Balance(
            fees=80000, paid=25000, still_to_pay=55000, overpaid=0
        )

    def test_paid_in_full_owes_nothing(self) -> None:
        balance = balance_of(fees=40000, paid=40000)

        assert (balance.still_to_pay, balance.overpaid) == (0, 0)
        assert balance.is_paid_in_full

    def test_overpayment_shows_as_nothing_owed_and_how_much_more_was_paid(self) -> None:
        balance = balance_of(fees=40000, paid=45000)

        assert balance == Balance(fees=40000, paid=45000, still_to_pay=0, overpaid=5000)
        assert balance.is_paid_in_full

    def test_payments_without_fees_are_all_overpaid(self) -> None:
        assert balance_of(fees=0, paid=18000) == Balance(
            fees=0, paid=18000, still_to_pay=0, overpaid=18000
        )

    def test_no_fees_and_no_payments(self) -> None:
        balance = balance_of(fees=0, paid=0)

        assert balance == Balance(fees=0, paid=0, still_to_pay=0, overpaid=0)
        assert not balance.is_paid_in_full

    def test_owing_is_not_paid_in_full(self) -> None:
        assert not balance_of(fees=100, paid=99).is_paid_in_full

    @pytest.mark.parametrize(("fees", "paid"), [(-1, 0), (0, -1)])
    def test_negative_totals_are_refused(self, fees: int, paid: int) -> None:
        with pytest.raises(ValueError, match="never negative"):
            balance_of(fees=fees, paid=paid)

    def test_collected_counts_only_up_to_the_fees(self) -> None:
        assert balance_of(fees=40000, paid=45000).collected == 40000
        assert balance_of(fees=40000, paid=10000).collected == 10000
