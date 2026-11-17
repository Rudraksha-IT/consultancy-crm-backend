"""The Stage engine: Stage + Applications + Visas + what changed -> new Stage (issues 74, 75).

Lead, Counselling and Lost are set by hand; Applying, Offer and Visa follow from Applications,
Offer Letters and Visas.
"""

from __future__ import annotations

import pytest

from applications.stage_engine import (
    ApplicationAdded,
    ApplicationChanged,
    VisaChanged,
    stage_after,
)
from applications.statuses import ApplicationStatus, OfferKind, VisaStatus
from students.models import Stage

A = ApplicationStatus
V = VisaStatus
EARLIER_THAN_APPLYING = (Stage.LEAD, Stage.COUNSELLING)
EARLIER_THAN_OFFER = (*EARLIER_THAN_APPLYING, Stage.APPLYING)


def after_added(stage: Stage, *statuses: ApplicationStatus) -> Stage:
    """The Stage after an Application was added; `statuses` are all of them, the new one too."""
    return stage_after(stage, statuses, (), ApplicationAdded())


def after_application(stage: Stage, *statuses: ApplicationStatus) -> Stage:
    """The Stage after the first of `statuses` changed to what it is now."""
    return stage_after(stage, statuses, (), ApplicationChanged(statuses[0]))


def after_visa(
    stage: Stage, *statuses: VisaStatus, applications: tuple[ApplicationStatus, ...] = (A.OFFER,)
) -> Stage:
    """The Stage after the first of `statuses` changed to what it is now."""
    return stage_after(stage, applications, statuses, VisaChanged(statuses[0]))


class TestStatuses:
    def test_stages_are_the_journey_then_lost(self) -> None:
        assert [stage.label for stage in Stage] == [
            "Lead",
            "Counselling",
            "Applying",
            "Offer",
            "Visa",
            "Lost",
        ]

    def test_application_statuses_are_applied_offer_and_rejected(self) -> None:
        assert [status.label for status in ApplicationStatus] == ["Applied", "Offer", "Rejected"]

    def test_an_offer_is_conditional_or_unconditional(self) -> None:
        assert [kind.label for kind in OfferKind] == ["Conditional", "Unconditional"]


class TestApplying:
    @pytest.mark.parametrize("stage", EARLIER_THAN_APPLYING)
    def test_the_first_application_moves_an_earlier_student_to_applying(self, stage: Stage) -> None:
        assert after_added(stage, A.APPLIED) == Stage.APPLYING

    @pytest.mark.parametrize("stage", [Stage.APPLYING, Stage.OFFER, Stage.VISA])
    def test_a_student_already_applying_or_further_stays(self, stage: Stage) -> None:
        assert after_added(stage, A.APPLIED, A.OFFER) == stage

    @pytest.mark.parametrize("stage", [Stage.APPLYING, Stage.OFFER])
    def test_every_application_rejected_leaves_the_stage_alone(self, stage: Stage) -> None:
        assert after_application(stage, A.REJECTED, A.REJECTED) == stage

    @pytest.mark.parametrize("stage", EARLIER_THAN_APPLYING)
    def test_setting_applied_or_rejected_by_hand_moves_nobody(self, stage: Stage) -> None:
        assert after_application(stage, A.APPLIED, A.REJECTED) == stage
        assert after_application(stage, A.REJECTED) == stage


class TestOffer:
    @pytest.mark.parametrize("stage", EARLIER_THAN_OFFER)
    def test_the_first_offer_moves_the_student_to_offer(self, stage: Stage) -> None:
        assert after_application(stage, A.OFFER, A.REJECTED) == Stage.OFFER

    @pytest.mark.parametrize("stage", [Stage.OFFER, Stage.VISA])
    def test_a_student_at_offer_or_visa_stays(self, stage: Stage) -> None:
        assert after_application(stage, A.OFFER, A.OFFER) == stage


class TestLost:
    @pytest.mark.parametrize("status", list(ApplicationStatus))
    def test_a_lost_student_is_never_moved_by_an_application(
        self, status: ApplicationStatus
    ) -> None:
        assert after_application(Stage.LOST, status) == Stage.LOST
        assert after_added(Stage.LOST, status) == Stage.LOST

    @pytest.mark.parametrize("status", list(VisaStatus))
    def test_a_lost_student_is_never_moved_by_a_visa(self, status: VisaStatus) -> None:
        assert after_visa(Stage.LOST, status) == Stage.LOST


class TestVisas:
    def test_visa_statuses_are_applied_granted_and_refused(self) -> None:
        assert [status.label for status in VisaStatus] == ["Applied", "Granted", "Refused"]

    @pytest.mark.parametrize("stage", (*EARLIER_THAN_OFFER, Stage.OFFER))
    def test_a_visa_applied_for_moves_the_student_to_visa(self, stage: Stage) -> None:
        assert after_visa(stage, V.APPLIED) == Stage.VISA

    @pytest.mark.parametrize("stage", [Stage.OFFER, Stage.VISA])
    def test_a_granted_visa_keeps_the_student_at_visa(self, stage: Stage) -> None:
        assert after_visa(stage, V.GRANTED, V.REFUSED) == Stage.VISA

    def test_a_refused_visa_moves_them_back_to_offer_when_an_offer_is_left(self) -> None:
        assert after_visa(Stage.VISA, V.REFUSED, applications=(A.REJECTED, A.OFFER)) == Stage.OFFER

    def test_a_refused_visa_with_no_offer_left_moves_them_back_to_counselling(self) -> None:
        assert (
            after_visa(Stage.VISA, V.REFUSED, applications=(A.REJECTED, A.APPLIED))
            == Stage.COUNSELLING
        )

    @pytest.mark.parametrize("other", [V.APPLIED, V.GRANTED])
    def test_a_refused_visa_with_another_applied_or_granted_keeps_them_at_visa(
        self, other: VisaStatus
    ) -> None:
        assert after_visa(Stage.VISA, V.REFUSED, other) == Stage.VISA

    def test_a_refused_visa_moves_only_a_student_at_visa(self) -> None:
        assert after_visa(Stage.OFFER, V.REFUSED) == Stage.OFFER

    def test_a_granted_visa_set_back_to_applied_keeps_them_at_visa(self) -> None:
        assert after_visa(Stage.VISA, V.APPLIED) == Stage.VISA
