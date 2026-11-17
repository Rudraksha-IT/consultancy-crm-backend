"""The pure pieces of CSV exports: formula neutralising and Intake words (spec 31b)."""

from __future__ import annotations

import pytest

from exports.lists import intake_text
from exports.services import safe_cell


class TestSafeCell:
    @pytest.mark.parametrize("start", ["=", "+", "-", "@", "\t", "\r"])
    def test_a_formula_start_is_prefixed(self, start: str) -> None:
        assert safe_cell(f"{start}1+1") == f"'{start}1+1"

    @pytest.mark.parametrize("value", ["", "Sita", "98 4100", " =1", "'=1", "a=b", "सीता"])
    def test_anything_else_is_unchanged(self, value: str) -> None:
        assert safe_cell(value) == value


class TestIntakeText:
    @pytest.mark.parametrize(
        ("intake", "words"),
        [("2027-02", "Feb 2027"), ("2030-12", "Dec 2030"), ("2028-01", "Jan 2028")],
    )
    def test_intakes_read_as_people_write_them(self, intake: str, words: str) -> None:
        assert intake_text(intake) == words
