"""The calendar month the Money out list covers (issue 36)."""

from __future__ import annotations

from datetime import date

import pytest

from expenses.types import Month


class TestMonth:
    @pytest.mark.parametrize(
        ("month", "first", "next_first"),
        [
            (Month(2026, 10), date(2026, 10, 1), date(2026, 11, 1)),
            (Month(2026, 12), date(2026, 12, 1), date(2027, 1, 1)),
            (Month(2027, 2), date(2027, 2, 1), date(2027, 3, 1)),
        ],
    )
    def test_a_month_runs_from_its_first_day_to_the_next_months(
        self, month: Month, first: date, next_first: date
    ) -> None:
        assert (month.first_day, month.next_first_day) == (first, next_first)

    def test_a_month_is_written_as_yyyy_mm(self) -> None:
        assert str(Month(2026, 3)) == "2026-03"
