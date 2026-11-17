"""A Course's Intakes are tidied before they are stored (spec 47, 102)."""

from __future__ import annotations

from datetime import date

from catalogue.intakes import first_gone_by, gone_by_message, intake_label, month_of, tidy_intakes


class TestTidyIntakes:
    def test_intakes_are_sorted_oldest_first(self) -> None:
        assert tidy_intakes(["2027-11", "2027-02", "2026-09"]) == [
            "2026-09",
            "2027-02",
            "2027-11",
        ]

    def test_repeated_intakes_are_kept_once(self) -> None:
        assert tidy_intakes(["2027-02", "2027-02", "2027-07"]) == ["2027-02", "2027-07"]

    def test_no_intakes_stay_none(self) -> None:
        assert tidy_intakes([]) == []


class TestPastMonths:
    """New Intakes can't be in a month gone by; those a Course has stay (issue 92)."""

    def test_the_month_of_a_day(self) -> None:
        assert month_of(date(2030, 5, 31)) == "2030-05"

    def test_an_intake_is_shown_as_month_and_year(self) -> None:
        assert intake_label("2030-04") == "Apr 2030"
        assert intake_label("2031-12") == "Dec 2031"

    def test_this_month_and_later_are_fine(self) -> None:
        assert first_gone_by(["2030-05", "2031-01"], (), date(2030, 5, 31)) is None

    def test_the_earliest_new_month_gone_by_is_named(self) -> None:
        gone = first_gone_by(["2030-04", "2030-01", "2030-07"], (), date(2030, 5, 1))

        assert gone == "2030-01"

    def test_a_month_the_course_already_has_may_stay(self) -> None:
        assert first_gone_by(["2030-01", "2030-07"], {"2030-01"}, date(2030, 5, 1)) is None

    def test_the_refusal_names_the_month(self) -> None:
        assert gone_by_message("2030-01") == "Jan 2030 has gone by. Pick this month or a later one."
