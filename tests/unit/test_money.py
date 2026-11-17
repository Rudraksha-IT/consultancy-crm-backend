"""Amounts in NPR as people in Nepal write them (issue 18)."""

from __future__ import annotations

import pytest

from payments.money import format_npr


@pytest.mark.parametrize(
    ("amount", "written"),
    [
        (1, "NPR 1"),
        (999, "NPR 999"),
        (1000, "NPR 1,000"),
        (25000, "NPR 25,000"),
        (100000, "NPR 1,00,000"),
        (125000, "NPR 1,25,000"),
        (9999999, "NPR 99,99,999"),
        (10000000, "NPR 1,00,00,000"),
        (100000000, "NPR 10,00,00,000"),
    ],
)
def test_amounts_use_lakh_and_crore_grouping(amount: int, written: str) -> None:
    """The last three digits, then groups of two."""
    assert format_npr(amount) == written
