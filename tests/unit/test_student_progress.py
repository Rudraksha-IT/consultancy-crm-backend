"""How far along a Student is, and their next step (spec 22a, 113; issues 33, 74, 75).

One rule for Home and the Student's page, for every Stage.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.utils import timezone

from applications.appointments import VisaDates
from applications.statuses import ApplicationStatus, VisaStatus
from home.progress import ApplicationAt, Progress, VisaAt, progress_of
from students.models import Stage

DEAKIN = "Deakin University"
MONASH = "Monash University"
A = ApplicationStatus
V = VisaStatus
TODAY = date(2026, 11, 14)


def app(status: ApplicationStatus, university: str = DEAKIN) -> ApplicationAt:
    """One of the Student's Applications."""
    return ApplicationAt(status=status, university=university)


class TestStagesSetByHand:
    @pytest.mark.parametrize(
        ("stage", "expected"),
        [
            (Stage.LEAD, Progress(8, "Have the first counselling session")),
            (Stage.COUNSELLING, Progress(20, "Choose a course and apply")),
        ],
    )
    def test_lead_and_counselling_have_a_fixed_place(
        self, stage: Stage, expected: Progress
    ) -> None:
        assert progress_of(stage, [], []) == expected

    def test_a_lost_student_is_at_nothing_with_the_reason_as_the_next_step(self) -> None:
        assert progress_of(Stage.LOST, [], [], lost_reason="Went to Japan") == Progress(
            0, "Lost: Went to Japan"
        )


class TestApplying:
    def test_an_applying_student_waits_for_an_offer_from_every_open_application(self) -> None:
        applications = [app(A.APPLIED), app(A.REJECTED, "Torrens"), app(A.APPLIED, MONASH)]

        assert progress_of(Stage.APPLYING, applications, []) == Progress(
            45, f"Waiting for an offer, {DEAKIN}, {MONASH}"
        )

    def test_with_every_application_rejected_new_options_are_needed(self) -> None:
        applications = [app(A.REJECTED), app(A.REJECTED, MONASH)]

        assert progress_of(Stage.APPLYING, applications, []) == Progress(35, "Find new options")


class TestOffer:
    def test_a_student_at_offer_chooses_one_of_the_offers_received(self) -> None:
        applications = [app(A.OFFER), app(A.APPLIED, MONASH), app(A.OFFER, "Torrens")]

        assert progress_of(Stage.OFFER, applications, []) == Progress(
            70, "Choose an offer (2 received)"
        )

    def test_with_a_chosen_offer_the_visa_papers_come_next(self) -> None:
        chosen = ApplicationAt(status=A.OFFER, university=MONASH, is_chosen=True)
        applications = [app(A.OFFER), chosen]

        assert progress_of(Stage.OFFER, applications, []) == Progress(
            76, "Prepare the visa papers, Monash University"
        )


def visa(status: VisaStatus, **dates: datetime | None) -> VisaAt:
    """One of the Student's Visas, with any appointments booked."""
    return VisaAt(status=status, dates=VisaDates(**dates))


def at(days: int, hour: int = 10) -> datetime:
    """`days` after TODAY at `hour` in the Consultancy's time zone."""
    return timezone.make_aware(datetime.combine(TODAY + timedelta(days=days), time(hour)))


class TestVisa:
    def test_with_nothing_booked_biometrics_come_first(self) -> None:
        found = progress_of(
            Stage.VISA, [app(A.OFFER)], [visa(V.REFUSED), visa(V.APPLIED)], today=TODAY
        )

        assert found == Progress(88, "Book biometrics")

    @pytest.mark.parametrize(
        ("dates", "step"),
        [
            ({"biometrics": at(2)}, "Biometrics coming up"),
            ({"biometrics": at(-5), "medical": at(1)}, "Medical coming up"),
            ({"medical": at(9), "interview": at(3)}, "Interview coming up"),
            ({"biometrics": at(0, 0)}, "Biometrics coming up"),
        ],
    )
    def test_the_soonest_appointment_from_today_on_leads(
        self, dates: dict[str, datetime], step: str
    ) -> None:
        found = progress_of(Stage.VISA, [app(A.OFFER)], [visa(V.APPLIED, **dates)], today=TODAY)

        assert found == Progress(88, step)

    def test_after_biometrics_with_nothing_coming_up_they_wait_for_the_embassy(self) -> None:
        found = progress_of(
            Stage.VISA, [app(A.OFFER)], [visa(V.APPLIED, biometrics=at(-1))], today=TODAY
        )

        assert found == Progress(88, "Waiting for the embassy")

    def test_a_granted_visa_is_the_end_of_the_journey(self) -> None:
        found = progress_of(Stage.VISA, [app(A.OFFER)], [visa(V.REFUSED), visa(V.GRANTED)])

        assert found == Progress(
            100, "Visa granted. Help with flights and pre-departure.", is_done=True
        )

    def test_only_a_granted_visa_is_done(self) -> None:
        assert not progress_of(Stage.VISA, [app(A.OFFER)], [visa(V.APPLIED)]).is_done
