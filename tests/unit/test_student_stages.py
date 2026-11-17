"""Which Stage moves Staff may make by hand (spec 38-41, 112; issues 11, 74). A pure function."""

from __future__ import annotations

import pytest

from students.models import Stage
from students.stages import (
    ALREADY_AT_STAGE_MESSAGE,
    AUTOMATIC_STAGE_MESSAGE,
    LOST_GOES_BACK_TO_COUNSELLING_MESSAGE,
    STAGE_FOLLOWS_APPLICATIONS_MESSAGE,
    manual_move_refusal,
)

MANUAL = (Stage.LEAD, Stage.COUNSELLING)
AUTOMATIC = (Stage.APPLYING, Stage.OFFER, Stage.VISA)


class TestMovingByHand:
    def test_lead_and_counselling_move_freely_between_each_other(self) -> None:
        assert manual_move_refusal(Stage.LEAD, Stage.COUNSELLING) is None
        assert manual_move_refusal(Stage.COUNSELLING, Stage.LEAD) is None

    @pytest.mark.parametrize("current", [*MANUAL, *AUTOMATIC])
    def test_any_student_who_is_not_lost_can_be_marked_lost(self, current: Stage) -> None:
        assert manual_move_refusal(current, Stage.LOST) is None

    def test_a_lost_student_comes_back_to_counselling(self) -> None:
        assert manual_move_refusal(Stage.LOST, Stage.COUNSELLING) is None


class TestRefusedMoves:
    @pytest.mark.parametrize("current", [*MANUAL, *AUTOMATIC, Stage.LOST])
    @pytest.mark.parametrize("target", AUTOMATIC)
    def test_applying_offer_and_visa_are_never_set_by_hand(
        self, current: Stage, target: Stage
    ) -> None:
        assert manual_move_refusal(current, target) == AUTOMATIC_STAGE_MESSAGE

    @pytest.mark.parametrize("stage", list(Stage))
    def test_moving_to_the_stage_they_are_at_is_refused(self, stage: Stage) -> None:
        expected = AUTOMATIC_STAGE_MESSAGE if stage in AUTOMATIC else ALREADY_AT_STAGE_MESSAGE

        assert manual_move_refusal(stage, stage) == expected

    def test_a_lost_student_comes_back_only_to_counselling(self) -> None:
        assert manual_move_refusal(Stage.LOST, Stage.LEAD) == LOST_GOES_BACK_TO_COUNSELLING_MESSAGE

    @pytest.mark.parametrize("current", AUTOMATIC)
    @pytest.mark.parametrize("target", MANUAL)
    def test_a_student_applying_or_further_is_not_moved_back_by_hand(
        self, current: Stage, target: Stage
    ) -> None:
        assert manual_move_refusal(current, target) == STAGE_FOLLOWS_APPLICATIONS_MESSAGE
