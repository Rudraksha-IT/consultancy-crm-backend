"""A Visa's Biometrics, Medical and Interview dates, and which one is next (spec 62a; issue 75)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.utils import timezone

from applications.appointments import Appointment, Booked, VisaDates, next_appointment, upcoming

TODAY = date(2026, 11, 14)


def at(days: int, hour: int = 10) -> datetime:
    """`days` after TODAY at `hour` in the Consultancy's time zone."""
    return timezone.make_aware(datetime.combine(TODAY + timedelta(days=days), time(hour)))


class TestAppointments:
    def test_the_three_appointments_in_order(self) -> None:
        assert [kind.label for kind in Appointment] == ["Biometrics", "Medical", "Interview"]

    def test_booked_lists_only_the_dates_set_soonest_first(self) -> None:
        dates = VisaDates(biometrics=at(5), interview=at(2))

        assert dates.booked() == [
            Booked(Appointment.INTERVIEW, at(2)),
            Booked(Appointment.BIOMETRICS, at(5)),
        ]

    def test_nothing_booked(self) -> None:
        assert VisaDates().booked() == []
        assert next_appointment(VisaDates(), TODAY) is None
        assert upcoming(VisaDates(), TODAY) is None


class TestNext:
    def test_the_soonest_from_today_on_is_next(self) -> None:
        dates = VisaDates(biometrics=at(-3), medical=at(4), interview=at(1))

        assert next_appointment(dates, TODAY) == Booked(Appointment.INTERVIEW, at(1))
        assert upcoming(dates, TODAY) == Booked(Appointment.INTERVIEW, at(1))

    def test_earlier_today_still_counts_as_coming_up(self) -> None:
        dates = VisaDates(medical=at(0, 0))

        assert upcoming(dates, TODAY) == Booked(Appointment.MEDICAL, at(0, 0))

    def test_with_all_of_them_past_the_latest_is_shown_and_none_is_coming_up(self) -> None:
        dates = VisaDates(biometrics=at(-9), medical=at(-2))

        assert next_appointment(dates, TODAY) == Booked(Appointment.MEDICAL, at(-2))
        assert upcoming(dates, TODAY) is None
